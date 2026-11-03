"""Evidence-led diagnosis and a layer-specific subset of the existing action contract."""

import json
import math
from dataclasses import dataclass
from typing import cast

from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.contracts import (
    CHANGES,
    Action,
    Scope,
    Tool,
    action_schema,
    arguments,
    array,
    enum,
)
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.trace import RunTrace, decision

DIAGNOSIS_VERSION = "uplink-layer-diagnosis-v1"
LAYERS = ("link", "ip", "dns", "proxy", "captive", "upstream")


def diagnosis_schema(evidence: tuple[str, ...]) -> dict[str, Json]:
    return {
        "type": "object",
        "properties": {"layer": enum(LAYERS), "evidence": array(evidence, 3)},
        "required": ["layer", "evidence"],
        "additionalProperties": False,
    }


def parse_diagnosis(text: str, evidence: tuple[str, ...]) -> dict[str, Json]:
    result = object_json(read_json(text, max_bytes=4096))
    if not Draft202012Validator(diagnosis_schema(evidence)).is_valid(result):
        raise ContractError("layer_diagnosis_refused")
    return result


def evidence_facts(raw: dict[str, Json], oracle: OracleResult) -> dict[str, Json]:
    """Summarize actual observations. No injected-fault name or proposed fix enters here."""
    facts: dict[str, Json] = {e.id: {"ok": e.ok, "kind": e.kind} for e in oracle.evidence}
    current, baseline = raw.get("current"), raw.get("known_good_snapshot")
    if isinstance(current, dict):
        facts["link"] = {"up": current.get("primary_up")}
        facts["lease"] = {"present": current.get("lease_present")}
        facts["route"] = {"present": current.get("default_gateway") is not None}
        if isinstance(baseline, dict):
            for label, key in (("dns", "dns_server"), ("proxy", "proxy_url")):
                facts["snapshot-" + label] = {"changed": current.get(key) != baseline.get(key)}
    else:
        # The macOS adapter emits drift booleans rather than private configuration values.
        facts["link"] = {"up": raw.get("wifi_up")}
        facts["route"] = {"present": raw.get("wifi_default_route")}
        for label in ("dns", "proxy"):
            facts["snapshot-" + label] = {"changed": raw.get(label + "_changed")}
    probes = raw.get("layer_probes")
    if isinstance(probes, dict):
        for label, field, key in (
            ("link", "up", "link_up"),
            ("lease", "present", "lease_present"),
            ("route", "present", "wifi_default_route"),
        ):
            if key in probes:
                facts[label] = {field: probes[key] if type(probes[key]) is bool else None}
        gateway = probes.get("gateway_ping")
        if isinstance(gateway, dict):
            facts["gateway"] = {k: gateway.get(k) for k in ("available", "ok")}
        for group in ("dns_configured", "dns_alternate", "https_direct", "https_proxy"):
            rows = probes.get(group)
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    facts[group.replace("_", "-") + "-" + str(index + 1)] = {
                        k: row.get(k) for k in ("available", "ok")
                    }
    return facts


def layer_tools(layer: str, facts: dict[str, Json], oracle: OracleResult) -> tuple[Tool, ...]:
    if layer not in LAYERS:
        raise ContractError("unknown_diagnosis_layer")
    if oracle.verdict == Verdict.ONLINE:
        return (Tool.DECLARE_ONLINE, Tool.REPORT, Tool.GIVE_UP)
    repairs = {
        "dns": (Tool.SET_DNS,),
        "proxy": (Tool.RESTORE_PROXY,),
        "ip": (Tool.RENEW_LEASE, Tool.REROUTE),
        "link": (Tool.REROUTE,),
        "captive": (),
        "upstream": (Tool.REROUTE,),
    }
    selected = (*repairs[layer], Tool.PROBE_ONLINE, Tool.REPORT, Tool.GIVE_UP)
    link = facts.get("link")
    # Cycling is costly and is unavailable when link failure is not actually observed.
    if layer == "link" and isinstance(link, dict) and link.get("up") is False:
        selected = (*selected, Tool.CYCLE_INTERFACE)
    return selected


