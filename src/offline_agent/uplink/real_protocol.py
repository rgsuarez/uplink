"""Small, standard-library-only protocol for the isolated lab recovery helper."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

if __package__:
    from .real_fleet import validate_authorization
else:
    from real_fleet import validate_authorization  # type: ignore[import-not-found,no-redef]

REAL_VERSION = "uplink-lab-recovery-v1"
REAL_FAULTS = ("wrong_dns", "stale_proxy", "dns_and_proxy", "wifi_power_off")
RESTORES = ("set_dns", "restore_proxy", "cycle_interface")
MAX_SECONDS = 40
PROBE_SECONDS = 55


@dataclass(frozen=True)
class RealSpec:
    run_id: str
    fault: str
    seconds: int = MAX_SECONDS
    probe_scripts: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{12}", self.run_id):
            raise ValueError("invalid_real_run_id")
        if self.fault not in REAL_FAULTS:
            raise ValueError("invalid_real_fault")
        if type(self.probe_scripts) is not bool:
            raise ValueError("invalid_probe_profile")
        maximum = PROBE_SECONDS if self.probe_scripts else MAX_SECONDS
        if type(self.seconds) is not int or not 10 <= self.seconds <= maximum:
            raise ValueError("invalid_watchdog_bound")


def request(operation: str, run_id: str, payload: object = None) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("invalid_real_run_id")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict) or any(not isinstance(k, str) for k in payload):
        raise ValueError("invalid_real_payload")
    expected = {
        "prepare": {"fault", "seconds"},
        "arm": set(),
        "inject": set(),
        "observe": set(),
        "apply": {"tool"},
        "restore": set(),
        "disarm": {"verified_at"},
        "retire": set(),
        "watchdog": set(),
    }
    fields = expected.get(operation)
    if fields is None or (
        set(payload) != fields
        and not (
            operation == "prepare"
            and fields <= set(payload)
            and set(payload) <= fields | {"authorization", "probe_scripts"}
        )
    ):
        raise ValueError("unknown_real_operation_or_fields")
    if operation == "prepare":
        if type(payload["seconds"]) is not int or not isinstance(payload["fault"], str):
            raise ValueError("invalid_real_prepare")
        RealSpec(run_id, payload["fault"], payload["seconds"], payload.get("probe_scripts", False))
        if "authorization" in payload:
            validate_authorization(payload["authorization"])
    if operation == "apply" and payload["tool"] not in RESTORES:
        raise ValueError("only_snapshot_restores_allowed")
    if operation == "disarm" and (
        type(payload["verified_at"]) not in (int, float)
        or not math.isfinite(payload["verified_at"])
    ):
        raise ValueError("invalid_verification_time")
    return {"version": 1, "operation": operation, "run_id": run_id, "payload": payload}


def validate_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"version", "operation", "run_id", "payload"}:
        raise ValueError("invalid_real_request")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid_real_version")
    if not isinstance(value["operation"], str) or not isinstance(value["run_id"], str):
        raise ValueError("invalid_real_header")
    return request(value["operation"], value["run_id"], value["payload"])
