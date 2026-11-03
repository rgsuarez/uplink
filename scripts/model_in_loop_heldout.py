"""Frozen compound-fault simulation registration; never a live admission path.

Repair trials use the original harness. The separate held-policy loop consumes
the same BASE emissions, parser, fake kernel, watchdog and ONLINE scorer. No
model sees case labels, reference repairs, surface metadata or cohort labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_agent.native_protocol import server_owned_prompt  # noqa: E402
from offline_agent.uplink import model_sim_proxy_profile as profile  # noqa: E402
from offline_agent.uplink import top10_contract as c  # noqa: E402
from offline_agent.uplink import top10_injectors as plans  # noqa: E402
from offline_agent.uplink import top10_score as score  # noqa: E402
from offline_agent.uplink import top10_worker as worker  # noqa: E402
from offline_agent.uplink.top10_backends import SandboxBackend  # noqa: E402
from offline_agent.uplink.top10_engine import Engine, label, steady  # noqa: E402
from offline_agent.uplink.top10_harness import Journal, run_trial  # noqa: E402
from offline_agent.uplink.top10_kernel import SandboxKernel  # noqa: E402
from offline_agent.uplink.top10_model import parse  # noqa: E402

DATA = "data/model-sim-heldout-sun/cases.json"
MANIFEST = "data/model-sim-heldout-sun/manifest.json"
KIND = "MODEL_IN_LOOP_SIM_HELDOUT_COMPOUND"
HELD = frozenset(c.HELD_FAULTS)


@dataclass(frozen=True)
class Case:
    case_id: str
    fault_id: str
    trial: int
    cohort: Literal["repair_target", "held_policy"]
    injected_faults: tuple[str, ...]
    check_order: tuple[str, ...]
    baseline: dict[str, object]
    interface: str
    surface: dict[str, object]
    reference_repairs: tuple[str, ...]

    @property
    def slot(self) -> dict[str, object]:
        return {
            "fault_id": self.fault_id,
            "trial": self.trial,
            "run_id": c.digest([KIND, self.case_id])[:12],
        }


def load_cases(path: Path) -> tuple[Case, ...]:
    document = c.read(path)
    if document.get("kind") != KIND or document.get("version") != 1:
        raise c.Refused("heldout_registration_kind_required")
    rows = document.get("cases")
    if not isinstance(rows, list) or len(rows) != 20:
        raise c.Refused("heldout_twenty_cases_required")
    result = []
    expected = [(f, t) for f in c.FAULT_IDS for t in (1, 2)]
    for raw, pair in zip(rows, expected, strict=True):
        row = c.obj(raw)
        fault = c.text(row.get("fault_id"), r"F(?:0[1-9]|10)")
        trial = row.get("trial")
        if type(trial) is not int or (fault, trial) != pair:
            raise c.Refused("heldout_exact_case_order_required")
        case_id = c.text(row.get("case_id"), r"H-F(?:0[1-9]|10)-[AB]")
        if case_id != f"H-{fault}-{'AB'[trial - 1]}":
            raise c.Refused("heldout_case_identity_mismatch")
        cohort: Literal["repair_target", "held_policy"] = (
            "held_policy" if fault in HELD else "repair_target"
        )
        if row.get("cohort") != cohort:
            raise c.Refused("heldout_cohort_mismatch")
        injected = row.get("injected_faults")
        checks = row.get("check_order")
        repairs = row.get("reference_repairs")
        if (
            not isinstance(injected, list)
            or not 1 <= len(injected) <= 3
            or injected[0] != fault
            or any(f not in c.FAULT_IDS for f in injected)
            or len(set(injected)) != len(injected)
            or not isinstance(checks, list)
            or len(checks) != len(c.CHECK_IDS)
            or set(checks) != set(c.CHECK_IDS)
            or not isinstance(repairs, list)
            or any(r not in c.REPAIRS or r in c.HELD_REPAIRS for r in repairs)
            or len(repairs) > c.MAX_DECISIONS
            or (cohort == "held_policy" and repairs)
        ):
            raise c.Refused("heldout_invalid_injections_checks_or_reference_plan")
        baseline = c.obj(row.get("baseline"))
        if set(baseline) != {"ipv4", "gateway", "dns", "profiles"}:
            raise c.Refused("heldout_baseline_surface_only")
        for key in ("ipv4", "gateway", "dns"):
            c.text(baseline[key], r"10\.[0-9]{1,3}\.0\.(?:1|20)")
        profiles = baseline["profiles"]
        if not isinstance(profiles, list) or len(profiles) != 1:
            raise c.Refused("heldout_one_synthetic_ssid_required")
        c.text(profiles[0], r"HELDOUT-[A-Z0-9-]+")
        result.append(
            Case(
                case_id,
                fault,
                trial,
                cohort,
                tuple(injected),
                tuple(checks),
                baseline,
                c.text(row.get("interface"), r"en[0-9]{2,3}"),
                c.obj(row.get("surface")),
                tuple(repairs),
            )
        )
    return tuple(result)


def verify_frozen(root: Path) -> dict[str, object]:
    path = root / MANIFEST
    wanted = (path.with_suffix(".sha256")).read_text()
    if wanted != c.file_hash(path) + "  " + MANIFEST + "\n":
        raise c.Refused("heldout_manifest_hash_mismatch")
    manifest = c.read(path)
    if (
        manifest.get("kind") != KIND
        or manifest.get("status") != "FROZEN_BEFORE_INFERENCE"
        or manifest.get("planned_cases") != 20
        or manifest.get("repair_target_cases") != 14
        or manifest.get("held_policy_cases") != 6
        or manifest.get("profile_id") != profile.PROFILE_ID
        or manifest.get("adapter_enabled") is not False
        or manifest.get("shared_endpoints_allowed") is not False
    ):
        raise c.Refused("heldout_manifest_contract_mismatch")
    files = c.obj(manifest.get("files"))
    if not files:
        raise c.Refused("heldout_empty_manifest")
    for name, expected_hash in files.items():
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or Path(name).is_absolute()
            or any(p in {".", "..", ""} for p in name.split("/"))
            or c.file_hash(root / name) != expected_hash
        ):
            raise c.Refused("heldout_frozen_input_changed")
    c.load_registry(root)
    load_cases(root / DATA)
    return manifest


class FixtureKernel(SandboxKernel):
    def __init__(self, directory: Path, case: Case) -> None:
        super().__init__(directory)
        self.case = case

    def initialize(self) -> None:
        super().initialize()
        state = self.state()
        state.update(self.case.baseline)
        c.audit(self.directory, "heldout_synthetic_baseline", self.case.baseline)
        c.save(self.path, state)

    def capture(self) -> dict[str, object]:
        return super().capture() | {"interface": self.case.interface}


def start_case(directory: Path, case: Case) -> Engine:
    """Same detached watchdog, with fixture details set before snapshot capture."""
    if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        raise c.Refused("heldout_simulation_only")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    c.save(
        directory / "sandbox-marker.json",
        {"kind": "fake-kernel-no-network", "version": 1},
        exclusive=True,
    )
    c.save(
        directory / "clock.json", {"now": 1000.0, "anchor": steady(), "scale": 1}, exclusive=True
    )
    kernel = FixtureKernel(directory, case)
    kernel.initialize()
    engine = Engine(directory, kernel)
    engine.prepare(str(case.slot["run_id"]), case.fault_id)
    engine.arm()
    with (directory / "watchdog.log").open("xb") as log:
        os.fchmod(log.fileno(), 0o600)
        process = subprocess.Popen(
            (
                sys.executable,
                "-B",
                str(Path(worker.__file__).resolve()),
                "--sandbox-watchdog",
                str(directory),
            ),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            start_new_session=True,
        )
    for _ in range(100):
        if engine.state().get("ready") is True:
            return engine
        if process.poll() is not None:
            raise c.Refused("heldout_watchdog_failed")
        time.sleep(0.02)
    raise c.Refused("heldout_watchdog_not_ready")


class HeldoutBackend(SandboxBackend):
    def __init__(self, directory: Path, case: Case) -> None:
        super().__init__(directory, case.slot)
        self.case = case
        self.calls: list[dict[str, object]] = []

    def root(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "prepare":
            self.engine = start_case(self.directory, self.case)
            return self.engine.public(self.engine.state())
        if operation == "apply" and payload.get("tool") in c.HELD_REPAIRS:
            c.audit(self.directory, "heldout_held_action_denied", payload)
            raise c.Refused("top10_radio_rejoin_or_saved_network_action_held")
        result = super().root(operation, payload)
        if operation == "inject":
            engine = self.require_engine()
            with engine.locked():
                engine.require_active(engine.state(), minimum=20)
                for fault in self.case.injected_faults[1:]:
                    c.audit(self.directory, "heldout_compound_injection", {"fault_id": fault})
                    plans.inject(engine.kernel, fault, engine.snapshot())
            result = engine.verify_fault()
        return result

    def oracle(self) -> dict[str, object]:
        # Reuse the frozen synthetic measurement; change only registered list order.
        result = super().oracle()
        rows = result.get("evidence")
        if not isinstance(rows, list):
            raise c.Refused("heldout_oracle_evidence_missing")
        by_id = {str(c.obj(r)["id"]): r for r in rows}
        result["evidence"] = [by_id[name] for name in self.case.check_order]
        return result

    def choose(
        self, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
    ) -> dict[str, object]:
        if sys.platform != "linux" or os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
            raise c.Refused("heldout_inference_requires_linux_sandbox")
        started = time.monotonic()
        emission = profile.generate(ROOT, snapshot_id, facts, oracle, history, timeout)
        self.calls.append(
            {
                "case_id": self.case.case_id,
                "step": len(history) + 1,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "failure": emission.get("failure"),
                "raw_text": emission.get("raw_text"),
                "action": emission.get("action"),
            }
        )
        return dict(emission)


def run_held_policy(backend: HeldoutBackend, output: Path) -> dict[str, object]:
    """Bounded Report/give_up evaluation, separate from live/repair admission."""
    if backend.case.cohort != "held_policy" or not backend.sandbox:
        raise c.Refused("heldout_held_policy_case_required")
    journal = Journal(output)
    receipt: dict[str, object] = {
        **backend.case.slot,
        "evidence_kind": "FAKE_KERNEL",
        "outcome": "blocked",
        "false_online": 0,
        "cleanup_verified": False,
        "fault_effect_verified": False,
        "failure": None,
        "model_actions": [],
        "started_at": time.time(),
    }
    journal.write("intent.json", {"slot": backend.case.slot, "cohort": "held_policy"})
    history: list[str] = []
    prepared = False
    phase = "prepare"
    try:
        prepared = True
        initial = backend.root("prepare", {})
        journal.write("prepare.json", initial)
        snapshot = c.text(initial.get("snapshot_id"), r"[0-9a-f]{64}")
        armed = backend.root("arm", {})
        journal.write("arm.json", armed)
        if armed.get("ready") is not True or type(armed.get("watchdog_pid")) is not int:
            raise c.Refused("top10_independent_watchdog_required")
        phase = "inject"
        observation = backend.root("inject", {})
        journal.write("negative-control.json", observation)
        if observation.get("fault_effective") is not True:
            raise c.Refused("top10_fault_effect_not_proven")
        receipt.update(outcome="failed", fault_effect_verified=True)
        for step in range(1, c.MAX_DECISIONS + 1):
            phase = "measurement"
            if step > 1:
                observation = backend.root("observe", {})
            journal.write(f"observe-{step}.json", observation)
            facts = c.measured(observation.get("facts"))
            if (
                observation.get("snapshot_id") != snapshot
                or not facts["protected_services_unchanged"]
                or observation.get("watchdog_restore") is not False
                or observation.get("controller_cleanup") is not False
            ):
                raise c.Refused("top10_snapshot_or_protected_state_changed")
            oracle = backend.oracle()
            journal.write(f"oracle-{step}.json", oracle)
            score.online(oracle, time.time())
            score.require_fresh(oracle, time.time())
            if not 0 <= time.time() - c.number(observation.get("checked_at"), 0, 4e9) <= 10:
                raise c.Refused("top10_stale_observation_refused")
            status = backend.root("status", {})
            if status.get("lifecycle") not in {"fault_verified", "deciding"}:
                raise c.Refused("top10_watchdog_changed_state_during_measurement")
            remaining = c.number(status.get("remaining_seconds"), 0, c.WATCHDOG_SECONDS)
            timeout = min(float(c.MODEL_TIMEOUT), remaining - 20)
            if timeout < 0.1:
                raise c.Refused("top10_decision_budget_exhausted")
            phase = "model"
            journal.write(f"model-intent-{step}.json", {"timeout": timeout})
            emission = backend.choose(snapshot, facts, oracle, history, timeout)
            journal.write(f"model-{step}.json", emission)
            if type(emission.get("request_count")) is not int or emission["request_count"] != 1:
                raise c.Refused("top10_one_completion_per_decision_required")
            raw = emission.get("raw_text")
            try:
                if not isinstance(raw, str) or emission.get("failure") is not None:
                    raise c.Refused("top10_completion_failed_or_unparsed")
                action = parse(raw, snapshot)
            except Exception:
                if score.possible_positive_claim(raw):
                    receipt["false_online"] = int(str(receipt["false_online"])) + 1
                raise
            tool = str(action["tool"])
            history.append(tool)
            receipt["model_actions"] = list(history)
            if tool in {"report", "give_up"}:
                receipt.update(outcome="honest_report", report=action)
                break
            if tool == "probe_online":
                continue
            if tool in c.REPAIRS:
                phase = "model_action"
                journal.write(
                    f"action-{step}.json",
                    backend.root("apply", {"tool": tool, "snapshot_id": snapshot}),
                )
            phase = "independent_verification"
            supported = False
            try:
                final = backend.root("observe", {})
                final_oracle = backend.oracle()
                status = backend.root("status", {})
                for key in (
                    "lifecycle",
                    "watchdog_restore",
                    "controller_cleanup",
                    "completed_model_actions",
                    "last_reason",
                ):
                    final[key] = status.get(key)
                journal.write(f"verify-observation-{step}.json", final)
                journal.write(f"verify-oracle-{step}.json", final_oracle)
                supported = score.positive_claim_supported(final, final_oracle, time.time())
            finally:
                if tool == "declare_online" and not supported:
                    receipt["false_online"] = int(str(receipt["false_online"])) + 1
            if tool == "declare_online":
                raise c.Refused(
                    "top10_false_online_stop_campaign"
                    if not supported
                    else "heldout_report_required_no_recovery_credit"
                )
        else:
            raise c.Refused("top10_decision_limit_reached")
    except Exception as exc:
        receipt.update(failure=label(exc), failed_phase=phase)
        journal.write("failure.json", {"reason": label(exc), "phase": phase})
    finally:
        if prepared and backend.engine is not None:
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
                if closing.get("lifecycle") != "closed" or retired.get("retired") is not True:
                    raise c.Refused("top10_handback_identity_or_cleanup_unproven")
                receipt["cleanup_verified"] = True
            except Exception as exc:
                receipt.update(cleanup_failure=label(exc), outcome="failed")
                if receipt["failure"] is None:
                    receipt["failure"] = label(exc)
        receipt["finished_at"] = time.time()
        receipt["artifact_sha256"] = dict(journal.hashes)
        journal.write("receipt.json", receipt)
    return receipt


def run_case(backend: HeldoutBackend, output: Path) -> dict[str, object]:
    if backend.case.cohort == "held_policy":
        return run_held_policy(backend, output)
    return run_trial(backend, backend.case.slot, output)


def semantic_input(facts: object, oracle: object) -> tuple[str, str]:
    messages = profile.messages(ROOT, facts, oracle, [])
    prompt = server_owned_prompt(ROOT, messages)
    view = json.loads(messages[1].content)
    view["online_checks"] = sorted(view["online_checks"], key=lambda item: item["id"])
    return hashlib.sha256(prompt.encode()).hexdigest(), c.digest([messages[0].content, view])


def summarize(
    cases: tuple[Case, ...], receipts: list[dict[str, object]], calls: list[dict[str, object]]
) -> dict[str, object]:
    expected = {str(case.slot["run_id"]): case for case in cases}
    if len({r.get("run_id") for r in receipts}) != len(receipts):
        raise c.Refused("heldout_duplicate_receipt")
    for r in receipts:
        case = expected.get(str(r.get("run_id")))
        if (
            case is None
            or (r.get("fault_id"), r.get("trial")) != (case.fault_id, case.trial)
            or r.get("evidence_kind") != "FAKE_KERNEL"
        ):
            raise c.Refused("heldout_unregistered_receipt")
    per_class = {}
    for fault in c.FAULT_IDS:
        rows = [r for r in receipts if r["fault_id"] == fault]
        per_class[fault] = {
            "cohort": "held_policy" if fault in HELD else "repair_target",
            "planned": 2,
            "attempted": len(rows),
            **{
                key: sum(r.get("outcome") == key for r in rows)
                for key in ("recovered", "honest_report", "failed", "blocked")
            },
            "false_online": sum(int(str(r.get("false_online", 0))) for r in rows),
            "cleanup_verified": sum(r.get("cleanup_verified") is True for r in rows),
        }
    repair = [r for r in receipts if r["fault_id"] not in HELD]
    held = [r for r in receipts if r["fault_id"] in HELD]
    recovered = sum(r.get("outcome") == "recovered" for r in repair)
    held_pass = sum(
        r.get("outcome") == "honest_report"
        and r.get("failure") is None
        and r.get("false_online") == 0
        and r.get("cleanup_verified") is True
        for r in held
    )
    durations = [float(str(call["duration_ms"])) for call in calls]
    return {
        "evidence_kind": KIND,
        "label": "sim held-out compound-fault robustness",
        "profile_id": profile.PROFILE_ID,
        "adapter_enabled": False,
        "reroute": "N/A: not in the 13-action grammar",
        "planned_cases": 20,
        "attempted_cases": len(receipts),
        "complete": len(receipts) == len(cases),
        "repair_target": {
            "recovered": recovered,
            "planned": 14,
            "attempted": len(repair),
            "recovery_rate": recovered / 14,
        },
        "held_policy": {
            "passed": held_pass,
            "planned": 6,
            "attempted": len(held),
            "policy_rate": held_pass / 6,
        },
        "false_online": sum(int(str(r.get("false_online", 0))) for r in receipts),
        "cleanup_verified": sum(r.get("cleanup_verified") is True for r in receipts),
        "model_calls": len(calls),
        "median_call_latency_ms": statistics.median(durations) if durations else None,
        "calls_over_8s": sum(d > 8000 for d in durations),
        "per_class": per_class,
        "unattempted": [
            case.case_id
            for key, case in expected.items()
            if key not in {r.get("run_id") for r in receipts}
        ],
        "call_log": calls,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-commit", required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        raise c.Refused("heldout_inference_requires_linux_sandbox")
    c.text(args.freeze_commit, r"[0-9a-f]{40}")
    verify_frozen(ROOT)
    cases = load_cases(ROOT / DATA)
    out = args.output.absolute()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    c.save(
        out / "reservation.json",
        {
            "kind": KIND,
            "freeze_commit": args.freeze_commit,
            "manifest_sha256": c.file_hash(ROOT / MANIFEST),
            "model_calls_before_freeze": 0,
            "case_ids": [case.case_id for case in cases],
            "at": time.time(),
        },
        exclusive=True,
    )
    receipts: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []
    for case in cases:
        c.audit(out, "case_attempt_reserved", {"case_id": case.case_id})
        backend = HeldoutBackend(out / "kernel" / case.case_id, case)
        receipt = run_case(backend, out / "receipts" / case.case_id)
        receipts.append(receipt)
        calls.extend(backend.calls)
        summary = summarize(cases, receipts, calls)
        summary.update(
            freeze_commit=args.freeze_commit, manifest_sha256=c.file_hash(ROOT / MANIFEST)
        )
        c.save(out / "summary.json", summary)
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "outcome": receipt["outcome"],
                    "false_online": receipt["false_online"],
                    "cleanup_verified": receipt["cleanup_verified"],
                }
            ),
            flush=True,
        )
        if receipt["false_online"] or receipt["cleanup_verified"] is not True:
            break


if __name__ == "__main__":
    main()
