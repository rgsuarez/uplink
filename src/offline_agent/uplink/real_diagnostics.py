"""Read-only Wi-Fi layer comparisons. They are diagnostic, never ONLINE evidence.

Private native configuration, interface addresses, route targets and DNS answers stay
in memory. Only fixed evidence IDs, booleans, bounded reasons and timings leave here.
"""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import ipaddress
import math
import platform
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.oracle import (
    DNS_NAMES,
    FRESH_SECONDS,
    ORACLE_VERSION,
    TLS_ENDPOINTS,
    OracleResult,
    ProxyConfig,
    ProxyRoute,
    SystemTransport,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.real_fleet import approved_serial

ALTERNATE_RESOLVER = _public_fixture("@PUBLIC_IPV4_003@")
DIRECT_TIMEOUT = 0.8


def command(
    argv: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[str] | None, str | None, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=1, check=False)
    except subprocess.TimeoutExpired:
        return None, "probe_timeout", (time.monotonic() - started) * 1000
    except OSError:
        return None, "command_unavailable", (time.monotonic() - started) * 1000
    return result, None, (time.monotonic() - started) * 1000


def unavailable(name: str, reason: str) -> dict[str, Json]:
    return {"id": name, "available": False, "ok": False, "reason": reason, "latency_ms": 0.0}


def oracle_rows(oracle: OracleResult, ids: tuple[str, str], kind: str) -> list[Json]:
    now = time.time()
    fresh = (
        oracle.version == ORACLE_VERSION
        and math.isfinite(oracle.checked_at)
        and 0 <= now - oracle.checked_at <= FRESH_SECONDS
    )
    rows: list[Json] = []
    for name in ids:
        matches = [e for e in oracle.evidence if e.id == name and e.kind == kind]
        if not fresh or len(matches) != 1:
            rows.append(unavailable(name, "oracle_missing_or_stale"))
            continue
        evidence = matches[0]
        available = (
            math.isfinite(evidence.latency_ms)
            and evidence.latency_ms >= 0
            and evidence.reason
            not in {
                "proxy_path_unavailable",
                "proxy_path_mismatch",
                "transport_unavailable",
                "wifi_route_not_exclusive",
            }
        )
        rows.append(
            {
                "id": name,
                "available": available,
                "ok": available and evidence.ok,
                "reason": "oracle_observed" if available else "oracle_path_unavailable",
                "latency_ms": evidence.latency_ms if available else 0.0,
            }
        )
    return rows


def source_address(value: str) -> str | None:
    try:
        address = ipaddress.IPv4Address(value.strip())
    except ipaddress.AddressValueError:
        return None
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_loopback
        or address.is_link_local
    ):
        return None
    return str(address)


def route_gateway(value: str, interface: str) -> str | None:
    fields: dict[str, list[str]] = {}
    for line in value.splitlines():
        key, separator, text = line.strip().partition(":")
        if separator and key in {"gateway", "interface"}:
            fields.setdefault(key, []).append(text.strip())
    gateways = fields.get("gateway", [])
    if fields.get("interface") != [interface] or len(gateways) != 1:
        return None
    return source_address(gateways[0])


def route_matches(value: str, interface: str) -> bool:
    return route_gateway(value, interface) is not None


def gateway_ping(interface: str, source: str, gateway: str) -> dict[str, Json]:
    # These flags were verified against lab's installed ping manual: -b binds
    # the interface, -S binds the source, and -t bounds its overall lifetime.
    result, error, elapsed = command(
        (
            "/sbin/ping",
            "-n",
            "-b",
            interface,
            "-S",
            source,
            "-c",
            "1",
            "-t",
            "1",
            gateway,
        )
    )
    available = error != "command_unavailable" and (result is None or result.returncode != 127)
    ok = result is not None and result.returncode == 0
    return {
        "id": "gateway",
        "available": available,
        "ok": available and ok,
        "reason": "gateway_replied" if ok else error or "gateway_ping_failed",
        "latency_ms": elapsed,
    }


def link_state(value: str, interface: str) -> bool | None:
    match = re.search(r"^" + re.escape(interface) + r": flags=[0-9a-fA-F]+<([^>]+)>", value)
    if match is None:
        return None
    if "UP" not in match[1].split(","):
        return False
    statuses = re.findall(r"^\s*status:\s*(active|inactive)\s*$", value, re.M)
    return statuses == ["active"] if len(statuses) == 1 else None


def alternate_dns(source: str, index: int) -> dict[str, Json]:
    name = f"dns-alternate-{index + 1}"
    # Source binding and a matching Wi-Fi route on both sides of the batch keep
    # the OOB path from supplying positive comparison evidence.
    result, error, elapsed = command(
        (
            "/usr/bin/dig",
            "-b",
            source,
            "@" + ALTERNATE_RESOLVER,
            DNS_NAMES[index],
            "A",
            "+short",
            "+time=1",
            "+tries=1",
        )
    )
    available = error != "command_unavailable" and (result is None or result.returncode != 127)
    ok = (
        result is not None
        and result.returncode == 0
        and any(source_address(line) is not None for line in result.stdout.splitlines())
    )
    return {
        "id": name,
        "available": available,
        "ok": available and ok,
        "reason": "alternate_dns_answered" if ok else error or "alternate_dns_failed",
        "latency_ms": elapsed,
    }


