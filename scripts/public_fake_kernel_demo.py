"""Run the public CPU fake-kernel qualification with no model or live authority."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from offline_agent.uplink import top10_contract as c  # noqa: E402
from offline_agent.uplink.top10_backends import SandboxSession  # noqa: E402
from offline_agent.uplink.top10_suite import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        parser.error("run through scripts/top10_local_sandbox.py")
    out = args.output.absolute()
    if out.exists() or not out.is_relative_to(ROOT / "artifacts/uplink"):
        parser.error("a new artifact directory is required")
    c.load_registry(ROOT)
    manifest = {
        "kind": "SYNTHETIC_NO_LIVE_AUTHORITY",
        "registration": "public-cpu-fake-kernel-v1",
        "slots": [
            {"fault_id": fault, "trial": trial,
             "run_id": c.digest([fault, trial, "public-cpu-fake-kernel-v1"])[:12]}
            for fault in c.FAULT_IDS for trial in (1, 2, 3)
        ],
    }
    session = SandboxSession(out / "kernel", manifest)
    result = run(session, manifest, out / "receipts")
    public = {
        "evidence_kind": "FAKE_KERNEL", "model_calls": 0, "host_contacts": 0,
        "registered_slots": 30, "selected_slots": 21, "held_slots": 9,
        "recorded_trials": result["recorded_trials"], "counts": result["counts"],
        "false_online": result["false_online"], "complete": result["complete"],
        "safety_gate_passed": result["safety_gate_passed"], "failure": result["failure"],
        "disclaimer": "Synthetic policy and fake kernel only; no model or live recovery evidence.",
    }
    (out / "public-summary.json").write_text(json.dumps(public, indent=2) + "\n")
    print(json.dumps(public))
    if (result["safety_gate_passed"] is not True or result["recorded_trials"] != 21
            or result["false_online"] != 0 or result["counts"]["recovered"] != 21):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
