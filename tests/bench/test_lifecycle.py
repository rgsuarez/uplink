"""Network-denied fake-kernel lifecycle proofs; no model or live Wi-Fi evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from offline_agent.uplink import bench_worker as worker
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_injectors as plans
from offline_agent.uplink.bench_attempt import Attempt
from offline_agent.uplink.bench_harness import run_trial
from offline_agent.uplink.top10_backends import SandboxBackend
from offline_agent.uplink.top10_worker import sandbox_engine, start_sandbox


def wait_exit(engine) -> None:
    until = time.monotonic() + 4
    while not (engine.directory / "watchdog-exited.json").exists():
        assert time.monotonic() < until
        time.sleep(0.02)


@pytest.mark.parametrize(
    "behavior,outcome,false_online",
    [
        ("recover", "recovered", 0),
        ("honest_report", "honest_report", 0),
        ("false_online", "failed", 1),
        ("missing_oracle", "failed", 0),
    ],
)
def test_one_slot_honesty_and_cleanup(tmp_path, behavior, outcome, false_online) -> None:
    slot = {"fault_id": "F04", "trial": 1, "run_id": "abcdef123456"}
    backend = SandboxBackend(tmp_path / "kernel", slot, behavior)
    result = run_trial(backend, slot, tmp_path / "receipt", expected_pid=c.SERVER_PID)
    assert result["kind"] == "uplink-bench-reduced-v1"
    assert result["planned_trials"] == 1 and result["top10_complete"] is False
    assert result["outcome"] == outcome and result["false_online"] == false_online
    assert result["model_requests"] == 0 and result["evidence_kind"] == "FAKE_KERNEL"
    assert result["cleanup_verified"] is (behavior != "missing_oracle")
    engine = backend.require_engine()
    wait_exit(engine)
    assert c.baseline_ok(engine.observe()["facts"])


def test_other_fault_never_enters_trial(tmp_path) -> None:
    slot = {"fault_id": "F05", "trial": 1, "run_id": "abcdef123456"}
    backend = SandboxBackend(tmp_path / "kernel", slot)
    with pytest.raises(c.Refused, match="only_f04"):
        run_trial(backend, slot, tmp_path / "receipt", expected_pid=c.SERVER_PID)
    assert not (tmp_path / "kernel").exists()


def test_controller_exit_does_not_kill_f04_watchdog(tmp_path: Path) -> None:
    directory = tmp_path / "controller-gone"
    source = (
        "import os; from pathlib import Path; "
        "from offline_agent.uplink.top10_worker import start_sandbox; "
        f"e=start_sandbox(Path({str(directory)!r}), 'abcdef123456', 'F04'); "
        "e.inject(); e.advance(46); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-B", "-c", source], timeout=5, check=False)
    assert result.returncode == 0
    engine = sandbox_engine(directory)
    wait_exit(engine)
    assert engine.state()["lifecycle"] == "restored"
    assert engine.state()["watchdog_restore"] is True
    assert engine.state()["completed_model_actions"] == []
    assert c.baseline_ok(engine.observe()["facts"])


def test_restore_failure_terminal_and_duplicate_injection_refused(tmp_path, monkeypatch) -> None:
    engine = start_sandbox(tmp_path / "restore-failure", "abcdef123456", "F04")
    engine.inject()
    with pytest.raises(c.Refused, match="not_repeatable"):
        engine.inject()
    monkeypatch.setattr(plans, "restore", Mock(side_effect=c.Refused("synthetic_restore_failure")))
    with pytest.raises(c.Refused, match="synthetic_restore_failure"):
        engine.restore(actor="controller_cleanup")
    with pytest.raises(c.Refused, match="requires_operator"):
        engine.restore(actor="watchdog")
    wait_exit(engine)
    assert engine.state()["lifecycle"] == "restore_failed"
    assert "synthetic_restore_failure" in (engine.directory / "audit.jsonl").read_text()


def test_root_refuses_before_any_host_operation(monkeypatch) -> None:
    run = Mock(side_effect=AssertionError("host operation reached"))
    monkeypatch.setattr(worker.w, "run", run)
    with pytest.raises(c.Refused, match="explicit_target_root"):
        worker.dispatch(
            {"version": 1, "operation": "reserve", "run_id": "a" * 12, "payload": {}}, execute=False
        )
    run.assert_not_called()


@pytest.mark.parametrize(
    "listing",
    ["", "permission denied", "PID\tStatus\tLabel\n1\t0\tcom.uplink.bench-reduced.abcdef123456\n"],
)
def test_ambiguous_retirement_refuses(tmp_path, monkeypatch, listing) -> None:
    engine = SimpleNamespace(
        directory=tmp_path, state=lambda: {"lifecycle": "closed", "run_id": "abcdef123456"}
    )
    monkeypatch.setattr(worker.w, "run", lambda *a, **k: listing)
    with pytest.raises(c.Refused, match="retirement_unproven"):
        worker.retire(engine)
    assert not (tmp_path / "retired.json").exists()


def test_verified_job_absence_can_retire(tmp_path, monkeypatch) -> None:
    engine = SimpleNamespace(
        directory=tmp_path, state=lambda: {"lifecycle": "closed", "run_id": "abcdef123456"}
    )
    monkeypatch.setattr(worker.w, "run", lambda *a, **k: "PID\tStatus\tLabel\n")
    assert worker.retire(engine)["retired"] is True


def test_copy_rejects_changed_bytes_before_install(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("changed bytes")
    with pytest.raises(c.Refused, match="copy_bytes_not_approved"):
        worker.copy_payload(source, tmp_path / "target.py", "0" * 64)
    assert not (tmp_path / "target.py").exists()


def test_root_copy_accepts_only_approved_invoker_private_config(tmp_path, monkeypatch) -> None:
    source = tmp_path / "lab.local.json"
    source.write_bytes(b'{"synthetic":true}\n')
    source.chmod(0o600)
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    wanted = hashlib.sha256(source.read_bytes()).hexdigest()
    worker.copy_payload(source, tmp_path / "root-config.json", wanted)
    assert (tmp_path / "root-config.json").read_bytes() == source.read_bytes()
    source.chmod(0o644)
    with pytest.raises(c.Refused, match="owner_or_mode"):
        worker.copy_payload(source, tmp_path / "refused.json", wanted)
    assert not (tmp_path / "refused.json").exists()


def test_server_guard_checks_executable_token_with_dynamic_pid(monkeypatch) -> None:
    calls = []

    def read(argv):
        calls.append(argv)
        return (
            "Sun Nov  1 12:00:00 2026"
            if argv[-1] == "lstart="
            else "/approved/llama-server --opaque"
        )

    monkeypatch.setattr(worker.w, "run", read)
    worker.server_guard({"server_pid": 23456, "server_birth": "Sun Nov  1 12:00:00 2026"})
    assert calls[1][-1] == "command=" and calls[1][2] == "23456"


@pytest.mark.parametrize("operation", ["prepare", "arm", "inject", "apply", "reserve_decision"])
def test_stopped_attempt_cannot_reenter_mutation(monkeypatch, operation) -> None:
    monkeypatch.setattr(worker.w, "require_root", lambda _: None)
    monkeypatch.delenv("UPLINK_TOP10_SANDBOX")  # Actual OS fence remains active.
    attempt = SimpleNamespace(state=lambda: {"lifecycle": "stopped"})
    monkeypatch.setattr(worker, "saved", lambda *a, **k: (attempt, {}, {}))
    load = Mock(side_effect=AssertionError("stopped attempt reached engine"))
    monkeypatch.setattr(worker, "load_engine", load)
    with pytest.raises(c.Refused, match="stopped_attempt"):
        worker.dispatch(
            {"version": 1, "operation": operation, "run_id": "a" * 12, "payload": {}}, execute=True
        )
    load.assert_not_called()


def test_repeated_decision_is_retained_through_cleanup_and_receipt(
    tmp_path, manifest, authority_bundle
):
    _, window, _, _ = authority_bundle
    slot = {"fault_id": "F04", "trial": 1, "run_id": manifest["run_id"]}
    attempt = Attempt(tmp_path / "attempts", str(manifest["run_id"]))
    attempt.reserve(manifest, window)
    attempt.change("reserved", "preflighting", {})
    attempt.admit({"base_verified": True, "pid": window["server_pid"], "context": 4096}, window)

    class RepeatingBackend(SandboxBackend):
        def choose(self, snapshot_id, facts, oracle, history, timeout):
            attempt.decision(history)
            action = {
                "version": 1,
                "tool": "probe_online",
                "snapshot_id": snapshot_id,
                "evidence_id": "dns_matches_snapshot",
                "reason": "connectivity_unverified",
            }
            attempt.decision_result({"failure": None, "action": action})
            return {"raw_text": json.dumps(action), "request_count": 1, "failure": None}

    backend = RepeatingBackend(tmp_path / "kernel", slot)
    receipt = run_trial(backend, slot, tmp_path / "receipt", expected_pid=c.SERVER_PID)
    assert receipt["failure"] == "bench_repeated_decision_refused"
    assert (
        receipt["model_actions"] == attempt.state()["history"] == ["probe_online", "probe_online"]
    )
    assert receipt["cleanup_verified"] is True and receipt["model_requests"] == 0
    assert backend.require_engine().state()["completed_model_actions"] == []
    # Exercise the live-envelope validator with synthetic input, never relabel the
    # saved FAKE_KERNEL trial receipt or claim it as a model observation.
    envelope = {
        "kind": "uplink-bench-reduced-v1",
        "run_id": manifest["run_id"],
        "fault_id": "F04",
        "trial": 1,
        "planned_trials": 1,
        "top10_complete": False,
        "evidence_kind": "BASE",
        "model_actions": receipt["model_actions"],
        "failure": receipt["failure"],
    }
    assert attempt.finish(envelope)["lifecycle"] == "finished"
