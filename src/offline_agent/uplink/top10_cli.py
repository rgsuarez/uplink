"""Plan-only defaults. Live commands require a current GO carrying KAYLIN-OK."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_backends import LiveSession, SandboxSession
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_suite import run

ROOT = Path(__file__).resolve().parents[3]


def plan(fault_id: str | None = None) -> dict[str, object]:
    rows = c.cases(ROOT)
    return {
        "mode": "PLAN_ONLY",
        "target_contacts": 0,
        "base_model_calls": 0,
        "arm": "BASE",
        "live_requires": "current KAYLIN-OK GO, owner handback and Richard root approval",
        "faults": [row for row in rows if fault_id is None or row["id"] == fault_id],
        "registered_trial_count": 30 if fault_id is None else 3,
        "trial_count": c.BENCH_TRIALS
        if fault_id is None
        else 0
        if fault_id in c.HELD_FAULTS
        else 3,
        "held_faults": c.execution_profile()["held_faults"],
        "selected_fault_ids": list(c.BENCH_FAULT_IDS),
        "portal_is_local_simulation": True,
        "maximum_minutes": 100,
    }


def live(
    go: Path | None,
    owner: Path | None,
    root_approval: Path | None = None,
    *,
    cleanup: bool = False,
) -> LiveSession:
    if go is None or owner is None or not go.name.startswith("GO-"):
        raise c.Refused("top10_named_go_and_owner_handback_required")
    if root_approval is None:
        raise c.Refused("top10_richard_root_approval_required")
    manifest_path = ROOT / "harness/top10/FROZEN.json"
    manifest = c.verify_manifest(ROOT, manifest_path)
    window = c.read(go, private=True)
    raw_owner = c.read_bytes(owner, maximum=16_384, private=True).decode()
    raw_approval = c.read_bytes(root_approval, maximum=16_384, private=True).decode()
    return LiveSession(
        ROOT, manifest, c.file_hash(manifest_path), window, raw_owner, raw_approval, cleanup=cleanup
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--go", type=Path)
    parser.add_argument("--owner-handback", type=Path)
    parser.add_argument("--root-approval", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sandbox-state", type=Path)
    args = parser.parse_args()
    try:
        if not args.execute and not args.sandbox:
            print(json.dumps(plan(), indent=2))
            return
        if args.output is None:
            raise c.Refused("top10_output_required")
        path = ROOT / "harness/top10/FROZEN.json"
        manifest = c.verify_manifest(ROOT, path)
        if args.sandbox:
            if (
                args.execute
                or args.go
                or args.owner_handback
                or args.root_approval
                or args.sandbox_state is None
            ):
                raise c.Refused("top10_separate_sandbox_required")
            session = SandboxSession(args.sandbox_state, manifest)
            result = run(session, manifest, args.output)
        else:
            result = run(
                live(args.go, args.owner_handback, args.root_approval), manifest, args.output
            )
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "evidence_kind",
                        "recorded_trials",
                        "counts",
                        "false_online",
                        "real_base_calls",
                        "failure",
                    )
                },
                indent=2,
            )
        )
        if result.get("safety_gate_passed") is not True:
            raise SystemExit(2)
    except Exception as exc:
        print(json.dumps({"refused": label(exc)}))
        raise SystemExit(2) from None


def fault_main(fault_id: str) -> None:
    parser = argparse.ArgumentParser(description="Frozen " + fault_id + ": plan-only by default")
    parser.add_argument("--phase", choices=("inject", "verify", "restore"), default="verify")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--go", type=Path)
    parser.add_argument("--owner-handback", type=Path)
    parser.add_argument("--root-approval", type=Path)
    args = parser.parse_args()
    try:
        if not args.execute:
            print(json.dumps(plan(fault_id), indent=2))
            return
        c.require_bench_fault(fault_id)
        session = live(
            args.go, args.owner_handback, args.root_approval, cleanup=args.phase == "restore"
        )
        slots = session.manifest.get("slots")
        if not isinstance(slots, list):
            raise c.Refused("top10_slots_required")
        slot = next(
            (
                c.obj(s)
                for s in slots
                if c.obj(s).get("run_id") == args.run_id and c.obj(s).get("fault_id") == fault_id
            ),
            None,
        )
        if slot is None:
            raise c.Refused("top10_registered_fault_slot_required")
        if args.phase != "restore":
            session.qualify(str(slot["run_id"]))
        result = session.backend(slot).root(args.phase, {})
        # Private coordinates and raw evidence are retained by the root worker.
        print(
            json.dumps(
                {"run_id": args.run_id, "phase": args.phase, "lifecycle": result.get("lifecycle")}
            )
        )
    except Exception as exc:
        print(json.dumps({"refused": label(exc)}))
        raise SystemExit(2) from None
