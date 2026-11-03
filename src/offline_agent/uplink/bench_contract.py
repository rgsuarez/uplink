"""Separate one-slot authority and immutable inventory; root-safe standard library only."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from datetime import datetime
from pathlib import Path

if __package__:
    from . import top10_contract as c
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]

KIND = "uplink-bench-reduced-v1"
MANIFEST_PATH = "harness/bench/FROZEN.json"
PROFILE_ID = "snapshot-restore-descriptions-v1"
PARENT_SHA256 = "856d66142767432191caae850ddafa860db8d6ca06b5d5e56e26665cf880924e"
PROMPT_SHA256 = "f68fac36a29da410753ba66e6507f70dbc251b324e76ee86f6befb0f7aa03949"
MODEL_SHA256 = "4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e"
MODEL_BYTES = 16756683904
SCORER_SHA256 = "c704c89f9bedeacc1a045f62a722deb3f99a2f2c087a1c12aa4724c17cd551ad"
BASE = Path("/private/var/db/uplink-bench-reduced")
ROOT_FILES = (
    "bench_worker.py",
    "bench_contract.py",
    "bench_attempt.py",
    "top10_worker.py",
    "top10_engine.py",
    "top10_kernel.py",
    "top10_injectors.py",
    "top10_contract.py",
    "top10_attempt.py",
    "top10_portal.py",
    "top10_oob.py",
    "uplink-top10-network",
)
WRITE_SET = {
    "fault": ["native:bad_dns"],
    "model_restore": [
        "native:restore_ipv4",
        "ipconfig:dhcp",
        "native:restore_dns",
        "native:restore_proxy",
        "ifconfig:snapshot_mtu",
    ],
    "cleanup": ["same captured-state restores", "native:verify_profiles (read-only)"],
    "root_files": ["/private/var/db/uplink-bench-reduced/** (one registered attempt)"],
    "watchdog": ["bootstrap and retire only system/com.uplink.bench-reduced.<run_id>"],
    "forbidden": [
        "radio",
        "interface cycling",
        "saved networks",
        "Keychain",
        "model server",
        "management network",
        "other fault injections",
    ],
}
LIMITS = {
    "trials": 1,
    "model_decisions": 4,
    "decision_seconds": 8,
    "tokenize_requests_per_decision": 1,
    "unscored_completions": 0,
    "watchdog_seconds": 45,
    "restore_seconds": 30,
    "controller_seconds": 110,
    "cooldown_seconds": 40,
    "slot_seconds": 150,
    "window_seconds": 1800,
}
ORACLES = {
    "src/offline_agent/uplink/oracle.py": c.ORACLE_SHA256,
    "src/offline_agent/uplink/top10_score.py": SCORER_SHA256,
    "tests/uplink/test_oracle.py": c.EDGE_ORACLE_SHA256,
}
NEW_FILES = (
    "harness/top10/FROZEN.json",
    "harness/bench/prompt.txt",
    "harness/bench/descriptions.json",
    "harness/bench/README.md",
    "harness/bench/authority.template.json",
    "harness/bench/owner.template.json",
    "harness/bench/richard.template.json",
    *(
        "src/offline_agent/uplink/" + name
        for name in (
            "bench_contract.py",
            "bench_profile.py",
            "bench_attempt.py",
            "bench_worker.py",
            "bench_bridge.py",
            "bench_harness.py",
            "bench_run.py",
            "bench_package.py",
        )
    ),
    "tests/bench/conftest.py",
    "tests/bench/test_profile.py",
    "tests/bench/test_authority.py",
    "tests/bench/test_lifecycle.py",
    "tests/bench/test_package.py",
    "tests/bench/test_bridge.py",
)


def sha(value: object) -> str:
    return c.text(value, r"[0-9a-f]{64}")


def sync_directory(path: Path) -> None:
    """A consumed intent must survive directory-entry loss before its effect starts."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def slot(manifest: dict[str, object]) -> dict[str, object]:
    return {"fault_id": "F04", "trial": 1, "run_id": manifest["run_id"]}


