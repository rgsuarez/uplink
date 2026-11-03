"""Exact source/spec/package and current-window binding for the new-fault controller."""

from __future__ import annotations

import re
from datetime import datetime

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.faults_v1 import IPV4_FAULTS, FaultSpec, commands, digest, parse_spec
from offline_agent.uplink.faults_v1_worker import PAYLOAD
from offline_agent.uplink.probe_serving import binding

KIND = "KAYLIN-OK-FAULTS-V1"
ENTRYPOINTS = (
    "scripts/run_faults_v1_model.py",
    "scripts/prepare_faults_v1_model.py",
    "scripts/faults_v1_dryrun.py",
)


def hash_value(value: Json) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError("newfault_hash_binding_required")
    return value


def specs(packet: dict[str, Json]) -> tuple[FaultSpec, ...]:
    if (
        type(packet.get("version")) is not int
        or packet.get("version") != 1
        or packet.get("kind") != "uplink-newfault-desk-packet"
    ):
        raise ContractError("newfault_packet_required")
    raw = packet.get("specs")
    if not isinstance(raw, list) or len(raw) != 2:
        raise ContractError("newfault_exact_two_specs_required")
    rows = tuple(parse_spec(object_json(r).get("spec")) for r in raw)
    if (
        tuple(s.fault for s in rows) != IPV4_FAULTS
        or len({s.run_id for s in rows}) != 2
        or any(s.target_ipv6 for s in rows)
    ):
        raise ContractError("newfault_ordered_ipv4_specs_required")
    for s, r in zip(rows, raw, strict=True):
        if object_json(r).get("spec_sha256") != digest(s.document()):
            raise ContractError("newfault_spec_hash_mismatch")
        if object_json(r).get("commands") != commands(s):
            raise ContractError("newfault_displayed_commands_mismatch")
    payload = object_json(packet.get("root_payload_sha256"))
    entrypoints = object_json(packet.get("entrypoints_sha256"))
    if set(payload) != set(PAYLOAD) or set(entrypoints) != set(ENTRYPOINTS):
        raise ContractError("newfault_complete_source_binding_required")
    for value in [*payload.values(), *entrypoints.values()]:
        hash_value(value)
    if packet.get("package_sha256") != digest(payload):
        raise ContractError("newfault_package_hash_mismatch")
    return rows


def stamp(value: Json) -> float:
    if not isinstance(value, str):
        raise ContractError("newfault_window_timestamp_required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ContractError("newfault_window_timezone_required")
    return parsed.timestamp()


def validate_window(
    window: dict[str, Json],
    packet: dict[str, Json],
    packet_sha: str,
    now: float,
    *,
    cleanup: bool = False,
    admission: bool = False,
) -> None:
    rows = specs(packet)
    if (
        type(window.get("version")) is not int
        or window.get("version") != 1
        or window.get("kind") != KIND
        or window.get("authorized") is not True
        or window.get("exclusive") is not True
        or window.get("owner_handback_verified") is not True
        or window.get("trials") != 2
        or type(window.get("trials")) is not int
        or window.get("action_mode") != "open"
    ):
        raise ContractError("newfault_exact_authorized_window_required")
    for key, wanted in (
        ("packet_sha256", packet_sha),
        ("runtime_sha256", packet.get("runtime_sha256")),
        ("package_sha256", packet.get("package_sha256")),
    ):
        if hash_value(window.get(key)) != hash_value(wanted):
            raise ContractError("newfault_window_binding_mismatch")
    hash_value(window.get("owner_handback_sha256"))
    binding({"arm": window.get("arm"), "adapter_path": window.get("adapter_path")})
    start, end = stamp(window.get("starts_at")), stamp(window.get("expires_at"))
    if not 0 < end - start <= 1200:
        raise ContractError("newfault_twenty_minute_window_required")
    if not cleanup and not start <= now < end - (180 if admission else 0):
        raise ContractError("newfault_window_not_current")
    worker = object_json(window.get("worker_windows"))
    if set(worker) != {s.run_id for s in rows}:
        raise ContractError("newfault_exact_worker_windows_required")
    for s in rows:
        expected = {
            "kind": "KAYLIN-OK-FAULTS-V1",
            "authorized": True,
            "exclusive": True,
            "r2_handback_verified": True,
            "starts_at": window["starts_at"],
            "expires_at": window["expires_at"],
            "spec_sha256": digest(s.document()),
            "package_sha256": packet["package_sha256"],
        }
        if worker[s.run_id] != expected:
            raise ContractError("newfault_worker_window_mismatch")
