"""Small hermetic tests for the model-to-plan trust boundary; never run network changes."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import re
from dataclasses import replace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import (
    CHANGES,
    Action,
    Scope,
    Tool,
    action_schema,
    arguments,
    parse_action,
)
from offline_agent.uplink.grammar import gbnf
from offline_agent.uplink.plans import (
    PlanContext,
    ServiceSettings,
    execute_change,
    plan_change,
)

SCOPE = Scope(("service-a", "service-b"), ("candidate-a",), ("snapshot-a",), ("evidence-a",))
SERVICES = (
    ServiceSettings(
        "service-a",
        "Synthetic Wi-Fi",
        "en0",
        (_public_fixture("@PUBLIC_IPV4_075@"),),
        (("web", "proxy.invalid", 8080, True), ("secureweb", "proxy.invalid", 8080, True)),
    ),
    ServiceSettings("service-b", "Synthetic Ethernet", "en1"),
)
CONTEXT = PlanContext(
    SCOPE, SERVICES, "snapshot-a", SERVICES, SCOPE.services, (("candidate-a", "service-b"),)
)


def sample(tool: Tool) -> str:
    values: dict[str, Json] = {
        "snapshot_id": "snapshot-a",
        "why": "snapshot_drift",
        "evidence_ids": ["evidence-a"],
        "service_id": "service-a",
        "policy": "snapshot",
        "candidate_id": "candidate-a",
        "service_ids": ["service-b", "service-a"],
        "component": "dns",
        "physical_step": "check_cable",
        "reason": "needs_human",
    }
    return json.dumps(
        {
            "version": 1,
            "tool": tool.value,
            "args": {key: values[key] for key in arguments(SCOPE)[tool]},
        }
    )


def grammar_pattern(grammar: str) -> str:
    """Independent recognizer for the emitted acyclic GBNF subset (no model required).

    Interpret the emitted grammar, not the schema or action parser. Native llama.cpp
    token-sampling integration remains a separate U2 check; this proves character syntax.
    """
    rules = dict(line.split(" ::= ", 1) for line in grammar.splitlines())
    token = re.compile(r'"(?:\\.|[^"\\])*"|\[[^\]]+\]|[a-z][a-z0-9-]*|\{[0-9,]+\}|[()|*+?]')

    def expand(name: str) -> str:
        parts = []
        for value in token.findall(rules[name]):
            if value.startswith('"'):
                parts.append(re.escape(json.loads(value)))
            elif value[0].isalpha():
                parts.append(f"(?:{expand(value)})")
            else:
                parts.append(value)
        return "".join(parts)

    return expand("root")


@pytest.mark.parametrize("tool", list(Tool))
def test_schema_and_generated_grammar_accept_each_typed_action(tool: Tool) -> None:
    text = sample(tool)
    assert parse_action(text, SCOPE).tool == tool
    assert re.fullmatch(grammar_pattern(gbnf(SCOPE)), text)


@pytest.mark.parametrize(
    "mutate",
    [
        {"tool": "shell"},
        {"version": 2},
        {"version": True},
        {"argv": ["sh", "-c", "id"]},
        {"args": {"service_id": "en0; id"}},
        {"args": {"command": "sudo id"}},
        {"args": {"service_id": "$(id)"}},
    ],
)
def test_unknown_shell_and_extra_fields_rejected(mutate: dict[str, Json]) -> None:
    value = json.loads(sample(Tool.SET_DNS))
    value.update(mutate)
    text = json.dumps(value)
    with pytest.raises(ContractError):
        parse_action(text, SCOPE)
    assert re.fullmatch(grammar_pattern(gbnf(SCOPE)), text) is None


@pytest.mark.parametrize("text", ["", "null", "[]", "NaN", "`id`", '{"tool":"x","tool":"y"}'])
def test_malformed_boundary_is_value_blind(text: str) -> None:
    with pytest.raises(ContractError):
        parse_action(text, SCOPE)


def test_empty_discovery_allows_diagnostics_but_never_changes() -> None:
    Draft202012Validator.check_schema(action_schema(Scope()))
    assert parse_action(sample(Tool.READ_VITALS)).tool == Tool.READ_VITALS
    with pytest.raises(ContractError):
        parse_action(sample(Tool.SET_DNS))
    assert re.fullmatch(grammar_pattern(gbnf()), sample(Tool.SET_DNS)) is None


@pytest.mark.parametrize("tool", sorted(CHANGES))
def test_every_change_card_and_absolute_execution_veto(
    tool: Tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = Mock(side_effect=AssertionError("must never spawn"))
    monkeypatch.setattr("subprocess.run", spawn)
    monkeypatch.setattr("subprocess.Popen", spawn)
    card = plan_change(parse_action(sample(tool), SCOPE), CONTEXT)
    rendered = card.for_confirmation()
    assert rendered["argv"] and rendered["why"] and rendered["evidence_ids"]
    assert "undo_argv" in rendered and rendered["snapshot_id"] == "snapshot-a"
    assert rendered["auto_revert_seconds"] == 20 and not rendered["executable"]
    if tool in {Tool.RENEW_LEASE, Tool.FLUSH_DNS_CACHE}:
        assert "inverse" in card.blocked_reason
    else:
        assert card.undo_argv
    with pytest.raises(ContractError, match="never_execute"):
        execute_change(card)
    spawn.assert_not_called()


def test_argv_binding_is_discovered_and_not_shell_interpreted() -> None:
    service = replace(SERVICES[0], name="Synthetic; $(id)")
    ctx = replace(CONTEXT, current=(service, SERVICES[1]))
    card = plan_change(parse_action(sample(Tool.SET_DNS), SCOPE), ctx)
    assert card.argv[0][2] == service.name  # one argv element, never a shell expression
    assert "Synthetic" not in repr(card)
    value = json.loads(sample(Tool.SET_DNS))
    value["args"]["policy"] = _public_fixture("@PUBLIC_IPV4_095@; id")
    with pytest.raises(ContractError):
        parse_action(json.dumps(value), SCOPE)


def test_untrusted_manual_action_revalidated_and_snapshot_required() -> None:
    with pytest.raises(ContractError):
        plan_change(Action(Tool.SET_DNS, {"argv": ["sh"]}), CONTEXT)
    with pytest.raises(ContractError):
        plan_change(
            parse_action(sample(Tool.SET_DNS), SCOPE), replace(CONTEXT, snapshot_id="other")
        )


def test_reroute_requires_prior_evidence_and_complete_service_order() -> None:
    with pytest.raises(ContractError, match="prior_evidence"):
        plan_change(
            parse_action(sample(Tool.REROUTE), SCOPE), replace(CONTEXT, candidate_services=())
        )
    value = json.loads(sample(Tool.SET_SERVICE_ORDER))
    value["args"]["service_ids"] = ["service-a"]
    with pytest.raises(ContractError, match="complete_service_order"):
        plan_change(parse_action(json.dumps(value), SCOPE), CONTEXT)


def test_managed_and_unavailable_state_refuse() -> None:
    action = parse_action(sample(Tool.RESTORE_PROXY), SCOPE)
    for settings in [replace(SERVICES[0], managed=True), replace(SERVICES[0], proxies=())]:
        with pytest.raises(ContractError):
            plan_change(action, replace(CONTEXT, current=(settings, SERVICES[1])))
