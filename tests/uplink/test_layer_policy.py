"""Layer boundaries remain enforced independently of model instructions and sampling."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.providers import ProviderTimeout
from offline_agent.uplink.contracts import Action, Scope, Tool, parse_action
from offline_agent.uplink.grammar import gbnf, schema_gbnf
from offline_agent.uplink.lab_protocol import LabSpec
from offline_agent.uplink.lab_runner import (
    GlimmerPolicy,
    configuration_facts,
    policy_state,
    probe,
    state,
)
from offline_agent.uplink.lab_simulator import SimulatedClient
from offline_agent.uplink.layer_policy import (
    ModelRecord,
    diagnosis_schema,
    evidence_facts,
    journal_model_records,
    layer_tools,
    parse_diagnosis,
    read_model_records,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.remote_settings import RemoteTargets
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import read_trace

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "invalid",
    [
        {"layer": "shell", "evidence": ["dns-1"]},
        {"layer": "dns", "evidence": ["invented"]},
        {"layer": "dns", "evidence": []},
        {"layer": "dns", "evidence": ["dns-1", "dns-1"]},
        {"layer": "dns", "evidence": ["dns-1"], "command": "touch file"},
    ],
)
def test_diagnosis_rejects_unknown_unsupported_or_empty_evidence(invalid: dict[str, Json]) -> None:
    with pytest.raises(ContractError, match="diagnosis_refused"):
        parse_diagnosis(json.dumps(invalid), ("dns-1",))


def test_diagnosis_closed_grammar_and_valid_evidence() -> None:
    value = {"layer": "dns", "evidence": ["dns-1"]}
    assert parse_diagnosis(json.dumps(value), ("dns-1",)) == value
    grammar = schema_gbnf(diagnosis_schema(("dns-1",)))
    assert "dns-1" in grammar and "shell" not in grammar


@pytest.mark.parametrize("up", [True, None, "false", 0])
def test_cycle_requires_an_observed_link_failure(up: Json) -> None:
    oracle = SimulatedClient(LabSpec("0123456789ab"))
    oracle.call("up")
    oracle.call("inject", {"fault": "wrong_dns"})
    measured = probe(oracle, Redactor(b"x" * 32))
    assert Tool.CYCLE_INTERFACE not in layer_tools("link", {"link": {"up": up}}, measured)
    assert Tool.CYCLE_INTERFACE not in layer_tools("dns", {"link": {"up": False}}, measured)


def test_layer_subset_refuses_cross_layer_action_and_keeps_cycle_last() -> None:
    client = SimulatedClient(LabSpec("0123456789ab"))
    client.call("up")
    client.call("inject", {"fault": "wrong_dns"})
    oracle = probe(client, Redactor(b"x" * 32))
    tools = layer_tools("dns", {"link": {"up": True}}, oracle)
    scope = Scope(("service-a",), (), ("snapshot-a",), ("dns-1",))
    args: dict[str, Json] = {
        "snapshot_id": "snapshot-a",
        "service_id": "service-a",
        "why": "dns_failed",
        "evidence_ids": ["dns-1"],
    }
    valid = Action(Tool.SET_DNS, {**args, "policy": "snapshot"})
    assert parse_action(json.dumps(valid.as_json()), scope, tools) == valid
    with pytest.raises(ContractError):
        parse_action(json.dumps(Action(Tool.CYCLE_INTERFACE, args).as_json()), scope, tools)
    assert "cycle_interface" not in gbnf(scope, tools)
    assert layer_tools("link", {"link": {"up": False}}, oracle)[-1] == Tool.CYCLE_INTERFACE
    client.call("undo")
    online = probe(client, Redactor(b"x" * 32))
    assert layer_tools("dns", {}, online) == (Tool.DECLARE_ONLINE, Tool.REPORT, Tool.GIVE_UP)


def test_model_gets_layer_evidence_without_fault_label_or_raw_identifiers() -> None:
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    client.call("up")
    baseline = configuration_facts(state(client, spec))
    client.call("inject", {"fault": "dns_plus_proxy"})
    observed = state(client, spec)
    observed.raw["layer_probes"] = client.call("layer_probe")
    raw = policy_state(observed, baseline).raw
    measured = probe(client, Redactor(b"x" * 32))
    facts = evidence_facts(raw, measured)
    assert facts["link"] == {"up": True}
    assert facts["snapshot-dns"] == {"changed": True}
    assert facts["snapshot-proxy"] == {"changed": True}
    assert facts["dns-alternate-1"] == {"available": True, "ok": True}
    assert facts["https-direct-1"] == {"available": True, "ok": True}
    assert not any(x in json.dumps(facts) for x in ("dns_plus_proxy", "192.0.2.", "203.0.113."))


def test_second_stage_timeout_retains_diagnosis_and_both_model_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr(
        "offline_agent.uplink.lab_runner.server_owned_prompt",
        lambda root, messages: json.dumps([m.content for m in messages]),
    )
    post = Mock(
        side_effect=[
            {
                "content": '{"layer":"dns","evidence":["dns-1"]}',
                "timings": {},
                "truncated": False,
                "stop": True,
            },
            ProviderTimeout(),
        ]
    )
    monkeypatch.setattr("offline_agent.uplink.lab_runner.HttpTransport.post", post)
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    client.call("up")
    client.call("inject", {"fault": "wrong_dns"})
    policy = GlimmerPolicy(ROOT, execute=True)
    scope = Scope(("service-a",), (), ("snapshot-a",), ("dns-1",))
    with pytest.raises(ProviderTimeout):
        policy.choose(scope, state(client, spec), probe(client, Redactor(b"x" * 32)), [])
    assert len(policy.records) == 2 and policy.records[1].failure == "http_timeout"
    assert len(read_model_records([r.as_json() for r in policy.records])) == 2
    with RunTrace(
        Redactor(b"x" * 32),
        directory=tmp_path,
        mode="test",
        model_name="synthetic-policy",
        provider="mock",
    ) as trace:
        journal_model_records(trace, policy.records, defer_action=False)
    steps = [r for r in read_trace(trace.path) if r["event"] == "step"]
    assert len(steps) == 2 and steps[0]["model"] is not None and steps[1]["model"] is None
    assert all(r["observation"] is not None for r in steps)


@pytest.mark.parametrize(
    "value", [None, [], [ModelRecord("action", "", "", duration_ms=float("nan")).as_json()]]
)
def test_missing_or_invalid_model_receipts_are_refused(value: Json) -> None:
    with pytest.raises(ContractError):
        read_model_records(value)


def test_ssh_routes_are_configurable_without_expanding_device_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UPLINK_OOB_HOST", "fixture-oob")
    monkeypatch.setenv("UPLINK_WIFI_HOST", "fixture-wifi")
    targets = RemoteTargets.from_environment()
    assert targets.oob_ssh[-1] == "fixture-oob" and targets.wifi_ssh[-1] == "fixture-wifi"
    assert "ControlPath=none" in targets.wifi_ssh
    assert RemoteTargets.from_environment("other-oob", "other-wifi").oob_host == "other-oob"
    for pair in (("-Fbad", "wifi"), ("host;true", "wifi"), ("same", "same")):
        with pytest.raises(ContractError):
            RemoteTargets(*pair)


def test_remote_diagnostic_refusal_is_zero_inference_and_transport_loss_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.real_protocol import RealSpec
    from offline_agent.uplink.real_runner import OOBLost
    from offline_agent.uplink.real_simulator import SimulatedLab
    from offline_agent.uplink.real_transport import LabBackend

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = LabBackend(spec)
    oracle = SimulatedLab(spec).probe()
    monkeypatch.setattr(
        backend,
        "rpc",
        Mock(
            return_value={
                "text": None,
                "timings": {},
                "records": [],
                "layer_probes": {},
                "error": "diagnostic_wifi_interface_refused",
            }
        ),
    )
    with pytest.raises(ContractError, match="diagnostic_wifi"):
        backend.choose(Scope(), {}, oracle)
    assert backend.last_model_request_count == 0 and backend.last_model_records == []
    monkeypatch.setattr(backend, "rpc", Mock(side_effect=OOBLost("oob_lost_no_retry")))
    with pytest.raises(OOBLost):
        backend.choose(Scope(), {}, oracle)
    assert backend.last_model_request_count is None


def test_layer_action_grammar_cites_one_decisive_evidence_id() -> None:
    from jsonschema import Draft202012Validator

    from offline_agent.uplink.layer_policy import action_guide, layer_action_schema

    scope = Scope(("service-a",), (), ("snapshot-a",), ("dns-1", "dns-2"))
    schema = layer_action_schema(scope, (Tool.SET_DNS,))
    valid = Action(
        Tool.SET_DNS,
        {
            "service_id": "service-a",
            "snapshot_id": "snapshot-a",
            "why": "dns_failed",
            "policy": "snapshot",
            "evidence_ids": ["dns-1"],
        },
    ).as_json()
    assert Draft202012Validator(schema).is_valid(valid)
    args = valid["args"]
    assert isinstance(args, dict)
    for ids in (["dns-1", "dns-1"], ["dns-1", "dns-2"], []):
        assert not Draft202012Validator(schema).is_valid(
            {**valid, "args": {**args, "evidence_ids": ids}}
        )
    grammar = schema_gbnf(schema)
    assert "{0,0}" not in grammar
    assert (
        json.loads(action_guide(scope, (Tool.SET_DNS,)))["set_dns"]["args"]["evidence_ids"][
            "maxItems"
        ]
        == 1
    )
