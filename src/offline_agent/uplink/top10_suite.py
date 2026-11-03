"""One reservation, frozen slot order, bounded cooldown and explicit handback."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_harness import Backend, Journal, run_trial
from offline_agent.uplink.top10_score import summarize


class Session(Protocol):
    sandbox: bool
    lost: bool
    consumed: bool
    proof: dict[str, object]

    @property
    def oob_evidence(self) -> dict[str, object]: ...

    def control(
        self, operation: str, slot: dict[str, object], payload: dict[str, object]
    ) -> dict[str, object]: ...
    def backend(self, slot: dict[str, object]) -> Backend: ...
    def now(self) -> float: ...
    def pause(self, seconds: float) -> None: ...


def run(session: Session, manifest: dict[str, object], output: Path) -> dict[str, object]:
    slots = c.selected_slots(manifest)
    journal = Journal(output)
    journal.write(
        "intent.json",
        {
            "manifest_document_sha256": c.digest(manifest),
            "slots": slots,
            "held_slots": c.held_slots(manifest),
            "sandbox": session.sandbox,
            "model_calls_before_freeze": 0,
        },
    )
    receipts: list[dict[str, object]] = []
    reserved = False
    started = session.now()
    failure: str | None = None
    try:
        reservation = session.control("reserve_suite", slots[0], {})
        reserved = True
        journal.write("reservation.json", reservation)
        if session.oob_evidence:
            journal.write("suite-oob.json", session.oob_evidence)
        preflight_start = time.monotonic()
        session.proof = session.control("preflight", slots[0], {})
        if time.monotonic() - preflight_start > 600:
            raise c.Refused("top10_preflight_budget_exceeded")
        journal.write("preflight.json", session.proof)
        for slot in slots:
            if (
                session.now() + c.TRIAL_SECONDS + c.HANDBACK_SECONDS + c.RESERVE_SECONDS
                > c.WINDOW_END
            ):
                raise c.Refused("top10_insufficient_handback_reserve")
            slot_start = time.monotonic()
            receipt = run_trial(session.backend(slot), slot, output / str(slot["run_id"]))
            elapsed = time.monotonic() - slot_start
            receipts.append(receipt)
            if session.lost:
                raise c.Refused("top10_management_lost_manual_handback_required")
            state = session.control("finish_slot", slot, receipt)
            journal.write(
                "timing-" + str(slot["run_id"]) + ".json",
                {
                    "work_seconds": elapsed,
                    "minimum_cooldown_seconds": 40,
                    "slot_seconds": c.TRIAL_SECONDS,
                },
            )
            if elapsed + 40 > c.TRIAL_SECONDS:
                raise c.Refused("top10_trial_budget_exceeded")
            if state.get("lifecycle") == "stopped":
                raise c.Refused("top10_trial_not_safe_to_continue")
            session.pause(40)
        journal.write(
            "handback.json",
            {
                "all_slots_restored": True,
                "model_pid_untouched": True,
                "elapsed_wall_seconds": session.now() - started,
            },
        )
    except BaseException as exc:
        failure = label(exc)
        journal.write("failure.json", {"reason": failure, "reserved": reserved})
        if session.oob_evidence:
            journal.write("oob-stop.json", session.oob_evidence)
        if reserved and not session.lost:
            try:
                session.control("stop_suite", slots[0], {"reason": failure})
            except Exception as stop_exc:
                journal.write("stop-failure.json", {"reason": label(stop_exc)})
    result = summarize(receipts, sandbox=session.sandbox)
    result.update(
        failure=failure,
        attempt_consumed=reserved or session.consumed,
        unattempted_slots=slots[len(receipts) :],
        elapsed_wall_seconds=session.now() - started,
        real_base_calls=0
        if session.sandbox
        else sum(int(str(r["model_requests"])) for r in receipts),
    )
    result["held_slots"] = c.held_slots(manifest)
    if failure is not None:
        result["safety_gate_passed"] = False
    result.update(
        registry_sha256=c.REGISTRY_SHA256,
        prompt_sha256=c.PROMPT_SHA256,
        oracle_sha256=c.ORACLE_SHA256,
    )
    journal.write("summary.json", result)
    journal.write("suite-receipt.json", result)
    return result
