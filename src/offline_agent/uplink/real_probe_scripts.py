"""Unprivileged probe context and exact before/after checks on approved lab Wi-Fi."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.oracle import DNS_NAMES, PORTAL_ENDPOINTS, TLS_ENDPOINTS
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_execution import episode
from offline_agent.uplink.real_diagnostics import route_gateway, source_address
from offline_agent.uplink.real_fleet import FLEET_GETTERS, approved_serial


def read_command(argv: tuple[str, ...]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=2, check=False)
    if result.returncode:
        raise ContractError("probe_context_read_unavailable")
    return result.stdout


def capture() -> tuple[dict[str, Json], dict[str, str]]:
    native = object_json(
        read_json(read_command((str(Path(__file__).with_name("uplink-network")), "read")))
    )
    iface = native.get("interface")
    if (
        native.get("serial") != approved_serial()
        or native.get("policy_safe") is not True
        or not isinstance(iface, str)
        or not re.fullmatch(r"en[0-9]+", iface)
        or iface == "en7"
    ):
        raise ContractError("probe_wifi_identity_refused")
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {
            getter: pool.submit(read_command, ("/usr/sbin/networksetup", getter, "Wi-Fi"))
            for getter in FLEET_GETTERS
        }
        settings = {getter: job.result() for getter, job in jobs.items()}
    return native, settings


def execute(
    program: dict[str, Json],
    run_id: str,
    deadline: float,
    root: Path,
    redactor: Redactor,
) -> dict[str, Json]:
    from offline_agent.uplink.real_bridge import proxies, require_host

    redactor.register_lab_identity()
    require_host()
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ContractError("invalid_probe_run_id")
    native, settings = capture()
    iface = str(native["interface"])
    address = source_address(read_command(("/usr/sbin/ipconfig", "getifaddr", iface)))
    gateway = route_gateway(read_command(("/sbin/route", "-n", "get", "default")), iface)
    if address is None or gateway is None:
        raise ContractError("probe_wifi_route_required")
    configured = settings["-getdnsservers"].split()
    if not configured or "There" in configured:
        configured = read_command(
            ("/usr/sbin/ipconfig", "getoption", iface, "domain_name_server")
        ).split()
    resolvers = []
    for item in configured:
        try:
            resolvers.append(str(ipaddress.IPv4Address(item)))
        except ipaddress.AddressValueError:
            continue
    effective = proxies(native.get("effective_proxy"), iface, dict(os.environ))
    routes = [
        effective.route(endpoint, redactor) for endpoint in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS)
    ]
    if not all(route.supported for route in routes):
        raise ContractError("probe_effective_proxy_unresolved")
    context: dict[str, object] = {
        "profile": "lab_wifi",
        "interface": iface,
        "sources": {"current": address},
        "gateways": {"current": gateway},
        "resolvers": {
            "configured": resolvers[0] if resolvers else None,
            "cloudflare": _public_fixture("@PUBLIC_IPV4_003@"),
            "google": _public_fixture("@PUBLIC_IPV4_095@"),
        },
        "names": list(DNS_NAMES),
        "portal_names": [urlsplit(e.url).hostname for e in PORTAL_ENDPOINTS],
        "https_paths": [urlsplit(e.url).path for e in TLS_ENDPOINTS],
        "portal_paths": [urlsplit(e.url).path for e in PORTAL_ENDPOINTS],
        "https_status": [e.status for e in TLS_ENDPOINTS],
        "portal_status": [e.status for e in PORTAL_ENDPOINTS],
        "portal_bodies": [e.body.decode() for e in PORTAL_ENDPOINTS],
        "proxy_routes": [route.proxy for route in routes],
        "state": {
            "dns_configured": bool(resolvers),
            "proxy_configured": any(r.proxy for r in routes),
            "wifi_default_route": True,
            "lease_present": True,
        },
    }
    result = episode(
        program,
        context,
        root / "artifacts/uplink/probe-scripts" / run_id,
        limit_ms=max(0, min(30_000, int((deadline - time.time() - 12) * 1000))),
    )
    after, after_settings = capture()
    keys = (
        "serial",
        "interface",
        "service_id",
        "dns_sha256",
        "proxies_sha256",
        "protected_sha256",
        "location_sha256",
        "service_order_sha256",
    )
    match = settings == after_settings and all(native[k] == after[k] for k in keys)
    route_ok = (
        route_gateway(read_command(("/sbin/route", "-n", "get", "default")), iface) == gateway
    )
    result.update(
        settings_unchanged=match,
        wifi_route_unchanged=route_ok,
        before_sha256=hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest(),
        after_sha256=hashlib.sha256(
            json.dumps(after_settings, sort_keys=True).encode()
        ).hexdigest(),
    )
    if not match:
        result.update(status="refused", reason="probe_settings_changed_no_retry")
    if not route_ok:
        result.update(status="refused", reason="probe_wifi_route_changed_no_retry")
    return object_json(redactor.scrub_json(cast(dict[str, Json], result)))
