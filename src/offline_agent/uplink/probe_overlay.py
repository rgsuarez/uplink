"""Compact video overlay derived from a receipt and its validated original trace."""

import argparse
import json
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_evidence import trace_probe_evidence
from offline_agent.uplink.trace_format import read_trace


def overlay(receipt: dict[str, Json], records: list[dict[str, Json]]) -> dict[str, Json]:
    execution_evidence = trace_probe_evidence(records)
    episodes = execution_evidence["episodes"]
    if not isinstance(episodes, list) or not episodes:
        raise ContractError("probe_episodes_required")
    checks = [object_json(row["oracle"]) for row in records if isinstance(row.get("oracle"), dict)]
    if not checks:
        raise ContractError("independent_oracle_receipt_required")
    decisions: list[Json] = []
    for value in episodes:
        row = object_json(value)
        execution = object_json(row.get("execution"))
        program = object_json(row.get("program"))
        decisions.append(
            {
                "probes": program.get("probes"),
                "hypothesis_before": row.get("hypothesis_before"),
                "hypothesis_after": row.get("hypothesis_after"),
                "action": row.get("action"),
                "decision_ms": row.get("decision_ms"),
                "probe_status": execution.get("status"),
                "probe_results": execution.get("rows"),
                "settings_unchanged": execution.get("settings_unchanged"),
                "budget": execution.get("budget"),
            }
        )
    return {
        "version": 1,
        "kind": "uplink-probe-overlay",
        "evidence_kind": receipt.get("evidence_kind"),
        "probe_enabled": execution_evidence["probe_enabled"],
        "non_read_state_returned": execution_evidence["non_read_state_returned"],
        "probe_execution": execution_evidence,
        "decisions": decisions,
        "independent_online_check": checks[-1],
        "application_handshake": receipt.get("app_resumed"),
        "recovered": receipt.get("recovered", receipt.get("outcome") == "recovered"),
        "source_trace": receipt.get("trace"),
    }


def render(value: dict[str, Json]) -> str:
    rows = value.get("decisions")
    if not isinstance(rows, list):
        raise ContractError("overlay_decisions_required")
    lines: list[str] = ["Probe-enabled: " + ("yes" if value.get("probe_enabled") is True else "no")]
    lines.append("Returned probes beyond read_state: " + str(value.get("non_read_state_returned")))
    for index, item in enumerate(rows, 1):
        row = object_json(item)
        before, after = row.get("hypothesis_before"), row.get("hypothesis_after")
        action = row.get("action")
        lines.append(f"Decision {index}")
        results = row.get("probe_results")
        if isinstance(results, list):
            for result in results:
                result = object_json(result)
                probe = object_json(result.get("probe"))
                target = probe.get("resolver", probe.get("path", probe.get("endpoint", "state")))
                status = "pass" if result.get("ok") is True else result.get("status", "unrecorded")
                lines.append(f"  Probe: {probe.get('op')} ({target}) -> {status}")
        lines.append(
            "  Hypothesis: "
            + str(before.get("layer") if isinstance(before, dict) else "unrecorded")
            + " -> "
            + str(after.get("layer") if isinstance(after, dict) else "unrecorded")
        )
        lines.append(
            "  Action: " + str(action.get("tool") if isinstance(action, dict) else "unrecorded")
        )
        elapsed = row.get("decision_ms")
        if type(elapsed) in (int, float):
            lines.append(f"  Decision time: {elapsed} ms")
        lines.append(
            "  Probe settings diff: "
            + ("match" if row.get("settings_unchanged") is True else "unproven")
        )
    check = object_json(value.get("independent_online_check"))
    lines.append("Independent check: " + str(check.get("verdict")))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    receipt = object_json(read_json(args.receipt.read_text(), max_bytes=500000))
    if args.trace.name != receipt.get("trace"):
        raise ContractError("overlay_source_trace_mismatch")
    result = overlay(receipt, list(read_trace(args.trace)))
    if receipt.get("evidence_kind") == "lab_wifi":
        redactor = Redactor.local(args.receipt.parent)
        redactor.register_lab_identity()
        result = object_json(redactor.scrub_serials_json(result))
    print(json.dumps(result, indent=2) if args.json else render(result))


if __name__ == "__main__":
    main()
