"""Closed macOS primitives and an explicit file-backed fake kernel for desk drills.

Injectors use the same primitive plans in both modes. The fake kernel never calls
an operating-system network command, a socket, SSH, a VM or a model.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import base64
import errno
import ipaddress
import json
import plistlib
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Protocol

if __package__:
    from . import top10_contract as c
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]


class Kernel(Protocol):
    sandbox: bool

    def capture(self) -> dict[str, object]: ...
    def admit(self, snapshot: dict[str, object], fault_id: str) -> None: ...
    def facts(self, snapshot: dict[str, object]) -> dict[str, bool]: ...
    def negative_control(self, fault_id: str, facts: dict[str, bool]) -> bool: ...
    def primitive(self, kind: str, operation: str, snapshot: dict[str, object]) -> None: ...


def usable(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
        return not (
            address.is_unspecified
            or address.is_loopback
            or address.is_multicast
            or address.is_link_local
        )
    except ValueError:
        return False


def decode(value: object) -> dict[str, object]:
    if value is None:
        return {}
    try:
        raw = base64.b64decode(str(value), validate=True)
        return c.obj(plistlib.loads(raw))
    except (ValueError, plistlib.InvalidFileException) as exc:
        raise c.Refused("top10_native_property_list_refused") from exc


def route_fields(value: str) -> dict[str, str]:
    result = {}
    for line in value.splitlines():
        key, sep, field = line.strip().partition(":")
        if sep and key in {"gateway", "interface"}:
            if key in result:
                raise c.Refused("top10_ambiguous_route")
            result[key] = field.strip()
    return result


def loopback_proxy_closed() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            return probe.connect_ex((_public_fixture("@PUBLIC_IPV4_056@"), 9)) == errno.ECONNREFUSED
    except OSError:
        return False


class MacKernel:
    sandbox = False

    def __init__(self, directory: Path, window: dict[str, object]) -> None:
        self.directory, self.window = directory, window
        self.binary = directory / "uplink-top10-network"
        self.portal_port = 0
        self.command_deadline: float | None = None
        self.last_capture: dict[str, object] = {}

    def run(
        self,
        label: str,
        argv: tuple[str, ...],
        *,
        timeout: float = 2,
        stdin: str | None = None,
        optional: bool = False,
    ) -> str:
        started = time.time()
        if self.command_deadline is not None:
            timeout = min(timeout, self.command_deadline - time.monotonic())
            if timeout <= 0:
                raise c.Refused("top10_restore_deadline_exceeded")
        try:
            result = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            c.audit(
                self.directory,
                "command",
                {"id": label, "reason": type(exc).__name__, "elapsed_s": time.time() - started},
            )
            if optional:
                return ""
            raise c.Refused("top10_bounded_command_failed") from exc
        c.audit(
            self.directory,
            "command",
            {
                "id": label,
                "exit_code": result.returncode,
                "elapsed_s": time.time() - started,
                "stdout_sha256": c.digest(result.stdout),
                "stderr_sha256": c.digest(result.stderr),
            },
        )
        if len(result.stdout.encode()) > 1_048_576 or len(result.stderr.encode()) > 262_144:
            raise c.Refused("top10_command_output_exceeded")
        if result.returncode:
            if optional:
                return ""
            label = result.stderr.strip()
            raise c.Refused(
                label
                if re.fullmatch(r"top10_[a-z0-9_]{1,110}", label)
                else "top10_native_command_refused"
            )
        return result.stdout

    def native(self, operation: str) -> dict[str, object]:
        args: tuple[str, ...] = (str(self.binary), operation)
        if operation != "read":
            args += (str(self.directory / "snapshot.json"),)
        if operation == "portal_proxy":
            if not 1024 <= self.portal_port <= 65535:
                raise c.Refused("top10_owned_portal_not_ready")
            args += (str(self.portal_port),)
        value = self.run(
            "native_" + operation, args, timeout=10 if operation == "restore_saved_network" else 4
        )
        try:
            return c.obj(json.loads(value))
        except ValueError as exc:
            raise c.Refused("top10_native_response_refused") from exc

    def capture(self) -> dict[str, object]:
        native = self.native("read")
        interface = c.text(native.get("interface"), r"en[0-9]+")
        oob_interface = c.text(self.window.get("oob_interface"), r"en[0-9]+")
        if interface == oob_interface:
            raise c.Refused("top10_wifi_is_management_interface")
        interface_text = self.run("wifi_interface", ("/sbin/ifconfig", interface))
        flags = re.search(r"flags=[0-9a-fx]+<([^>]+)>", interface_text)
        mtu = re.search(r"\bmtu ([0-9]+)\b", interface_text)
        if not flags or not mtu:
            raise c.Refused("top10_interface_state_unparsed")
        address = self.run(
            "wifi_ipv4", ("/usr/sbin/ipconfig", "getifaddr", interface), optional=True
        ).strip()
        lease = self.run(
            "wifi_lease", ("/usr/sbin/ipconfig", "getpacket", interface), optional=True
        )
        route = route_fields(
            self.run(
                "wifi_route",
                ("/sbin/route", "-n", "get", "-ifscope", interface, "default"),
                optional=True,
            )
        )
        client = str(ipaddress.IPv4Address(str(self.window["oob_client_ipv4"])))
        control = route_fields(
            self.run("management_route", ("/sbin/route", "-n", "get", "-host", client))
        )
        oob_address = self.run(
            "management_ipv4", ("/usr/sbin/ipconfig", "getifaddr", oob_interface)
        ).strip()
        dns_config = decode(native.get("dns"))
        dns_active = decode(native.get("dynamic_dns"))
        values = dns_config.get("ServerAddresses", dns_active.get("ServerAddresses", []))
        servers = (
            [v for v in values if isinstance(v, str) and usable(v)]
            if isinstance(values, list)
            else []
        )
        return {
            "version": 1,
            "native": native,
            "interface": interface,
            "interface_up": "UP" in flags[1].split(","),
            "mtu": int(mtu[1]),
            "ipv4": address,
            "lease_present": bool(lease),
            "lease_sha256": c.digest(lease),
            "gateway": route.get("gateway", ""),
            "route_interface": route.get("interface", ""),
            "dns_server": servers[0] if servers else "",
            "oob_route": control,
            "oob_address": oob_address,
            "captured_at": time.time(),
        }

    def admit(self, snapshot: dict[str, object], fault_id: str) -> None:
        c.require_bench_fault(fault_id)
        native = c.obj(snapshot["native"])
        if (
            native.get("policy_safe") is not True
            or native.get("radio_on") is not True
            or not isinstance(native.get("ssid_data"), str)
            or not isinstance(native.get("bssid"), str)
            or snapshot.get("interface_up") is not True
            or not usable(str(snapshot.get("ipv4")))
            or not usable(str(snapshot.get("gateway")))
            or not usable(str(snapshot.get("dns_server")))
            or snapshot.get("lease_present") is not True
            or decode(native.get("ipv4")).get("ConfigMethod") != "DHCP"
            or c.obj(snapshot.get("oob_route")).get("interface") != self.window["oob_interface"]
            or snapshot.get("oob_address") != c.OOB_SERVER_IPV4
        ):
            raise c.Refused("top10_safe_dhcp_wifi_baseline_required")
        if fault_id == "F06" and not loopback_proxy_closed():
            raise c.Refused("top10_loopback_port_9_not_proven_closed")
        if fault_id == "F07" and self.portal_port:  # noqa: SIM102 - keep the admission rationale together
            # The unchanged ONLINE oracle binds to Wi-Fi. A simulation that
            # works only after dropping that binding is unsupported, not proof.
            if not self.curl(snapshot, proxy=_public_fixture(f"http://@PUBLIC_IPV4_056@:{self.portal_port}"), portal=True):
                raise c.Refused("top10_interface_bound_portal_unsupported")
        if fault_id == "F09":
            mtu = snapshot["mtu"]
            if type(mtu) is not int or not 1280 < mtu <= 2304:
                raise c.Refused("top10_mtu_variant_unsupported")
            value = self.run(
                "valid_mtu_range",
                ("/usr/sbin/networksetup", "-listValidMTURange", str(snapshot["interface"])),
            )
            numbers = [int(v) for v in re.findall(r"\b[0-9]{3,5}\b", value)]
            if len(numbers) != 2 or not numbers[0] <= 1280 <= numbers[1]:
                raise c.Refused("top10_mtu_range_unproven")

    def curl(
        self,
        snapshot: dict[str, object],
        *,
        proxy: str = "",
        portal: bool = False,
        bind: bool = True,
    ) -> bool:
        interface = c.text(snapshot["interface"], r"en[0-9]+")
        url = (
            "http://captive.apple.com/hotspot-detect.html"
            if portal
            else "https://www.gstatic.com/generate_204"
        )
        args: tuple[str, ...] = (
            "/usr/bin/curl",
            "--disable",
            "--ipv4",
            "--silent",
            "--max-time",
            "1",
            "--connect-timeout",
            "1",
            "--max-filesize",
            "4096",
            "--proto",
            "=http,https",
            "--config",
            "-",
            "--write-out",
            "\n%{http_code}",
            url,
        )
        if bind:
            args = args[:2] + ("--interface", interface) + args[2:]
        if any(x in proxy for x in ('"', "\n", "\r", "\\")):
            raise c.Refused("top10_proxy_configuration_refused")
        value = self.run(
            "portal_probe" if portal else "https_probe",
            args,
            stdin=f'proxy = "{proxy}"\nnoproxy = ""\n',
            timeout=1.5,
            optional=True,
        )
        return (
            (value.endswith("\n302") and "UPLINK LOCAL PORTAL SIMULATION" in value)
            if portal
            else value == "\n204"
        )

    def facts(self, snapshot: dict[str, object]) -> dict[str, bool]:
        current = self.capture()
        self.last_capture = current
        native, before = c.obj(current["native"]), c.obj(snapshot["native"])
        interface = str(snapshot["interface"])
        address = str(current["ipv4"])
        up = current["interface_up"] is True and native.get("radio_on") is True
        private_equal = all(
            native.get(k) == before.get(k)
            for k in (
                "service_id",
                "interface",
                "protected_sha256",
                "location_sha256",
                "service_order_sha256",
                "ipv6_sha256",
            )
        )
        control_equal = (
            current["oob_route"] == snapshot["oob_route"]
            and current["oob_address"] == snapshot["oob_address"]
        )
        active = up and usable(address)
        gateway = str(snapshot["gateway"])
        # Independent negative probes use captured source/target scope, never a model argument.
        df = (
            self.run(
                "baseline_size_df_ping",
                (
                    "/sbin/ping",
                    "-D",
                    "-c",
                    "1",
                    "-W",
                    "1000",
                    "-S",
                    address,
                    "-s",
                    str(int(str(snapshot["mtu"])) - 28),
                    gateway,
                ),
                timeout=1.5,
                optional=True,
            )
            if active
            else ""
        )
        dns = (
            self.run(
                "configured_dns",
                (
                    "/usr/bin/dig",
                    "-b",
                    address,
                    "@" + str(current["dns_server"]),
                    "www.apple.com",
                    "A",
                    "+short",
                    "+time=1",
                    "+tries=1",
                ),
                timeout=1.5,
                optional=True,
            )
            if active and current["dns_server"]
            else ""
        )
        proxy_config = decode(native.get("proxies"))
        if proxy_config.get("HTTPSProxy") == _public_fixture("@PUBLIC_IPV4_056@") and proxy_config.get("HTTPSPort") == 9:
            self.last_capture["closed_loopback_proxy"] = loopback_proxy_closed()
        proxy = ""
        if proxy_config.get("HTTPSEnable") == 1:
            host = c.text(proxy_config.get("HTTPSProxy"), r"[A-Za-z0-9_.:-]{1,253}")
            port = int(c.number(proxy_config.get("HTTPSPort"), 1, 65535))
            proxy = f"http://{host}:{port}"
        portal = (
            proxy_config.get("HTTPEnable") == 1
            and proxy_config.get("HTTPProxy") == _public_fixture("@PUBLIC_IPV4_056@")
            and self.portal_port >= 1024
            and proxy_config.get("HTTPPort") == self.portal_port
            and self.curl(
                snapshot, proxy=_public_fixture(f"http://@PUBLIC_IPV4_056@:{self.portal_port}"), portal=True, bind=False
            )
        )
        native_equal = all(
            native.get(k) == before.get(k)
            for k in ("dns_sha256", "proxies_sha256", "ipv4_sha256", "ipv6_sha256", "cw_sha256")
        )
        try:
            linklocal = ipaddress.IPv4Address(address).is_link_local
        except ValueError:
            linklocal = False
        facts = {
            "radio_on": native.get("radio_on") is True,
            "interface_up": current["interface_up"] is True,
            "associated_with_snapshot": native.get("ssid_data") == before.get("ssid_data")
            and native.get("bssid") == before.get("bssid"),
            "ipv4_usable": usable(address),
            "ipv4_linklocal": linklocal,
            "dhcp_lease_present": current["lease_present"] is True,
            "gateway_matches_snapshot": current["gateway"] == snapshot["gateway"]
            and current["route_interface"] == interface,
            "dns_matches_snapshot": native.get("dns_sha256") == before.get("dns_sha256"),
            "proxy_matches_snapshot": native.get("proxies_sha256") == before.get("proxies_sha256"),
            "saved_network_matches_snapshot": native.get("cw_sha256") == before.get("cw_sha256"),
            "mtu_matches_snapshot": current["mtu"] == snapshot["mtu"],
            "protected_services_unchanged": private_equal and control_equal,
            "persistent_baseline_equal": native_equal and current["mtu"] == snapshot["mtu"],
            "ipv4_path_ok": active and self.curl(snapshot),
            "https_path_ok": active and self.curl(snapshot, proxy=proxy),
            "mtu_probe_ok": bool(df),
            "configured_dns_ok": any(usable(line.strip()) for line in dns.splitlines()),
            "portal_redirect_seen": portal,
        }
        dynamic_changed = any(current[key] != snapshot[key] for key in ("ipv4", "lease_sha256"))
        dynamic_allowed = (
            decode(before.get("ipv4")).get("ConfigMethod") == "DHCP"
            and decode(native.get("ipv4")).get("ConfigMethod") == "DHCP"
            and facts["dhcp_lease_present"]
            and facts["ipv4_usable"]
            and facts["gateway_matches_snapshot"]
        )
        if dynamic_changed and not dynamic_allowed:
            facts["persistent_baseline_equal"] = False
        dynamic_reason = (
            "dhcp_renewal_allowed_by_registry" if dynamic_allowed else "dynamic_change_not_admitted"
        )
        stamp = time.time_ns()
        c.save(
            self.directory / f"observation-{stamp}.private.json",
            {
                "current": current,
                "facts": facts,
                "dynamic_change_reason": dynamic_reason if dynamic_changed else "unchanged",
            },
            exclusive=True,
        )
        return c.measured(facts)

    def negative_control(self, fault_id: str, facts: dict[str, bool]) -> bool:
        if not c.fault_effect(fault_id, facts):
            return False
        if fault_id == "F06":
            return self.last_capture.get("closed_loopback_proxy") is True
        if fault_id != "F10":
            return True
        native = c.obj(self.last_capture.get("native"))
        configured = decode(native.get("ipv4"))
        return (
            self.last_capture.get("ipv4") == _public_fixture("@PUBLIC_IPV4_083@")
            and self.last_capture.get("gateway") == _public_fixture("@PUBLIC_IPV4_081@")
            and configured.get("ConfigMethod") == "Manual"
            and configured.get("Addresses") == [_public_fixture("@PUBLIC_IPV4_083@")]
            and configured.get("SubnetMasks") == [_public_fixture("@PUBLIC_IPV4_092@")]
            and configured.get("Router") == _public_fixture("@PUBLIC_IPV4_081@")
        )

    def primitive(self, kind: str, operation: str, snapshot: dict[str, object]) -> None:
        if (
            kind == "native"
            and operation
            in {
                "radio_off",
                "restore_radio",
                "can_restore_network",
                "forget_current_profile",
                "restore_saved_network",
            }
        ) or (kind == "ifconfig" and operation in {"down", "snapshot_up"}):
            raise c.Refused("top10_keychain_rejoin_or_link_operation_held")
        interface = c.text(snapshot["interface"], r"en[0-9]+")
        if kind == "native":
            if operation not in {
                "radio_off",
                "restore_radio",
                "bad_dns",
                "restore_dns",
                "wrong_gateway",
                "stale_ipv4",
                "restore_ipv4",
                "stale_proxy",
                "portal_proxy",
                "restore_proxy",
                "forget_current_profile",
                "restore_saved_network",
                "restore_configuration",
                "verify_profiles",
            }:
                raise c.Refused("top10_native_primitive_refused")
            self.native(operation)
        elif kind == "ifconfig" and operation in {
            "down",
            "snapshot_up",
            "mtu_1280",
            "snapshot_mtu",
        }:
            args = {
                "down": ("down",),
                "snapshot_up": ("up",),
                "mtu_1280": ("mtu", "1280"),
                "snapshot_mtu": ("mtu", str(int(c.number(snapshot["mtu"], 1281, 2304)))),
            }[operation]
            self.run("interface_" + operation, ("/sbin/ifconfig", interface, *args))
        elif kind == "ipconfig" and operation in {"linklocal_manual", "dhcp"}:
            args = (
                ("MANUAL", _public_fixture("@PUBLIC_IPV4_060@"), _public_fixture("@PUBLIC_IPV4_091@"))
                if operation == "linklocal_manual"
                else ("DHCP",)
            )
            self.run(
                "ipv4_" + operation, ("/usr/sbin/ipconfig", "set", interface, *args), timeout=4
            )
        else:
            raise c.Refused("top10_primitive_refused")


def initial_fake_state() -> dict[str, object]:
    return {
        "radio_on": True,
        "interface_up": True,
        "associated": True,
        "ipv4": _public_fixture("@PUBLIC_IPV4_011@"),
        "gateway": _public_fixture("@PUBLIC_IPV4_009@"),
        "lease_present": True,
        "dns": _public_fixture("@PUBLIC_IPV4_009@"),
        "proxy": "off",
        "profiles": ["synthetic-network"],
        "mtu": 1500,
        "ipv4_config": "DHCP",
        "protected": "unchanged",
        "restore_credential_available": True,
        "managed_pid": c.SERVER_PID,
        "managed_birth": "synthetic-fixed-birth",
        "adapters": [],
        "portal_port": 53421,
    }


class SandboxKernel:
    sandbox = True

    def __init__(self, directory: Path, *, fail_operation: str | None = None) -> None:
        self.directory = directory
        self.path = directory / "fake-kernel.private.json"
        self.fail_operation = fail_operation
        self.portal_port = 53421
        self.last_capture: dict[str, object] = {}

    def state(self) -> dict[str, object]:
        return c.read(self.path, private=True)

    def initialize(self) -> None:
        c.save(self.path, initial_fake_state(), exclusive=True)

    def capture(self) -> dict[str, object]:
        return {
            "version": 1,
            "kernel": self.state(),
            "interface": "en99",
            "mtu": 1500,
            "captured_at": time.time(),
            "evidence_kind": "FAKE_KERNEL",
        }

    def admit(self, snapshot: dict[str, object], fault_id: str) -> None:
        state = self.state()
        if state.get("managed_pid") != c.SERVER_PID or state.get("adapters") != []:
            raise c.Refused("top10_base_identity_required")
        if state.get("protected") != "unchanged":
            raise c.Refused("top10_protected_configuration_changed")
        if (
            fault_id in {"F01", "F02", "F08"}
            and state.get("restore_credential_available") is not True
        ):
            raise c.Refused("top10_noninteractive_restore_credential_unavailable")

    def facts(self, snapshot: dict[str, object]) -> dict[str, bool]:
        state, before = self.state(), c.obj(snapshot["kernel"])
        self.last_capture = state
        link = (
            state["radio_on"] is True
            and state["interface_up"] is True
            and state["associated"] is True
        )
        ip_ok = (
            link
            and usable(str(state["ipv4"]))
            and state["gateway"] == before["gateway"]
            and state["ipv4"] == before["ipv4"]
        )
        persistent = all(
            state[k] == before[k] for k in ("dns", "proxy", "profiles", "mtu", "ipv4_config")
        )
        facts = {
            "radio_on": state["radio_on"] is True,
            "interface_up": state["interface_up"] is True,
            "associated_with_snapshot": state["associated"] is True,
            "ipv4_usable": usable(str(state["ipv4"])),
            "ipv4_linklocal": ipaddress.IPv4Address(str(state["ipv4"])).is_link_local,
            "dhcp_lease_present": state["lease_present"] is True,
            "gateway_matches_snapshot": state["gateway"] == before["gateway"],
            "dns_matches_snapshot": state["dns"] == before["dns"],
            "proxy_matches_snapshot": state["proxy"] == before["proxy"],
            "saved_network_matches_snapshot": state["profiles"] == before["profiles"],
            "mtu_matches_snapshot": state["mtu"] == before["mtu"],
            "protected_services_unchanged": all(
                state[k] == before[k]
                for k in ("protected", "managed_pid", "managed_birth", "adapters")
            ),
            "persistent_baseline_equal": persistent,
            "ipv4_path_ok": ip_ok,
            "https_path_ok": ip_ok and state["proxy"] == "off",
            "mtu_probe_ok": link and int(str(state["mtu"])) >= int(str(before["mtu"])),
            "configured_dns_ok": ip_ok and state["dns"] == before["dns"],
            "portal_redirect_seen": state["proxy"] == "owned-portal",
        }
        return c.measured(facts)

    def negative_control(self, fault_id: str, facts: dict[str, bool]) -> bool:
        return c.fault_effect(fault_id, facts) and (
            fault_id != "F10"
            or (
                self.last_capture.get("ipv4") == _public_fixture("@PUBLIC_IPV4_083@")
                and self.last_capture.get("gateway") == _public_fixture("@PUBLIC_IPV4_081@")
                and self.last_capture.get("ipv4_config") == "Manual"
            )
        )

    def primitive(self, kind: str, operation: str, snapshot: dict[str, object]) -> None:
        if operation == self.fail_operation:
            raise c.Refused("top10_synthetic_primitive_failure")
        state, before = self.state(), c.obj(snapshot["kernel"])
        if state["protected"] != before["protected"]:
            raise c.Refused("top10_protected_configuration_changed")
        if kind == "native":
            if operation == "radio_off":
                state["radio_on"] = False
            elif operation == "restore_radio":
                state["radio_on"] = before["radio_on"]
            elif operation == "bad_dns":
                state["dns"] = _public_fixture("@PUBLIC_IPV4_075@")
            elif operation == "restore_dns":
                state["dns"] = before["dns"]
            elif operation in {"wrong_gateway", "stale_ipv4"}:
                state.update(
                    ipv4_config="Manual",
                    lease_present=False,
                    gateway=_public_fixture("@PUBLIC_IPV4_066@") if operation == "wrong_gateway" else _public_fixture("@PUBLIC_IPV4_081@"),
                )
                if operation == "stale_ipv4":
                    state["ipv4"] = _public_fixture("@PUBLIC_IPV4_083@")
            elif operation in {"restore_ipv4", "restore_configuration"}:
                for key in ("ipv4_config", "ipv4", "gateway", "lease_present"):
                    state[key] = before[key]
                if operation == "restore_configuration":
                    for key in ("dns", "proxy"):
                        state[key] = before[key]
            elif operation == "stale_proxy":
                state["proxy"] = "closed-loopback"
            elif operation == "portal_proxy":
                state["proxy"] = "owned-portal"
            elif operation == "restore_proxy":
                state["proxy"] = before["proxy"]
            elif operation == "forget_current_profile":
                state.update(profiles=[], associated=False)
            elif operation == "restore_saved_network":
                if state["restore_credential_available"] is not True:
                    raise c.Refused("top10_noninteractive_restore_credential_unavailable")
                state.update(profiles=before["profiles"], associated=before["associated"])
            elif operation == "verify_profiles":
                if state["profiles"] != before["profiles"]:
                    raise c.Refused("top10_profiles_not_restored")
            else:
                raise c.Refused("top10_native_primitive_refused")
        elif kind == "ifconfig":
            if operation == "down":
                state["interface_up"] = False
            elif operation == "snapshot_up":
                state["interface_up"] = before["interface_up"]
            elif operation == "mtu_1280":
                state["mtu"] = 1280
            elif operation == "snapshot_mtu":
                state["mtu"] = before["mtu"]
            else:
                raise c.Refused("top10_primitive_refused")
        elif kind == "ipconfig":
            if operation == "linklocal_manual":
                state.update(ipv4=_public_fixture("@PUBLIC_IPV4_060@"), lease_present=False)
            elif operation == "dhcp":
                for key in ("ipv4", "gateway", "lease_present"):
                    state[key] = before[key]
            else:
                raise c.Refused("top10_primitive_refused")
        else:
            raise c.Refused("top10_primitive_refused")
        c.audit(self.directory, "fake_primitive", {"kind": kind, "operation": operation})
        c.save(self.path, state)
