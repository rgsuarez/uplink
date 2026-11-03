"""Idempotent fleet handback lines and a quiet interval between physical trials."""

import fcntl
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from offline_agent.contracts import Json, object_json, read_json
from offline_agent.uplink.real_fleet import (
    APPROVED_FAULTS,
    TRIAL_INTERVAL_SECONDS,
    validate_authorization,
)
from offline_agent.uplink.snapshots import write_private_json


def timestamp(value: Json, reason: str) -> float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(cast(float, value))
        or cast(float, value) < 0
    ):
        raise ValueError(reason)
    return float(cast(float, value))


def fault_attempted(receipt: dict[str, Json]) -> bool:
    """Only positive pre-injection evidence permits a no-change handback."""
    state = receipt.get("injection_state")
    if "injection_state" in receipt and (
        not isinstance(state, str) or state not in {"not_requested", "requested", "acknowledged"}
    ):
        raise ValueError("invalid_trial_injection_state")
    injected = receipt.get("network_fault_injected")
    if "network_fault_injected" in receipt and type(injected) is not bool:
        raise ValueError("invalid_trial_injection_evidence")
    final = receipt.get("final_state", {})
    if not isinstance(final, dict):
        raise ValueError("invalid_trial_final_state")
    watchdog = final.get("watchdog")
    if watchdog is not None and not isinstance(watchdog, str):
        raise ValueError("invalid_trial_final_state")
    if state == "not_requested":
        if injected is True or final.get("injected_at") is not None or receipt.get("changes"):
            raise ValueError("contradictory_trial_injection_evidence")
        return False
    if state in {"requested", "acknowledged"} or injected is True:
        return True
    # Old watchdog receipts have no injection_state. A false acknowledgement alone
    # cannot prove no change after an armed controller disappears.
    return not (
        injected is False
        and receipt.get("controller_exit_code") == 0
        and type(receipt.get("controller_exit_code")) is int
        and receipt.get("controller_killed") is False
        and receipt.get("hard_stop") is False
        and receipt.get("deadline") is None
        and final.get("injected_at") is None
        and watchdog not in {"armed", "reverted", "disarmed", "failed"}
        and not receipt.get("changes")
    )


def receipt_digest(fields: dict[str, Json]) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, allow_nan=False).encode()).hexdigest()


def ledger_anchor(entry: dict[str, Json]) -> float | None:
    """Read prior handbacks without rewriting their original classification or text."""
    common = {"run_id", "completed_at", "number", "line", "receipt_sha256"}
    version = entry.get("version")
    if version is None:
        expected = common
    elif type(version) is int and version == 2:
        expected = common | {"version", "kind", "cooldown_anchor_at"}
    else:
        raise ValueError("invalid_trial_ledger_version")
    if set(entry) != expected:
        raise ValueError("invalid_trial_ledger_fields")
    run_id, digest, line = entry.get("run_id"), entry.get("receipt_sha256"), entry.get("line")
    if (
        not isinstance(run_id, str)
        or not re.fullmatch(r"[0-9a-f]{12}", run_id)
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(line, str)
    ):
        raise ValueError("invalid_trial_ledger_entry")
    completed = timestamp(entry.get("completed_at"), "unproven_trial_cooldown")
    at = datetime.fromtimestamp(completed, ZoneInfo("America/Chicago"))
    prefix = re.escape(f"{at:%Y-%m-%d %H:%M:%S} CT ")
    fault = "(?:" + "|".join(APPROVED_FAULTS) + ")"
    label = r"[a-zA-Z0-9_.:-]{1,100}"
    if version == 2 and entry.get("kind") == "refused_no_fault":
        pattern = (
            prefix
            + "refusal "
            + run_id
            + r" \| "
            + fault
            + r" \| refused_no_fault \| diff match n/a \(no change\) \| revert time n/a s"
            + r" \| OOB ok (?:yes|no) \| Wi-Fi ssh back n/a \(no change\) \| reason "
            + label
        )
        if (
            entry.get("number") is not None
            or entry.get("cooldown_anchor_at") is not None
            or re.fullmatch(pattern, line) is None
        ):
            raise ValueError("invalid_no_fault_handback")
        return None
    number = entry.get("number")
    if type(number) is not int or number < 1:
        raise ValueError("invalid_trial_log_number")
    if version == 2 and entry.get("kind") != "fault_attempt":
        raise ValueError("invalid_trial_ledger_kind")
    pattern = (
        prefix
        + "trial "
        + str(number)
        + r" \| "
        + fault
        + r" \| "
        + label
        + r" \| diff match (?P<diff>yes|no) \| revert time (?:[0-9]+\.[0-9]{3}|unverified) s"
        + r" \| OOB ok (?P<oob>yes|no) \| Wi-Fi ssh back (?P<wifi>yes|no)"
    )
    match = re.fullmatch(pattern, line)
    if match is None:
        raise ValueError("invalid_trial_handback_line")
    restored = all(match.group(key) == "yes" for key in ("diff", "oob", "wifi"))
    if version is None:
        # Old rows only retain the canonical line and its completion time. Completion
        # after all three verified signals is conservative relative to the restore.
        return completed if restored else None
    anchor = entry.get("cooldown_anchor_at")
    if anchor is None:
        return None
    checked = timestamp(anchor, "unproven_trial_cooldown")
    if not restored or checked != completed:
        raise ValueError("unproven_trial_cooldown")
    return checked


