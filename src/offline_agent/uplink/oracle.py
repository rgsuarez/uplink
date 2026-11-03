"""Versioned independent connectivity oracle; no model output participates in a verdict.

The application path is explicit: env proxy > system proxy, with explicit exclusions.
PAC/WPAD configurations that cannot be evaluated are unknown, never silently bypassed.
DNS runs in a bounded child because getaddrinfo has no portable timeout. curl performs
cert-validated HTTP(S), bounded in time and output, with redirects disabled. Proxy
credentials go through stdin, never process arguments, diagnostics, or traces.
"""

import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation

ORACLE_VERSION = "uplink-online-v1"
FRESH_SECONDS = 30.0


class Verdict(StrEnum):
    ONLINE = "ONLINE"
    CAPTIVE = "CAPTIVE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class Endpoint:
    id: str
    url: str
    status: int
    body: bytes


SUCCESS = b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"
TLS_ENDPOINTS = (
    Endpoint("tls-apple", "https://www.apple.com/library/test/success.html", 200, SUCCESS),
    Endpoint("tls-google", "https://www.gstatic.com/generate_204", 204, b""),
)
PORTAL_ENDPOINTS = (
    Endpoint("portal-apple", "http://captive.apple.com/hotspot-detect.html", 200, SUCCESS),
    Endpoint("portal-google", "http://connectivitycheck.gstatic.com/generate_204", 204, b""),
)
DNS_NAMES = ("www.apple.com", "www.gstatic.com")


@dataclass(frozen=True)
class ProxyRoute:
    id: str
    source: str
    proxy: str | None = field(repr=False)
    supported: bool = True


@dataclass(frozen=True)
class ProxyConfig:
    system: Mapping[str, str] = field(repr=False)
    env: Mapping[str, str] = field(repr=False)
    exceptions: tuple[str, ...] = field(default=(), repr=False)
    pac: bool = False
    autodiscovery: bool = False
    known: bool = True
    exclude_simple: bool = False

    def route(self, endpoint: Endpoint, redactor: Redactor) -> ProxyRoute:
        url = urlsplit(endpoint.url)
        host = url.hostname or ""
        scheme = url.scheme
        # Lowercase environment names win, including explicit empty values.
        value = self.env.get(scheme + "_proxy", self.env.get(scheme.upper() + "_PROXY"))
        if scheme == "http" and "REQUEST_METHOD" in self.env and "http_proxy" not in self.env:
            value = None  # CGI HTTP_PROXY injection protection.
        if value is None:
            value = self.env.get("all_proxy", self.env.get("ALL_PROXY"))
        no_proxy = self.env.get("no_proxy", self.env.get("NO_PROXY", ""))
        bypass = tuple(x.strip() for x in no_proxy.split(",") if x.strip())
        if any("/" in x or ":" in x for x in (*bypass, *self.exceptions)):
            proxy, source, supported = None, "complex_proxy_exclusion_unresolved", False
        elif any(_excluded(host, x) for x in bypass):
            proxy, source, supported = None, "env_bypass", True
        elif value is not None:
            proxy, source, supported = value or None, "environment", True
        elif not self.known:
            proxy, source, supported = None, "system_unavailable", False
        elif any(_excluded(host, x) for x in self.exceptions) or (
            self.exclude_simple and "." not in host
        ):
            proxy, source, supported = None, "system_bypass", True
        elif self.pac or self.autodiscovery:
            proxy, source, supported = None, "pac_or_wpad_unresolved", False
        else:
            proxy, source, supported = self.system.get(scheme), "system", True
        if proxy:
            if "://" not in proxy:
                proxy = "http://" + proxy
            try:
                parsed = urlsplit(proxy)
                supported &= bool(parsed.hostname) and parsed.scheme in {
                    "http",
                    "https",
                    "socks5",
                    "socks5h",
                    "socks4",
                    "socks4a",
                }
                supported &= not any(c in proxy for c in '\r\n\0"')
                supported &= parsed.port is None or 1 <= parsed.port <= 65535
            except ValueError:
                supported = False
        route_id = redactor.handle("path", json.dumps([source, proxy, supported]))
        return ProxyRoute(route_id, source, proxy, supported)