def direct_https(interface: str, index: int, route: ProxyRoute) -> dict[str, Json]:
    started = time.monotonic()
    endpoint = TLS_ENDPOINTS[index]
    result = SystemTransport(interface=interface).fetch(endpoint, route, DIRECT_TIMEOUT)
    available = result.error not in {"transport_unavailable", "proxy_path_unavailable"}
    ok = (
        available
        and route.proxy is None
        and route.supported
        and result.path_id == route.id
        and result.error is None
        and result.status == endpoint.status
        and result.body_matches
        and result.tls_verified
        and not result.redirected
    )
    return {
        "id": f"https-direct-{index + 1}",
        "available": available,
        "ok": ok,
        "reason": "direct_https_verified" if ok else "direct_https_failed",
        "latency_ms": (time.monotonic() - started) * 1000,
        "uses_configured_dns": True,
    }


def layer_probe(oracle: OracleResult, redactor: Redactor) -> dict[str, Json]:
    """Gather bounded differential evidence without changing any network setting."""
    if platform.system() != "Darwin":
        raise ContractError("macos_layer_probe_requires_darwin")
    started = time.monotonic()
    result: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-layer-evidence",
        "link_up": None,
        "lease_present": None,
        "wifi_default_route": None,
        "gateway_ping": unavailable("gateway", "wifi_identity_unavailable"),
        "dns_configured": oracle_rows(oracle, ("dns-1", "dns-2"), "dns"),
        "https_proxy": oracle_rows(oracle, ("tls-apple", "tls-google"), "tls"),
        "dns_alternate": [
            unavailable(f"dns-alternate-{i + 1}", "wifi_identity_unavailable") for i in range(2)
        ],
        "https_direct": [
            unavailable(f"https-direct-{i + 1}", "wifi_identity_unavailable") for i in range(2)
        ],
    }
    native, _, _ = command((str(Path(__file__).with_name("uplink-network")), "read"))
    if native is None or native.returncode:
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        return result
    try:
        configuration = object_json(read_json(native.stdout, max_bytes=65536))
    except ContractError as exc:
        raise ContractError("diagnostic_wifi_identity_unavailable") from exc
    interface = configuration.get("interface")
    if (
        not isinstance(interface, str)
        or not re.fullmatch(r"en[0-9]+", interface)
        or interface == "en7"
        or configuration.get("serial") != approved_serial()
    ):
        raise ContractError("diagnostic_wifi_interface_refused")
    with ThreadPoolExecutor(max_workers=5) as pool:
        address_job = pool.submit(command, ("/usr/sbin/ipconfig", "getifaddr", interface))
        link_job = pool.submit(command, ("/sbin/ifconfig", interface))
        route_job = pool.submit(command, ("/sbin/route", "-n", "get", "default"))
        address, _, _ = address_job.result()
        link, _, _ = link_job.result()
        route_before, _, _ = route_job.result()
        source = (
            source_address(address.stdout)
            if address is not None and address.returncode == 0
            else None
        )
        result["lease_present"] = source is not None if address is not None else None
        result["link_up"] = (
            link_state(link.stdout, interface)
            if link is not None and link.returncode == 0
            else None
        )
        gateway = (
            route_gateway(route_before.stdout, interface)
            if route_before is not None and route_before.returncode == 0
            else None
        )
        wifi_route = gateway is not None
        result["wifi_default_route"] = wifi_route if route_before is not None else None
        if source is not None and gateway is not None:
            gateway_job = pool.submit(gateway_ping, interface, source, gateway)
            direct_routes = [
                ProxyConfig({}, {}).route(endpoint, redactor) for endpoint in TLS_ENDPOINTS
            ]
            dns_jobs = [pool.submit(alternate_dns, source, i) for i in range(2)]
            https_jobs = [
                pool.submit(direct_https, interface, i, direct_routes[i]) for i in range(2)
            ]
            result["gateway_ping"] = gateway_job.result()
            result["dns_alternate"] = [job.result() for job in dns_jobs]
            result["https_direct"] = [job.result() for job in https_jobs]
            route_after, _, _ = command(("/sbin/route", "-n", "get", "default"))
            if (
                route_after is None
                or route_after.returncode
                or not route_matches(route_after.stdout, interface)
            ):
                result["wifi_default_route"] = False
                result["gateway_ping"] = unavailable("gateway", "wifi_route_changed")
                for group in ("dns_alternate", "https_direct"):
                    result[group] = [
                        unavailable(f"{group.replace('_', '-')}-{i + 1}", "wifi_route_changed")
                        for i in range(2)
                    ]
        else:
            result["gateway_ping"] = unavailable("gateway", "wifi_address_and_route_required")
            for group in ("dns_alternate", "https_direct"):
                result[group] = [
                    unavailable(
                        f"{group.replace('_', '-')}-{i + 1}", "wifi_address_and_route_required"
                    )
                    for i in range(2)
                ]
    # Preserve the supplied oracle rather than reprobing or strengthening its verdict.
    result["dns_configured"] = oracle_rows(oracle, ("dns-1", "dns-2"), "dns")
    result["https_proxy"] = oracle_rows(oracle, ("tls-apple", "tls-google"), "tls")
    result["elapsed_ms"] = (time.monotonic() - started) * 1000
    return result