def action_guide(scope: Scope, tools: tuple[Tool, ...], *, snapshot_only: bool = False) -> str:
    fields = arguments(
        Scope(
            ("$services",),
            ("$candidates",),
            ("$snapshots",),
            ("$evidence",),
            profile=scope.profile,
        )
    )
    for values in fields.values():
        if isinstance(values.get("evidence_ids"), dict):
            object_json(values["evidence_ids"])["maxItems"] = 1
    if snapshot_only:
        for tool in (Tool.SET_DNS, Tool.RESTORE_PROXY):
            fields[tool]["policy"] = enum(("snapshot",))
    return json.dumps(
        {
            tool.value: {
                "args": fields[tool],
                "cost": 10 if tool == Tool.CYCLE_INTERFACE else 1 if tool in CHANGES else 0,
            }
            for tool in tools
            if tool in fields
        },
        separators=(",", ":"),
    )


def layer_action_schema(
    scope: Scope,
    tools: tuple[Tool, ...],
    *,
    snapshot_only: bool = False,
) -> dict[str, Json]:
    """One decisive citation keeps sampling inside the unique-evidence contract."""
    schema = action_schema(scope, tools)
    variants = schema["oneOf"]
    assert isinstance(variants, list)
    for variant in variants:
        properties = object_json(object_json(variant)["properties"])
        values = object_json(object_json(properties["args"])["properties"])
        if snapshot_only and "policy" in values:
            values["policy"] = enum(("snapshot",))
        if isinstance(values.get("evidence_ids"), dict):
            object_json(values["evidence_ids"])["maxItems"] = 1
    return schema


@dataclass
class ModelRecord:
    stage: str
    prompt: str
    grammar: str
    text: str | None = None
    timings: dict[str, Json] | None = None
    failure: str | None = None
    duration_ms: float = 0.0

    def as_json(self) -> dict[str, Json]:
        return {
            "stage": self.stage,
            "prompt": self.prompt,
            "grammar": self.grammar,
            "text": self.text,
            "timings": self.timings,
            "failure": self.failure,
            "duration_ms": self.duration_ms,
        }


def read_model_records(value: Json) -> list[ModelRecord]:
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        raise ContractError("invalid_model_records")
    result = []
    for item in value:
        row = object_json(item)
        if (
            set(row) != {"stage", "prompt", "grammar", "text", "timings", "failure", "duration_ms"}
            or row["stage"] not in {"diagnosis", "action"}
            or any(not isinstance(row[k], str) for k in ("prompt", "grammar"))
            or any(row[k] is not None and not isinstance(row[k], str) for k in ("text", "failure"))
            or type(row["duration_ms"]) not in (int, float)
            or not math.isfinite(cast(float, row["duration_ms"]))
            or cast(float, row["duration_ms"]) < 0
        ):
            raise ContractError("invalid_model_record")
        result.append(
            ModelRecord(
                str(row["stage"]),
                str(row["prompt"]),
                str(row["grammar"]),
                cast(str | None, row["text"]),
                object_json(row["timings"]) if row["timings"] is not None else None,
                cast(str | None, row["failure"]),
                float(cast(float, row["duration_ms"])),
            )
        )
    return result


def journal_model_records(
    trace: RunTrace, records: list[ModelRecord], *, defer_action: bool
) -> None:
    for record in records:
        # The final typed action's emission is attached to its normal plan/verify step.
        text = None if defer_action and record.stage == "action" else record.text
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation(
                (),
                json.dumps(
                    {
                        "version": DIAGNOSIS_VERSION,
                        "stage": record.stage,
                        "prompt": record.prompt,
                        "grammar": record.grammar,
                    }
                ),
                stderr=record.failure or "",
                duration_ms=record.duration_ms,
                source="model_input",
            ),
            model_text=text,
            timings=record.timings if text is not None else None,
            choice=decision(
                "model_" + record.stage + ("_failed" if record.failure else "_returned"),
                accepted=record.failure is None,
            ),
        )
