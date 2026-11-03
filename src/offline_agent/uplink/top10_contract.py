"""Frozen TOP10 contract and bounded private I/O, also importable by system Python.

No network, subprocess or model action occurs on import. Live authority is separate
from the campaign definition. Public inputs carry no device coordinates or secrets.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import math
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import cast

VERSION = "uplink-top10-v2"
REGISTRY_SHA256 = "d93784fbb40e8912ad26a0d1ab613bc55bd8022e5346818159cf8100dd358c0f"
PROMPT_SHA256 = "bab956b281eb061c4bf5410e1d40ad477dbf72e69ebaeb42f1a42eb0824f8ecf"
ORACLE_SHA256 = "8ada6a245ee5929d08b393d5f9077ba46712aa9302e31109d443f035a7755d42"
EDGE_ORACLE_SHA256 = "556dd71e13700e83784866e296a036d2de2247c87ff2199f65dbf5fe52a3b1dd"
FAULT_IDS = tuple(f"F{n:02d}" for n in range(1, 11))
BENCH_FAULT_IDS = ("F03", "F04", "F05", "F06", "F07", "F09", "F10")
HELD_FAULTS = {
    "F01": "rejoin by exact BSSID unproven on bench",
    "F02": "rejoin by exact BSSID unproven on bench",
    "F08": "Keychain access forbidden on bench; restore unproven",
}
BENCH_TRIALS = 21
OOB_INTERFACE = "en7"
OOB_SERVER_IPV4 = _public_fixture("@PUBLIC_IPV4_062@")
OOB_JUMP_HOST = "relay"
OOB_REQUIRED = 3
OOB_MAX_ATTEMPTS = 5
OOB_MAX_FAILURES = 1
OOB_TIMEOUT = 8.0
OOB_FRESH_SECONDS = 90
RELAY_MAX_LOAD1_PER_CORE = 0.75
RELAY_MAX_LOAD5_PER_CORE = 1.0
HELD_REPAIRS = ("restore_radio", "restore_interface", "restore_saved_network")
PRIVILEGED_SCOPES = (
    "root_suite_ledger_and_snapshot_files",
    "root_readback_and_resource_guards",
    "inject_F03_F04_F05_F06_F07_F09_F10",
    "snapshot_ipv4_and_dhcp_restore",
    "snapshot_dns_restore",
    "snapshot_proxy_restore",
    "snapshot_mtu_restore",
    "root_launchd_watchdog_bootstrap_print_bootout",
)
SERVER_PID = 19904
# KAYLIN-OK-TOP10-FRI-CORRECTION-2: two non-overlapping timelines (data only). The later
# timeline is bound once its owner release opens; until then the earlier one stays bound.
TIMELINES = (
    {
        "owner_release_earliest": "2026-10-30T21:05:00-05:00",
        "owner_release_latest": "2026-10-30T21:15:00-05:00",
        "relay_load_check_at": "2026-10-30T21:15:00-05:00",
        "kaylin_verify_by": "2026-10-30T21:25:00-05:00",
        "start": "2026-10-30T21:30:00-05:00",
        "latest_start": "2026-10-30T21:45:00-05:00",
        "end": "2026-10-30T23:10:00-05:00",
    },
    {
        "owner_release_earliest": "2026-10-31T07:05:00-05:00",
        "owner_release_latest": "2026-10-31T07:15:00-05:00",
        "relay_load_check_at": "2026-10-31T07:15:00-05:00",
        "kaylin_verify_by": "2026-10-31T07:25:00-05:00",
        "start": "2026-10-31T07:30:00-05:00",
        "latest_start": "2026-10-31T07:45:00-05:00",
        "end": "2026-10-31T09:10:00-05:00",
    },
)
TIMELINE = TIMELINES[
    int(time.time() >= datetime.fromisoformat(TIMELINES[1]["owner_release_earliest"]).timestamp())
]
WINDOW_START = datetime.fromisoformat(TIMELINE["start"]).timestamp()
WINDOW_END = datetime.fromisoformat(TIMELINE["end"]).timestamp()
LATEST_START = datetime.fromisoformat(TIMELINE["latest_start"]).timestamp()
OWNER_LOOKBACK_SECONDS = 1800
OWNER_RELEASE_EARLIEST = datetime.fromisoformat(TIMELINE["owner_release_earliest"]).timestamp()
OWNER_RELEASE_LATEST = datetime.fromisoformat(TIMELINE["owner_release_latest"]).timestamp()
KAYLIN_VERIFY_BY = datetime.fromisoformat(TIMELINE["kaylin_verify_by"]).timestamp()
RELAY_LOAD_CHECK_AT = datetime.fromisoformat(TIMELINE["relay_load_check_at"]).timestamp()
TRIAL_SECONDS = 150
HANDBACK_SECONDS = 600
RESERVE_SECONDS = 300
WATCHDOG_SECONDS = 45
RESTORE_SECONDS = 30
MAX_DECISIONS = 4
MODEL_TIMEOUT = 8
REPAIRS = (
    "restore_radio",
    "restore_interface",
    "restore_ipv4",
    "renew_lease",
    "restore_dns",
    "restore_gateway",
    "restore_proxy",
    "restore_saved_network",
    "restore_mtu",
)
TOOLS = (*REPAIRS, "probe_online", "report", "give_up", "declare_online")
FACTS = (
    "radio_on",
    "interface_up",
    "associated_with_snapshot",
    "ipv4_usable",
    "ipv4_linklocal",
    "dhcp_lease_present",
    "gateway_matches_snapshot",
    "dns_matches_snapshot",
    "proxy_matches_snapshot",
    "saved_network_matches_snapshot",
    "mtu_matches_snapshot",
    "protected_services_unchanged",
    "persistent_baseline_equal",
    "ipv4_path_ok",
    "https_path_ok",
    "mtu_probe_ok",
    "configured_dns_ok",
    "portal_redirect_seen",
)
CHECK_IDS = ("dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google")
EVIDENCE_IDS = (*FACTS, *CHECK_IDS)
REASONS = (
    "snapshot_repair",
    "connectivity_unverified",
    "needs_human",
    "no_safe_action",
    "verified",
)
ACTIVE = frozenset({"snapshotted", "armed", "injecting", "fault_verified", "deciding"})
TRANSITIONS = {
    "reserved": {"snapshotted", "closed"},
    "snapshotted": {"armed", "restoring"},
    "armed": {"injecting", "restoring"},
    "injecting": {"fault_verified", "restoring"},
    "fault_verified": {"deciding", "restoring"},
    "deciding": {"deciding", "restoring"},
    "restoring": {"restored", "restore_failed"},
    "restored": {"closed"},
    "restore_failed": set(),
    "closed": set(),
}


class Refused(ValueError):
    """Only closed reason labels may leave a boundary."""


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise Refused("top10_object_required")
    return cast(dict[str, object], value)


def text(value: object, pattern: str, reason: str = "top10_text_refused") -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise Refused(reason)
    return value


def number(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise Refused("top10_number_required")
    parsed = float(str(value))
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise Refused("top10_number_outside_bound")
    return parsed


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(read_bytes(path, maximum=32 * 1024 * 1024)).hexdigest()


def read_bytes(path: Path, *, maximum: int = 4_000_000, private: bool = False) -> bytes:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused("top10_symlink_refused")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
            raise Refused("top10_bounded_regular_file_required")
        if private and (info.st_mode & 0o077 or info.st_uid not in {0, os.getuid()}):
            raise Refused("top10_private_file_required")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise Refused("top10_file_size_changed")
    return data


def reject_constant(value: str) -> None:
    raise Refused("top10_nonfinite_json_refused")


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Refused("top10_duplicate_json_key")
        result[key] = value
    return result


def parse_json(value: str | bytes) -> dict[str, object]:
    try:
        return obj(
            json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_pairs)
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Refused("top10_json_refused") from exc


def read(path: Path, *, private: bool = False) -> dict[str, object]:
    return parse_json(read_bytes(path, private=private))


def save(path: Path, value: object, *, exclusive: bool = False) -> None:
    """Atomic, fsynced projection. The caller records intent before calling this."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused("top10_symlink_refused")
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def audit(directory: Path, event: str, detail: object) -> None:
    fd = os.open(
        directory / "audit.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        json.dump({"at": time.time(), "event": event, "detail": detail}, stream, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def transition(directory: Path, state: dict[str, object], next_state: str, reason: str) -> None:
    current = str(state["lifecycle"])
    if next_state not in TRANSITIONS.get(current, set()):
        raise Refused("top10_illegal_state_transition")
    audit(directory, "transition", {"from": current, "to": next_state, "reason": reason})
    state["lifecycle"] = next_state
    save(directory / "state.json", state)


def load_registry(root: Path) -> dict[str, object]:
    path = root / "configs/top10-faults.v1.json"
    if file_hash(path) != REGISTRY_SHA256:
        raise Refused("top10_frozen_registry_changed")
    if (root / "configs/top10-faults.v1.sha256").read_text() != (
        REGISTRY_SHA256 + "  configs/top10-faults.v1.json\n"
    ):
        raise Refused("top10_registry_anchor_changed")
    if file_hash(root / "harness/top10/prompt.txt") != PROMPT_SHA256:
        raise Refused("top10_frozen_prompt_changed")
    if file_hash(root / "src/offline_agent/uplink/oracle.py") != ORACLE_SHA256:
        raise Refused("top10_independent_oracle_changed")
    if file_hash(root / "tests/uplink/test_oracle.py") != EDGE_ORACLE_SHA256:
        raise Refused("top10_edge_oracle_changed")
    return read(path)


def cases(root: Path) -> tuple[dict[str, object], ...]:
    rows = load_registry(root)["faults"]
    if not isinstance(rows, list) or tuple(obj(r).get("id") for r in rows) != FAULT_IDS:
        raise Refused("top10_exact_fault_order_required")
    return tuple(obj(r) for r in rows)


def execution_profile() -> dict[str, object]:
    return {
        "version": "bench-20261029-0550-v1",
        "selected_fault_ids": list(BENCH_FAULT_IDS),
        "held_faults": [
            {"fault_id": fault, "status": "held", "reason": reason}
            for fault, reason in HELD_FAULTS.items()
        ],
        "planned_trials": BENCH_TRIALS,
        "oob_jump_host": OOB_JUMP_HOST,
        "oob_interface": OOB_INTERFACE,
        "oob_server_ipv4": OOB_SERVER_IPV4,
        "oob_consecutive_successes": OOB_REQUIRED,
        "oob_max_attempts": OOB_MAX_ATTEMPTS,
        "oob_max_failures": OOB_MAX_FAILURES,
        "oob_probe_timeout_seconds": OOB_TIMEOUT,
        "relay_max_load1_per_core": RELAY_MAX_LOAD1_PER_CORE,
        "relay_max_load5_per_core": RELAY_MAX_LOAD5_PER_CORE,
        "keychain_reads": "forbidden",
        "privileged_scopes_require_richard_approval": list(PRIVILEGED_SCOPES),
    }


def selected_slots(manifest: dict[str, object]) -> list[dict[str, object]]:
    slots = manifest.get("slots")
    if not isinstance(slots, list) or len(slots) != 30:
        raise Refused("top10_thirty_registered_slots_required")
    expected = [(fault, trial) for fault in FAULT_IDS for trial in (1, 2, 3)]
    rows = [obj(slot) for slot in slots]
    if [(r.get("fault_id"), r.get("trial")) for r in rows] != expected:
        raise Refused("top10_frozen_trial_order_changed")
    result = [row for row in rows if row["fault_id"] in BENCH_FAULT_IDS]
    if len(result) != BENCH_TRIALS:
        raise Refused("top10_exact_bench_scope_required")
    return result


def held_slots(manifest: dict[str, object]) -> list[dict[str, object]]:
    selected_slots(manifest)
    rows = manifest["slots"]
    assert isinstance(rows, list)
    return [
        obj(row)
        | {
            "status": "held",
            "reason": HELD_FAULTS[str(obj(row)["fault_id"])],
            "model_calls": 0,
            "injected": False,
        }
        for row in rows
        if obj(row)["fault_id"] in HELD_FAULTS
    ]


def require_bench_fault(fault_id: object) -> None:
    if fault_id not in BENCH_FAULT_IDS:
        raise Refused("top10_fault_held_or_not_selected_for_bench")


def measured(value: object) -> dict[str, bool]:
    row = obj(value)
    if set(row) != set(FACTS) or any(type(row[k]) is not bool for k in FACTS):
        raise Refused("top10_complete_measured_facts_required")
    return {k: row[k] is True for k in FACTS}


def baseline_ok(value: object) -> bool:
    row = measured(value)
    negative = {"portal_redirect_seen", "ipv4_linklocal"}
    return all(row[k] for k in FACTS if k not in negative) and not any(row[k] for k in negative)


def fault_effect(fault_id: str, value: object) -> bool:
    """Frozen negative controls over independent measurements, never injector status."""
    row = measured(value)
    predicates = {
        "F01": not row["radio_on"],
        "F02": row["radio_on"] and not row["interface_up"],
        "F03": row["ipv4_linklocal"] and not row["dhcp_lease_present"],
        "F04": not row["dns_matches_snapshot"] and not row["configured_dns_ok"],
        "F05": not row["gateway_matches_snapshot"] and not row["ipv4_path_ok"],
        "F06": not row["proxy_matches_snapshot"]
        and not row["https_path_ok"]
        and not row["portal_redirect_seen"],
        "F07": row["portal_redirect_seen"] and not row["proxy_matches_snapshot"],
        "F08": not row["saved_network_matches_snapshot"] and not row["associated_with_snapshot"],
        "F09": not row["mtu_matches_snapshot"] and not row["mtu_probe_ok"],
        "F10": (
            not row["persistent_baseline_equal"]
            and not row["ipv4_path_ok"]
            and not row["dhcp_lease_present"]
            and not row["gateway_matches_snapshot"]
            and all(
                row[k]
                for k in (
                    "radio_on",
                    "interface_up",
                    "associated_with_snapshot",
                    "dns_matches_snapshot",
                    "proxy_matches_snapshot",
                    "saved_network_matches_snapshot",
                    "mtu_matches_snapshot",
                    "ipv4_usable",
                )
            )
            and not row["ipv4_linklocal"]
        ),
    }
    if fault_id not in predicates:
        raise Refused("top10_unknown_fault")
    return predicates[fault_id] and row["protected_services_unchanged"]


def verify_manifest(root: Path, path: Path) -> dict[str, object]:
    load_registry(root)
    manifest = read(path)
    if (
        manifest.get("version") != VERSION
        or manifest.get("status") != "FROZEN"
        or manifest.get("registry_sha256") != REGISTRY_SHA256
        or manifest.get("prompt_sha256") != PROMPT_SHA256
        or manifest.get("oracle_sha256") != ORACLE_SHA256
        or manifest.get("arm") != "BASE"
        or manifest.get("server_pid") != SERVER_PID
        or manifest.get("execution_profile") != execution_profile()
    ):
        raise Refused("top10_frozen_manifest_required")
    files = obj(manifest.get("files"))
    if not 20 <= len(files) <= 1000 or set(files) != runtime_files(root):
        raise Refused("top10_manifest_file_count_refused")
    for name, wanted in files.items():
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or Path(name).is_absolute()
            or any(part in {".", ".."} for part in name.split("/"))
            or file_hash(root / name) != wanted
        ):
            raise Refused("top10_frozen_runtime_changed")
    slots = manifest.get("slots")
    if not isinstance(slots, list) or len(slots) != 30:
        raise Refused("top10_thirty_slots_required")
    expected = [(fault, trial) for fault in FAULT_IDS for trial in (1, 2, 3)]
    seen = set()
    # System Python on the target may be 3.9; both sequence lengths are checked above.
    for raw, pair in zip(slots, expected):  # noqa: B905
        row = obj(raw)
        if (row.get("fault_id"), row.get("trial")) != pair:
            raise Refused("top10_frozen_trial_order_changed")
        run_id = text(row.get("run_id"), r"[0-9a-f]{12}")
        if run_id in seen:
            raise Refused("top10_duplicate_run_id")
        seen.add(run_id)
    return manifest


