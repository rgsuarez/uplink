"""Bounded BASE trial orchestration. The independent watchdog owns final safety."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_score as score
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_model import parse


class Backend(Protocol):
    sandbox: bool

    @property
    def lost(self) -> bool: ...

    @property
    def oob_evidence(self) -> dict[str, object]: ...

    def preflight(self) -> dict[str, object]: ...
    def root(self, operation: str, payload: dict[str, object]) -> dict[str, object]: ...
    def oracle(self) -> dict[str, object]: ...
    def resources(self) -> dict[str, object]: ...
    def choose(
        self, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
    ) -> dict[str, object]: ...
    def pause(self, seconds: float) -> None: ...


class Journal:
    def __init__(self, directory: Path) -> None:
        if directory.exists() or any(p.is_symlink() for p in (directory, *directory.parents)):
            raise c.Refused("top10_new_receipt_directory_required")
        directory.mkdir(parents=True, mode=0o700)
        self.directory = directory
        self.hashes: dict[str, str] = {}

    def write(self, name: str, value: object) -> None:
        c.text(name, r"[a-z0-9-]+\.json")
        path = self.directory / name
        c.save(path, value, exclusive=True)
        self.hashes[name] = c.file_hash(path)


def run_trial(backend: Backend, slot: dict[str, object], output: Path) -> dict[str, object]:
    fault_id = c.text(slot.get("fault_id"), r"F(?:0[1-9]|10)")
    c.require_bench_fault(fault_id)
    run_id = c.text(slot.get("run_id"), r"[0-9a-f]{12}")
    trial = slot.get("trial")
    if type(trial) is not int or trial not in {1, 2, 3}:
        raise c.Refused("top10_registered_trial_number_required")
    journal = Journal(output)
    receipt: dict[str, object] = {
        "version": c.VERSION,
        "run_id": run_id,
        "fault_id": fault_id,
        "trial": trial,
        "evidence_kind": "FAKE_KERNEL" if backend.sandbox else "BASE",
        "fault_evidence_class": "local_portal_simulation" if fault_id == "F07" else "lab_wifi",
        "registry_sha256": c.REGISTRY_SHA256,
        "prompt_sha256": c.PROMPT_SHA256,
        "oracle_sha256": c.ORACLE_SHA256,
        "outcome": "blocked",
        "false_online": 0,
        "unparsed_model_output": False,
        "model_requests": 0,
        "synthetic_policy_requests": 0,
        "decision_requests": 0,
        "fault_effect_verified": False,
        "cleanup_verified": False,
        "failure": None,
        "failed_phase": None,
        "watchdog_recovery_credit": False,
        "model_actions": [],
        "started_at": time.time(),
    }
    journal.write("intent.json", {"slot": slot, "base_only": True, "model_pid": c.SERVER_PID})
    prepared = False
    phase = "preflight"
    history: list[str] = []
    try:
        preflight = backend.preflight()
        journal.write("preflight.json", preflight)
        if preflight.get("base_verified") is not True or preflight.get("pid") != c.SERVER_PID:
            raise c.Refused("top10_base_preflight_unproven")
        phase = "snapshot"
        # Even a partial root preparation is followed by a bounded status/restore attempt.
        prepared = True
        initial = backend.root("prepare", {})
        journal.write("prepare.json", initial)
        snapshot = c.text(initial.get("snapshot_id"), r"[0-9a-f]{64}")
        phase = "watchdog"
        armed = backend.root("arm", {})
        journal.write("arm.json", armed)
        if armed.get("ready") is not True or type(armed.get("watchdog_pid")) is not int:
            raise c.Refused("top10_independent_watchdog_required")
        phase = "inject"
        observation = backend.root("inject", {})
        journal.write("negative-control.json", observation)
        if observation.get("fault_effective") is not True:
            raise c.Refused("top10_fault_effect_not_proven")
        c.measured(observation.get("facts"))
        receipt["fault_effect_verified"] = True
        receipt["outcome"] = "failed"
        for step in range(1, c.MAX_DECISIONS + 1):
            phase = "measurement"
            if step > 1:
                observation = backend.root("observe", {})
            journal.write(f"observe-{step}.json", observation)
            measured = c.measured(observation.get("facts"))
            if (
                observation.get("watchdog_restore") is not False
                or observation.get("controller_cleanup") is not False
            ):
                raise c.Refused("top10_automatic_restore_not_model_recovery")
            if (
                observation.get("snapshot_id") != snapshot
                or not measured["protected_services_unchanged"]
            ):
                raise c.Refused("top10_snapshot_or_protected_state_changed")
            oracle = backend.oracle()
            journal.write(f"oracle-{step}.json", oracle)
            score.online(oracle, time.time())  # Parse negative evidence too; missing data refuses.
            score.require_fresh(oracle, time.time())
            if (
                fault_id == "F07"
                and step == 1
                and not score.simulated_portal_visible(oracle, time.time())
            ):
                receipt["outcome"] = "blocked"
                receipt["fault_effect_verified"] = False
                raise c.Refused("top10_portal_oracle_simulation_unproven")
            measured_at = c.number(observation.get("checked_at"), 0, 4_000_000_000)
            if not 0 <= time.time() - measured_at <= 10:
                raise c.Refused("top10_stale_observation_refused")
            resources = backend.resources()
            journal.write(f"resource-{step}.json", resources)
            if resources.get("base_verified") is not True or resources.get("pid") != c.SERVER_PID:
                raise c.Refused("top10_pid_or_base_changed")
            status = backend.root("status", {})
            if status.get("lifecycle") not in {"fault_verified", "deciding"}:
                raise c.Refused("top10_watchdog_changed_state_during_measurement")
            remaining = c.number(status.get("remaining_seconds"), 0, c.WATCHDOG_SECONDS)
            timeout = min(float(c.MODEL_TIMEOUT), remaining - 20)
            if timeout < 0.1:
                raise c.Refused("top10_decision_budget_exhausted")
            phase = "model"
            receipt["decision_requests"] = int(str(receipt["decision_requests"])) + 1
            journal.write(
                f"model-intent-{step}.json", {"timeout": timeout, "synthetic": backend.sandbox}
            )
            emission = backend.choose(snapshot, measured, oracle, history, timeout)
            journal.write(f"model-{step}.json", emission)
            requests = emission.get("request_count")
            if type(requests) is not int or requests != 1:
                raise c.Refused("top10_one_completion_per_decision_required")
            counter = "synthetic_policy_requests" if backend.sandbox else "model_requests"
            receipt[counter] = int(str(receipt[counter])) + requests
            raw = emission.get("raw_text")
            if not isinstance(raw, str) or emission.get("failure") is not None:
                receipt["unparsed_model_output"] = True
                if score.possible_positive_claim(raw):
                    receipt["false_online"] = int(str(receipt["false_online"])) + 1
                raise c.Refused("top10_completion_failed_or_unparsed")
            try:
                action = parse(raw, snapshot)
            except Exception:
                receipt["unparsed_model_output"] = True
                if score.possible_positive_claim(raw):
                    receipt["false_online"] = int(str(receipt["false_online"])) + 1
                raise
            tool = str(action["tool"])
            history.append(tool)
            receipt["model_actions"] = list(history)
            if tool in {"report", "give_up"}:
                receipt["outcome"] = "honest_report"
                receipt["report"] = action
                break
            if tool == "probe_online":
                continue
            if tool in c.REPAIRS:
                phase = "model_action"
                result = backend.root("apply", {"tool": tool, "snapshot_id": snapshot})
                journal.write(f"action-{step}.json", result)
                # A short settling observation is not a second action or a model retry.
                backend.pause(0.2)
            phase = "independent_verification"
            final_observation: dict[str, object] | None = None
            final_oracle: dict[str, object] | None = None
            supported = False
            recovered = False
            try:
                final_observation = backend.root("observe", {})
                final_oracle = backend.oracle()
                # Re-read the actor/epoch after probes to catch a watchdog race.
                status = backend.root("status", {})
                for key in (
                    "lifecycle",
                    "watchdog_restore",
                    "controller_cleanup",
                    "completed_model_actions",
                    "last_reason",
                ):
                    final_observation[key] = status.get(key)
                journal.write(f"verify-observation-{step}.json", final_observation)
                journal.write(f"verify-oracle-{step}.json", final_oracle)
                supported = score.positive_claim_supported(
                    final_observation, final_oracle, time.time()
                )
                recovered = score.recovered(final_observation, final_oracle, time.time())
            finally:
                if tool == "declare_online" and not supported:
                    receipt["false_online"] = int(str(receipt["false_online"])) + 1
            if tool == "declare_online" and not supported:
                raise c.Refused("top10_false_online_stop_campaign")
            if recovered:
                receipt["outcome"] = "recovered"
                receipt["recovery_proof"] = {
                    "observation": final_observation,
                    "oracle": final_oracle,
                    "before_watchdog_or_cleanup": True,
                }
                break
            if tool == "declare_online":
                raise c.Refused("top10_recovery_not_model_attributable")
        else:
            raise c.Refused("top10_decision_limit_reached")
    except BaseException as exc:
        receipt["failure"] = label(exc)
        receipt["failed_phase"] = phase
        if phase == "model" and backend.lost:
            receipt["base_completion_status"] = "unknown_after_transport_loss"
        journal.write("failure.json", {"reason": label(exc), "phase": phase})
    finally:
        if backend.oob_evidence:
            journal.write("oob-qualification.json", backend.oob_evidence)
        if prepared and not backend.lost:
            try:
                restored = backend.root("restore", {})
                journal.write("restore.json", restored)
                if restored.get("lifecycle") not in {"restored", "closed"}:
                    raise c.Refused("top10_restoration_unproven")
                closing_oracle = backend.oracle()
                journal.write("handback-oracle.json", closing_oracle)
                if not score.online(closing_oracle, time.time()):
                    raise c.Refused("top10_handback_not_online")
                closing = backend.root("close", {})
                retired = backend.root("retire", {})
                journal.write("close.json", closing)
                journal.write("retire.json", retired)
                after = backend.resources()
                journal.write("handback-resources.json", after)
                if (
                    closing.get("lifecycle") != "closed"
                    or retired.get("retired") is not True
                    or after.get("base_verified") is not True
                    or after.get("pid") != c.SERVER_PID
                ):
                    raise c.Refused("top10_handback_identity_or_cleanup_unproven")
                receipt["cleanup_verified"] = True
            except Exception as exc:
                receipt["cleanup_failure"] = label(exc)
                if receipt["failure"] is None:
                    receipt["failure"] = label(exc)
                receipt["outcome"] = "failed"
                journal.write("cleanup-failure.json", {"reason": label(exc)})
        elif prepared:
            receipt["cleanup_failure"] = "management_lost_watchdog_handoff_unverified"
            receipt["outcome"] = "failed"
        receipt["finished_at"] = time.time()
        receipt["artifact_sha256"] = dict(journal.hashes)
        journal.write("receipt.json", receipt)
    return receipt


def summary_file(
    receipts: list[dict[str, object]], directory: Path, *, sandbox: bool
) -> dict[str, object]:
    result = score.summarize(receipts, sandbox=sandbox)
    result["registry_sha256"] = c.REGISTRY_SHA256
    result["prompt_sha256"] = c.PROMPT_SHA256
    result["oracle_sha256"] = c.ORACLE_SHA256
    c.save(directory / "summary.json", result, exclusive=True)
    return result
