"""MODEL_IN_LOOP_SIM: the REAL Glimmer BASE model drives the frozen TOP10 harness against the
FAKE_KERNEL (simulated) fault engine. Needs a llama-server on synthetic address:8080.

This is NOT live Wi-Fi evidence and NOT the TOP10 result. It measures the model's
decisions (repair / report / false ONLINE) on the same frozen prompt, grammar,
fact projection and scoring as the live run. No frozen file is modified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("UPLINK_TOP10_SANDBOX", "1")

from offline_agent.uplink import top10_contract as c  # noqa: E402
from offline_agent.uplink import top10_model  # noqa: E402
from offline_agent.uplink.top10_backends import SandboxBackend, SandboxSession  # noqa: E402
from offline_agent.uplink.top10_suite import run  # noqa: E402

CALL_LOG: list[dict[str, object]] = []


class ModelBackend(SandboxBackend):
    def choose(  # type: ignore[override]
        self, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
    ) -> dict[str, object]:
        # Real model call, frozen prompt/grammar, frozen live 8 s model timeout.
        t0 = time.monotonic()
        record = top10_model.generate(ROOT, snapshot_id, facts, oracle, history, timeout)
        record["duration_ms"] = int((time.monotonic() - t0) * 1000)
        CALL_LOG.append(
            {
                "fault_id": self.slot["fault_id"],
                "trial": self.slot["trial"],
                "duration_ms": record.get("duration_ms"),
                "over_live_8s": (record.get("duration_ms") or 0) > 8000,
                "failure": record.get("failure"),
                "action": record.get("action"),
                "raw_text": record.get("raw_text"),
            }
        )
        return record


class ModelSession(SandboxSession):
    def backend(self, slot: dict[str, object]) -> ModelBackend:  # type: ignore[override]
        c.require_bench_fault(slot.get("fault_id"))
        self.attempt.require_slot(slot, self.manifest)
        return ModelBackend(self.base / "runs" / str(slot["run_id"]), slot, self.behavior)


def manifest(trials: tuple[int, ...]) -> dict[str, object]:
    return {
        "kind": "SYNTHETIC_NO_LIVE_AUTHORITY",
        "slots": [
            {"fault_id": f, "trial": t, "run_id": c.digest([f, t, "mil"])[:12]}
            for f in c.FAULT_IDS
            for t in (1, 2, 3)
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--label", default="model-in-loop-sim")
    args = ap.parse_args()
    out = args.output.absolute()
    m = manifest((1, 2, 3))
    session = ModelSession(out / "kernel", m)
    started = time.time()
    result = run(session, m, out / "receipts")
    per: dict[str, dict[str, object]] = defaultdict(
        lambda: {"trials": 0, "recovered": 0, "honest_report": 0, "failed": 0, "blocked": 0,
                 "false_online": 0, "actions": []}
    )
    for p in sorted((out / "receipts").glob("*/receipt.json")):
        r = json.loads(p.read_text())
        row = per[r["fault_id"]]
        row["trials"] += 1  # type: ignore[operator]
        row[r["outcome"]] += 1  # type: ignore[operator]
        row["false_online"] += r.get("false_online", 0)  # type: ignore[operator]
        row["actions"].append({"trial": r["trial"], "outcome": r["outcome"],  # type: ignore[union-attr]
                               "model_actions": r.get("model_actions"), "failure": r.get("failure")})
    over = sum(1 for x in CALL_LOG if x["over_live_8s"])
    summary = {
        "evidence_kind": "MODEL_IN_LOOP_SIM",
        "disclaimer": "Real BASE model, simulated fault engine (FAKE_KERNEL). Not live Wi-Fi, not TOP10.",
        "label": args.label,
        "started_at": started,
        "finished_at": time.time(),
        "suite_complete": result.get("complete"),
        "suite_failure": result.get("failure"),
        "counts": result.get("counts"),
        "false_online": result.get("false_online"),
        "model_calls": len(CALL_LOG),
        "calls_over_live_8s_cap": over,
        "per_fault": per,
        "call_log": CALL_LOG,
    }
    (out / "MODEL-IN-LOOP-SUMMARY.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("evidence_kind", "suite_complete", "suite_failure",
          "counts", "false_online", "model_calls", "calls_over_live_8s_cap")}, default=str))
    for f, row in per.items():
        print(f, {k: v for k, v in row.items() if k != "actions"})


if __name__ == "__main__":
    main()
