"""Read-only presentation of pinned MODEL_IN_LOOP_SIM records. No inference or host probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

from offline_agent.contracts import read_json
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_score as score
from offline_agent.uplink.model_identity import GGUF_NAME, GGUF_SHA256
from offline_agent.uplink.real_transport import parse_oracle
from offline_agent.uplink.top10_model import parse

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "baseline": (
        "artifacts/uplink/mil-base-fix-20261101-132128/result.json",
        "06859b248badb41a6070bfbe024375017cb0c88feef88e2315284a73afc71455",
        "authorized-repair-v1",
    ),
    "rejected-combined": (
        "artifacts/uplink/mil-proxy-fix-20261101-141659/result.json",
        "ee25f372da39f47838783cedf4951535bace1d6cc077d7d7a8308c32cd2902b9",
        "authorized-proxy-repair-v1:combined",
    ),
    "rejected-description": (
        "artifacts/uplink/mil-proxy-fix-20261101-142618/result.json",
        "05e969c633701ece1ab98b604e04df1f74233becfd0682e175442db46ab1a2fb",
        "authorized-proxy-repair-v1:description_only",
    ),
    "adopted": (
        "artifacts/uplink/mil-proxy-fix-20261101-143834/result.json",
        "a3074b9f0c8ddc5e190f50b1e6f0bfbe8d98487c1ac6ec7e7a6af1164d8b1ed0",
        "snapshot-restore-descriptions-v1",
    ),
}
OUTCOMES = ("recovered", "honest_report", "failed", "blocked")
FAULTS = {"F04": "bad configured DNS", "F06": "stale proxy"}
DISPLAY_FACTS = (
    "dns_matches_snapshot",
    "proxy_matches_snapshot",
    "ipv4_path_ok",
    "protected_services_unchanged",
    "persistent_baseline_equal",
)
LABEL = (
    "MODEL_IN_LOOP_SIM | recorded BASE Muse Glimmer Q4 | post-hoc development evidence\n"
    "Adopted descriptions tuned after F06/F07 failures; not held-out and not live Wi-Fi.\n"
    "Replay reads saved files only: zero new model calls or host probes.\n"
)


def load_run(name: str, root: Path = ROOT) -> dict[str, object]:
    path, digest, profile = RUNS[name]
    content = c.read_bytes(root / path, maximum=2_000_000)
    if hashlib.sha256(content).hexdigest() != digest:
        raise c.Refused("sim_replay_source_hash_mismatch")
    row = c.parse_json(content)
    model = c.obj(row.get("model"))
    if (
        row.get("evidence_kind") != "MODEL_IN_LOOP_SIM"
        or row.get("adapter_enabled") is not False
        or row.get("profile_id") != profile
        or model.get("filename") != GGUF_NAME
        or model.get("sha256") != GGUF_SHA256
        or model.get("bytes") != 16756683904
    ):
        raise c.Refused("sim_replay_base_simulation_required")
    receipts = row.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != 21:
        raise c.Refused("sim_replay_complete_population_required")
    expected = {(fault, trial) for fault in c.BENCH_FAULT_IDS for trial in (1, 2, 3)}
    seen: set[tuple[str, int]] = set()
    ids: set[str] = set()
    counts = dict.fromkeys(OUTCOMES, 0)
    false_online = 0
    for value in receipts:
        receipt = c.obj(value)
        fault, trial = receipt.get("fault_id"), receipt.get("trial")
        if not isinstance(fault, str) or type(trial) is not int or (fault, trial) not in expected:
            raise c.Refused("sim_replay_registered_population_required")
        run_id = c.text(receipt.get("run_id"), r"[0-9a-f]{12}")
        if (fault, trial) in seen or run_id in ids or receipt.get("outcome") not in OUTCOMES:
            raise c.Refused("sim_replay_duplicate_or_invalid_trial")
        seen.add((fault, trial))
        ids.add(run_id)
        counts[str(receipt["outcome"])] += 1
        false_online += int(c.number(receipt.get("false_online"), 0, 4))
    summary = c.obj(row.get("summary"))
    if (
        seen != expected
        or summary.get("counts") != counts
        or summary.get("false_online") != false_online
        or summary.get("model_calls") != 21
    ):
        raise c.Refused("sim_replay_summary_disagrees_with_receipts")
    return row


def summary(root: Path = ROOT) -> str:
    lines = [
        LABEL,
        "| Recorded run | Recovered | Honest reports | False ONLINE |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in RUNS:
        row = load_run(name, root)
        totals = c.obj(row["summary"])
        counts = c.obj(totals["counts"])
        lines.append(
            f"| {name} | {counts['recovered']}/21 | {counts['honest_report']} | "
            f"{totals['false_online']} |"
        )
    lines += [
        "",
        "Seven fault classes, three trials each. Both 6/21 variants were rejected.",
        "Zero false ONLINE in these four scored runs is an observation, not a universal guarantee.",
        "Live Bench F04 trial: PENDING separate Richard and Kaylin authorization.",
    ]
    return "\n".join(lines) + "\n"


def artifact(
    records: dict[str, object], receipt: dict[str, object], name: str
) -> dict[str, object]:
    row = c.obj(records.get(name))
    digest = hashlib.sha256(
        (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode()
    ).hexdigest()
    if c.obj(receipt.get("artifact_sha256")).get(name) != digest:
        raise c.Refused("sim_replay_artifact_hash_mismatch")
    return row


def oracle_text(value: dict[str, object]) -> str:
    checked = c.number(value.get("checked_at"), 0, 4_000_000_000)
    # Historical validation at the recorded epoch. Never represents connectivity now.
    with patch("offline_agent.uplink.real_transport.time.time", return_value=checked):
        parsed = parse_oracle(read_json(json.dumps(value, allow_nan=False)))
    return ", ".join(e.id + "=" + ("PASS" if e.ok else "FAIL") for e in parsed.evidence)


def trajectory(name: str, fault: str, trial: int, root: Path = ROOT) -> str:
    row = load_run(name, root)
    receipts = row["receipts"]
    if fault not in FAULTS or trial not in (1, 2, 3) or not isinstance(receipts, list):
        raise c.Refused("sim_replay_demo_selection_refused")
    receipt = next(
        c.obj(v) for v in receipts if c.obj(v)["fault_id"] == fault and c.obj(v)["trial"] == trial
    )
    run_id = str(receipt["run_id"])
    records = c.obj(c.obj(row.get("trial_records")).get(run_id))
    decisions = int(c.number(receipt.get("decision_requests"), 1, 4))
    lines = [
        LABEL,
        f"## {fault}: {FAULTS[fault]} | {name} | trial {trial}",
        f"Source result SHA-256: `{RUNS[name][1]}`",
        f"Recorded run ID: `{run_id}`",
        "Fault engine: FAKE_KERNEL. Real model completion was recorded by the simulation wrapper.",
        "",
    ]
    for index in range(1, decisions + 1):
        observation = artifact(records, receipt, f"observe-{index}.json")
        oracle = artifact(records, receipt, f"oracle-{index}.json")
        emission = artifact(records, receipt, f"model-{index}.json")
        facts = c.measured(observation.get("facts"))
        snapshot = c.text(observation.get("snapshot_id"), r"[0-9a-f]{64}")
        raw = emission.get("raw_text")
        if (
            not isinstance(raw, str)
            or emission.get("request_count") != 1
            or emission.get("failure") is not None
        ):
            raise c.Refused("sim_replay_complete_model_record_required")
        action = parse(raw, snapshot)
        if emission.get("action") != action or emission.get("admitted_tools") != list(c.TOOLS):
            raise c.Refused("sim_replay_model_projection_changed")
        duration = c.number(emission.get("duration_ms"), 0, 60000)
        lines += [
            f"### Decision {index}",
            "Observed: " + ", ".join(k + "=" + str(facts[k]).lower() for k in DISPLAY_FACTS),
            "Before: " + oracle_text(oracle),
            f"Model selected `{action['tool']}`; reason code `{action['reason']}`.",
            "All 13 tools remained in the grammar. "
            f"Recorded model-call time: {duration / 1000:.3f} s.",
        ]
        if action["tool"] in {"report", "give_up"}:
            if receipt["outcome"] != "honest_report":
                raise c.Refused("sim_replay_report_outcome_mismatch")
            lines.append(
                "REPORT: no model-selected repair. Human attention requested; no recovery credit."
            )
            continue
        final = artifact(records, receipt, f"verify-observation-{index}.json")
        after = artifact(records, receipt, f"verify-oracle-{index}.json")
        checked = max(
            c.number(final.get("checked_at"), 0, 4_000_000_000),
            c.number(after.get("checked_at"), 0, 4_000_000_000),
        )
        with patch("offline_agent.uplink.real_transport.time.time", return_value=checked):
            recovered = score.recovered(final, after, checked)
        if receipt["outcome"] == "recovered" and not recovered:
            raise c.Refused("sim_replay_recovery_proof_refused")
        lines += [
            "After: " + oracle_text(after),
            "Verifier: "
            + (
                "RECOVERED in the recorded simulation, before cleanup."
                if recovered
                else "No verified model recovery in this step."
            ),
        ]
    cleanup = receipt.get("cleanup_verified")
    if type(cleanup) is not bool:
        raise c.Refused("sim_replay_cleanup_evidence_required")
    lines += [
        "",
        f"Recorded outcome: `{receipt['outcome']}`; false ONLINE: {receipt['false_online']}.",
        "Cleanup: "
        + ("recorded verified" if cleanup else "NOT verified")
        + "; never counted as model recovery.",
        "This is saved evidence replay. No network state changed and no model ran during replay.",
    ]
    return "\n\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", choices=tuple(RUNS), default="adopted")
    parser.add_argument("--fault", choices=tuple(FAULTS), default="F04")
    parser.add_argument("--trial", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        print(summary() if args.summary else trajectory(args.run, args.fault, args.trial), end="")
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        reason = str(exc) if isinstance(exc, c.Refused) else type(exc).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,119}", reason):
            reason = type(exc).__name__
        parser.exit(2, "sim_replay_refused:" + reason + "\n")


if __name__ == "__main__":
    main()