def append_trial(root: Path, receipt: dict[str, Json], *, outcome: str | None = None) -> str:
    run_id, fault = receipt.get("run_id"), receipt.get("fault")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("trial_run_id_required")
    if fault not in APPROVED_FAULTS:
        raise ValueError("trial_fault_not_authorized")
    validate_authorization(receipt.get("authorization"))
    for key in ("fleet_diff_match", "oob_ok", "wifi_ssh_back"):
        if type(receipt.get(key)) is not bool:
            raise ValueError("complete_trial_handback_required")
    completed = timestamp(receipt.get("completed_at"), "trial_completion_time_required")
    duration = receipt.get("revert_seconds")
    if duration is not None and (
        type(duration) not in (int, float)
        or not math.isfinite(cast(float, duration))
        or cast(float, duration) < 0
    ):
        raise ValueError("invalid_revert_duration")
    attempted = fault_attempted(receipt)
    if not attempted and (receipt.get("recovered") is True or receipt.get("passed") is True):
        raise ValueError("no_fault_cannot_claim_recovery")
    kind = "fault_attempt" if attempted else "refused_no_fault"
    anchor = (
        completed
        if attempted
        and receipt.get("rollback_verified") is True
        and all(receipt[key] is True for key in ("fleet_diff_match", "oob_ok", "wifi_ssh_back"))
        else None
    )
    label = outcome or (
        "PASS" if receipt.get("recovered") is True else str(receipt.get("failure") or "FAILED")
    )
    label = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", label)[:100]
    fingerprint_fields: dict[str, Json] = {
        "fault": fault,
        "outcome": label,
        "duration": duration,
        **{key: receipt[key] for key in ("fleet_diff_match", "oob_ok", "wifi_ssh_back")},
    }
    fingerprint = receipt_digest({**fingerprint_fields, "kind": kind, "cooldown_anchor_at": anchor})
    directory = root / "artifacts/uplink/fleet-ledger"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "TRIAL-LOG.md"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as log:
            if os.fstat(log.fileno()).st_size > 524288:
                raise ValueError("trial_log_size_limit")
            log.seek(0)
            existing = log.read()
            if existing and not existing.endswith("\n"):
                raise ValueError("incomplete_trial_log_line_refused")
            intent = directory / f"{run_id}-intent.json"
            if intent.is_symlink():
                raise ValueError("trial_intent_symlink_refused")
            if intent.exists():
                entry = object_json(read_json(intent.read_text()))
                ledger_anchor(entry)
                if entry.get("run_id") != run_id or entry.get("completed_at") != completed:
                    raise ValueError("trial_log_intent_mismatch")
                expected_fingerprint = (
                    receipt_digest(fingerprint_fields) if "version" not in entry else fingerprint
                )
                if entry.get("receipt_sha256") != expected_fingerprint:
                    raise ValueError("trial_receipt_changed_after_log_intent")
                line = entry.get("line")
                if not isinstance(line, str):
                    raise ValueError("invalid_trial_log_intent")
            else:
                numbers = [
                    int(n)
                    for n in re.findall(
                        r"(?m)^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} CT trial (\d+) \|", existing
                    )
                ]
                if len(numbers) != len(set(numbers)):
                    raise ValueError("ambiguous_trial_number")
                number = max(numbers, default=0) + 1 if attempted else None
                at = datetime.fromtimestamp(completed, ZoneInfo("America/Chicago"))
                revert = f"{cast(float, duration):.3f}" if duration is not None else "unverified"

                def yes(key: str) -> str:
                    return "yes" if receipt[key] is True else "no"

                line = (
                    f"{at:%Y-%m-%d %H:%M:%S} CT trial {number} | {fault} | {label} | "
                    f"diff match {yes('fleet_diff_match')} | revert time {revert} s | "
                    f"OOB ok {yes('oob_ok')} | Wi-Fi ssh back {yes('wifi_ssh_back')}"
                    if attempted
                    else f"{at:%Y-%m-%d %H:%M:%S} CT refusal {run_id} | {fault} | "
                    "refused_no_fault | "
                    f"diff match n/a (no change) | revert time n/a s | OOB ok {yes('oob_ok')} | "
                    f"Wi-Fi ssh back n/a (no change) | reason {label}"
                )
                entry = {
                    "version": 2,
                    "kind": kind,
                    "cooldown_anchor_at": anchor,
                    "run_id": run_id,
                    "completed_at": completed,
                    "number": number,
                    "line": line,
                    "receipt_sha256": fingerprint,
                }
                ledger_anchor(entry)
                write_private_json(intent, entry)
            if line not in existing.splitlines():
                saved_number = entry.get("number")
                if saved_number is not None and re.search(rf"\btrial {saved_number} \|", existing):
                    raise ValueError("trial_number_already_used")
                log.write(line + "\n")
                log.flush()
                os.fsync(log.fileno())
            logged = directory / f"{run_id}-logged.json"
            if logged.is_symlink():
                raise ValueError("trial_ledger_symlink_refused")
            if logged.exists():
                if object_json(read_json(logged.read_text())) != entry:
                    raise ValueError("trial_logged_entry_mismatch")
            else:
                write_private_json(logged, entry)
            return line
    finally:
        os.close(lock)


def wait_for_trial_slot(
    root: Path,
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    completed = []
    for path in (root / "artifacts/uplink/fleet-ledger").glob("*-logged.json"):
        if path.is_symlink():
            raise ValueError("trial_ledger_symlink_refused")
        entry = object_json(read_json(path.read_text()))
        if entry.get("run_id") != path.name.removesuffix("-logged.json"):
            raise ValueError("trial_ledger_run_id_mismatch")
        value = ledger_anchor(entry)
        if value is not None:
            completed.append(value)
    if not completed:
        return
    deadline = max(completed) + TRIAL_INTERVAL_SECONDS
    while (remaining := deadline - clock()) > 0:
        sleep(min(30.0, remaining))
