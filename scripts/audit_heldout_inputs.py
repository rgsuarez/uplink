"""No-inference fixture and semantic-overlap audit, frozen before the held-out run."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import model_in_loop_heldout as heldout

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_injectors as plans
from offline_agent.uplink.top10_score import online

PRIOR_SHA256 = "a3074b9f0c8ddc5e190f50b1e6f0bfbe8d98487c1ac6ec7e7a6af1164d8b1ed0"


class ObservationOnly:
    def __init__(self, kernel: heldout.FixtureKernel, snapshot: dict[str, object]) -> None:
        self.kernel, self.snapshot = kernel, snapshot

    def observe(self) -> dict[str, object]:
        return {"facts": self.kernel.facts(self.snapshot)}


def audit(prior_path: Path, workspace: Path) -> dict[str, object]:
    if hashlib.sha256(prior_path.read_bytes()).hexdigest() != PRIOR_SHA256:
        raise c.Refused("heldout_prior_evidence_hash_mismatch")
    prior = c.read(prior_path)
    receipts = prior["receipts"]
    records = c.obj(prior["trial_records"])
    if not isinstance(receipts, list) or len(receipts) != 21:
        raise c.Refused("heldout_prior_21_receipts_required")
    prior_rows = []
    for raw in receipts:
        receipt = c.obj(raw)
        trial = c.obj(records[str(receipt["run_id"])])
        facts = c.obj(trial["observe-1.json"])["facts"]
        oracle = c.obj(trial["oracle-1.json"]) | {"checked_at": time.time()}
        prompt_hash, semantic_hash = heldout.semantic_input(facts, oracle)
        actual = str(c.obj(trial["model-1.json"])["prompt"])
        if hashlib.sha256(actual.encode()).hexdigest() != prompt_hash:
            raise c.Refused("heldout_adopted_prompt_replay_mismatch")
        prior_rows.append(
            {
                "fault_id": receipt["fault_id"],
                "trial": receipt["trial"],
                "prompt_sha256": prompt_hash,
                "semantic_sha256": semantic_hash,
            }
        )
    workspace.mkdir(mode=0o700, parents=True, exist_ok=False)
    rows = []
    for case in heldout.load_cases(heldout.ROOT / heldout.DATA):
        directory = workspace / case.case_id
        directory.mkdir(mode=0o700)
        kernel = heldout.FixtureKernel(directory, case)
        kernel.initialize()
        snapshot = kernel.capture()
        if not c.baseline_ok(kernel.facts(snapshot)):
            raise c.Refused("heldout_reference_baseline_unhealthy")
        for fault in case.injected_faults:
            plans.inject(kernel, fault, snapshot)
        facts = kernel.facts(snapshot)
        if not kernel.negative_control(case.fault_id, facts):
            raise c.Refused("heldout_reference_negative_control_failed")
        # The original oracle is obtained from the original backend, using only
        # a frozen fake-kernel observation. No model or watchdog is needed here.
        backend = heldout.HeldoutBackend(directory, case)

        backend.engine = ObservationOnly(kernel, snapshot)  # type: ignore[assignment]
        oracle = backend.oracle()
        prompt_hash, semantic_hash = heldout.semantic_input(facts, oracle)
        if online(oracle, time.time()):
            raise c.Refused("heldout_negative_oracle_unexpectedly_online")
        for repair in case.reference_repairs:
            plans.repair(kernel, repair, snapshot)
        repaired = c.baseline_ok(kernel.facts(snapshot))
        if repaired != (case.cohort == "repair_target"):
            raise c.Refused("heldout_reference_solution_or_hold_mismatch")
        if not c.baseline_ok(plans.restore(kernel, snapshot)):
            raise c.Refused("heldout_reference_cleanup_failed")
        rows.append(
            {
                "case_id": case.case_id,
                "cohort": case.cohort,
                "injected_faults": list(case.injected_faults),
                "facts": facts,
                "check_order": list(case.check_order),
                "prompt_sha256": prompt_hash,
                "semantic_sha256": semantic_hash,
                "negative_control_verified": True,
                "reference_repairs_verified": repaired,
                "reference_cleanup_verified": True,
                "exact_prior_overlap": [r for r in prior_rows if r["prompt_sha256"] == prompt_hash],
                "semantic_prior_overlap": [
                    r for r in prior_rows if r["semantic_sha256"] == semantic_hash
                ],
            }
        )
    for row in rows:
        row["semantic_within_set_overlap"] = [
            other["case_id"]
            for other in rows
            if other["case_id"] != row["case_id"]
            and other["semantic_sha256"] == row["semantic_sha256"]
        ]
    return {
        "kind": heldout.KIND,
        "model_calls": 0,
        "prior_result_sha256": PRIOR_SHA256,
        "cases_sha256": c.file_hash(heldout.ROOT / heldout.DATA),
        "semantic_definition": (
            "Unchanged system text and user JSON; online-check pairs sorted by id. "
            "Surface metadata and grammar snapshot id are excluded because they do not "
            "change diagnostic semantics."
        ),
        "prior_unique_prompts": len({r["prompt_sha256"] for r in prior_rows}),
        "prior_unique_semantic_inputs": len({r["semantic_sha256"] for r in prior_rows}),
        "heldout_unique_prompts": len({r["prompt_sha256"] for r in rows}),
        "heldout_unique_semantic_inputs": len({r["semantic_sha256"] for r in rows}),
        "semantic_prior_overlap_cases": [r["case_id"] for r in rows if r["semantic_prior_overlap"]],
        "exact_prior_overlap_cases": [r["case_id"] for r in rows if r["exact_prior_overlap"]],
        "cases": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.prior, args.workspace)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}))
