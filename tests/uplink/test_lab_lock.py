"""Watchdog lock contention waits before dispatch; no operation is retried."""

import fcntl
import io
import time
from unittest.mock import Mock

import pytest

from offline_agent.uplink import lab_worker


@pytest.mark.parametrize("busy", (0, 1, 3))
def test_short_watchdog_lock_hold_is_acquired_within_bound(
    monkeypatch: pytest.MonkeyPatch, busy: int
) -> None:
    now = [0.0]
    acquire = Mock(side_effect=[*[BlockingIOError()] * busy, None])
    monkeypatch.setattr(fcntl, "flock", acquire)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    lab_worker.acquire_operation_lock(io.StringIO())
    assert acquire.call_count == busy + 1
    assert now[0] == pytest.approx(busy * 0.01)
    assert all(call.args[1] == fcntl.LOCK_EX | fcntl.LOCK_NB for call in acquire.call_args_list)


def test_contended_lock_has_bounded_classified_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(fcntl, "flock", Mock(side_effect=BlockingIOError()))
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    with pytest.raises(ValueError, match="^lab_lock_busy_no_operation$") as error:
        lab_worker.acquire_operation_lock(io.StringIO())
    assert isinstance(error.value.__cause__, BlockingIOError)
    assert now[0] == pytest.approx(0.5)


def test_lock_errors_other_than_contention_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    acquire = Mock(side_effect=PermissionError())
    sleeper = Mock()
    monkeypatch.setattr(fcntl, "flock", acquire)
    monkeypatch.setattr(time, "sleep", sleeper)
    with pytest.raises(PermissionError):
        lab_worker.acquire_operation_lock(io.StringIO())
    acquire.assert_called_once()
    sleeper.assert_not_called()
