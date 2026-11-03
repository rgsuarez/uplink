"""Read-only macOS adapter. Persist typed, redacted evidence, never command output."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import ipaddress
import os
import plistlib
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, cast

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.oracle import ProxyConfig, proxy_config
from offline_agent.uplink.privacy import MAC_PATTERN, Redactor
from offline_agent.uplink.records import RawObservation

NS = "/usr/sbin/networksetup"
IP = "/usr/sbin/ipconfig"
SC = "/usr/sbin/scutil"
IF = "/sbin/ifconfig"
FIXED_READS = frozenset(
    {
        (NS, "-listallhardwareports"),
        (NS, "-listallnetworkservices"),
        (NS, "-listnetworkserviceorder"),
        (SC, "--dns"),
        (SC, "--proxy"),
        (SC, "--nwi"),
        ("/sbin/route", "-n", "get", "default"),
        ("/usr/sbin/netstat", "-rn"),
        (IF, "-l"),
    }
)
SERVICE_READS = (
    "-getinfo",
    "-getdnsservers",
    "-getsearchdomains",
    "-getwebproxy",
    "-getsecurewebproxy",
    "-getautoproxyurl",
    "-getproxyautodiscovery",
    "-getsocksfirewallproxy",
)
KINDS = (
    "wifi",
    "ethernet",
    "iphone_usb",
    "thunderbolt",
    "bluetooth",
    "loopback",
    "tunnel",
    "other",
)
INTERFACE_RE = re.compile(r"[a-z][a-z0-9]{0,14}")


@dataclass(frozen=True)
class ReadResult:
    text: str = field(default="", repr=False)
    status: str = "available"
    stderr: str = field(default="", repr=False)
    exit_code: int | None = None


class Reader(Protocol):
    def read(self, argv: tuple[str, ...]) -> ReadResult: ...


class ReadOnlyRunner:
    def read(self, argv: tuple[str, ...]) -> ReadResult:
        # Defense in depth: caller discovery membership is enforced separately below.
        allowed = argv in FIXED_READS
        if len(argv) == 3:
            allowed |= argv[0] == NS and argv[1] in (
                *SERVICE_READS,
                "-listpreferredwirelessnetworks",
            )
            allowed |= argv[0] == IP and argv[1] in {"getifaddr", "getpacket", "getsummary"}
            allowed &= bool(argv[2]) and not argv[2].startswith("-") and "\0" not in argv[2]
        if len(argv) == 2 and argv[0] == IF:
            allowed |= bool(INTERFACE_RE.fullmatch(argv[1]))
        if not allowed:
            raise ContractError("non_readonly_command_refused")
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except FileNotFoundError:
            return ReadResult(status="missing_tool")
        except PermissionError:
            return ReadResult(status="permission_denied")
        except subprocess.TimeoutExpired:
            return ReadResult(status="timeout")
        except OSError:
            return ReadResult(status="unavailable")
        combined = (result.stdout + result.stderr).lower()
        if any(
            x in combined for x in ("permission denied", "not permitted", "administrator access")
        ):
            return ReadResult(result.stdout, "permission_denied", result.stderr, result.returncode)
        if result.returncode:
            return ReadResult(result.stdout, "command_failed", result.stderr, result.returncode)
        if len(result.stdout) > 262144:
            return ReadResult(status="output_limit")
        return ReadResult(result.stdout, "available", result.stderr, result.returncode)


@dataclass(frozen=True)
class Observation:
    id: str
    source: str
    target: str | None
    status: str


@dataclass(frozen=True)
class ProxyVital:
    kind: str
    enabled: bool | None
    host_id: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class ServiceVital:
    id: str
    interface: str | None
    kind: str
    enabled: bool
    address_mode: str
    dns_mode: str
    dns: tuple[str, ...]
    search_domain_ids: tuple[str, ...]
    proxies: tuple[ProxyVital, ...]


@dataclass(frozen=True)
class InterfaceVital:
    id: str
    kind: str
    up: bool | None
    active: bool | None
    addresses: tuple[str, ...]
    leased: bool
    connection: str


@dataclass(frozen=True)
class RouteVital:
    destination: str
    gateway: str
    interface: str


@dataclass(frozen=True)
class Candidate:
    id: str
    kind: str
    service_id: str | None
    interface: str
    network_id: str | None
    evidence: tuple[str, ...]
    score: int
    eligible: bool
    available: bool
    hotspot_possible: bool = False


@dataclass(frozen=True)
class Vitals:
    services: tuple[ServiceVital, ...]
    interfaces: tuple[InterfaceVital, ...]
    service_order: tuple[str, ...]
    default_interface: str | None
    default_gateway: str | None
    routes: tuple[RouteVital, ...]
    system_dns: tuple[str, ...]
    nwi_interfaces: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    observations: tuple[Observation, ...]

    def as_json(self) -> dict[str, Json]:
        # JSON roundtrip normalizes immutable tuples into the shared Json contract.
        import json

        return object_json(json.loads(json.dumps(asdict(self))))


@dataclass(frozen=True)
class Inspection:
    vitals: Vitals
    proxies: ProxyConfig = field(repr=False)
    # Names exist only in memory, for a future confirmation UI, never persisted.
    service_names: Mapping[str, str] = field(repr=False)


def address(value: str) -> str | None:
    try:
        # Interface scopes are represented separately, never copied as arbitrary text.
        return str(ipaddress.ip_address(value.split("%", 1)[0]))
    except ValueError:
        return None


def addresses(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(ip for token in re.split(r"[\s,{}]+", text) if (ip := address(token)))
    )


def kind_for(name: str, interface: str = "") -> str:
    lower = name.lower()
    if interface.startswith(("utun", "ipsec", "ppp", "gif", "stf")):
        return "tunnel"
    if interface.startswith("lo"):
        return "loopback"
    if "wi-fi" in lower or "airport" in lower:
        return "wifi"
    if "iphone" in lower and "usb" in lower:
        return "iphone_usb"
    if "thunderbolt" in lower or "bridge" in lower:
        return "thunderbolt"
    if "bluetooth" in lower or lower == "pan":
        return "bluetooth"
    if any(x in lower for x in ("ethernet", "usb", "lan")):
        return "ethernet"
    return "other"


def parse_hardware(text: str) -> dict[str, str]:
    result = {}
    for match in re.finditer(r"Hardware Port: ([^\n]+)\nDevice: ([^\n]+)", text):
        if INTERFACE_RE.fullmatch(match[2].strip()):
            result[match[2].strip()] = kind_for(match[1], match[2])
    return result


def parse_services(text: str) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (line.removeprefix("*"), not line.startswith("*"))
        for line in (x.strip() for x in text.splitlines())
        if line and not line.startswith("An asterisk") and not line.startswith("-")
    )


def parse_service_order(text: str) -> tuple[tuple[str, str | None], ...]:
    result = []
    pending: str | None = None
    for line in text.splitlines():
        match = re.match(r"\((?:\d+|\*)\) (.+)", line)
        if match:
            pending = match[1].removeprefix("*")
        device = re.search(r"Device: ([a-z][a-z0-9]*)\)", line)
        if pending is not None and line.startswith("(Hardware Port:"):
            result.append((pending, device[1] if device else None))
            pending = None
    return tuple(result)


def parse_interface(interface: str, kind: str, text: str, ip: str, packet: str) -> InterfaceVital:
    flag = re.search(r"flags=\d+<([^>]+)>", text)
    up = "UP" in flag[1].split(",") if flag else None
    status = re.search(r"status: (active|inactive)", text)
    active = status[1] == "active" if status else None
    current = list(addresses(ip))
    for match in re.finditer(r"\binet6? ([^\s]+)", text):
        parsed = address(match[1])
        if parsed and parsed not in current:
            current.append(parsed)
    lease_ip = re.search(r"(?:yiaddr|yi_address)\s*[:=]\s*([^\s]+)", packet)
    leased = bool(
        lease_ip
        and address(lease_ip[1]) not in {None, _public_fixture("@PUBLIC_IPV4_001@")}
        and address(lease_ip[1]) in current
    )
    usable = any(
        not ipaddress.ip_address(x).is_loopback
        and not ipaddress.ip_address(x).is_link_local
        and not ipaddress.ip_address(x).is_unspecified
        for x in current
    )
    # SSID association strings are deliberately ignored; macOS 26 hides that evidence.
    connection = (
        "leased"
        if leased and usable
        else "addressed"
        if usable
        else ("down" if up is False else "unknown")
    )
    return InterfaceVital(interface, kind, up, active, tuple(current), leased, connection)


def parse_proxy(kind: str, text: str | None, redactor: Redactor) -> ProxyVital:
    if text is None:
        return ProxyVital(kind, None)
    fields = dict(re.findall(r"^([^:\n]+):[ \t]*([^\n]*)", text, re.M))
    enabled_text = fields.get("Enabled", fields.get("Auto Proxy Discovery", "")).strip().lower()
    enabled = {"yes": True, "on": True, "1": True, "no": False, "off": False, "0": False}.get(
        enabled_text
    )
    host = fields.get("Server", fields.get("URL", "")).strip()
    raw_port = fields.get("Port", "").strip()
    port = int(raw_port) if raw_port.isdigit() and 1 <= int(raw_port) <= 65535 else None
    return ProxyVital(kind, enabled, redactor.handle("host", host) if host else None, port)


def parse_routes(text: str, interfaces: set[str], redactor: Redactor) -> tuple[RouteVital, ...]:
    result = []
    for line in text.splitlines():
        columns = line.split()
        interface = next((x for x in reversed(columns) if x in interfaces), None)
        if len(columns) < 3 or interface is None:
            continue
        dest, gateway = columns[:2]
        network = re.sub(r"%[^/]+", "", dest)
        number, separator, prefix = network.partition("/")
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,2}", number):
            octets = number.split(".")
            network = ".".join(octets + ["0"] * (4 - len(octets)))
            network += "/" + (prefix if separator else str(len(octets) * 8))
        try:
            destination = str(ipaddress.ip_network(network, strict=False))
        except ValueError:
            destination = "default" if dest == "default" else redactor.handle("route", dest)
        parsed_gateway = (
            redactor.handle("interface", gateway, remember=False)
            if gateway in interfaces
            else redactor.handle("link", gateway, remember=False)
            if re.fullmatch(r"link#\d+", gateway)
            else redactor.handle("mac", gateway)
            if MAC_PATTERN.fullmatch(gateway)
            else address(gateway) or redactor.handle("gateway", gateway)
        )
        result.append(RouteVital(destination, parsed_gateway, interface))
    return tuple(result)


EVIDENCE_SCORES = {
    "verified_online_snapshot": 100,
    "prior_lease": 80,
    "current_lease": 60,
    "current_address": 40,
    "saved_network": 30,
    "configured_service": 10,
}


def rank_candidates(candidates: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
    return tuple(sorted(candidates, key=lambda c: (-c.score, not c.available, c.id)))


def candidate(
    redactor: Redactor,
    kind: str,
    service_id: str | None,
    interface: str,
    network_id: str | None,
    evidence: tuple[str, ...],
    available: bool,
    hotspot_possible: bool = False,
) -> Candidate:
    evidence = tuple(sorted(set(evidence)))
    score = max((EVIDENCE_SCORES[e] for e in evidence), default=0)
    eligible = bool(
        set(evidence)
        & {"verified_online_snapshot", "prior_lease", "current_lease", "saved_network"}
    )
    return Candidate(
        redactor.handle("candidate", f"{service_id}:{interface}:{network_id}"),
        kind,
        service_id,
        interface,
        network_id,
        evidence,
        score,
        eligible,
        available,
        hotspot_possible,
    )


def prior_leases(directory: Path, redactor: Redactor) -> tuple[set[str], str]:
    """Read only bounded, already-readable DHCP plist metadata, never root/keychain."""
    found: set[str] = set()
    try:
        paths = sorted(directory.iterdir())[:128]
        for path in paths:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                continue
            match = re.match(r"([a-z]+\d+)[,._-]", path.name)
            if not match:
                continue
            try:
                data: object = plistlib.loads(path.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            if not isinstance(data, dict):
                continue
            row = cast(dict[str, object], data)
            # Register all identifier strings but emit only interface + lease existence.
            for key, value in row.items():
                if isinstance(value, str) and any(
                    x in key.lower() for x in ("ssid", "host", "name", "mac")
                ):
                    redactor.handle("identifier", value)
            assigned = row.get("IPAddress", row.get("ip_address"))
            if isinstance(assigned, str) and address(assigned) not in {None, _public_fixture("@PUBLIC_IPV4_001@")}:
                found.add(match[1])
    except PermissionError:
        return found, "permission_denied"
    except OSError:
        return found, "unavailable"
    return found, "available"


class MacDiagnostics:
    def __init__(
        self,
        reader: Reader,
        redactor: Redactor,
        *,
        budget: float = 60,
        on_read: Callable[[RawObservation], None] | None = None,
    ) -> None:
        self.reader, self.redactor, self.budget = reader, redactor, budget
        self.on_read = on_read
        self.observations: list[Observation] = []
        self.services: set[str] = set()
        self.interfaces: set[str] = set()
        self.started = 0.0

    def read(self, argv: tuple[str, ...], target: str | None = None) -> ReadResult:
        allowed = argv in FIXED_READS
        if len(argv) == 3 and argv[0] == NS and argv[1] in SERVICE_READS:
            allowed |= argv[2] in self.services
        if len(argv) == 3 and (
            argv[0] == IP or (argv[0] == NS and argv[1] == "-listpreferredwirelessnetworks")
        ):
            allowed |= argv[2] in self.interfaces
        if len(argv) == 2 and argv[0] == IF:
            allowed |= argv[1] in self.interfaces
        if not allowed:
            raise ContractError("diagnostic_argument_not_discovered")
        command_started = time.monotonic()
        result = (
            ReadResult(status="budget_exhausted")
            if time.monotonic() - self.started > self.budget
            else self.reader.read(argv)
        )
        self.redactor.learn_command(argv, result.text)
        if self.on_read is not None:
            self.on_read(
                RawObservation(
                    argv,
                    result.text,
                    result.stderr,
                    result.exit_code,
                    (time.monotonic() - command_started) * 1000,
                    result.status,
                )
            )
        # Register known sensitive shapes to independently scan the final serialization.
        for value in MAC_PATTERN.findall(result.text):
            self.redactor.handle("mac", value)
        for value in re.findall(r"(?i)(?:[a-z][a-z0-9_-]*\.)+[a-z][a-z0-9_-]*", result.text):
            self.redactor.handle("host", value)
        source = (
            Path(argv[0]).name
            + ":"
            + (argv[1] if argv[0] != IF or argv[1] == "-l" else "interface")
        )
        self.observations.append(
            Observation(f"read-{len(self.observations) + 1:03d}", source, target, result.status)
        )
        return result

    def inspect(self, *, env: Mapping[str, str], lease_directory: Path | None = None) -> Inspection:
        self.started = time.monotonic()
        self.observations = []
        hardware = parse_hardware(self.read((NS, "-listallhardwareports")).text)
        listed = parse_services(self.read((NS, "-listallnetworkservices")).text)
        self.services = {name for name, _ in listed}
        order = parse_service_order(self.read((NS, "-listnetworkserviceorder")).text)
        self.interfaces = {
            x for x in self.read((IF, "-l")).text.split() if INTERFACE_RE.fullmatch(x)
        } | set(hardware)
        service_ids = {name: self.redactor.handle("service", name) for name, _ in listed}
        service_interfaces = dict(order)
        service_vitals: list[ServiceVital] = []
        for name, enabled in listed:
            sid = service_ids[name]
            reads = {flag: self.read((NS, flag, name), sid) for flag in SERVICE_READS}
            interface = service_interfaces.get(name)
            dns_read = reads["-getdnsservers"]
            dns = addresses(dns_read.text)
            dns_mode = (
                "manual"
                if dns
                else "dhcp"
                if dns_read.status == "available" and "aren't any DNS Servers" in dns_read.text
                else "unavailable"
            )
            search = reads["-getsearchdomains"]
            domains = tuple(
                self.redactor.handle("domain", line.strip())
                for line in search.text.splitlines()
                if line.strip() and "aren't any" not in line
            )
            proxies = tuple(
                parse_proxy(
                    kind,
                    reads[flag].text if reads[flag].status == "available" else None,
                    self.redactor,
                )
                for kind, flag in (
                    ("http", "-getwebproxy"),
                    ("https", "-getsecurewebproxy"),
                    ("pac", "-getautoproxyurl"),
                    ("wpad", "-getproxyautodiscovery"),
                    ("socks", "-getsocksfirewallproxy"),
                )
            )
            info = reads["-getinfo"].text
            mode = (
                "dhcp"
                if "DHCP Configuration" in info
                else "static"
                if "Manual Configuration" in info
                else "unknown"
            )
            service_vitals.append(
                ServiceVital(
                    sid,
                    interface,
                    hardware.get(interface or "", kind_for(name, interface or "")),
                    enabled,
                    mode,
                    dns_mode,
                    dns,
                    domains,
                    proxies,
                )
            )
        interface_vitals = []
        saved: dict[str, tuple[str, ...]] = {}
        for interface in sorted(self.interfaces):
            reads_if = {
                flag: self.read((IP, flag, interface), interface).text
                for flag in ("getifaddr", "getpacket", "getsummary")
            }
            status = self.read((IF, interface), interface).text
            kind = hardware.get(interface, kind_for("", interface))
            interface_vitals.append(
                parse_interface(
                    interface, kind, status, reads_if["getifaddr"], reads_if["getpacket"]
                )
            )
            if kind == "wifi":
                text = self.read((NS, "-listpreferredwirelessnetworks", interface), interface).text
                saved[interface] = tuple(
                    line.strip() for line in text.splitlines()[1:] if line.strip()
                )
        dns_text = self.read((SC, "--dns")).text
        system_dns = tuple(
            dict.fromkeys(
                ip
                for match in re.finditer(r"nameserver\[\d+\]\s*:\s*(\S+)", dns_text)
                if (ip := address(match[1]))
            )
        )
        proxy_read = self.read((SC, "--proxy"))
        nwi_text = self.read((SC, "--nwi")).text
        nwi = tuple(
            x for x in sorted(self.interfaces) if re.search(rf"\b{re.escape(x)}\b", nwi_text)
        )
        route_text = self.read(("/sbin/route", "-n", "get", "default")).text
        default_if = re.search(r"interface:\s*(\S+)", route_text)
        gateway = re.search(r"gateway:\s*(\S+)", route_text)
        routes = parse_routes(
            self.read(("/usr/sbin/netstat", "-rn")).text, self.interfaces, self.redactor
        )
        prior: set[str] = set()
        if lease_directory is not None:
            prior, status = prior_leases(lease_directory, self.redactor)
            self.observations.append(Observation("lease-metadata", "dhcp_metadata", None, status))
        candidates: list[Candidate] = []
        by_interface = {i.id: i for i in interface_vitals}
        for svc in service_vitals:
            if svc.interface not in by_interface or svc.kind in {"loopback", "tunnel", "other"}:
                continue
            vit = by_interface[svc.interface]
            evidence = ["configured_service"]
            if vit.leased:
                evidence.append("current_lease")
            elif vit.connection == "addressed":
                evidence.append("current_address")
            if vit.id in prior:
                evidence.append("prior_lease")
            candidates.append(
                candidate(
                    self.redactor,
                    svc.kind,
                    svc.id,
                    vit.id,
                    None,
                    tuple(evidence),
                    svc.enabled and vit.connection in {"leased", "addressed"},
                )
            )
            for name in saved.get(vit.id, ()):
                nid = self.redactor.handle("network", name)
                candidates.append(
                    candidate(
                        self.redactor,
                        "saved_wifi",
                        svc.id,
                        vit.id,
                        nid,
                        ("saved_network",),
                        False,
                        any(x in name.lower() for x in ("hotspot", "iphone", "android")),
                    )
                )
        effective_proxies = proxy_config(
            proxy_read.text if proxy_read.status == "available" else None, env
        )
        vitals = Vitals(
            tuple(service_vitals),
            tuple(interface_vitals),
            tuple(service_ids[name] for name, _ in order if name in service_ids),
            default_if[1] if default_if and default_if[1] in self.interfaces else None,
            address(gateway[1]) if gateway else None,
            routes,
            system_dns,
            nwi,
            rank_candidates(tuple(candidates)),
            tuple(self.observations),
        )
        return Inspection(vitals, effective_proxies, {v: k for k, v in service_ids.items()})
