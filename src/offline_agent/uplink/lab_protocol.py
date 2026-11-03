"""Immutable laboratory topology and operations. Standard library only for VM workers."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

LAB_VERSION = "uplink-linux-lab-v1"
VM_NAME = "lima-uplink-harness"
FAULTS = (
    "wrong_dns",
    "stale_proxy",
    "dns_plus_proxy",
    "lease_loss",
    "bad_default_route",
    "interface_down",
    "captive_portal",
    "isp_down_saved_alternate",
    "healthy",
    "site_only_outage",
    "mtu_black_hole",
    "misleading_status",
)
# Separate development packages do not change the frozen twelve-case catalog.
FAULT_PACKAGES = ("test_host_blackhole", "wifi_ipv6_off", "owned_pf_outbound")
DNS_NAMES = ("one.uplink.test", "two.uplink-alt.test")
HTTPS_URLS = ("https://one.uplink.test/ok", "https://two.uplink-alt.test/ok")
PORTAL_URLS = ("http://one.uplink.test/portal", "http://two.uplink-alt.test/portal")
APP_URL = "https://one.uplink.test/app"
SERVER_IP = _public_fixture("@PUBLIC_IPV4_086@")
PRIMARY_IP, PRIMARY_GW = _public_fixture("@PUBLIC_IPV4_082@"), _public_fixture("@PUBLIC_IPV4_081@")
ALTERNATE_IP, ALTERNATE_GW = _public_fixture("@PUBLIC_IPV4_072@"), _public_fixture("@PUBLIC_IPV4_066@")
DEAD_DNS = _public_fixture("@PUBLIC_IPV4_073@")
PROXY_URL = _public_fixture("http://@PUBLIC_IPV4_086@:8888")
OPERATIONS = (
    "up",
    "check",
    "inject",
    "undo",
    "observe",
    "layer_probe",
    "probe_script",
    "resolve",
    "fetch",
    "apply",
    "down",
    "arm",
    "disarm",
    "fixture_check",
    "fault_package_check",
)
APPLY_POLICIES = {
    "set_dns": ("snapshot", "dhcp"),
    "restore_proxy": ("snapshot", "off"),
    "renew_lease": ("snapshot",),
    "cycle_interface": ("snapshot",),
    "reroute": ("alternate", "primary"),
    "restore_test_host_route": ("snapshot",),
    "restore_wifi_ipv6": ("snapshot",),
    "restore_owned_pf_anchor": ("snapshot",),
}


@dataclass(frozen=True)
class LabSpec:
    run_id: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{12}", self.run_id):
            raise ValueError("lab_run_id_requires_12_hex")

    @property
    def client(self) -> str:
        return "ul-" + self.run_id + "-c"

    @property
    def internet(self) -> str:
        return "ul-" + self.run_id + "-i"

    @property
    def interfaces(self) -> tuple[str, str, str, str]:
        stem = "u" + self.run_id[:8]
        return stem + "c0", stem + "i0", stem + "c1", stem + "i1"

    @property
    def state(self) -> Path:
        return Path("/var/lib/uplink-harness/runs") / self.run_id

    @property
    def resolver_directory(self) -> Path:
        return Path("/etc/netns") / self.client

    def namespaced(self, *argv: str, internet: bool = False) -> tuple[str, ...]:
        return ("ip", "netns", "exec", self.internet if internet else self.client, *argv)


def catalog(root: Path) -> dict[str, object]:
    raw: object = json.loads((root / "docs/UPLINK-FAULTS.json").read_text())
    if not isinstance(raw, dict):
        raise ValueError("fault_catalog_required")
    data = cast(dict[str, object], raw)
    rows = data.get("faults")
    if not isinstance(rows, list):
        raise ValueError("fault_rows_required")
    parsed: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_fault_row")
        parsed.append(cast(dict[str, object], row))
    if {r.get("id") for r in parsed} != set(FAULTS) or len(parsed) != len(FAULTS):
        raise ValueError("fault_catalog_contract_drift")
    return data


def catalog_digest(root: Path) -> str:
    catalog(root)
    return hashlib.sha256((root / "docs/UPLINK-FAULTS.json").read_bytes()).hexdigest()


def request(
    operation: str, run_id: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    LabSpec(run_id)
    if operation not in OPERATIONS:
        raise ValueError("unknown_lab_operation")
    value = payload or {}
    package = operation == "up" and set(value) == {"fault_package"}
    if package and value["fault_package"] not in FAULT_PACKAGES:
        raise ValueError("unknown_fault_package")
    sealed = operation in {"up", "inject"} and set(value) == {"fixture", "sha256"}
    if sealed:
        if __package__:
            from .sealed_registry import selection
        else:
            from sealed_registry import selection  # type: ignore[no-redef,import-not-found]
        selection(value)
    allowed: dict[str, set[str]] = {
        "inject": {"fault"},
        "resolve": {"target"},
        "fetch": {"target"},
        "apply": {"tool", "policy"},
        "arm": {"seconds"},
        "probe_script": {"program"},
    }
    if (
        not sealed
        and not package
        and set(value) != allowed.get(operation, set())
        and not (
            operation == "arm"
            and set(value) == {"seconds", "probe_scripts"}
            and value["probe_scripts"] is True
        )
    ):
        raise ValueError("invalid_lab_payload")
    if operation == "inject" and not sealed and value["fault"] not in (*FAULTS, *FAULT_PACKAGES):
        raise ValueError("unknown_fault")
    if operation in {"resolve", "fetch"}:
        maximum = 1 if operation == "resolve" else 4
        if type(value["target"]) is not int or not 0 <= value["target"] <= maximum:
            raise ValueError("invalid_probe_target")
    if operation == "apply":
        tool, policy = value["tool"], value["policy"]
        if not isinstance(tool, str) or policy not in APPLY_POLICIES.get(tool, ()):
            raise ValueError("unsupported_lab_action")
    if operation == "arm" and (
        type(value["seconds"]) is not int
        or not 1 <= value["seconds"] <= (55 if value.get("probe_scripts") is True else 20)
    ):
        raise ValueError("invalid_rollback_deadline")
    if operation == "probe_script" and not isinstance(value["program"], dict):
        raise ValueError("invalid_probe_program_payload")
    return {"version": 1, "operation": operation, "run_id": run_id, "payload": value}
