"""Pins for the explicitly approved device, exact restoration and fleet handback."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import Action, Scope, Tool, parse_action
from offline_agent.uplink.grammar import gbnf
from offline_agent.uplink.real_fleet import (
    APPROVED_FAULTS,
    FLEET_GETTERS,
    approval,
    approved_serial,
    load_clearance,
    require_exception,
    validate_authorization,
)
from offline_agent.uplink.real_journal import append_trial, wait_for_trial_slot
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_transport import LabBackend
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import read_trace, validate_record


def authorization() -> dict[str, Json]:
    return cast(dict[str, Json], {**approval(), "clearance_sha256": "a" * 64})


@pytest.mark.parametrize("fault", APPROVED_FAULTS)
def test_serial_and_only_dns_proxy_faults_are_approved(fault: str) -> None:
    assert approved_serial() == "LABTEST0001"
    assert (
        require_exception(approved_serial(), fault, authorization())["serial"] == approved_serial()
    )


@pytest.mark.parametrize(
    "serial,fault,proof",
    (
        ("synthetic-other-device", "wrong_dns", True),
        ("LABTEST0001", "wifi_power_off", True),
        ("LABTEST0001", "wrong_dns", False),
    ),
)
def test_managed_guard_remains_closed_outside_the_exception(
    serial: str, fault: str, proof: bool
) -> None:
    with pytest.raises(ValueError, match="managed_device_refused"):
        require_exception(serial, fault, authorization() if proof else None)


def test_clearance_must_be_present_scoped_and_not_a_symlink(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fleet_clearance_required"):
        load_clearance(tmp_path)
    path = tmp_path / "FLEET-CLEARANCE.local.md"
    content = "Fleet OK received 2026-10-27 for primary Wi-Fi DNS/proxy tests.\n"
    path.write_text(content)
    assert (
        load_clearance(tmp_path)["clearance_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    )
    path.write_text("HOLD; no clearance")
    with pytest.raises(ValueError, match="scope_unrecognized"):
        load_clearance(tmp_path)
    path.unlink()
    other = tmp_path / "other"
    other.write_text(content)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="fleet_clearance_required"):
        load_clearance(tmp_path)


def test_lab_profile_exposes_snapshot_dns_proxy_only() -> None:
    scope = Scope(("service-a",), (), ("snapshot-a",), ("dns-1",), "lab_dns_proxy_snapshot")
    common: dict[str, Json] = {
        "service_id": "service-a",
        "snapshot_id": "snapshot-a",
        "why": "snapshot_drift",
        "evidence_ids": ["dns-1"],
    }
    valid = Action(Tool.SET_DNS, {**common, "policy": "snapshot"})
    parse_action(json.dumps(valid.as_json()), scope)
    for action in (
        Action(Tool.CYCLE_INTERFACE, common),
        Action(Tool.SET_DNS, {**common, "policy": "google"}),
    ):
        with pytest.raises(ContractError):
            parse_action(json.dumps(action.as_json()), scope)
    assert "cycle_interface" not in gbnf(scope)
    assert "set_service_order" not in gbnf(scope)


def test_fleet_settings_compare_every_getter_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import real_worker

    saved = {name: "saved output\n" for name in FLEET_GETTERS}
    monkeypatch.setattr(real_worker, "read", lambda _: {"settings": saved})
    monkeypatch.setattr(real_worker, "fleet_settings", lambda: dict(saved))
    assert real_worker.fleet_check(tmp_path)["match"] is True
    changed = {**saved, "-getsearchdomains": "changed output\n"}
    monkeypatch.setattr(real_worker, "fleet_settings", lambda: changed)
    result = real_worker.fleet_check(tmp_path)
    assert result["match"] is False
    fields = result["fields"]
    assert isinstance(fields, dict) and fields["-getsearchdomains"] is False
    monkeypatch.setattr(real_worker, "read", lambda _: {"settings": {}})
    with pytest.raises(ValueError, match="complete_fleet"):
        real_worker.fleet_check(tmp_path)


def test_diff_failure_is_terminal_before_any_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import real_worker

    events: list[str] = []
    monkeypatch.setattr(real_worker, "audit", lambda *args: events.append("audit"))
    monkeypatch.setattr(real_worker, "private", lambda *args: events.append("persist"))
    state: dict[str, object] = {"lifecycle": "injected", "watchdog": "armed", "ready": True}
    with pytest.raises(ValueError, match="restore_failed_no_retry"):
        real_worker.stop_run(tmp_path, state, "fleet_settings_diff_mismatch")
    assert events == ["audit", "persist"]
    assert state["lifecycle"] == "rollback_failed" and state["ready"] is False
    native = Mock(side_effect=AssertionError("no second restore"))
    monkeypatch.setattr(real_worker, "native", native)
    with pytest.raises(ValueError, match="already_attempted_no_retry"):
        real_worker.restore(tmp_path, state, "retry")
    native.assert_not_called()


@pytest.mark.parametrize("finished", (950.0, 1001.0, None, float("nan")))
def test_root_prepare_refuses_early_or_unproven_trial_spacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finished: float | None
) -> None:
    from offline_agent.uplink import real_worker

    (tmp_path / "active.json").write_text("{}")
    monkeypatch.setattr(real_worker, "BASE", tmp_path)
    monkeypatch.setattr(real_worker, "steady_time", lambda: 1000.0)
    monkeypatch.setattr(real_worker, "boot_id", lambda: "a" * 64)

    def previous(path: Path) -> dict[str, object]:
        if path.name == "active.json":
            return {"run_id": "abcdef123456"}
        return {
            "lifecycle": "complete",
            "finished_at_mono": finished,
            "finished_clock_basis": real_worker.CLOCK_BASIS,
            "finished_boot_id": "a" * 64,
        }

    monkeypatch.setattr(real_worker, "read", previous)
    target = tmp_path / "new"
    with pytest.raises(ValueError, match="cooldown_active|timing_unproven"):
        real_worker.prepare(target, {"fault": "wrong_dns", "seconds": 40})
    assert not target.exists()


@pytest.mark.parametrize(
    "reply,allowed", (('{"wifi_ssh":true}', True), ('{"wifi_ssh":false}', False), ("", False))
)
def test_normal_wifi_ssh_requires_a_fresh_verified_connection(
    monkeypatch: pytest.MonkeyPatch, reply: str, allowed: bool
) -> None:
    backend = LabBackend(
        RealSpec("0123456789ab", "wrong_dns"), execute=True, authorization=authorization()
    )
    monkeypatch.setattr(backend, "oob", Mock())
    run = Mock(return_value=subprocess.CompletedProcess([], 0, reply, ""))
    monkeypatch.setattr("subprocess.run", run)
    assert backend.wifi_ssh("en0") is allowed
    command = run.call_args.args[0]
    assert "ControlPath=none" in command and "lab-wifi" in command
    run.reset_mock()
    with pytest.raises(ContractError, match="interface_refused"):
        backend.wifi_ssh("en7")
    run.assert_not_called()


def test_trace_records_approval_and_keeps_version_three_readable(tmp_path: Path) -> None:
    from offline_agent.uplink.privacy import Redactor

    with RunTrace(
        Redactor(b"x" * 32), directory=tmp_path, mode="real_eval", authorization=authorization()
    ) as trace:
        pass
    first = list(read_trace(trace.path))[0]
    assert first["trace_version"] == 4
    validate_authorization(first["authorization"])
    old = {**first, "trace_version": 3}
    old.pop("authorization")
    validate_record(old)


def receipt(run_id: str = "0123456789ab") -> dict[str, Json]:
    return {
        "run_id": run_id,
        "fault": "dns_and_proxy",
        "authorization": authorization(),
        "completed_at": 1000.0,
        "injection_state": "acknowledged",
        "recovered": True,
        "rollback_verified": True,
        "fleet_diff_match": True,
        "oob_ok": True,
        "wifi_ssh_back": True,
        "revert_seconds": 12.5,
    }


def test_trial_handback_is_idempotent_and_waits_two_minutes(tmp_path: Path) -> None:
    row = receipt()
    line = append_trial(tmp_path, row)
    assert append_trial(tmp_path, row) == line
    assert (tmp_path / "TRIAL-LOG.md").read_text() == line + "\n"
    assert "trial 1 | dns_and_proxy | PASS | diff match yes | revert time 12.500 s" in line
    clock = [1000.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    wait_for_trial_slot(tmp_path, clock=lambda: clock[0], sleep=sleep)
    assert sum(sleeps) == 120 and max(sleeps) <= 30
    with pytest.raises(ValueError, match="receipt_changed"):
        append_trial(tmp_path, {**row, "fleet_diff_match": False})


def test_empty_ledger_and_partial_handback_fail_safely(tmp_path: Path) -> None:
    sleep = Mock(side_effect=AssertionError("empty ledger must not wait"))
    wait_for_trial_slot(tmp_path, sleep=sleep)
    row = receipt()
    row.pop("oob_ok")
    with pytest.raises(ValueError, match="complete_trial_handback"):
        append_trial(tmp_path, row)
    assert not (tmp_path / "TRIAL-LOG.md").exists()


def refusal(run_id: str = "111111111111", **updates: Json) -> dict[str, Json]:
    return {
        **receipt(run_id),
        "injection_state": "not_requested",
        "recovered": False,
        "failure": "trial_cooldown_active",
        "fleet_diff_match": False,
        "rollback_verified": False,
        "wifi_ssh_back": False,
        "revert_seconds": None,
        **updates,
    }


def test_pre_fault_refusal_is_idempotent_and_consumes_no_trial_number(tmp_path: Path) -> None:
    row = refusal()
    line = append_trial(tmp_path, row)
    assert "refused_no_fault" in line and "diff match n/a (no change)" in line
    assert " CT trial " not in line and "PASS" not in line
    assert append_trial(tmp_path, row) == line
    assert (tmp_path / "TRIAL-LOG.md").read_text() == line + "\n"
    entry = json.loads(
        (tmp_path / "artifacts/uplink/fleet-ledger/111111111111-logged.json").read_text()
    )
    assert entry["number"] is None and entry["cooldown_anchor_at"] is None
    assert entry["kind"] == "refused_no_fault"
    assert " CT trial 1 |" in append_trial(tmp_path, receipt())
    with pytest.raises(ValueError, match="receipt_changed"):
        append_trial(tmp_path, {**row, "injection_state": "requested"})


@pytest.mark.parametrize("state", ("requested", "acknowledged"))
def test_requested_or_acknowledged_injection_stays_a_numbered_attempt(
    tmp_path: Path, state: str
) -> None:
    row = refusal(injection_state=state)
    line = append_trial(tmp_path, row)
    assert " CT trial 1 |" in line and "refused_no_fault" not in line
    assert "diff match no" in line


@pytest.mark.parametrize(
    "ambiguous",
    [
        {"deadline": 1100.0},
        {"controller_exit_code": -9, "controller_killed": True},
        {"hard_stop": True},
        {"final_state": {"watchdog": "armed"}},
        {"final_state": {"injected_at": 900.0}},
    ],
)
def test_legacy_missing_injection_acknowledgement_never_hides_an_armed_attempt(
    tmp_path: Path, ambiguous: dict[str, Json]
) -> None:
    row = refusal(
        network_fault_injected=False,
        controller_exit_code=0,
        controller_killed=False,
        hard_stop=False,
    )
    row.pop("injection_state")
    row.update(ambiguous)
    line = append_trial(tmp_path, row)
    assert " CT trial 1 |" in line and "refused_no_fault" not in line


def test_legacy_unarmed_controller_refusal_is_not_a_fault_trial(tmp_path: Path) -> None:
    row = refusal(
        network_fault_injected=False,
        controller_exit_code=0,
        controller_killed=False,
        hard_stop=False,
    )
    row.pop("injection_state")
    assert "refused_no_fault" in append_trial(tmp_path, row)


def test_missing_injection_evidence_is_an_attempt_not_an_inferred_no_change(tmp_path: Path) -> None:
    row = refusal()
    row.pop("injection_state")
    assert " CT trial 1 |" in append_trial(tmp_path, row)


@pytest.mark.parametrize(
    "updates",
    [
        {"injection_state": "unknown"},
        {"injection_state": []},
        {"network_fault_injected": "false"},
        {"final_state": []},
        {"network_fault_injected": True},
        {"recovered": True},
        {"passed": True},
        {"completed_at": float("nan")},
    ],
)
def test_invalid_or_contradictory_no_fault_receipt_is_rejected(
    tmp_path: Path, updates: dict[str, Json]
) -> None:
    with pytest.raises(ValueError):
        append_trial(tmp_path, {**refusal(), **updates})
    assert not (tmp_path / "TRIAL-LOG.md").exists()


def test_quiet_interval_uses_last_verified_restore_and_ssh_not_later_refusals(
    tmp_path: Path,
) -> None:
    append_trial(tmp_path, receipt())
    append_trial(tmp_path, refusal(completed_at=1110.0))
    append_trial(
        tmp_path,
        refusal("222222222222", completed_at=1115.0, injection_state="requested"),
    )
    clock = [1100.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    wait_for_trial_slot(tmp_path, clock=lambda: clock[0], sleep=sleep)
    assert sum(sleeps) == 20.0
    # Waiting is only a quiet-interval gate. Actual unresolved attempts remain
    # stopped by the root run lifecycle and the controller's no-retry policy.


def test_unverified_wifi_return_cannot_start_a_new_quiet_interval(tmp_path: Path) -> None:
    append_trial(tmp_path, {**receipt(), "wifi_ssh_back": False})
    sleep = Mock(side_effect=AssertionError("unverified handback is not a timing anchor"))
    wait_for_trial_slot(tmp_path, clock=lambda: 1000.0, sleep=sleep)


def make_legacy_entry(root: Path, row: dict[str, Json]) -> tuple[Path, Path]:
    append_trial(root, row)
    directory = root / "artifacts/uplink/fleet-ledger"
    intent = directory / f"{row['run_id']}-intent.json"
    logged = directory / f"{row['run_id']}-logged.json"
    entry = json.loads(logged.read_text())
    for key in ("version", "kind", "cooldown_anchor_at"):
        entry.pop(key)
    label = "PASS" if row.get("recovered") is True else str(row.get("failure") or "FAILED")
    original = {
        "fault": row["fault"],
        "outcome": label,
        "duration": row["revert_seconds"],
        **{key: row[key] for key in ("fleet_diff_match", "oob_ok", "wifi_ssh_back")},
    }
    entry["receipt_sha256"] = hashlib.sha256(
        json.dumps(original, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    content = json.dumps(entry, indent=2) + "\n"
    intent.write_text(content)
    logged.write_text(content)
    return intent, logged


def test_legacy_verified_handback_drives_cooldown_without_rewriting_old_rows(
    tmp_path: Path,
) -> None:
    intent, logged = make_legacy_entry(tmp_path, receipt())
    # An old pre-fault refusal remains a numbered legacy row, but its false diff
    # and SSH fields cannot move the verified-restoration anchor.
    old_refusal = refusal(completed_at=1110.0, injection_state="requested")
    other_intent, other_logged = make_legacy_entry(tmp_path, old_refusal)
    paths = (intent, logged, other_intent, other_logged, tmp_path / "TRIAL-LOG.md")
    originals = {path: path.read_bytes() for path in paths}
    clock = [1100.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    wait_for_trial_slot(tmp_path, clock=lambda: clock[0], sleep=sleep)
    assert sum(sleeps) == 20.0
    append_trial(tmp_path, receipt())
    append_trial(tmp_path, {**old_refusal, "injection_state": "not_requested"})
    assert all(path.read_bytes() == originals[path] for path in paths)


@pytest.mark.parametrize(
    "corruption",
    ({"line": "diff match yes | Wi-Fi ssh back yes"}, {"version": 99}, {"number": True}),
)
def test_malformed_legacy_or_new_ledger_is_refused(
    tmp_path: Path, corruption: dict[str, Json]
) -> None:
    _, logged = make_legacy_entry(tmp_path, receipt())
    entry = json.loads(logged.read_text())
    entry.update(corruption)
    logged.write_text(json.dumps(entry))
    with pytest.raises(ValueError):
        wait_for_trial_slot(tmp_path, clock=lambda: 1000.0, sleep=Mock())
