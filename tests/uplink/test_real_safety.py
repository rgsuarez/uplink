"""Boundary pins for the DNS/proxy-only fleet exception. No live effects."""

import json
import time
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from offline_agent.contracts import Json
from offline_agent.uplink import (
    real_journal,
    real_preflight,
    real_runner,
    real_transport,
    real_worker,
)
from offline_agent.uplink.model_identity import MODEL_NAME, expected_model
from offline_agent.uplink.real_fleet import approval
from offline_agent.uplink.real_preflight import Checks
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_runner import OOBLost, recover
from offline_agent.uplink.real_simulator import SimulatedLab
from offline_agent.uplink.trace_format import read_trace


@pytest.fixture(autouse=True)
def fake_build_gates(monkeypatch: pytest.MonkeyPatch, fake_preflight_checks: Checks) -> None:
    monkeypatch.setattr(real_preflight, "LabChecks", lambda *_: fake_preflight_checks)


def authorization() -> dict[str, Json]:
    return cast(dict[str, Json], {**approval(), "clearance_sha256": "a" * 64})


class LiveFixture(SimulatedLab):
    """Exercise live controller gates against synthetic effects and provenance."""

    dry = False

    def __init__(self, spec: RealSpec) -> None:
        super().__init__(spec)
        self.authorization = authorization()

    def identity(self) -> dict[str, Json]:
        value = expected_model(chip="Apple M4 Max", provider="llama_cpp", active_name=MODEL_NAME)
        value.update(verification="server_verified", verified_at=time.time())
        return value


class LostBeforePrepare(LiveFixture):
    def oob(self) -> None:
        self.calls.append("oob")
        raise OOBLost("oob_lost_no_retry")


def test_post_trial_wifi_ssh_requires_its_own_success(tmp_path: Path) -> None:
    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = LiveFixture(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["recovered"] is True and row["wifi_ssh_back"] is True
    assert backend.calls.count("wifi_ssh") == 2


def test_oob_loss_cannot_reuse_baseline_wifi_ssh_success(tmp_path: Path) -> None:
    class LostAfterInject(LiveFixture):
        def oob(self) -> None:
            super().oob()
            if "inject" in self.calls:
                raise OOBLost("oob_lost_no_retry")

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = LostAfterInject(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["hard_stop"] is True and row["oob_ok"] is False
    assert row["wifi_ssh_back"] is False and row["recovered"] is False
    assert backend.calls.count("wifi_ssh") == 1 and "restore" not in backend.calls


def test_missing_post_trial_wifi_ssh_is_a_hard_stop(tmp_path: Path) -> None:
    class MissingAfterRestore(LiveFixture):
        def wifi_ssh(self, interface: str) -> bool:
            super().wifi_ssh(interface)
            return self.calls.count("wifi_ssh") == 1

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = MissingAfterRestore(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["hard_stop"] is True and row["recovered"] is False
    assert row["wifi_ssh_back"] is False and backend.calls.count("wifi_ssh") == 2
    assert row["failure"] == "normal_wifi_ssh_not_restored_no_retry"


def test_preflight_oob_loss_retains_authorization_and_can_be_journaled(tmp_path: Path) -> None:
    from offline_agent.uplink.privacy import Redactor

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = LostBeforePrepare(spec)
    row = recover(backend, spec, tmp_path / "result", confirm=lambda _: True)
    redactor = Redactor.local(tmp_path / "result")
    redactor.register_lab_identity()
    assert row["authorization"] == redactor.scrub_serials_json(authorization())
    assert backend.authorization == authorization()
    assert row["hard_stop"] is True and row["injection_state"] == "not_requested"
    line = real_journal.append_trial(tmp_path, {**row, "authorization": backend.authorization})
    assert "OOB ok no" in line and "Wi-Fi ssh back n/a (no change)" in line
    first = next(read_trace(tmp_path / "result/traces" / str(row["trace"])))
    assert first["authorization"] == row["authorization"]


@pytest.mark.parametrize("journal_failure", (False, True))
def test_cli_persists_abort_even_when_trial_journal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, journal_failure: bool
) -> None:
    monkeypatch.setattr(
        real_runner, "__file__", str(tmp_path / "src/offline_agent/uplink/real_runner.py")
    )
    monkeypatch.setattr(real_preflight, "load_clearance", lambda _: authorization())
    monkeypatch.setattr(real_journal, "wait_for_trial_slot", lambda _: None)
    monkeypatch.setattr(
        real_transport, "LabBackend", lambda spec, **kwargs: LostBeforePrepare(spec)
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "uplink-real",
            "--execute",
            "--confirm-live",
            "--confirm-restores",
            "--output",
            str(tmp_path / "result"),
        ],
    )
    if journal_failure:
        monkeypatch.setattr(
            real_journal, "append_trial", Mock(side_effect=OSError("synthetic_journal_failure"))
        )
        with pytest.raises(OSError, match="synthetic_journal_failure"):
            real_runner.main()
    else:
        with pytest.raises(SystemExit) as stopped:
            real_runner.main()
        assert stopped.value.code == 1
        assert "OOB ok no" in (tmp_path / "TRIAL-LOG.md").read_text()
    abort = (tmp_path / "ABORT-U1.md").read_text()
    assert "oob_lost_no_retry" in abort and "No retry." in abort
    rows = list((tmp_path / "result").glob("*-receipt.json"))
    assert len(rows) == 1
    assert json.loads(rows[0].read_text())["hard_stop"] is True


def test_approved_restore_never_changes_wifi_power(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot: dict[str, object] = {
        "serial": "synthetic",
        "service_id": "synthetic",
        "interface": "en0",
        "protected_sha256": "p",
        "location_sha256": "l",
        "service_order_sha256": "o",
        "dns_sha256": "good",
        "proxies_sha256": "good",
        "wifi_up": True,
    }
    current = {**snapshot, "dns_sha256": "bad"}
    native = Mock(return_value=current)
    set_power = Mock(side_effect=AssertionError("power mutation is not authorized"))
    monkeypatch.setattr(real_worker, "read", lambda _: snapshot)
    monkeypatch.setattr(real_worker, "native", native)
    monkeypatch.setattr(real_worker, "power", lambda _: False)
    monkeypatch.setattr(real_worker, "set_power", set_power)
    state: dict[str, object] = {
        "lifecycle": "injected",
        "watchdog": "armed",
        "authorization": authorization(),
    }
    with pytest.raises(ValueError, match="restore_failed_no_retry"):
        real_worker.restore(tmp_path, state, "test")
    native.assert_called_once_with(tmp_path)
    set_power.assert_not_called()
    assert state["lifecycle"] == "rollback_failed"
    assert state["reason"] == "wifi_power_drift_outside_approved_scope"
    with pytest.raises(ValueError, match="already_attempted_no_retry"):
        real_worker.restore(tmp_path, state, "retry")
    native.assert_called_once_with(tmp_path)


@pytest.mark.parametrize("duration", (None, -1.0, 60.001))
def test_recovery_cannot_pass_with_missing_or_late_restoration(
    tmp_path: Path, duration: float | None
) -> None:
    class UnprovenDeadline(SimulatedLab):
        def observation(self) -> dict[str, Json]:
            return {**super().observation(), "baseline_restored_after_s": duration}

    spec = RealSpec("0123456789ab", "wrong_dns")
    receipt = real_runner.recover(UnprovenDeadline(spec), spec, tmp_path, confirm=lambda _: True)
    assert receipt["recovered"] is False and receipt["hard_stop"] is True
    assert receipt["rollback_verified"] is True
    assert receipt["failure"] == "rollback_deadline_unproven_no_retry"