def _excluded(host: str, pattern: str) -> bool:
    candidate = pattern.lower()
    if candidate == "*":
        return True
    if ":" in candidate:
        # Port-qualified/CIDR exclusions need a fully modeled client; do not guess.
        return False
    return (
        fnmatch.fnmatchcase(host.lower(), candidate)
        or host.lower().endswith("." + candidate.lstrip("."))
        or host.lower() == candidate.lstrip(".")
    )


def proxy_config(text: str | None, env: Mapping[str, str]) -> ProxyConfig:
    if text is None or "<dictionary>" not in text:
        return ProxyConfig({}, dict(env), known=False)
    values: dict[str, str] = {}
    depth = 0
    for line in text.splitlines():
        if depth == 1:
            match = re.fullmatch(r"\s*([A-Za-z]+)\s*:\s*([^{}]+?)\s*", line)
            if match:
                values[match[1]] = match[2]
        depth += line.count("{") - line.count("}")
    system: dict[str, str] = {}
    known = depth == 0 and "__SCOPED__" not in text
    known &= all(
        values.get(key, "0") in {"0", "1"}
        for key in (
            "HTTPEnable",
            "HTTPSEnable",
            "SOCKSEnable",
            "ProxyAutoConfigEnable",
            "ProxyAutoDiscoveryEnable",
            "ExcludeSimpleHostnames",
        )
    )
    for scheme, key in (("http", "HTTP"), ("https", "HTTPS")):
        if values.get(key + "Enable") == "1":
            host, port = values.get(key + "Proxy", ""), values.get(key + "Port", "")
            if host and port.isdigit() and 1 <= int(port) <= 65535:
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                system[scheme] = f"http://{host}:{port}"
            else:
                known = False
    if values.get("SOCKSEnable") == "1":
        host, port = values.get("SOCKSProxy", ""), values.get("SOCKSPort", "")
        if host and port.isdigit() and 1 <= int(port) <= 65535:
            for scheme in ("http", "https"):
                system.setdefault(scheme, f"socks5h://{host}:{port}")
        else:
            known = False
    match = re.search(r"ExceptionsList\s*:\s*<array>\s*\{([^}]+)\}", text, re.S)
    exceptions = tuple(re.findall(r"\d+\s*:\s*(\S+)", match[1])) if match else ()
    return ProxyConfig(
        system,
        dict(env),
        exceptions,
        values.get("ProxyAutoConfigEnable") == "1",
        values.get("ProxyAutoDiscoveryEnable") == "1",
        known,
        values.get("ExcludeSimpleHostnames") == "1",
    )


@dataclass(frozen=True)
class Fetch:
    path_id: str
    status: int = 0
    body_matches: bool = False
    tls_verified: bool = False
    redirected: bool = False
    error: str | None = None


class Transport(Protocol):
    def resolve(self, name: str, timeout: float) -> bool: ...
    def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch: ...


