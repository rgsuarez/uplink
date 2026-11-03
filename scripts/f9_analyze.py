"""Read the immutable lab snapshot; never rewrite or rescore its records."""

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def analyze(snapshot: Path, output: Path) -> dict[str, Any]:
    text = (snapshot / "run/server.time").read_text()
    calls: dict[int, dict[str, Any]] = {}
    keep: float | None = None
    for number, line in enumerate(text.splitlines(), 1):
        selected = re.search(r"f_keep = ([\d.]+)", line)
        if selected:
            keep = float(selected[1])
        elif "selected slot by LRU" in line:
            keep = None
        task = re.search(r"id\s+(\d+) \| task (\d+) \|", line)
        if task:
            slot, task_id = int(task[1]), int(task[2])
            if "processing task," in line:
                calls[task_id] = {
                    "task": task_id,
                    "slot": slot,
                    "line": number,
                    "f_keep": keep,
                    "cancelled": False,
                    "prefill_ms": None,
                    "prefill_tokens": None,
                    "decode_ms": None,
                    "decode_tokens": None,
                }
            timing = re.search(
                r"(prompt eval|       eval) time =\s*([\d.]+) ms /\s*(\d+) tokens", line
            )
            if timing and task_id in calls:
                prefix = "prefill" if timing[1] == "prompt eval" else "decode"
                calls[task_id][prefix + "_ms"] = float(timing[2])
                calls[task_id][prefix + "_tokens"] = int(timing[3])
        cancelled = re.search(r"cancel task, id_task = (\d+)", line)
        if cancelled and int(cancelled[1]) in calls:
            calls[int(cancelled[1])]["cancelled"] = True
    complete = [
        c for c in calls.values() if c["prefill_ms"] is not None and c["decode_ms"] is not None
    ]
    if not calls:
        raise ValueError("missing_server_call_evidence")
    summary: dict[str, Any] = {
        "snapshot_incomplete": True,
        "launched_calls": len(calls),
        "completed_timing_pairs": len(complete),
        "cancelled_calls": sum(c["cancelled"] for c in calls.values()),
        "double_bos_warnings": text.count("final prompt starts with 2 BOS tokens"),
        "http_500_server_exceptions": text.count('"code":500'),
        "prefill_seconds": sum(c["prefill_ms"] for c in complete) / 1000,
        "decode_seconds": sum(c["decode_ms"] for c in complete) / 1000,
        "prefill_tokens": sum(c["prefill_tokens"] for c in complete),
        "decode_tokens": sum(c["decode_tokens"] for c in complete),
        "timing_scope": (
            "All completed timing pairs, including grammar probe and partial relaxed pass; "
            "cancelled work excluded"
        ),
        "legacy_harness_sha": None,
        "legacy_harness_provenance": (
            "Snapshot lacks checkout SHA and harness source; bench wrapper bytes are retained"
        ),
        "evaluations": {},
    }
    for label in ("parity", "relaxed"):
        rows = [
            json.loads(s)
            for s in (snapshot / f"run/eval-{label}/results.jsonl").read_text().splitlines()
        ]
        summary["evaluations"][label] = {
            "rows_completed_at_snapshot": len(rows),
            "fixed_verified": sum(
                r["result"]["outcome"] == "fixed" and r["verified_green"] for r in rows
            ),
            "provider_errors_original": sum(
                r["result"]["outcome"] == "provider_error" for r in rows
            ),
            "green_without_fixed_terminal": sum(
                r["verified_green"] and r["result"]["outcome"] != "fixed" for r in rows
            ),
            "per_task": [
                {
                    "id": r["task_id"],
                    "outcome_original": r["result"]["outcome"],
                    "verified_green": r["verified_green"],
                    "elapsed_s": r["result"]["elapsed_s"],
                }
                for r in rows
            ],
        }
    output.mkdir(parents=True, exist_ok=False)
    (output / "analysis.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (output / "per-call.csv").open("w") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(next(iter(calls.values()))), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(calls.values())
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.snapshot, args.out), indent=2))
