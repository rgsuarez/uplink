"""Base-model descriptive evidence from hash-bound receipts and validated traces only.

Forced actions are never open-choice credit. Labels constrained by the Wi-Fi
grammar are reported as labels, not independent diagnosis-accuracy measurements.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.native_protocol import EOT
from offline_agent.uplink.model_identity import GGUF_SHA256, LLAMA_SERVER_BUILD
from offline_agent.uplink.probe_evidence import trace_probe_evidence
from offline_agent.uplink.probe_serving import VERSION
from offline_agent.uplink.probe_trial_window import read_object
from offline_agent.uplink.trace_format import read_trace


def online_at(oracle: dict[str, Json] | None, at: str) -> bool | None:
    if oracle is None:
        return None
    if oracle.get("verdict") != "ONLINE":
        return False
    checks: list[Json] = []
    for group in ("dns", "https", "captive"):
        rows = oracle.get(group)
        if not isinstance(rows, list) or len(rows) != 2:
            raise ContractError("score_incomplete_online_oracle")
        checks.extend(rows)
    age = (datetime.fromisoformat(at) - datetime.fromisoformat(str(oracle["ts"]))).total_seconds()
    return 0 <= age <= 30 and all(object_json(row).get("ok") is True for row in checks)


def text_list(value: Json) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ContractError("score_string_list_required")
    return [str(x) for x in value]


def serving_matches(value: Json, arm: str, at: str) -> bool:
    if not isinstance(value, dict):
        return False
    checked = value.get("checked_at")
    source = value.get("runtime_sha256")
    return (
        value.get("version") == VERSION
        and value.get("arm") == arm
        and value.get("base_gguf_sha256") == GGUF_SHA256
        and value.get("build") == LLAMA_SERVER_BUILD
        and value.get("adapter_gguf_sha256") == None
        and type(value.get("adapter_count")) is int
        and value["adapter_count"] == 0
        and type(checked) in (int, float)
        and math.isfinite(float(str(checked)))
        and 0 <= datetime.fromisoformat(at).timestamp() - float(str(checked)) <= 120
        and isinstance(source, str)
        and re.fullmatch(r"[0-9a-f]{64}", source) is not None
    )


def score_trial(receipt_path: Path, arm: str) -> dict[str, Json]:
    if arm != "base":
        raise ContractError("score_arm_required")
    receipt = read_object(receipt_path)
    name = receipt.get("trace")
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".jsonl"):
        raise ContractError("score_trace_basename_required")
    trace = receipt_path.parent / "traces" / name
    if (
        trace.is_symlink()
        or not trace.is_file()
        or trace.stat().st_size > 32_000_000
        or not trace.resolve().is_relative_to(receipt_path.parent.resolve())
    ):
        raise ContractError("score_local_bounded_trace_required")
    digest = hashlib.sha256(trace.read_bytes()).hexdigest()
    evidence = object_json(receipt.get("probe_execution"))
    if evidence.get("trace_sha256") != digest:
        raise ContractError("score_trace_digest_mismatch")
    records = list(read_trace(trace))
    if (
        not records
        or records[-1]["event"] != "finish"
        or receipt.get("kind") != "uplink-real-eval"
        or receipt.get("fault") != "dns_and_proxy"
        or receipt.get("probe_scripts") is not True
    ):
        raise ContractError("score_complete_probe_trial_required")
    # RunTrace has its own run UUID. Bind it to the receipt's exact filename/hash,
    # never assume it is the root worker's twelve-character run id.
    if records[0]["run_id"] != trace.stem:
        raise ContractError("score_trace_identity_mismatch")
    counted = trace_probe_evidence(records, profile="lab_wifi")
    decisions: list[Json] = []
    current_oracle: dict[str, Json] | None = None
    false_online = online_claims = unknown_online = unparsed_model = 0
    for event in records:
        if isinstance(event.get("oracle"), dict):
            current_oracle = object_json(event["oracle"])
        observation = event.get("observation")
        if isinstance(observation, dict) and observation.get("source") == "generated_probe_episode":
            episode = object_json(read_json(str(observation["stdout"]), max_bytes=131072))
            action, after = episode.get("action"), episode.get("hypothesis_after")
            if not isinstance(action, dict) or not isinstance(after, dict):
                decisions.append({"step": event["step"], "status": "action_unavailable"})
                continue
            raw = episode.get("action_verbatim")
            if not isinstance(raw, str):
                raise ContractError("score_action_emission_required")
            emitted = object_json(read_json(raw.removesuffix(EOT)))
            if emitted.get("action") != action or emitted.get("hypothesis_after") != after:
                raise ContractError("score_action_receipt_disagrees_with_emission")
            tools = text_list(episode.get("admitted_tools"))
            if not tools or len(set(tools)) != len(tools) or action.get("tool") not in tools:
                raise ContractError("score_action_not_in_recorded_choices")
            mode = episode.get("action_mode")
            if not isinstance(mode, str) or mode not in {"forced", "open"}:
                raise ContractError("score_action_mode_unproven")
            citations = text_list(after.get("evidence"))
            before = object_json(episode.get("hypothesis_before", {}))
            before_citations = text_list(before.get("evidence", []))
            action_citations = text_list(object_json(action.get("args")).get("evidence_ids", []))
            decisions.append(
                {
                    "step": event["step"],
                    "status": "recorded",
                    "action_mode": mode,
                    "open_choice": mode == "open" and len(tools) > 1,
                    "admitted_tools": [str(x) for x in tools],
                    "action": action["tool"],
                    "layer": after.get("layer"),
                    "layer_before": before.get("layer"),
                    "hypothesis_before_citations": [str(x) for x in before_citations],
                    "hypothesis_citations": [str(x) for x in citations],
                    "action_citations": [str(x) for x in action_citations],
                    "arm_verified": serving_matches(episode.get("serving"), arm, str(event["ts"])),
                    "runtime_sha256": object_json(episode.get("serving", {})).get("runtime_sha256"),
                    "layer_is_grammar_constrained": action.get("tool")
                    in {"set_dns", "restore_proxy"},
                }
            )
        model = event.get("model")
        if event["event"] == "step" and isinstance(model, dict):
            try:
                emitted = object_json(read_json(str(model["text"]).removesuffix(EOT)))
            except ValueError:
                unparsed_model += 1
                continue
            action = emitted.get("action", emitted)
            if isinstance(action, dict) and action.get("tool") == "declare_online":
                online_claims += 1
                truth = online_at(current_oracle, str(event["ts"]))
                false_online += truth is False
                unknown_online += truth is None
    recorded = [object_json(row) for row in decisions if object_json(row)["status"] == "recorded"]
    arm_verified = bool(recorded) and all(row["arm_verified"] is True for row in recorded)
    open_choices = sum(row["open_choice"] is True for row in recorded)
    sources = {
        str(row["runtime_sha256"])
        for row in recorded
        if isinstance(row["runtime_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", str(row["runtime_sha256"]))
    }
    source = next(iter(sources)) if len(sources) == 1 else None
    source_bound = source is not None and all(row["runtime_sha256"] == source for row in recorded)
    return {
        "arm": arm,
        "run_id": receipt.get("run_id"),
        "trace_id": records[0]["run_id"],
        "receipt_path": str(receipt_path),
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "trace_sha256": digest,
        "evidence_kind": receipt.get("evidence_kind"),
        "arm_verified": arm_verified,
        "runtime_sha256": source if source_bound else None,
        "decisions": decisions,
        "open_action_choices": open_choices,
        "forced_action_count": sum(row["action_mode"] == "forced" for row in recorded),
        "false_online": false_online if not unknown_online and not unparsed_model else None,
        "observed_false_online": false_online,
        "online_claims": online_claims,
        "unknown_online_claims": unknown_online,
        "unparsed_model_outputs": unparsed_model,
        "pc1_probe_enabled": counted["probe_enabled"],
        "non_read_state_returned": counted["non_read_state_returned"],
        "probe_accounting": counted["accounting"],
        "recovery_claim_in_receipt": receipt.get("recovered"),
        "comparable_open_trial": receipt.get("evidence_kind") == "lab_wifi"
        and arm_verified
        and source_bound
        and bool(recorded)
        and len(recorded) == len(decisions)
        and all(row["action_mode"] == "open" for row in recorded)
        and counted["accounting"] == "complete"
        and not unknown_online
        and not unparsed_model,
    }