class SystemTransport:
    """Only fixed probes; never shell, no downloaded code, no model, no settings writes."""

    def __init__(
        self,
        on_probe: Callable[[RawObservation], None] | None = None,
        *,
        interface: str | None = None,
    ) -> None:
        if interface is not None and not re.fullmatch(r"en[0-9]+", interface):
            raise ValueError("invalid_probe_interface")
        self.on_probe = on_probe
        self.interface = interface

    def resolve(self, name: str, timeout: float) -> bool:
        started = time.monotonic()
        if name not in DNS_NAMES:
            raise ValueError("unregistered_dns_probe")
        code = "import socket,sys; print(bool(socket.getaddrinfo(sys.argv[1],443)))"
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", code, name],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if self.on_probe:
                self.on_probe(
                    RawObservation(
                        (sys.executable, "-I", "-c", code, name),
                        result.stdout.decode(errors="replace"),
                        result.stderr.decode(errors="replace"),
                        result.returncode,
                        (time.monotonic() - started) * 1000,
                        "dns_probe",
                    )
                )
            return result.returncode == 0 and result.stdout.strip() == b"True"
        except (OSError, subprocess.SubprocessError):
            if self.on_probe:
                self.on_probe(
                    RawObservation(
                        (sys.executable, "-I", "-c", code, name),
                        duration_ms=(time.monotonic() - started) * 1000,
                        source="dns_probe_unavailable",
                    )
                )
            return False

    def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch:
        started = time.monotonic()
        if endpoint not in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS):
            raise ValueError("unregistered_http_probe")
        if not route.supported:
            return Fetch(route.id, error="proxy_path_unavailable")
        # Disable curlrc first. Explicit proxy/noproxy override all ambient proxy bypasses.
        proxy = (route.proxy or "").replace("\\", "\\\\").replace('"', '\\"')
        config = f'proxy = "{proxy}"\nnoproxy = ""\n'
        command = [
            "/usr/bin/curl",
            "--disable",
            "--config",
            "-",
            "--silent",
            "--max-time",
            str(timeout),
            "--connect-timeout",
            str(min(2.0, timeout)),
            "--max-filesize",
            "8192",
            "--proto",
            "=http,https",
            "--request",
            "GET",
            "--write-out",
            "\nUPLINK_STATUS:%{http_code}",
            endpoint.url,
        ]
        if self.interface is not None:
            command[2:2] = ["--interface", self.interface]
        try:
            result = subprocess.run(
                command,
                input=config.encode(),
                capture_output=True,
                timeout=timeout + 0.5,
                check=False,
                env={
                    k: v
                    for k, v in os.environ.items()
                    if k.upper() not in {"CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"}
                },
            )
        except subprocess.TimeoutExpired:
            if self.on_probe:
                self.on_probe(
                    RawObservation(
                        tuple(command),
                        duration_ms=(time.monotonic() - started) * 1000,
                        source="http_probe_timeout",
                    )
                )
            return Fetch(route.id, error="timeout")
        except OSError:
            if self.on_probe:
                self.on_probe(
                    RawObservation(
                        tuple(command),
                        duration_ms=(time.monotonic() - started) * 1000,
                        source="http_probe_unavailable",
                    )
                )
            return Fetch(route.id, error="transport_unavailable")
        if self.on_probe:
            self.on_probe(
                RawObservation(
                    tuple(command),
                    result.stdout.decode(errors="replace"),
                    result.stderr.decode(errors="replace"),
                    result.returncode,
                    (time.monotonic() - started) * 1000,
                    "http_probe",
                )
            )
        if result.returncode:
            return Fetch(route.id, error="tls_or_transport_failed")
        body, separator, code = result.stdout.rpartition(b"\nUPLINK_STATUS:")
        if not separator or not code.strip().isdigit() or len(body) > 8192:
            return Fetch(route.id, error="invalid_transport_receipt")
        status = int(code.strip())
        return Fetch(
            route.id,
            status,
            body.strip() == endpoint.body,
            endpoint.url.startswith("https://"),
            300 <= status < 400,
        )


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    ok: bool
    reason: str
    path_id: str | None = None
    latency_ms: float = 0.0
    target_id: str | None = None
    status: int | None = None
    proxy_used: str | None = None

    def as_json(self) -> dict[str, Json]:
        return {
            "id": self.id,
            "kind": self.kind,
            "ok": self.ok,
            "reason": self.reason,
            "path_id": self.path_id,
            "latency_ms": self.latency_ms,
            "target_id": self.target_id,
            "status": self.status,
            "proxy_used": self.proxy_used,
        }


