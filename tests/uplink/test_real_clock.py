"""Cross-process root timers cannot inherit Python 3.9's process-local clock origin."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.uplink import real_worker

BOOT = "a" * 64


@pytest.mark.parametrize("legacy_origin", (0.02, 900000.0))
def test_root_time_uses_the_shared_clock_not_process_relative_monotonic(
    monkeypatch: pytest.MonkeyPatch, legacy_origin: float
) -> None:
    monkeypatch.setattr("time.monotonic", lambda: legacy_origin)
    clock = Mock(return_value=123456.0)
    monkeypatch.setattr("time.clock_gettime", clock)
    assert real_worker.steady_time() == 123456.0
    assert clock.call_count == 1


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1.0))
def test_shared_clock_rejects_nonfinite_and_negative_values(
    monkeypatch: pytest.MonkeyPatch, value: float
) -> None:
    monkeypatch.setattr("time.clock_gettime", lambda _: value)
    with pytest.raises(ValueError, match="clock_invalid"):
        real_worker.steady_time()


def clock_state() -> dict[str, object]:
    return {
        "clock_basis": real_worker.CLOCK_BASIS,
        "boot_id": BOOT,
        "armed_at_mono": 1000.0,
        "deadline_mono": 1040.0,
        "watchdog": "armed",
        "ready": True,
        "lifecycle": "injected",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"clock_basis": None},
        {"boot_id": "b" * 64},
        {"deadline_mono": float("nan")},
        {"armed_at_mono": 1011.0},
        {"deadline_mono": 1061.0},
        {"deadline_mono": None},
    ],
)
def test_unproven_clock_metadata_never_authorizes_a_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    monkeypatch.setattr(real_worker, "boot_id", lambda: BOOT)
    monkeypatch.setattr(real_worker, "steady_time", lambda: 1010.0)
    command = Mock(side_effect=AssertionError("no launchd or native command"))
    monkeypatch.setattr(real_worker, "run", command)
    with pytest.raises(ValueError, match="clock_unproven"):
        real_worker.require_armed(tmp_path, {**clock_state(), **change})
    command.assert_not_called()


@pytest.mark.parametrize("elapsed,allowed", ((119.999, False), (120.0, True)))
def test_cooldown_boundary_uses_shared_clock_even_when_wall_time_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed: float, allowed: bool
) -> None:
    monkeypatch.setattr(real_worker, "boot_id", lambda: BOOT)
    monkeypatch.setattr(real_worker, "steady_time", lambda: 1000.0 + elapsed)
    monkeypatch.setattr("time.time", lambda: 999999999.0)
    state = {
        "lifecycle": "complete",
        "finished_clock_basis": real_worker.CLOCK_BASIS,
        "finished_boot_id": BOOT,
        "finished_at_mono": 1000.0,
    }
    if allowed:
        real_worker.require_cooldown(tmp_path, state)
    else:
        with pytest.raises(ValueError, match="cooldown_active"):
            real_worker.require_cooldown(tmp_path, state)


def test_legacy_terminal_run_gets_one_full_barrier_without_rewriting_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(real_worker, "boot_id", lambda: BOOT)
    monkeypatch.setattr(real_worker, "steady_time", lambda: now[0])
    monkeypatch.setattr(real_worker, "read", lambda p: json.loads(p.read_text()))
    audit = Mock()
    monkeypatch.setattr(real_worker, "audit", audit)
    state = {"lifecycle": "reverted", "finished_at_mono": 41.3, "fleet_check": {"match": True}}
    original = json.dumps(state)
    with pytest.raises(ValueError, match="restarted_after_clock_upgrade"):
        real_worker.require_cooldown(tmp_path, state)
    barrier = (tmp_path / "cooldown-clock.json").read_bytes()
    now[0] = 1119.999
    with pytest.raises(ValueError, match="cooldown_active"):
        real_worker.require_cooldown(tmp_path, state)
    assert (tmp_path / "cooldown-clock.json").read_bytes() == barrier
    assert audit.call_count == 1 and json.dumps(state) == original
    now[0] = 1120.0
    real_worker.require_cooldown(tmp_path, state)
    assert audit.call_count == 1


@pytest.mark.parametrize("lifecycle", ("injected", "rollback_started", "rollback_failed"))
def test_clock_upgrade_cannot_bypass_an_unresolved_run(tmp_path: Path, lifecycle: str) -> None:
    with pytest.raises(ValueError, match="unresolved"):
        real_worker.require_cooldown(
            tmp_path, {"lifecycle": lifecycle, "fleet_check": {"match": True}}
        )
    assert not (tmp_path / "cooldown-clock.json").exists()


@pytest.mark.parametrize("unproven", (False, True))
def test_restarted_watchdog_restores_before_marking_ready_when_expired_or_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unproven: bool
) -> None:
    monkeypatch.setattr(real_worker, "BASE", tmp_path)
    (tmp_path / "lock").touch()
    monkeypatch.setattr(real_worker, "require_lab", lambda _: None)
    monkeypatch.setattr(real_worker, "secure_directory", lambda _: None)
    monkeypatch.setattr(real_worker, "boot_id", lambda: BOOT)
    monkeypatch.setattr(real_worker, "steady_time", lambda: 1041.0)
    state = clock_state()
    state["ready"] = False
    if unproven:
        state.pop("clock_basis")
    monkeypatch.setattr(real_worker, "read", lambda _: state)
    restore = Mock(return_value={"baseline_equal": True})
    monkeypatch.setattr(real_worker, "restore", restore)
    audit = Mock()
    monkeypatch.setattr(real_worker, "audit", audit)
    real_worker.watchdog("0123456789ab")
    assert restore.call_count == 1 and state["ready"] is False
    assert restore.call_args.args[-1] == (
        "watchdog_clock_unproven" if unproven else "deadline_expired"
    )
    audit.assert_not_called()


def test_restore_remains_possible_when_shared_clock_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = {"wifi_up": True, "dns_sha256": "good", "proxies_sha256": "good"}
    current = {**snapshot, "dns_sha256": "bad"}
    calls: list[str] = []
    monkeypatch.setattr(real_worker, "read", lambda _: snapshot)
    monkeypatch.setattr(
        real_worker, "steady_time", Mock(side_effect=ValueError("clock_unavailable"))
    )
    monkeypatch.setattr(real_worker, "power", lambda _: True)
    monkeypatch.setattr(real_worker, "fleet_check", lambda _: {"match": True})
    monkeypatch.setattr(real_worker, "observe", lambda *_: {"baseline_equal": True})

    def native(directory: Path, operation: str = "read") -> dict[str, object]:
        calls.append(operation)
        if operation == "restore":
            current.update(snapshot)
        return current

    monkeypatch.setattr(real_worker, "native", native)
    state = {**clock_state(), "injected_at_mono": 1001.0}
    real_worker.restore(tmp_path, state, "clock_unproven")
    assert calls == ["read", "restore"] and state["lifecycle"] == "reverted"
    assert state["timing_unproven"] is True and state["baseline_restored_after_s"] is None
    with pytest.raises(ValueError, match="already_attempted"):
        real_worker.restore(tmp_path, state, "again")