def validate_registration(value: object) -> dict[str, object]:
    row = c.obj(value)
    if set(row) != {
        "kind",
        "status",
        "run_id",
        "registration_nonce",
        "fault_id",
        "trial",
        "planned_trials",
        "arm",
        "profile_id",
        "prompt_sha256",
        "model_sha256",
        "model_bytes",
        "profile_sha256",
        "parent_sha256",
        "oracles",
        "limits",
        "privileged_write_set",
        "files",
    }:
        raise c.Refused("bench_registration_fields_refused")
    fixed = {
        "kind": KIND,
        "status": "SEALED",
        "fault_id": "F04",
        "trial": 1,
        "planned_trials": 1,
        "arm": "BASE",
        "profile_id": PROFILE_ID,
        "prompt_sha256": PROMPT_SHA256,
        "model_sha256": MODEL_SHA256,
        "model_bytes": MODEL_BYTES,
        "parent_sha256": PARENT_SHA256,
        "oracles": ORACLES,
        "limits": LIMITS,
        "privileged_write_set": WRITE_SET,
    }
    if any(c.digest(row.get(k)) != c.digest(v) for k, v in fixed.items()) or any(
        type(row[k]) is not int for k in ("trial", "planned_trials", "model_bytes")
    ):
        raise c.Refused("bench_exact_one_slot_registration_required")
    c.text(row["run_id"], r"[0-9a-f]{12}")
    c.text(row["registration_nonce"], r"[0-9a-f]{32}")
    sha(row["profile_sha256"])
    files = c.obj(row["files"])
    if not 175 <= len(files) <= 240:
        raise c.Refused("bench_explicit_inventory_required")
    for name, digest in files.items():
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or Path(name).is_absolute()
            or any(p in {".", "..", ""} for p in name.split("/"))
        ):
            raise c.Refused("bench_inventory_path_refused")
        sha(digest)
    return row


def verify_manifest(root: Path) -> dict[str, object]:
    manifest = validate_registration(c.read(root / MANIFEST_PATH))
    files = c.obj(manifest["files"])
    parent_path = root / "harness/top10/FROZEN.json"
    if c.file_hash(parent_path) != PARENT_SHA256:
        raise c.Refused("bench_parent_anchor_changed")
    parent = c.read(parent_path)
    if set(files) != set(c.obj(parent["files"])) | set(NEW_FILES):
        raise c.Refused("bench_exact_inventory_membership_required")
    for name, digest in c.obj(parent["files"]).items():
        if files.get(name) != digest:
            raise c.Refused("bench_frozen_file_replaced")
    historical = parent.get("slots")
    if not isinstance(historical, list):
        raise c.Refused("bench_parent_slots_invalid")
    if any(c.obj(s).get("run_id") == manifest["run_id"] for s in historical):
        raise c.Refused("bench_historical_run_id_refused")
    for name, digest in files.items():
        if c.file_hash(root / name) != digest:
            raise c.Refused("bench_package_file_changed")
    profile_files = {
        name: files.get(name)
        for name in (
            "src/offline_agent/uplink/bench_profile.py",
            "harness/bench/prompt.txt",
            "harness/bench/descriptions.json",
            "templates/glimmer_native.jinja",
            "templates/tokenizer_config.json",
        )
    }
    if (
        c.digest(profile_files) != manifest["profile_sha256"]
        or files.get("harness/bench/prompt.txt") != PROMPT_SHA256
        or any(files.get(k) != v for k, v in ORACLES.items())
    ):
        raise c.Refused("bench_profile_or_oracle_binding_changed")
    return manifest


