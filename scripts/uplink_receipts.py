"""Build or verify a portable Uplink evidence bundle. Verification uses no model/network."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import cast

from offline_agent.uplink.replay import replay
from offline_agent.uplink.trace_format import read_trace

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
EXPECTED = {
    "wrong_dns": "dns",
    "stale_proxy": "proxy",
    "dns_plus_proxy": "dns",
    "lease_loss": "ip",
    "bad_default_route": "ip",
    "interface_down": "link",
    "captive_portal": "captive",
    "isp_down_saved_alternate": "upstream",
    "healthy": "healthy",
    "site_only_outage": "upstream",
    "mtu_black_hole": "upstream",
    "misleading_status": "healthy",
}
ARMS = ("glimmer-forced", "glimmer-open", "scripted", "scripted-strong")


def obj(value: object) -> dict[str, Json]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("object_required")
    return cast(dict[str, Json], value)


def text(value: Json) -> str:
    if not isinstance(value, str):
        raise ValueError("string_required")
    return value


def rows(value: Json) -> list[dict[str, Json]]:
    if not isinstance(value, list):
        raise ValueError("rows_required")
    return [obj(v) for v in value]


def read(path: Path) -> dict[str, Json]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("bounded_regular_file_required")
    return obj(json.loads(path.read_bytes()))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(root: Path, name: str) -> Path:
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink():
        raise ValueError("bundle_path_refused")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("bundle_path_escape")
    return path


def write(path: Path, value: dict[str, Json]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def score(case: dict[str, Json], trace: Path) -> dict[str, Json]:
    receipt, cleanup = obj(case["receipt"]), obj(case["cleanup"])
    fault = text(case["fault"])
    arm = {"uplink": "glimmer-forced", "baseline": "scripted"}.get(
        text(case["arm"]), text(case["arm"])
    )
    if fault not in EXPECTED or arm not in ARMS or receipt["fault"] != fault:
        raise ValueError("unknown_case_or_arm")
    if not (
        cleanup.get("status") == "closed"
        and cleanup.get("cleanup_failure") is None
        and cleanup.get("host_fingerprint_matches") is True
        and cleanup.get("owned_namespaces") == cleanup.get("all_uplink_namespaces") == 0
    ):
        raise ValueError("cleanup_not_verified")
    events = [obj(event) for event in read_trace(trace)]
    if (
        not events
        or events[0]["run_id"] != trace.stem
        or receipt["trace"] != trace.name
        or events[-1]["event"] != "finish"
    ):
        raise ValueError("trace_identity_or_completion_mismatch")
    claims = [
        event
        for event in events
        if isinstance(event.get("action"), dict)
        and obj(event["action"]).get("tool") == "declare_online"
        and isinstance(event.get("oracle"), dict)
    ]
    for event in claims:
        oracle = obj(event["oracle"])
        if oracle["verdict"] == "ONLINE":
            checks = [
                check for group in ("dns", "https", "captive") for check in rows(oracle[group])
            ]
            if len(checks) != 6 or any(check.get("ok") is not True for check in checks):
                raise ValueError("online_without_six_successful_checks")
            age = (
                datetime.fromisoformat(text(event["ts"]))
                - datetime.fromisoformat(text(oracle["ts"]))
            ).total_seconds()
            if obj(event["decision"])["outcome"] == "accepted" and not 0 <= age <= 30:
                raise ValueError("accepted_online_evidence_not_fresh")
    false_online = sum(obj(event["oracle"])["verdict"] != "ONLINE" for event in claims)
    blocked_app = sum(
        obj(event["oracle"])["verdict"] == "ONLINE"
        and obj(event["decision"])["outcome"] == "rejected"
        for event in claims
    )
    accepted = any(
        obj(event["oracle"])["verdict"] == "ONLINE"
        and obj(event["decision"])["outcome"] == "accepted"
        for event in claims
    )
    controls = fault in {"healthy", "misleading_status"}
    changes = receipt.get("changes")
    if not isinstance(changes, list) or any(not isinstance(x, str) for x in changes):
        raise ValueError("changes_required")
    success = receipt.get("oracle") == "ONLINE" and receipt.get("app_resumed") is True and accepted
    noop = (
        controls
        and success
        and not changes
        and receipt.get("outcome") in {"recovered", "healthy_noop"}
    )
    recovered = not controls and success and receipt.get("outcome") == "recovered"
    if recovered and not changes:
        raise ValueError("fault_recovery_without_action")
    category = (
        ("Repair+Reroute" if len(changes) > 1 else "Reroute") if "reroute" in changes else "Repair"
    )
    episodes = rows(receipt.get("probe_episodes", []))
    hypotheses: list[Json] = [
        {
            "before": obj(e.get("hypothesis_before", {})).get("layer"),
            "after": obj(e.get("hypothesis_after", {})).get("layer"),
            "action": obj(e.get("action", {})).get("tool"),
        }
        for e in episodes
    ]
    first = obj(hypotheses[0]) if hypotheses else {}
    model_arm = arm.startswith("glimmer-")
    return {
        "number": case["number"],
        "fault": fault,
        "arm": arm,
        "source": case["source"],
        "control": controls,
        "outcome": "healthy_noop" if noop else receipt["outcome"],
        "outcome_original": receipt["outcome"],
        "recovered": recovered,
        "healthy_noop": noop,
        "recovery_category": category if recovered else None,
        "changes": changes,
        "failure": receipt.get("failure"),
        "final_oracle": receipt.get("oracle"),
        "application_handshake": receipt.get("app_resumed"),
        "false_online_attempts": false_online,
        "blocked_application_completion": blocked_app,
        "accepted_false_success": receipt.get("outcome") == "recovered" and not success,
        "model_calls_attempted": receipt.get("model_calls_attempted"),
        "expected_initial_label": EXPECTED[fault],
        "hypotheses": hypotheses,
        "initial_label_correct": first.get("before") == EXPECTED[fault] if model_arm else None,
        "post_probe_label_correct": first.get("after") == EXPECTED[fault] if model_arm else None,
        "cleanup_verified": True,
        "trace_sha256": digest(trace),
        "trace_records": len(events),
    }


def summary(cases: list[dict[str, Json]], source: str) -> dict[str, Json]:
    rollup: list[Json] = []
    pairs = {(text(c["fault"]), text(c["arm"])) for c in cases}
    if not cases or len(pairs) != len(cases) or any(c["source"] != source for c in cases):
        raise ValueError("missing_duplicate_or_mixed_source_cases")
    for arm in ARMS:
        selected = [c for c in cases if c["arm"] == arm]
        if not selected:
            continue
        rollup.append(
            {
                "arm": arm,
                "cells": len(selected),
                "faults": sum(c["control"] is False for c in selected),
                "recoveries": sum(c["recovered"] is True for c in selected),
                "repairs": sum(c["recovery_category"] == "Repair" for c in selected),
                "reroutes": sum(c["recovery_category"] == "Reroute" for c in selected),
                "repair_and_reroute": sum(
                    c["recovery_category"] == "Repair+Reroute" for c in selected
                ),
                "healthy_noops": sum(c["healthy_noop"] is True for c in selected),
                "failures": sum(c["failure"] is not None for c in selected),
                "false_online_attempts": sum(
                    cast(int, c["false_online_attempts"]) for c in selected
                ),
                "blocked_application_completion": sum(
                    cast(int, c["blocked_application_completion"]) for c in selected
                ),
                "accepted_false_success": sum(
                    c["accepted_false_success"] is True for c in selected
                ),
                "post_probe_labels_correct": sum(
                    c["post_probe_label_correct"] is True for c in selected
                )
                if arm.startswith("glimmer-")
                else None,
                "labels_scored": len(selected) if arm.startswith("glimmer-") else 0,
            }
        )
    return {
        "version": 1,
        "source": source,
        "oracle": "uplink-linux-lab-v1",
        "diagnosis_metric": "first-decision-layer-v1",
        "development_exposed": True,
        "cases": cast(list[Json], cases),
        "rollup": rollup,
    }


def verify(root: Path) -> dict[str, Json]:
    manifest = read(root / "manifest.json")
    if (
        local(root, "manifest.sha256").read_text()
        != digest(root / "manifest.json") + "  manifest.json\n"
    ):
        raise ValueError("manifest_hash_mismatch")
    if manifest.get("version") != 1:
        raise ValueError("manifest_version_refused")
    files = obj(manifest["files"])
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != set(files) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("bundle_inventory_mismatch")
    for name, expected_digest in files.items():
        path = local(root, name)
        if not path.is_file() or path.stat().st_size > 1_048_576:
            raise ValueError("bounded_regular_file_required")
        if digest(path) != expected_digest:
            raise ValueError("bundle_hash_mismatch")
    computed = []
    namespace_ids = []
    for item in rows(manifest["cases"]):
        trace = local(root, text(item["trace"]))
        case = read(local(root, text(item["case"])))
        namespace_ids.append(text(case["run_id"]))
        computed.append(score(case, trace))
        replay(trace, speed=0, color=False, output=io.StringIO())
    cleanup = read(root / "final-cleanup.json")
    runs = rows(cleanup["runs"])
    if (
        cleanup.get("owned_namespaces") != 0
        or cleanup.get("active_owned_daemons") != 0
        or len(runs) != len(namespace_ids)
        or len(set(namespace_ids)) != len(namespace_ids)
        or {text(run["run_id"]) for run in runs} != set(namespace_ids)
        or any(
            run.get("status") != "closed"
            or run.get("cleanup_failure") is not None
            or run.get("host_fingerprint_matches") is not True
            for run in runs
        )
    ):
        raise ValueError("final_cleanup_not_verified")
    result = summary(computed, text(manifest["source"]))
    planned = manifest.get("planned_arms")
    if not isinstance(planned, list) or not planned or any(a not in ARMS for a in planned):
        raise ValueError("planned_arms_required")
    expected = {(fault, text(arm)) for fault in EXPECTED for arm in planned}
    present = {(text(c["fault"]), text(c["arm"])) for c in computed}
    if (
        not present <= expected
        or manifest.get("planned_cells") != len(expected)
        or type(manifest.get("complete")) is not bool
        or manifest["complete"] != (present == expected)
    ):
        raise ValueError("completion_or_schedule_mismatch")
    if result != read(root / "summary.json"):
        raise ValueError("summary_recomputation_mismatch")
    return {
        "verified": True,
        "cells": len(computed),
        "complete": manifest["complete"],
        "source": manifest["source"],
        "rollup": result["rollup"],
    }


def build(run: Path, output: Path, *, planned_arms: tuple[str, ...]) -> dict[str, Json]:
    from offline_agent.release import scan_bytes
    from offline_agent.uplink.privacy import Redactor

    state = read(run / "state.json")
    source = text(state["source"])
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source_sha_required")
    if output.exists() or not planned_arms or any(a not in ARMS for a in planned_arms):
        raise ValueError("new_output_and_known_arms_required")
    output.mkdir(parents=True)
    (output / "cases").mkdir()
    (output / "traces").mkdir()
    index: list[Json] = []
    scored = []
    validation: list[Json] = []
    for path in sorted(run.glob("case-*/result.json")):
        case = read(path)
        if type(case.get("number")) is not int or not 1 <= cast(int, case["number"]) <= 1000:
            raise ValueError("case_number_required")
        receipt = obj(case["receipt"])
        trace = path.parent / "traces" / text(receipt["trace"])
        transfer = read(path.parent / "transfer-manifest.json")
        if transfer.get("traces/" + trace.name) != digest(trace):
            raise ValueError("source_trace_transfer_mismatch")
        row = score(case, trace)
        if row["arm"] not in planned_arms:
            raise ValueError("unplanned_arm")
        case_name = f"cases/{cast(int, case['number']):02}.json"
        trace_name = "traces/" + trace.name
        for original, name in ((path, case_name), (trace, trace_name)):
            data = original.read_bytes()
            if scan_bytes(name, data) or (
                original == trace and not Redactor(b"x" * 32).scan_trace(data.decode())["passed"]
            ):
                raise ValueError("public_receipt_privacy_refused")
            shutil.copyfile(original, output / name)
        replay(trace, speed=0, color=False, output=io.StringIO())
        scored.append(row)
        index.append({"case": case_name, "trace": trace_name})
        validation.append(
            {
                "number": case["number"],
                "trace": trace_name,
                "sha256": digest(trace),
                "validated": True,
                "replayed": True,
                "privacy_passed": True,
            }
        )
    result = summary(scored, source)
    expected = {(f, a) for f in EXPECTED for a in planned_arms}
    seen = {(text(c["fault"]), text(c["arm"])) for c in scored}
    if not seen <= expected:
        raise ValueError("unexpected_matrix_cell")
    write(output / "summary.json", result)
    write(output / "trace-validation.json", {"version": 1, "rows": validation})
    final_cleanup = run / "final-cleanup.json"
    if scan_bytes("final-cleanup.json", final_cleanup.read_bytes()):
        raise ValueError("public_cleanup_privacy_refused")
    shutil.copyfile(final_cleanup, output / "final-cleanup.json")
    manifest: dict[str, Json] = {
        "version": 1,
        "source": source,
        "complete": seen == expected,
        "planned_cells": len(expected),
        "planned_arms": list(planned_arms),
        "cases": index,
        "files": {
            str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()
        },
    }
    write(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(digest(output / "manifest.json") + "  manifest.json\n")
    return verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify", type=Path)
    modes.add_argument("--build", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    args = parser.parse_args()
    if args.build and args.output is None:
        parser.error("--build requires --output")
    try:
        answer = (
            verify(args.verify)
            if args.verify
            else build(args.build, args.output, planned_arms=tuple(args.arms))
        )
    except (ValueError, OSError) as exc:
        reason = str(exc) if re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__
        raise SystemExit("bundle_refused:" + reason) from exc
    print(json.dumps(answer, indent=2))