def runtime_files(root: Path) -> set[str]:
    """Explicit asset classes. Private config, GO files and raw receipts never ship."""
    fixed = {
        "configs/top10-faults.v1.json",
        "configs/top10-faults.v1.sha256",
        "harness/top10/prompt.txt",
        "harness/top10/prompt.sha256",
        "harness/top10/GO-WINDOW.template.json",
        "harness/top10/OWNER-HANDBACK.template.json",
        "harness/top10/RICHARD-APPROVAL.template.json",
        "harness/macos/Top10Network.swift",
        "harness/macos/UplinkNetwork.swift",
        "src/offline_agent/uplink/uplink-top10-network",
        "src/offline_agent/uplink/uplink-network",
        "tests/uplink/test_oracle.py",
        "scripts/check_macos_swift.py",
        "docs/UPLINK-FAULTS.json",
        "pyproject.toml",
        "uv.lock",
        "LICENSE",
        "templates/manifest.json",
        "templates/glimmer_native.jinja",
        "templates/tokenizer_config.json",
        "templates/MODEL-LICENSE",
    }
    fixed.update(str(p.relative_to(root)) for p in (root / "src/offline_agent").rglob("*.py"))
    fixed.update(str(p.relative_to(root)) for p in (root / "scripts").glob("top10_*.py"))
    fixed.update(str(p.relative_to(root)) for p in (root / "tests/top10").glob("*.py"))
    fixed.update(str(row["script"]) for row in cases(root))
    return fixed


