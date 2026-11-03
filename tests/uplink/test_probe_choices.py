"""Pins for V4's state-only probe and mismatched proxy-diagnosis failures."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.contracts import Action, Scope, Tool
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.lab_runner import Emission, GlimmerPolicy, LabState
from offline_agent.uplink.layer_policy import ModelRecord
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.probe_choices import (
    evidence_ids,
    targeted_wifi_probes,
    wifi_repair_schema,
)
from offline_agent.uplink.probe_policy import choose_with_probes, hypothesis_schema
from offline_agent.uplink.probe_protocol import program_schema, validate_program

SCOPE = Scope(
    ("service",),
    (),
    ("snapshot",),
    ("dns-1", "tls-apple", "portal-apple"),
    profile="lab_dns_proxy_snapshot",
)
FACTS: dict[str, Json] = {
    "dns-1": {"kind": "dns", "ok": True},
    "tls-apple": {"kind": "tls", "ok": False},
    "portal-apple": {"kind": "portal", "ok": False},
    "snapshot-dns": {"changed": False},
    "snapshot-proxy": {"changed": True},
}
PROBES: tuple[dict[str, Json], ...] = (
    {"op": "dns_query", "name": "one", "resolver": "configured", "timeout_ms": 1000},
    {"op": "https_head", "endpoint": "one", "path": "current", "timeout_ms": 1000},
    {"op": "captive_check", "endpoint": "one", "timeout_ms": 1000},
)


@pytest.mark.parametrize("probe", PROBES)
def test_targeted_choices_admit_real_measurements_with_evidence(probe: dict[str, Json]) -> None:
    schema = object_json(json.loads(json.dumps(program_schema("lab_wifi"))))
    choices = targeted_wifi_probes(schema, FACTS)
    assert len(choices) == 3
    assert all(object_json(c)["evidence_ids"] for c in choices)
    value = {"version": 1, "probes": [probe]}
    assert Draft202012Validator(schema).is_valid(value)
    assert validate_program(value, "lab_wifi")[0].op == probe["op"]
    grammar = schema_gbnf(schema)
    assert all(op in grammar for op in ("dns_query", "https_head", "captive_check"))
    assert "read_state" not in grammar and "tcp_connect" not in grammar
    assert "effective proxy route" in str(choices)


@pytest.mark.parametrize(
    "probe",
    [
        {"op": "read_state", "timeout_ms": 1000},
        {**PROBES[0], "resolver": "google"},
        {**PROBES[1], "path": "direct"},
        {**PROBES[2], "timeout_ms": 5000},
    ],
)
def test_unhealthy_wifi_refuses_state_only_bypass_and_long_probes(probe: dict[str, Json]) -> None:
    schema = object_json(json.loads(json.dumps(program_schema("lab_wifi"))))
    targeted_wifi_probes(schema, FACTS)
    assert not Draft202012Validator(schema).is_valid({"version": 1, "probes": [probe]})


def test_missing_evidence_refuses_before_any_generation_or_execution() -> None:
    schema = object_json(json.loads(json.dumps(program_schema("lab_wifi"))))
    with pytest.raises(ContractError, match="targeted_probe_evidence_unavailable"):
        targeted_wifi_probes(schema, {})
    # The base DSL still admits the actual startup control and healthy state reads.
    assert validate_program(
        {"version": 1, "probes": [{"op": "read_state", "timeout_ms": 1000}]}, "lab_wifi"
    )


@pytest.mark.parametrize("status", ["refused", "timeout", "not_requested"])
def test_unreturned_probe_does_not_become_layer_evidence(status: str) -> None:
    facts: dict[str, Json] = {
        "probe-1": {"probe": PROBES[1], "status": status, "ok": False},
    }
    assert evidence_ids(facts, "proxy") == ()


def repair(tool: Tool = Tool.RESTORE_PROXY, citation: str = "tls-apple") -> dict[str, Json]:
    return Action(
        tool,
        {
            "service_id": "service",
            "snapshot_id": "snapshot",
            "why": "snapshot_drift",
            "policy": "snapshot",
            "evidence_ids": [citation],
        },
    ).as_json()


def proxy_schema(facts: dict[str, Json]) -> dict[str, Json]:
    base: dict[str, Json] = {"properties": {"hypothesis_after": hypothesis_schema(tuple(facts))}}
    return wifi_repair_schema(base, SCOPE, (Tool.RESTORE_PROXY,), facts)


@pytest.mark.parametrize(
    "layer,hypothesis_id,action_id,accepted",
    [
        ("proxy", "snapshot-proxy", "tls-apple", True),
        ("proxy", "tls-apple", "tls-apple", True),
        ("dns", "dns-1", "dns-1", False),
        ("dns", "snapshot-proxy", "tls-apple", False),
        ("proxy", "dns-1", "tls-apple", False),
        ("proxy", "snapshot-proxy", "dns-1", False),
    ],
)
def test_proxy_action_layer_and_citations_are_correlated(
    layer: str,
    hypothesis_id: str,
    action_id: str,
    accepted: bool,
) -> None:
    schema = proxy_schema(FACTS)
    value = {
        "hypothesis_after": {"layer": layer, "evidence": [hypothesis_id]},
        "action": repair(citation=action_id),
    }
    assert Draft202012Validator(schema).is_valid(value) is accepted
    grammar = schema_gbnf(schema)
    assert "dns-1" not in grammar


def test_missing_proxy_oracle_cannot_fall_back_to_dns_citation() -> None:
    with pytest.raises(ContractError, match="repair_layer_evidence_unavailable"):
        proxy_schema({"dns-1": FACTS["dns-1"], "snapshot-proxy": FACTS["snapshot-proxy"]})


@pytest.mark.parametrize("mode", ["forced", "open"])
@pytest.mark.parametrize("probe", PROBES)
def test_real_wifi_decision_prompt_and_schema_accept_evidence_bound_repair(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    probe: dict[str, Json],
) -> None:
    policy = GlimmerPolicy(
        Path("."), execute=True, probe_scripts=True, probe_profile="lab_wifi", action_mode=mode
    )
    responses = [
        {
            "hypothesis_before": {"layer": "proxy", "evidence": ["snapshot-proxy"]},
            "program": {"version": 1, "probes": [probe]},
        },
        {
            "hypothesis_after": {"layer": "proxy", "evidence": ["snapshot-proxy"]},
            "action": repair(),
        },
    ]

    def generate(stage: str, messages: object, grammar: str) -> Emission:
        text = json.dumps(responses.pop(0))
        policy.records.append(ModelRecord(stage, str(messages), grammar, text=text))
        return Emission(text, {})

    monkeypatch.setattr(policy, "generate", generate)
    executor = Mock(
        return_value={
            "status": "completed",
            "settings_unchanged": True,
            "wifi_route_unchanged": True,
            "rows": [{"probe": probe, "ok": False, "status": "completed"}],
        }
    )
    policy.probe_executor = executor
    oracle = OracleResult(
        Verdict.DEGRADED,
        1,
        (
            Evidence("dns-1", "dns", True, "resolved"),
            Evidence("tls-apple", "tls", False, "transport_failed"),
            Evidence("portal-apple", "portal", False, "transport_failed"),
        ),
    )
    observed = LabState(
        {"wifi_up": True, "wifi_default_route": True, "dns_changed": False, "proxy_changed": True},
        "",
        "",
        True,
        True,
        None,
    )
    result = choose_with_probes(policy, SCOPE, observed, oracle)
    assert json.loads(result.text) == repair()
    assert policy.probe_result["hypothesis_after"] == {
        "layer": "proxy",
        "evidence": ["snapshot-proxy"],
    }
    assert policy.probe_result["action_mode"] == mode
    executor.assert_called_once_with({"version": 1, "probes": [probe]})
    assert "targeted_probe_choices" in policy.records[0].prompt
    assert "read_state" not in policy.records[0].grammar
    assert "proxy" in policy.records[1].grammar
    assert result.raw_text == policy.records[1].text


def test_v4_state_only_output_is_refused_without_executing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = GlimmerPolicy(Path("."), execute=True, probe_scripts=True, probe_profile="lab_wifi")
    old = {
        "hypothesis_before": {"layer": "dns", "evidence": ["dns-1"]},
        "program": {"version": 1, "probes": [{"op": "read_state", "timeout_ms": 1000}]},
    }
    policy.records.append(ModelRecord("diagnosis", "synthetic", "synthetic"))
    monkeypatch.setattr(policy, "generate", Mock(return_value=Emission(json.dumps(old), {})))
    executor = Mock(return_value={"status": "refused", "reason": "synthetic", "rows": []})
    policy.probe_executor = executor
    oracle = OracleResult(Verdict.OFFLINE, 1, (Evidence("dns-1", "dns", False, "failed"),))
    with pytest.raises(ContractError, match="probe_plan_schema_refused"):
        choose_with_probes(policy, SCOPE, LabState({}, "", "", True, True, None), oracle)
    executor.assert_not_called()
    assert policy.records[-1].failure == "probe_plan_schema_refused"
