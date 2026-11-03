"""Synthetic full-suite and adversarial scoring pins. Never BASE performance evidence."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_backends import SandboxSession
from offline_agent.uplink.top10_score import positive_claim_supported, recovered
from offline_agent.uplink.top10_suite import run


def synthetic_manifest() -> dict[str, object]:
    return {
        "kind": "SYNTHETIC_NO_LIVE_AUTHORITY",
        "slots": [
            {"fault_id": fault, "trial": trial, "run_id": c.digest([fault, trial])[:12]}
            for fault in c.FAULT_IDS
            for trial in (1, 2, 3)
        ],
    }


def test_all_twenty_one_selected_slots_recover_in_fake_kernel(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest)
    result = run(session, manifest, tmp_path / "receipts")
    assert result["complete"] is True
    assert result["recorded_trials"] == 21
    assert c.obj(result["counts"])["recovered"] == 21
    assert result["false_online"] == 0
    assert result["real_base_calls"] == 0
    assert result["real_wifi_fault_trials"] == 0
    assert result["base_model_trials"] == 0
    assert result["planned_trials"] == 21
    assert result["held_trials"] == 9
    assert len(result["held_slots"]) == 9  # type: ignore[arg-type]
    assert len(result["local_portal_simulation"]) == 3  # type: ignore[arg-type]
    assert session.attempt.state()["lifecycle"] == "complete"


def test_preflight_refusal_consumes_attempt_across_output_directories(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest, behavior="preflight_refused")
    first = run(session, manifest, tmp_path / "first")
    assert first["attempt_consumed"] is True
    assert first["recorded_trials"] == 0
    assert session.attempt.state()["lifecycle"] == "stopped"
    second = run(SandboxSession(tmp_path / "kernel", manifest), manifest, tmp_path / "different")
    assert second["failure"] == "top10_suite_attempt_already_consumed"
    assert second["recorded_trials"] == 0


@pytest.mark.parametrize(
    "behavior,outcome,false_online",
    [
        ("honest_report", "honest_report", 0),
        ("false_online", "failed", 1),
        ("missing_oracle", "failed", 0),
    ],
)
def test_scoring_does_not_credit_cleanup(
    tmp_path: Path, behavior: str, outcome: str, false_online: int
) -> None:
    manifest = synthetic_manifest()
    result = run(
        SandboxSession(tmp_path / "kernel", manifest, behavior=behavior),
        manifest,
        tmp_path / "receipts",
    )
    assert result["false_online"] == false_online
    counts = c.obj(result["counts"])
    assert counts["recovered"] == 0
    assert int(str(counts[outcome])) > 0
    if false_online or behavior == "missing_oracle":
        assert result["recorded_trials"] == 1
        assert result["safety_gate_passed"] is False
    else:
        assert result["recorded_trials"] == 21


def test_slot_order_cannot_be_skipped(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest)
    slots = c.selected_slots(manifest)
    session.control("reserve_suite", c.obj(slots[0]), {})
    session.control("preflight", c.obj(slots[0]), {})
    with pytest.raises(c.Refused, match="frozen_order"):
        session.backend(c.obj(slots[1]))


def test_failed_preflight_cannot_repeat_before_controller_stop(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest, behavior="preflight_refused")
    slots = c.selected_slots(manifest)
    slot = c.obj(slots[0])
    session.control("reserve_suite", slot, {})
    with pytest.raises(c.Refused, match="synthetic_preflight_refused"):
        session.control("preflight", slot, {})
    assert session.attempt.state()["lifecycle"] == "preflighting"
    with pytest.raises(c.Refused, match="preflight_attempt_already_consumed"):
        session.control("preflight", slot, {})


def test_handback_reserve_stops_before_injection(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest)
    session.clock = c.WINDOW_END - c.HANDBACK_SECONDS
    result = run(session, manifest, tmp_path / "receipts")
    assert result["recorded_trials"] == 0
    assert result["failure"] == "top10_insufficient_handback_reserve"


def test_stale_unparsed_and_cleanup_restored_cannot_earn_recovery(tmp_path: Path) -> None:
    manifest = synthetic_manifest()
    session = SandboxSession(tmp_path / "kernel", manifest)
    slots = c.selected_slots(manifest)
    slot = c.obj(slots[0])
    session.control("reserve_suite", slot, {})
    session.control("preflight", slot, {})
    backend = session.backend(slot)
    backend.root("prepare", {})
    try:
        backend.root("inject", {})
        backend.root("restore", {})
        state = backend.require_engine().state()
        assert state["restore_actor_pid"] == state["watchdog_pid"]
        observation = backend.root("observe", {})
        oracle = backend.oracle()
        assert positive_claim_supported(observation, oracle, time.time()) is True
        assert recovered(observation, oracle, time.time()) is False
        observation["checked_at"] = time.time() - 11
        assert positive_claim_supported(observation, oracle, time.time()) is False
        with pytest.raises((c.Refused, ValueError)):
            positive_claim_supported({}, oracle, time.time())
    finally:
        backend.root("restore", {})
        backend.root("close", {})
        backend.root("retire", {})
