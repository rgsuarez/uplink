"""Closed desk fault packages. Existing DNS/proxy authority never admits these faults."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

VERSION = "uplink-bounded-faults-v1"
FAULTS = ("test_host_blackhole", "wifi_ipv6_off", "owned_pf_outbound")
IPV4_FAULTS = ("test_host_blackhole", "owned_pf_outbound")
TOOLS = ("restore_test_host_route", "restore_wifi_ipv6", "restore_owned_pf_anchor")
SECONDS = 60
TARGET_NAME = "www.gstatic.com"
TARGET_URL = "https://www.gstatic.com/generate_204"
PF = "/sbin/pfctl"
ROUTE = "/sbin/route"
NS = "/usr/sbin/networksetup"


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("fault_package_object_required")
    return cast(dict[str, object], value)


@dataclass(frozen=True)
class FaultSpec:
    run_id: str
    fault: str
    target_ipv4: str
    target_ipv6: str
    wifi_interface: str
    # Explicit exclusion inventory from the operator's read-only preflight.
    protected_networks: tuple[str, ...]
    tailscale_addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{12}", self.run_id) or self.fault not in FAULTS:
            raise ValueError("invalid_fault_package")
        if not re.fullmatch(r"en[0-9]+", self.wifi_interface) or self.wifi_interface == "en7":
            raise ValueError("primary_wifi_interface_required")
        if not self.protected_networks or len(self.protected_networks) > 128:
            raise ValueError("protected_network_inventory_required")
        networks = [ipaddress.ip_network(n, strict=False) for n in self.protected_networks]
        if not self.tailscale_addresses or len(self.tailscale_addresses) > 1024:
            raise ValueError("tailscale_address_inventory_required")
        management = {ipaddress.ip_address(value) for value in self.tailscale_addresses}
        for value, version in ((self.target_ipv4, 4), (self.target_ipv6, 6)):
            # No IPv6 target is needed for an IPv4-only effect. If one is supplied,
            # it still passes the original exclusions; IPv6-off always requires it.
            if version == 6 and value == "" and self.fault in IPV4_FAULTS:
                continue
            address = ipaddress.ip_address(value)
            if (
                address.version != version
                or str(address) != value
                or not address.is_global
                or address.is_multicast
                or address.is_reserved
                or isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
            ):
                raise ValueError("public_test_destination_required")
            if any(address in n for n in networks if n.version == version):
                raise ValueError("test_destination_overlaps_protected_network")
            if address in management:
                raise ValueError("test_destination_matches_tailscale_management")

    @property
    def anchor(self) -> str:
        return "com.apple/uplink-v1-" + self.run_id

    @property
    def tool(self) -> str:
        return TOOLS[FAULTS.index(self.fault)]

    def document(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "fault": self.fault,
            "target_ipv4": self.target_ipv4,
            "target_ipv6": self.target_ipv6,
            "wifi_interface": self.wifi_interface,
            "protected_networks": list(self.protected_networks),
            "tailscale_addresses": list(self.tailscale_addresses),
        }


def parse_spec(value: object) -> FaultSpec:
    row = obj(value)
    keys = {
        "run_id",
        "fault",
        "target_ipv4",
        "target_ipv6",
        "wifi_interface",
        "protected_networks",
        "tailscale_addresses",
    }
    if set(row) != keys or any(
        not isinstance(row[k], str) for k in keys - {"protected_networks", "tailscale_addresses"}
    ):
        raise ValueError("fault_spec_fields_refused")
    networks = row["protected_networks"]
    if not isinstance(networks, list) or any(not isinstance(n, str) for n in networks):
        raise ValueError("protected_network_inventory_required")
    tailscale = row["tailscale_addresses"]
    if not isinstance(tailscale, list) or any(not isinstance(n, str) for n in tailscale):
        raise ValueError("tailscale_address_inventory_required")
    return FaultSpec(
        str(row["run_id"]),
        str(row["fault"]),
        str(row["target_ipv4"]),
        str(row["target_ipv6"]),
        str(row["wifi_interface"]),
        tuple(networks),
        tuple(tailscale),
    )


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_commands(spec: FaultSpec) -> dict[str, tuple[str, ...]]:
    rows = {
        "default_ipv4": (ROUTE, "-n", "get", "-inet", "default"),
        "default_ipv6": (ROUTE, "-n", "get", "-inet6", "default"),
        "ipv4_route_table": ("/usr/sbin/netstat", "-rn", "-f", "inet"),
        "ipv6_route_table": ("/usr/sbin/netstat", "-rn", "-f", "inet6"),
        "interface_inventory": ("/sbin/ifconfig", "-l"),
        "wifi_info": (NS, "-getinfo", "Wi-Fi"),
        "wifi_v6_routes": (NS, "-getv6additionalroutes", "Wi-Fi"),
        "service_order": (NS, "-listnetworkserviceorder"),
        "pf_main_rules": (PF, "-sr"),
        "pf_anchor_rules": (PF, "-a", spec.anchor, "-sr"),
        "pf_anchor_children": (PF, "-a", spec.anchor, "-s", "Anchors"),
        "pf_anchor_tables": (PF, "-a", spec.anchor, "-s", "Tables"),
    }
    for getter in (
        "-getdnsservers",
        "-getsearchdomains",
        "-getwebproxy",
        "-getsecurewebproxy",
        "-getautoproxyurl",
        "-getproxybypassdomains",
        "-getsocksfirewallproxy",
        "-getproxyautodiscovery",
    ):
        rows[getter] = (NS, getter, "Wi-Fi")
    return rows


def commands(spec: FaultSpec) -> dict[str, object]:
    inject: tuple[str, ...]
    revert: tuple[str, ...]
    if spec.fault == "test_host_blackhole":
        inject = (
            ROUTE,
            "-n",
            "add",
            "-host",
            "-ifscope",
            spec.wifi_interface,
            spec.target_ipv4,
            _public_fixture("@PUBLIC_IPV4_056@"),
            "-blackhole",
        )
        revert = (
            ROUTE,
            "-n",
            "delete",
            "-host",
            "-ifscope",
            spec.wifi_interface,
            spec.target_ipv4,
            _public_fixture("@PUBLIC_IPV4_056@"),
            "-blackhole",
        )
    elif spec.fault == "wifi_ipv6_off":
        inject = (NS, "-setv6off", "Wi-Fi")
        revert = (NS, "-setv6automatic", "Wi-Fi")
    else:
        inject = (PF, "-a", spec.anchor, "-f", "fault.rules")
        revert = (PF, "-a", spec.anchor, "-F", "rules")
    rule = (
        f"block drop out quick on {spec.wifi_interface} inet proto tcp "
        f"from any to {spec.target_ipv4} port 443\n"
    )
    return {
        "inject": list(inject),
        "revert": list(revert),
        "pf_rule": rule,
        "auto_revert_seconds": SECONDS,
        "repair_tool": spec.tool,
    }


def target_command(spec: FaultSpec) -> tuple[str, ...]:
    v6 = spec.fault == "wifi_ipv6_off"
    address = "[" + spec.target_ipv6 + "]" if v6 else spec.target_ipv4
    return (
        "/usr/bin/curl",
        "--disable",
        "--silent",
        "--show-error",
        "--noproxy",
        "*",
        "--ipv6" if v6 else "--ipv4",
        "--interface",
        spec.wifi_interface,
        "--resolve",
        TARGET_NAME + ":443:" + address,
        "--connect-timeout",
        "2",
        "--max-time",
        "3",
        "--max-filesize",
        "4096",
        "--write-out",
        "\n%{http_code}",
        TARGET_URL,
    )


def validate_before(spec: FaultSpec, snapshot: dict[str, str], facts: object) -> None:
    if set(snapshot) != set(snapshot_commands(spec)) or any(
        not isinstance(v, str) for v in snapshot.values()
    ):
        raise ValueError("complete_fault_snapshot_required")
    row = obj(facts)
    checked = row.get("checked_at")
    if type(checked) not in (int, float) or not 0 <= time.time() - float(str(checked)) <= 30:
        raise ValueError("fresh_fault_before_state_required")
    for key in (
        "ssh_ipv4_verified",
        "oob_ipv4_verified",
        "tailscale_inventory_verified",
        "protected_inventory_verified",
        "target_dns_binding_verified",
        "target_not_management_or_resolver",
        "target_probe_passed",
        "online_six_passed",
        "protected_services_unchanged",
        "no_existing_target_host_route",
    ):
        if row.get(key) is not True:
            raise ValueError("fault_before_state_not_admitted")
    if not any(
        line.strip() == "interface: " + spec.wifi_interface
        for line in snapshot["default_ipv4"].splitlines()
    ):
        raise ValueError("wifi_default_route_required")
    if spec.fault == "wifi_ipv6_off" and "IPv6: Automatic" not in snapshot["wifi_info"]:
        raise ValueError("only_automatic_ipv6_snapshot_supported")
    if spec.fault == "owned_pf_outbound":
        if row.get("pf_enabled") is not True or row.get("no_target_pf_states") is not True:
            raise ValueError("pf_enabled_without_target_states_required")
        if not re.search(r'^anchor "com\.apple/\*"(?: all)?\s*$', snapshot["pf_main_rules"], re.M):
            raise ValueError("existing_pf_parent_hook_required_no_main_rules_edit")
        if any(
            snapshot[k].strip()
            for k in ("pf_anchor_rules", "pf_anchor_children", "pf_anchor_tables")
        ):
            raise ValueError("owned_empty_anchor_required")


def parse_action(value: object, spec: FaultSpec, snapshot_id: str) -> dict[str, object]:
    row = obj(value)
    expected = {
        "version": 1,
        "tool": spec.tool,
        "snapshot_id": snapshot_id,
        "policy": "snapshot",
        "evidence_ids": ["fault-v1-target", "fault-v1-drift"],
    }
    if type(row.get("version")) is not int or row != expected:
        raise ValueError("fault_action_refused")
    return expected


def action_schema(spec: FaultSpec, snapshot_id: str) -> dict[str, object]:
    """A single snapshot-only repair, usable by a constrained agent tool caller."""
    action = {
        "version": 1,
        "tool": spec.tool,
        "snapshot_id": snapshot_id,
        "policy": "snapshot",
        "evidence_ids": ["fault-v1-target", "fault-v1-drift"],
    }
    return {
        "type": "object",
        "properties": {k: {"const": v} for k, v in action.items()},
        "required": list(action),
        "additionalProperties": False,
    }


def admit_window(value: object, spec: FaultSpec, now: float, package_sha256: str) -> None:
    row = obj(value)
    if row.get("kind") != "KAYLIN-OK-FAULTS-V1" or row.get("authorized") is not True:
        raise ValueError("new_fault_window_required")
    if (
        row.get("spec_sha256") != digest(spec.document())
        or row.get("package_sha256") != package_sha256
    ):
        raise ValueError("exact_fault_package_window_required")
    if row.get("exclusive") is not True or row.get("r2_handback_verified") is not True:
        raise ValueError("exclusive_released_window_required")
    times = []
    for key in ("starts_at", "expires_at"):
        if not isinstance(row.get(key), str):
            raise ValueError("fault_window_time_required")
        stamp = datetime.fromisoformat(str(row[key]))
        if stamp.tzinfo is None:
            raise ValueError("fault_window_timezone_required")
        times.append(stamp.astimezone(UTC).timestamp())
    if not math.isfinite(now) or not times[0] <= now <= times[1] - 90:
        raise ValueError("fault_window_closed_or_insufficient")


def restoration(before: dict[str, str], after: dict[str, str]) -> dict[str, object]:
    if not before or set(before) != set(after):
        raise ValueError("complete_exact_diff_required")
    fields = {k: before[k] == after[k] for k in before}
    return {
        "match": all(fields.values()),
        "fields": fields,
        "before_sha256": digest(before),
        "after_sha256": digest(after),
    }