def validate_window(
    value: object,
    manifest: dict[str, object],
    manifest_sha256: str,
    now: float,
    *,
    cleanup: bool = False,
    new_trial: bool = False,
) -> dict[str, object]:
    row = obj(value)
    required = {
        "version",
        "kind",
        "authorized",
        "approved_by",
        "manifest_sha256",
        "arm",
        "server_pid",
        "server_birth",
        "start",
        "end",
        "owner_handback_sha256",
        "private_config_sha256",
        "oob_interface",
        "oob_client_ipv4",
        "oob_server_ipv4",
        "root_approval_sha256",
    }
    if (
        set(row) != required
        or type(row.get("version")) is not int
        or row.get("version") != 1
        or row.get("authorized") is not True
    ):
        raise Refused("top10_exact_authorized_window_required")
    if (
        row.get("kind") != "KAYLIN-OK-TOP10-FAULTS"
        or row.get("approved_by") != "Kaylin"
        or row.get("manifest_sha256") != manifest_sha256
        or row.get("arm") != "BASE"
        or row.get("server_pid") != SERVER_PID
        or manifest.get("server_pid") != SERVER_PID
    ):
        raise Refused("top10_window_binding_mismatch")
    text(row.get("owner_handback_sha256"), r"[0-9a-f]{64}")
    text(row.get("private_config_sha256"), r"[0-9a-f]{64}")
    text(row.get("root_approval_sha256"), r"[0-9a-f]{64}")
    text(row.get("server_birth"), r"[A-Za-z0-9 :+-]{15,80}")
    text(row.get("oob_interface"), r"en[0-9]+")
    if row["oob_interface"] != OOB_INTERFACE or row["oob_server_ipv4"] != OOB_SERVER_IPV4:
        raise Refused("top10_exact_bench_oob_pin_required")
    import ipaddress

    try:
        client = ipaddress.IPv4Address(str(row.get("oob_client_ipv4")))
        beginning = datetime.fromisoformat(str(row["start"]))
        ending = datetime.fromisoformat(str(row["end"]))
        if (
            client.is_unspecified
            or client.is_loopback
            or client.is_multicast
            or beginning.tzinfo is None
            or ending.tzinfo is None
        ):
            raise Refused("top10_explicit_window_coordinates_required")
        start, end = beginning.timestamp(), ending.timestamp()
    except ValueError as exc:
        raise Refused("top10_window_coordinates_refused") from exc
    if start != WINDOW_START or end != WINDOW_END:
        raise Refused("top10_exact_100_minute_slot_required")
    number(now, 0, 4_000_000_000)
    if now < start or (not cleanup and now >= end):
        raise Refused("top10_outside_window")
    if new_trial and now + TRIAL_SECONDS + HANDBACK_SECONDS + RESERVE_SECONDS > end:
        raise Refused("top10_insufficient_handback_reserve")
    return row


