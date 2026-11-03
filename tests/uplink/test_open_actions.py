"""Open choice changes policy freedom without broadening reversible effect boundaries."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json
from offline_agent.providers import Message
from offline_agent.uplink.contracts import CHANGES, Action, Scope, Tool
from offline_agent.uplink.lab_runner import Emission, GlimmerPolicy, LabState
from offline_agent.uplink.layer_policy import action_guide, layer_action_schema
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.probe_policy import choose_with_probes, open_actions, permitted_actions

SCOPE = Scope(("service",), ("alternate",), ("snapshot",), ("dns-1",))
OFFLINE = OracleResult(Verdict.OFFLINE, 1, ())
DRIFT: dict[str, Json] = {"snapshot-dns": {"changed": True}, "snapshot-proxy": {"changed": True}}


def test_model_can_choose_both_repairs_reroute_or_handoff() -> None:
    tools = open_actions(SCOPE, DRIFT, OFFLINE, alternate_qualified=True, attempted=())
    assert {Tool.SET_DNS, Tool.RESTORE_PROXY, Tool.REROUTE, Tool.REPORT, Tool.GIVE_UP} <= set(tools)
    assert Tool.PROBE_ONLINE in tools and Tool.READ_ROUTES in tools
    assert not {
        Tool.CYCLE_INTERFACE,
        Tool.RENEW_LEASE,
        Tool.FLUSH_DNS_CACHE,
        Tool.SET_SERVICE_ORDER,
    } & set(tools)
    assert permitted_actions(SCOPE, DRIFT, OFFLINE, alternate_qualified=True, attempted=()) == (
        Tool.SET_DNS,
    )


def test_healthy_network_only_allows_reads_or_terminal_choices() -> None:
    tools = open_actions(
        SCOPE, DRIFT, OracleResult(Verdict.ONLINE, 1, ()), alternate_qualified=True, attempted=()
    )
    assert not CHANGES & set(tools)
    assert {Tool.DECLARE_ONLINE, Tool.REPORT, Tool.GIVE_UP} <= set(tools)


def test_missing_or_exhausted_evidence_cannot_open_effects() -> None:
    tools = open_actions(SCOPE, {}, OFFLINE, alternate_qualified=False, attempted=())
    assert not CHANGES & set(tools) and Tool.GIVE_UP in tools
    tools = open_actions(
        SCOPE,
        DRIFT,
        OFFLINE,
        alternate_qualified=True,
        attempted=("set_dns", "restore_proxy", "reroute"),
    )
    assert not CHANGES & set(tools)
    tools = open_actions(Scope(), DRIFT, OFFLINE, alternate_qualified=True, attempted=())
    assert not CHANGES & set(tools)


def test_link_and_lease_actions_need_measured_failure_and_general_scope() -> None:
    facts: dict[str, Json] = {"link": {"up": False}, "lease": {"present": False}}
    tools = open_actions(SCOPE, facts, OFFLINE, alternate_qualified=False, attempted=())
    assert {Tool.CYCLE_INTERFACE, Tool.RENEW_LEASE} <= set(tools)
    restricted = Scope(
        SCOPE.services, (), SCOPE.snapshots, SCOPE.evidence, "lab_dns_proxy_snapshot"
    )
    assert not CHANGES & set(
        open_actions(restricted, facts, OFFLINE, alternate_qualified=False, attempted=())
    )


@pytest.mark.parametrize("policy,allowed", [("snapshot", True), ("google", False), ("off", False)])
def test_open_grammar_only_allows_snapshot_restore(policy: str, allowed: bool) -> None:
    tools = (Tool.SET_DNS, Tool.RESTORE_PROXY, Tool.REPORT, Tool.GIVE_UP)
    schema = layer_action_schema(SCOPE, tools, snapshot_only=True)
    action = Action(
        Tool.SET_DNS,
        {
            "service_id": "service",
            "snapshot_id": "snapshot",
            "why": "snapshot_drift",
            "evidence_ids": ["dns-1"],
            "policy": policy,
        },
    )
    assert Draft202012Validator(schema).is_valid(action.as_json()) is allowed
    guide = json.loads(action_guide(SCOPE, tools, snapshot_only=True))
    assert guide["set_dns"]["args"]["policy"] == {"enum": ["snapshot"]}
    assert not Draft202012Validator(schema).is_valid(Action(Tool.SET_DNS, {}).as_json())


@pytest.mark.parametrize("mode,probes", [("other", True), ("open", False)])
def test_mode_requires_explicit_probe_contract(mode: str, probes: bool) -> None:
    with pytest.raises(ContractError, match="action_mode"):
        GlimmerPolicy(Path("."), execute=False, probe_scripts=probes, action_mode=mode)


def test_open_model_can_handoff_even_when_a_reversible_route_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = [
        {
            "op": "ping_gateway",
            "path": "alternate",
            "count": 1,
            "interval_ms": 200,
            "timeout_ms": 1000,
        },
        {"op": "dns_query", "name": "one", "resolver": "alternate", "timeout_ms": 1000},
    ]
    before = {
        "hypothesis_before": {"layer": "dns", "evidence": ["snapshot-dns"]},
        "program": {"version": 1, "probes": probes},
    }
    after = {
        "hypothesis_after": {"layer": "upstream", "evidence": ["probe-1"]},
        "action": Action(Tool.GIVE_UP, {"reason": "needs_human"}).as_json(),
    }
    policy = GlimmerPolicy(Path("."), execute=True, probe_scripts=True, action_mode="open")
    monkeypatch.setattr(
        policy,
        "generate",
        Mock(side_effect=[Emission(json.dumps(before), {}), Emission(json.dumps(after), {})]),
    )
    policy.probe_executor = Mock(
        return_value={
            "status": "completed",
            "settings_unchanged": True,
            "rows": [{"probe": p, "ok": True, "status": "completed"} for p in probes],
        }
    )
    raw: dict[str, Json] = {
        "current": {"primary_up": True, "dns_server": "same", "proxy_url": "same"},
        "known_good_snapshot": {"dns_server": "same", "proxy_url": "same"},
        "prior_alternate_online": True,
    }
    result = choose_with_probes(
        policy,
        SCOPE,
        LabState(raw, "same", "same", True, True, None),
        OFFLINE,
        history=(
            Message("assistant", json.dumps(Action(Tool.READ_VITALS, {}).as_json())),
            Message("user", '{"fault":"sealed-evaluator-label","private":"not-for-prompts"}'),
        ),
    )
    assert json.loads(result.text)["tool"] == "give_up"
    assert policy.probe_result["action_mode"] == "open"
    assert policy.probe_result["previous_read_actions"] == ["read_vitals"]
    prompts = str(policy.generate.call_args_list)  # type: ignore[attr-defined]
    assert "previous_read_actions" in prompts
    assert "sealed-evaluator-label" not in prompts and "not-for-prompts" not in prompts
    admitted = policy.probe_result["admitted_tools"]
    assert isinstance(admitted, list) and "reroute" in admitted