@dataclass(frozen=True)
class OracleResult:
    verdict: Verdict
    checked_at: float
    evidence: tuple[Evidence, ...]
    version: str = ORACLE_VERSION

    def fresh_online(self, now: float) -> bool:
        expected = {"dns-1", "dns-2", *(e.id for e in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS))}
        return (
            self.version == ORACLE_VERSION
            and self.verdict == Verdict.ONLINE
            and len(self.evidence) == len(expected)
            and {e.id for e in self.evidence} == expected
            and all(e.ok for e in self.evidence)
            and all(e.path_id for e in self.evidence if e.kind != "dns")
            and math.isfinite(now)
            and 0 <= now - self.checked_at <= FRESH_SECONDS
        )

    def as_json(self) -> dict[str, Json]:
        return {
            "version": self.version,
            "verdict": self.verdict.value,
            "checked_at": self.checked_at,
            "evidence": [e.as_json() for e in self.evidence],
        }


class OnlineOracle:
    def __init__(self, transport: Transport, proxies: ProxyConfig, redactor: Redactor) -> None:
        self.transport, self.proxies, self.redactor = transport, proxies, redactor
        self.latest: OracleResult | None = None

    def probe(self, *, timeout: float = 3.0) -> OracleResult:
        if not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError("probe_timeout_out_of_bounds")
        started = time.time()
        evidence: list[Evidence] = []
        captive = False
        for index, name in enumerate(DNS_NAMES, 1):
            probe_started = time.monotonic()
            try:
                ok = self.transport.resolve(name, timeout)
            except (OSError, TimeoutError):
                ok = False
            evidence.append(
                Evidence(
                    f"dns-{index}",
                    "dns",
                    ok,
                    "system_resolved" if ok else "system_resolution_failed",
                    latency_ms=round((time.monotonic() - probe_started) * 1000, 3),
                    target_id=self.redactor.handle("host", name),
                )
            )
        for endpoint in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS):
            probe_started = time.monotonic()
            route = self.proxies.route(endpoint, self.redactor)
            try:
                result = (
                    self.transport.fetch(endpoint, route, timeout)
                    if route.supported
                    else Fetch(route.id, error="proxy_path_unavailable")
                )
            except (OSError, TimeoutError):
                result = Fetch(route.id, error="transport_failed")
            path_ok = route.supported and result.path_id == route.id
            is_tls = endpoint in TLS_ENDPOINTS
            ok = (
                path_ok
                and result.error is None
                and result.status == endpoint.status
                and result.body_matches
                and not result.redirected
                and (not is_tls or result.tls_verified)
            )
            captive |= (
                not is_tls
                and path_ok
                and result.error is None
                and (result.redirected or (result.status == 200 and not ok))
            )
            reason = (
                "verified"
                if ok
                else "proxy_path_mismatch"
                if not path_ok
                else result.error or "unexpected_response"
            )
            evidence.append(
                Evidence(
                    endpoint.id,
                    "tls" if is_tls else "portal",
                    ok,
                    reason,
                    route.id,
                    round((time.monotonic() - probe_started) * 1000, 3),
                    self.redactor.handle("host", urlsplit(endpoint.url).hostname or ""),
                    result.status,
                    self.redactor.handle("proxy", route.proxy, remember=False)
                    if route.proxy
                    else "direct"
                    if route.supported
                    else "unresolved",
                )
            )
        verdict = (
            Verdict.CAPTIVE
            if captive
            else Verdict.ONLINE
            if all(e.ok for e in evidence)
            else Verdict.DEGRADED
            if any(e.ok for e in evidence)
            else Verdict.OFFLINE
        )
        self.latest = OracleResult(verdict, started, tuple(evidence))
        return self.latest

    def declare_online(self, *, now: float | None = None) -> OracleResult:
        if self.latest is None or not self.latest.fresh_online(time.time() if now is None else now):
            raise ContractError("fresh_latest_online_oracle_required")
        return self.latest


def oracle_fingerprint() -> str:
    """Acceptance identity for snapshots; changes to endpoints invalidate old provenance."""
    payload = [(e.id, e.url, e.status, e.body.hex()) for e in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS)]
    return hashlib.sha256(json.dumps([ORACLE_VERSION, DNS_NAMES, payload]).encode()).hexdigest()