def timestamp(value: object) -> float:
    if not isinstance(value, str):
        raise c.Refused("bench_explicit_time_required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise c.Refused("bench_time_unparsed") from exc
    if parsed.tzinfo is None:
        raise c.Refused("bench_timezone_required")
    return parsed.timestamp()


def binding(manifest: dict[str, object], manifest_sha: str, package_sha: str) -> dict[str, object]:
    return {
        k: manifest[k]
        for k in ("run_id", "registration_nonce", "profile_sha256", "prompt_sha256", "model_sha256")
    } | {
        "manifest_sha256": sha(manifest_sha),
        "package_sha256": sha(package_sha),
    }


def authority(
    value: object,
    manifest: dict[str, object],
    manifest_sha: str,
    package_sha: str,
    now: float,
    *,
    cleanup: bool = False,
    starting: bool = False,
) -> dict[str, object]:
    row = c.obj(value)
    fields = set(binding(manifest, manifest_sha, package_sha)) | {
        "kind",
        "authorized",
        "approved_by",
        "approval_nonce",
        "arm",
        "server_pid",
        "server_birth",
        "server_context",
        "start",
        "latest_start",
        "end",
        "owner_handback_sha256",
        "richard_approval_sha256",
        "private_config_sha256",
        "oob_interface",
        "oob_server_ipv4",
        "oob_client_ipv4",
        "manual_recovery_confirmed",
        "limits",
        "privileged_write_set",
    }
    if (
        set(row) != fields
        or row.get("kind") != "KAYLIN-OK-BENCH-REDUCED-v1"
        or row.get("authorized") is not True
        or row.get("approved_by") != "Kaylin"
        or row.get("arm") != "BASE"
        or row.get("manual_recovery_confirmed") is not True
        or row.get("limits") != LIMITS
        or row.get("privileged_write_set") != WRITE_SET
        or any(row.get(k) != v for k, v in binding(manifest, manifest_sha, package_sha).items())
    ):
        raise c.Refused("bench_exact_kaylin_binding_required")
    c.text(row["approval_nonce"], r"[0-9a-f]{32}")
    if row["approval_nonce"] == manifest["registration_nonce"]:
        raise c.Refused("bench_fresh_approval_nonce_required")
    for key in ("owner_handback_sha256", "richard_approval_sha256", "private_config_sha256"):
        sha(row[key])
    if type(row["server_pid"]) is not int or c.number(row["server_pid"], 2, 2**31) < 2:
        raise c.Refused("bench_actual_server_pid_required")
    c.text(row["server_birth"], r"[A-Za-z0-9 :+-]{15,80}")
    if type(row["server_context"]) is not int or not 4096 <= row["server_context"] <= 8192:
        raise c.Refused("bench_approved_context_required")
    if row["oob_interface"] != c.OOB_INTERFACE or row["oob_server_ipv4"] != c.OOB_SERVER_IPV4:
        raise c.Refused("bench_pinned_oob_required")
    try:
        client = ipaddress.IPv4Address(str(row["oob_client_ipv4"]))
    except ValueError as exc:
        raise c.Refused("bench_oob_client_invalid") from exc
    if client.is_unspecified or client.is_loopback or client.is_multicast:
        raise c.Refused("bench_oob_client_invalid")
    start, latest, end = (timestamp(row[k]) for k in ("start", "latest_start", "end"))
    c.number(now, 0, 4_000_000_000)
    if end - start != 1800 or not start <= latest <= start + 150:
        raise c.Refused("bench_exact_thirty_minute_window_required")
    if not cleanup and not start <= now < end:
        raise c.Refused("bench_outside_window")
    if starting and not start <= now <= latest:
        raise c.Refused("bench_outside_launch_window")
    return row


def approvals(
    window: dict[str, object], owner: object, richard: object, now: float, *, cleanup: bool = False
) -> None:
    for raw, key in ((owner, "owner_handback_sha256"), (richard, "richard_approval_sha256")):
        if not isinstance(raw, str) or len(raw.encode()) > 16384:
            raise c.Refused("bench_exact_approval_document_required")
        if hashlib.sha256(raw.encode()).hexdigest() != window[key]:
            raise c.Refused("bench_approval_document_changed")
    owner_row, root_row = c.parse_json(str(owner)), c.parse_json(str(richard))
    keys = (
        "run_id",
        "registration_nonce",
        "manifest_sha256",
        "package_sha256",
        "profile_sha256",
        "prompt_sha256",
        "model_sha256",
    )
    if (
        set(owner_row)
        != {*keys, "kind", "approved_by", "released", "released_at", "server_pid", "server_birth"}
        or owner_row.get("kind") != "BENCH-REDUCED-OWNER-v1"
        or owner_row.get("approved_by") != "Kaylin"
        or owner_row.get("released") is not True
        or any(owner_row.get(k) != window[k] for k in (*keys, "server_pid", "server_birth"))
    ):
        raise c.Refused("bench_fresh_owner_handback_required")
    released = timestamp(owner_row["released_at"])
    if not cleanup and not 0 <= now - released <= 1800:
        raise c.Refused("bench_stale_owner_handback")
    if (
        set(root_row)
        != {
            *keys,
            "kind",
            "approved_by",
            "authorized",
            "privileged_write_set",
            "limits",
            "start",
            "end",
            "approval_nonce",
            "window_binding_sha256",
        }
        or root_row.get("kind") != "RICHARD-OK-BENCH-REDUCED-v1"
        or root_row.get("approved_by") != "Richard"
        or root_row.get("authorized") is not True
        or root_row.get("window_binding_sha256")
        != c.digest({k: v for k, v in window.items() if k != "richard_approval_sha256"})
        or any(
            root_row.get(k) != window[k]
            for k in (*keys, "privileged_write_set", "limits", "start", "end", "approval_nonce")
        )
    ):
        raise c.Refused("bench_exact_richard_authorization_required")
