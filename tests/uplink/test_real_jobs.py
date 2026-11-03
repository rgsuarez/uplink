"""Only verified terminal watchdog jobs may be retired, and arming requires one job."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.uplink import real_worker
from offline_agent.uplink.real_fleet import approval
from offline_agent.uplink.real_protocol import request


def finished() -> dict[str, object]:
    return {
        "lifecycle": "complete",
        "watchdog": "disarmed",
        "fault": "dns_and_proxy",
        "fleet_check": {"match": True},
        "authorization": {**approval(), "clearance_sha256": "a" * 64},
    }


def test_empty_registry_retirement_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(real_worker, "registered_watchdogs", lambda: ())
    run = Mock(side_effect=AssertionError("no effect for empty registry"))
    monkeypatch.setattr(real_worker, "run", run)
    assert real_worker.retire_finished_watchdogs() == {
        "job_retired": True,
        "retired_count": 0,
        "registered_jobs": 0,
    }
    assert request("retire", "0123456789ab")["payload"] == {}
    run.assert_not_called()


def test_verified_terminal_jobs_are_audited_unloaded_and_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ["0123456789ab", "abcdef012345"]
    remaining = list(names)
    states = {name: finished() for name in names}
    events: list[str] = []
    monkeypatch.setattr(real_worker, "BASE", tmp_path)
    monkeypatch.setattr(real_worker, "registered_watchdogs", lambda: tuple(remaining))
    monkeypatch.setattr(real_worker, "read", lambda p: states[p.parent.name])
    monkeypatch.setattr(real_worker, "audit", lambda *args: events.append("audit"))
    monkeypatch.setattr(real_worker, "private", lambda *args: events.append("persist"))

    def run(argv: tuple[str, ...], **kwargs: object) -> str:
        assert argv[:2] == ("/bin/launchctl", "bootout")
        events.append("bootout")
        remaining.remove(argv[-1].removeprefix("system/com.uplink.real."))
        return ""

    monkeypatch.setattr(real_worker, "run", run)
    result = real_worker.retire_finished_watchdogs()
    assert result["retired_count"] == 2 and result["registered_jobs"] == 0
    assert events == ["audit", "bootout", "persist", "audit"] * 2
    assert all(
        state["job_retired"] is True and state["lifecycle"] == "complete"
        for state in states.values()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"lifecycle": "injected"},
        {"lifecycle": "rollback_failed"},
        {"watchdog": "armed"},
        {"fleet_check": {"match": False}},
        {"authorization": None},
    ],
)
def test_whole_batch_is_refused_before_any_stop_for_unfinished_or_unowned_job(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    monkeypatch.setattr(
        real_worker, "registered_watchdogs", lambda: ("0123456789ab", "abcdef012345")
    )
    monkeypatch.setattr(
        real_worker,
        "read",
        lambda p: finished() if p.parent.name == "0123456789ab" else {**finished(), **change},
    )
    run = Mock(side_effect=AssertionError("no partial retirement"))
    monkeypatch.setattr(real_worker, "run", run)
    with pytest.raises(ValueError):
        real_worker.retire_finished_watchdogs()
    run.assert_not_called()


def test_arm_refuses_any_existing_job_before_creating_a_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(real_worker, "clock_matches", lambda _: True)
    monkeypatch.setattr(real_worker, "registered_watchdogs", lambda: ("0123456789ab",))
    with pytest.raises(ValueError, match="require_retirement"):
        real_worker.arm(tmp_path, {"lifecycle": "prepared", "seconds": 40})
    assert not (tmp_path / "watchdog.plist").exists()


def test_malformed_owned_label_is_not_silently_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        real_worker, "run", lambda *args, **kwargs: "- 0 com.uplink.real.unrecognized\n"
    )
    with pytest.raises(ValueError, match="unrecognized_watchdog"):
        real_worker.registered_watchdogs()
