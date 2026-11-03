"""KAYLIN-OK-TOP10-FRI-CORRECTION-2: 14:30 refused; Fri 21:30 and Sat 07:30 timelines admitted."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import importlib.util
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_stage_receiver import check_authority

CONTRACT = Path(c.__file__)
OLD = ("2026-10-30T14:30:00-05:00", "2026-10-30T16:10:00-05:00")
FRI = {
    "owner_release_earliest": "2026-10-30T21:05:00-05:00",
    "owner_release_latest": "2026-10-30T21:15:00-05:00",
    "relay_load_check_at": "2026-10-30T21:15:00-05:00",
    "kaylin_verify_by": "2026-10-30T21:25:00-05:00",
    "start": "2026-10-30T21:30:00-05:00",
    "latest_start": "2026-10-30T21:45:00-05:00",
    "end": "2026-10-30T23:10:00-05:00",
}
SAT = {
    "owner_release_earliest": "2026-10-31T07:05:00-05:00",
    "owner_release_latest": "2026-10-31T07:15:00-05:00",
    "relay_load_check_at": "2026-10-31T07:15:00-05:00",
    "kaylin_verify_by": "2026-10-31T07:25:00-05:00",
    "start": "2026-10-31T07:30:00-05:00",
    "latest_start": "2026-10-31T07:45:00-05:00",
    "end": "2026-10-31T09:10:00-05:00",
}


def ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def contract_at(monkeypatch: pytest.MonkeyPatch, instant: str) -> ModuleType:
    monkeypatch.setattr(time, "time", lambda: ts(instant))
    spec = importlib.util.spec_from_file_location("top10_contract_timeline_probe", CONTRACT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.undo()
    return module


def window(start: str, end: str) -> dict[str, object]:
    return {
        "version": 1,
        "kind": "KAYLIN-OK-TOP10-FAULTS",
        "authorized": True,
        "approved_by": "Kaylin",
        "manifest_sha256": "a" * 64,
        "arm": "BASE",
        "server_pid": c.SERVER_PID,
        "server_birth": "Thu Oct 29 01:00:00 2026",
        "start": start,
        "end": end,
        "owner_handback_sha256": "b" * 64,
        "private_config_sha256": "c" * 64,
        "oob_interface": "en7",
        "oob_server_ipv4": _public_fixture("@PUBLIC_IPV4_062@"),
        "root_approval_sha256": "d" * 64,
        "oob_client_ipv4": _public_fixture("@PUBLIC_IPV4_010@"),
    }


def test_timelines_are_exactly_the_corrected_pair() -> None:
    assert c.TIMELINES == (FRI, SAT)
    for row in (FRI, SAT):
        assert ts(row["end"]) - ts(row["start"]) == 6000
        assert ts(row["latest_start"]) - ts(row["start"]) == 900
        assert ts(row["relay_load_check_at"]) == ts(row["owner_release_latest"])


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        ("2026-10-30T14:30:00-05:00", FRI),
        ("2026-10-30T21:05:00-05:00", FRI),
        ("2026-10-30T23:30:00-05:00", FRI),
        ("2026-10-31T07:04:59-05:00", FRI),
        ("2026-10-31T07:05:00-05:00", SAT),
        ("2026-10-31T10:00:00-05:00", SAT),
    ],
)
def test_bound_timeline_by_load_instant(
    monkeypatch: pytest.MonkeyPatch, instant: str, expected: dict[str, str]
) -> None:
    m = contract_at(monkeypatch, instant)
    assert expected == m.TIMELINE
    assert ts(expected["start"]) == m.WINDOW_START
    assert ts(expected["end"]) == m.WINDOW_END
    assert ts(expected["latest_start"]) == m.LATEST_START
    assert ts(expected["owner_release_earliest"]) == m.OWNER_RELEASE_EARLIEST
    assert ts(expected["owner_release_latest"]) == m.OWNER_RELEASE_LATEST
    assert ts(expected["kaylin_verify_by"]) == m.KAYLIN_VERIFY_BY
    assert ts(expected["relay_load_check_at"]) == m.RELAY_LOAD_CHECK_AT


@pytest.mark.parametrize("instant", ["2026-10-30T14:30:00-05:00", "2026-10-31T07:05:00-05:00"])
def test_1430_window_is_refused(monkeypatch: pytest.MonkeyPatch, instant: str) -> None:
    m = contract_at(monkeypatch, instant)
    with pytest.raises(m.Refused, match="top10_exact_100_minute_slot_required"):
        m.validate_window(window(*OLD), {"server_pid": m.SERVER_PID}, "a" * 64, ts(OLD[0]) + 1)
    with pytest.raises(m.Refused):
        m.validate_owner_release(ts("2026-10-30T14:10:00-05:00"), ts(OLD[0]))
    with pytest.raises(m.Refused):
        m.validate_launch(ts(OLD[0]), ts("2026-10-30T14:10:00-05:00"))


@pytest.mark.parametrize(
    ("instant", "row"),
    [("2026-10-30T21:05:00-05:00", FRI), ("2026-10-31T07:05:00-05:00", SAT)],
)
def test_new_window_accepted_at_each_checkpoint(
    monkeypatch: pytest.MonkeyPatch, instant: str, row: dict[str, str]
) -> None:
    m = contract_at(monkeypatch, instant)
    early, late = ts(row["owner_release_earliest"]), ts(row["owner_release_latest"])
    relay, verify = ts(row["relay_load_check_at"]), ts(row["kaylin_verify_by"])
    start, latest = ts(row["start"]), ts(row["latest_start"])
    # owner release window, checked at the relay checkpoint
    assert m.validate_owner_release(early, relay) == early
    assert m.validate_owner_release(late, relay) == late
    for bad in (early - 0.001, late + 0.001):
        with pytest.raises(m.Refused):
            m.validate_owner_release(bad, start)
    assert relay <= verify < start
    # launch admission: at or after start, before latest start
    m.validate_launch(start, early)
    m.validate_launch(latest - 0.001, late)
    for bad in (start - 0.001, latest):
        with pytest.raises(m.Refused):
            m.validate_launch(bad, late)
    m.validate_window(
        window(row["start"], row["end"]), {"server_pid": m.SERVER_PID}, "a" * 64, start
    )
    with pytest.raises(m.Refused, match="top10_exact_100_minute_slot_required"):
        m.validate_window(window(*OLD), {"server_pid": m.SERVER_PID}, "a" * 64, start)


def receiver_error(start: str, end: str) -> str:
    approval = "synthetic approval"
    row = window(start, end) | {
        "root_approval_sha256": hashlib.sha256(approval.encode()).hexdigest()
    }
    owner = "{}"
    row["owner_handback_sha256"] = hashlib.sha256(owner.encode()).hexdigest()
    try:
        check_authority(
            {"window": row, "owner": owner, "root_approval": approval, "manifest_sha256": "a" * 64},
            ts(start) + 1,
        )
    except ValueError as exc:
        return str(exc)
    return ""


def test_stage_receiver_refuses_1430_and_admits_both_new_windows() -> None:
    assert receiver_error(*OLD) == "top10_stage_exact_release_required"
    for row in (FRI, SAT):
        assert receiver_error(row["start"], row["end"]) not in {
            "top10_stage_exact_release_required",
            "top10_stage_preflight_window_required",
        }
