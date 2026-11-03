"""Bounded open-action control loop. Root rollback and the ONLINE oracle stay independent."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Protocol

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.faults_v1 import IPV4_FAULTS, FaultSpec, digest, parse_action
from offline_agent.uplink.faults_v1_model import CHOICES, REPAIRS, VERSION, observation, parse
from offline_agent.uplink.oracle import FRESH_SECONDS
from offline_agent.uplink.probe_trial_window import require_resources, save
from offline_agent.uplink.real_transport import parse_oracle


class Backend(Protocol):
    lost: bool

    def now(self) -> float: ...
    def pause(self, seconds: float) -> None: ...
    def paths(self) -> dict[str, Json]: ...
    def preflight(self, spec: FaultSpec, paths: dict[str, Json]) -> dict[str, Json]: ...
    def call(
        self, spec: FaultSpec, operation: str, payload: dict[str, Json]
    ) -> dict[str, Json]: ...
    def oracle(self) -> dict[str, Json]: ...
    def resources(self) -> dict[str, Json]: ...
    def choose(
        self,
        measured: dict[str, Json],
        oracle: dict[str, Json],
        history: list[Json],
        timeout: float,
    ) -> dict[str, Json]: ...


def reason(exc: BaseException) -> str:
    label = str(exc)
    return label if re.fullmatch(r"[a-z][a-z0-9_]{0,119}", label) else type(exc).__name__


def fresh(value: Json, now: float, seconds: float = 10) -> None:
    if (
        type(value) not in (int, float)
        or not math.isfinite(float(str(value)))
        or not 0 <= now - float(str(value)) <= seconds
    ):
        raise ContractError("newfault_fresh_measurement_required")


def oracle_online(value: dict[str, Json], now: float) -> bool:
    parsed = parse_oracle(value)
    fresh(parsed.checked_at, now, FRESH_SECONDS)
    return parsed.fresh_online(now)


def paths_ok(row: dict[str, Json], now: float) -> None:
    fresh(row.get("checked_at"), now)
    if any(row.get(k) is not True for k in ("oob_ipv4_verified", "ssh_ipv4_verified")):
        raise ContractError("newfault_ipv4_management_paths_required")


class Journal:
    """Exclusive files and a final hash index, using the existing private receipt writer."""

    def __init__(self, directory: Path) -> None:
        if directory.exists() or any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ContractError("newfault_new_private_output_required")
        directory.mkdir(mode=0o700, parents=True)
        self.directory = directory
        self.files: dict[str, Json] = {}

    def write(self, name: str, value: Json) -> None:
        if not re.fullmatch(r"[a-z0-9-]+\.json", name):
            raise ContractError("newfault_artifact_basename_required")
        path = self.directory / name
        save(path, value)
        self.files[name] = hashlib.sha256(path.read_bytes()).hexdigest()


def recover(
    backend: Backend, spec: FaultSpec, window: dict[str, Json], output: Path
) -> dict[str, Json]:
    if spec.fault not in IPV4_FAULTS or spec.target_ipv6:
        raise ContractError("newfault_ipv4_only")
    journal = Journal(output)
    receipt: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-newfault-model-trial",
        "run_id": spec.run_id,
        "fault": spec.fault,
        "evidence_kind": "MOCK" if getattr(backend, "dry", True) else "lab_wifi",
        "action_mode": "open",
        "model_contract": VERSION,
        "spec_sha256": digest(spec.document()),
        "arm": window.get("arm"),
        "runtime_sha256": window.get("runtime_sha256"),
        "package_sha256": window.get("package_sha256"),
        "trial_passed": False,
        "false_online_attempts": 0,
        "repair_attempted": False,
        "outcome": "refused",
        "failure": None,
        "cleanup_verified": False,
        "decisions": [],
        "resource_samples": [],
    }
    journal.write(
        "intent.json",
        {"spec": json.loads(json.dumps(spec.document())), "window": window, "attempts": 1},
    )
    prepared = False
    root_started = False
    repaired = False
    verified = False
    gave_up = False
    baseline_swap: Json = None
    decisions: list[Json] = []
    samples: list[Json] = []

    def resource() -> None:
        row = backend.resources()
        samples.append(row)
        journal.write(f"resource-{len(samples):02d}.json", row)
        require_resources(row, baseline_swap)

    try:
        proofs = backend.paths()
        paths_ok(proofs, backend.now())
        admission = backend.preflight(spec, proofs)
        journal.write("preflight.json", admission)
        baseline_swap = object_json(admission.get("resources")).get("swap_mib")
        if baseline_swap is None:
            raise ContractError("newfault_baseline_resources_required")
        if (
            admission.get("runtime_sha256") != window.get("runtime_sha256")
            or admission.get("package_sha256") != window.get("package_sha256")
            or admission.get("arm") != window.get("arm")
        ):
            raise ContractError("newfault_preflight_binding_mismatch")
        if not oracle_online(object_json(admission.get("oracle")), backend.now()):
            raise ContractError("newfault_baseline_online_required")
        initial_resources = object_json(admission.get("resources"))
        samples.append(initial_resources)
        journal.write("resource-01.json", initial_resources)
        require_resources(initial_resources, baseline_swap)
        proofs = backend.paths()
        paths_ok(proofs, backend.now())
        before = object_json(admission.get("before_facts")) | proofs
        # This timestamp is the age of the collector's evidence, never refreshed here.
        before["checked_at"] = object_json(admission.get("before_facts")).get("checked_at")
        fresh(before.get("checked_at"), backend.now(), 30)
        root_window = object_json(window["worker_windows"])[spec.run_id]
        root_started = True
        prepared_row = backend.call(
            spec,
            "prepare",
            {"window": root_window, "before_facts": before, "oracle": admission["oracle"]},
        )
        journal.write("prepare.json", prepared_row)
        prepared = prepared_row.get("prepared") is True
        snapshot = prepared_row.get("snapshot_id")
        if (
            not prepared
            or not isinstance(snapshot, str)
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot)
        ):
            raise ContractError("newfault_snapshot_preparation_unproven")
        journal.write("arm.json", backend.call(spec, "arm", {}))
        for _ in range(30):
            state = backend.call(spec, "observe", {})
            if state.get("ready") is True:
                break
            backend.pause(0.1)
        else:
            raise ContractError("newfault_watchdog_not_ready")
        paths_ok(backend.paths(), backend.now())
        injected = backend.call(spec, "inject", {})
        journal.write("inject.json", injected)
        if (
            injected.get("fault_effective") is not True
            or injected.get("target_online") is not False
        ):
            raise ContractError("newfault_negative_control_required")
        history: list[Json] = []
        for step in range(1, 5):
            resource()
            measured = backend.call(spec, "measure", {})
            journal.write(f"measure-{step}.json", measured)
            fresh(measured.get("checked_at"), backend.now())
            observation(measured)
            if measured.get("snapshot_id") != snapshot:
                raise ContractError("newfault_snapshot_changed")
            oracle = backend.oracle()
            journal.write(f"oracle-{step}.json", oracle)
            oracle_online(oracle, backend.now())  # Parse all evidence, including negative verdicts.
            budget = 8.0
            if not repaired:
                left = measured.get("remaining_seconds")
                if type(left) not in (int, float) or float(str(left)) < 32:
                    raise ContractError("newfault_model_budget_insufficient")
                budget = min(budget, float(str(left)) - 24)
            emission = backend.choose(measured, oracle, history, budget)
            journal.write(f"model-{step}.json", emission)
            during = emission.get("resource_samples", [])
            if not isinstance(during, list):
                raise ContractError("newfault_model_resource_samples_invalid")
            for sample in during:
                row = object_json(sample)
                samples.append(row)
                require_resources(row, baseline_swap)
            if emission.get("error") is not None:
                raise ContractError("newfault_model_failed_see_emission_receipt")
            if (
                emission.get("action_mode") != "open"
                or emission.get("admitted_tools") != list(CHOICES)
                or not isinstance(emission.get("text"), str)
            ):
                raise ContractError("newfault_open_choice_unproven")
            action = parse(str(emission["text"]), snapshot)
            if emission.get("action") != action:
                raise ContractError("newfault_action_emission_mismatch")
            tool = str(action["tool"])
            choice: dict[str, Json] = {
                "step": step,
                "action": action,
                "mode": "open",
                "admitted_tools": list(CHOICES),
                "accepted": False,
            }
            decisions.append(choice)
            history.append(tool)
            if tool == "give_up":
                choice["accepted"] = True
                gave_up = True
                receipt["outcome"] = "gave_up"
                break
            if tool == "probe_online":
                choice["accepted"] = True
                continue
            if tool == "declare_online":
                if (
                    not oracle_online(oracle, backend.now())
                    or measured.get("target_online") is not True
                    or measured.get("baseline_equal") is not True
                    or not repaired
                ):
                    receipt["false_online_attempts"] = 1
                    choice["reason"] = "newfault_false_online_refused"
                    raise ContractError("newfault_false_online_refused")
                # Re-measure after emission; never accept using only pre-generation evidence.
                final = backend.oracle()
                journal.write("verify-oracle.json", final)
                if not oracle_online(final, backend.now()):
                    raise ContractError("newfault_final_online_required")
                verify = backend.call(spec, "verify", {"oracle": final})
                journal.write("verify.json", verify)
                if (
                    verify.get("verified") is not True
                    or verify.get("target_online") is not True
                    or object_json(verify.get("exact_diff")).get("match") is not True
                ):
                    raise ContractError("newfault_final_verification_unproven")
                verified = True
                choice["accepted"] = True
                receipt["oracle"] = final
                break
            if tool in REPAIRS:
                # The menu stays open; measured state and the root gate decide admission.
                changed = "route_changed" if tool == REPAIRS[0] else "pf_changed"
                if (
                    repaired
                    or tool != spec.tool
                    or measured.get(changed) is not True
                    or measured.get("target_online") is not False
                ):
                    choice["reason"] = "newfault_wrong_or_repeated_repair"
                    raise ContractError("newfault_wrong_or_repeated_repair")
                parse_action(action, spec, snapshot)
                paths_ok(backend.paths(), backend.now())
                request: dict[str, Json] = {
                    "operation": "apply",
                    "spec": json.loads(json.dumps(spec.document())),
                    "payload": {"action": action},
                }
                journal.write("apply-request.json", request)
                receipt["repair_attempted"] = True
                applied = backend.call(spec, "apply", {"action": action})
                journal.write("apply.json", applied)
                if applied.get("match") is not True:
                    raise ContractError("newfault_repair_exact_diff_failed")
                repaired = True
                choice["accepted"] = True
        else:
            raise ContractError("newfault_step_budget_exhausted")
    except (ValueError, OSError, RuntimeError, KeyboardInterrupt) as exc:
        receipt["failure"] = reason(exc)
        journal.write("failure.json", {"reason": reason(exc), "stop_series": True})
    finally:
        try:
            if root_started and backend.lost:
                raise ContractError("newfault_transport_lost_watchdog_handback_unknown")
            if prepared:
                state = backend.call(spec, "observe", {})
                if state.get("lifecycle") not in {"verified", "restored"}:
                    if state.get("lifecycle") in {"rollback_failed", "restoring"}:
                        raise ContractError("newfault_rollback_failed_no_second_fix")
                    journal.write("abort-restore.json", backend.call(spec, "restore", {}))
                journal.write("retire.json", backend.call(spec, "retire", {}))
                evidence = backend.call(spec, "receipt", {})
                journal.write("root-receipt.json", evidence)
                final_state = object_json(evidence.get("state"))
                diff = object_json(evidence.get("exact_diff"))
                if (
                    final_state.get("lifecycle") not in {"verified", "restored"}
                    or final_state.get("watchdog_registered") is not False
                    or diff.get("match") is not True
                ):
                    raise ContractError("newfault_cleanup_unproven")
                timing = object_json(evidence.get("timing"))
                if verified:
                    injected_at, restored_at = (
                        timing.get("injected_mono"),
                        timing.get("restored_mono"),
                    )
                    if (
                        timing.get("clock_basis") != "posix-monotonic-v1"
                        or timing.get("timing_unproven") is not False
                        or timing.get("boot_id") != final_state.get("boot_id")
                        or timing.get("run_id") != spec.run_id
                        or type(injected_at) not in (int, float)
                        or type(restored_at) not in (int, float)
                        or not 0 <= float(str(restored_at)) - float(str(injected_at)) <= 60
                    ):
                        raise ContractError("newfault_restore_deadline_unproven")
                proofs = backend.paths()
                paths_ok(proofs, backend.now())
                journal.write("handback-paths.json", proofs)
                resource()
                receipt["cleanup_verified"] = True
                receipt["timing"] = evidence.get("timing")
            elif root_started:
                raise ContractError("newfault_prepare_result_unproven")
        except (ValueError, OSError, RuntimeError, KeyboardInterrupt) as exc:
            receipt["cleanup_failure"] = reason(exc)
            receipt["cleanup_verified"] = False
            journal.write(
                "cleanup-failure.json",
                {"reason": reason(exc), "operator_notification_required": True},
            )
        receipt["decisions"], receipt["resource_samples"] = decisions, samples
        receipt["trial_passed"] = (
            verified and receipt["cleanup_verified"] is True and receipt["failure"] is None
        )
        receipt["outcome"] = (
            "recovered"
            if receipt["trial_passed"]
            else "gave_up_restored"
            if gave_up and receipt["cleanup_verified"] is True
            else "failed"
        )
        receipt["artifact_sha256"] = dict(journal.files)
        journal.write("receipt.json", receipt)
    return receipt