def validate_owner_release(released_at: object, now: float) -> float:
    number(now, 0, 4_000_000_000)
    released = number(released_at, OWNER_RELEASE_EARLIEST, OWNER_RELEASE_LATEST)
    if released > now:
        raise Refused("top10_owner_release_in_future")
    return released


def validate_launch(now: float, released_at: object) -> None:
    """Admission only; an admitted run and cleanup do not expire with the handback."""
    number(now, 0, 4_000_000_000)
    released = validate_owner_release(released_at, now)
    if now < WINDOW_START or now >= LATEST_START:
        raise Refused("top10_launch_outside_start_window")
    if now >= released + OWNER_LOOKBACK_SECONDS:
        raise Refused("top10_owner_release_expired_before_start")


def validate_root_approval(raw: object, window: dict[str, object]) -> dict[str, object]:
    """Exact operator-owned artifact. A GO alone never grants privileged scopes."""
    if not isinstance(raw, str) or not 0 < len(raw.encode()) <= 16_384:
        raise Refused("top10_richard_root_approval_required")
    if hashlib.sha256(raw.encode()).hexdigest() != window.get("root_approval_sha256"):
        raise Refused("top10_root_approval_binding_changed")
    row = parse_json(raw)
    if set(row) != {
        "version",
        "kind",
        "authorized",
        "approved_by",
        "manifest_sha256",
        "private_config_sha256",
        "window_binding_sha256",
        "start",
        "end",
        "scopes",
        "manual_recovery_authorized",
    }:
        raise Refused("top10_root_approval_fields_refused")
    if (
        type(row["version"]) is not int
        or row["version"] != 1
        or row["kind"] != "RICHARD-OK-TOP10-PRIVILEGED"
        or row["authorized"] is not True
        or row["approved_by"] != "Richard Suarez"
        or row["scopes"] != list(PRIVILEGED_SCOPES)
        or row["manual_recovery_authorized"] is not False
        or any(
            row[key] != window[key]
            for key in ("manifest_sha256", "private_config_sha256", "start", "end")
        )
        or row["window_binding_sha256"]
        != digest({key: value for key, value in window.items() if key != "root_approval_sha256"})
    ):
        raise Refused("top10_exact_richard_privileged_approval_required")
    return row
