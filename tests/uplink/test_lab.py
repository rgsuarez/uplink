"""Small hermetic lab/controller pins. No namespaces, daemons, SSH or models run here."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.lab_protocol import FAULTS, LAB_VERSION, LabSpec, request
from offline_agent.uplink.lab_runner import GlimmerPolicy, LimaClient, ScriptedPolicy, evaluate
from offline_agent.uplink.lab_simulator import SimulatedClient, SimulatedPolicy
from offline_agent.uplink.lab_worker import dispatch, require_vm
from offline_agent.uplink.trace_format import read_trace

ROOT = Path(__file__).resolve().parents[2]


def test_proxy_uses_namespace_config_at_confined_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import lab_worker

    spawn = Mock()
    monkeypatch.setattr(lab_worker, "spawn", spawn)
    spec = LabSpec("0123456789ab")
    lab_worker.start_proxy(spec, tmp_path)
    command = spawn.call_args.args[2]
    assert command[:4] == ("ip", "netns", "exec", spec.internet)
    assert command[4:] == ("tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf")
    contents = (tmp_path / "tinyproxy/tinyproxy.conf").read_text()
    assert "LogFile" not in contents and "PidFile" not in contents
    lab_worker.remove_proxy_config(tmp_path)
    assert not (tmp_path / "tinyproxy").exists()
    lab_worker.remove_proxy_config(tmp_path)  # Empty partial setup is safe.


@pytest.mark.parametrize("foreign", ("symlink", "extra_file", "parent_symlink"))
def test_proxy_cleanup_refuses_unowned_config(tmp_path: Path, foreign: str) -> None:
    from offline_agent.uplink import lab_worker

    directory = tmp_path / "tinyproxy"
    outside = tmp_path / "existing"
    outside.mkdir()
    if foreign == "parent_symlink":
        (outside / "tinyproxy").mkdir()
        (outside / "tinyproxy/tinyproxy.conf").write_text("preserve")
        parent = tmp_path / "other"
        parent.symlink_to(outside)
        with pytest.raises(ValueError, match="parent_symlink_refused"):
            lab_worker.remove_proxy_config(parent)
        assert (outside / "tinyproxy/tinyproxy.conf").read_text() == "preserve"
        return
    if foreign == "symlink":
        directory.symlink_to(outside)
    else:
        directory.mkdir()
        (directory / "unowned").write_text("preserve")
    with pytest.raises(ValueError, match="refused"):
        lab_worker.remove_proxy_config(tmp_path)
    assert outside.is_dir() and directory.exists()


@pytest.mark.parametrize(
    "status,stdout,reason",
    (
        (0, '{"ready":true}', None),
        (2, '{"ok":false,"reason":"lab_daemon_exited_proxy"}', "lab_daemon_exited_proxy"),
        (2, "not json", "unparseable_worker_receipt"),
        (2, '{"reason":"private arbitrary text"}', "unparseable_worker_receipt"),
    ),
)
def test_lab_transport_retains_only_safe_worker_refusal_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    stdout: str,
    reason: str | None,
) -> None:
    import subprocess

    from offline_agent.uplink.lab_stage import WorkerBundle

    worker = tmp_path / "lab_worker.py"
    worker.touch()
    client = LimaClient("0123456789ab", worker, True, WorkerBundle(tmp_path, {}))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr(client, "command", lambda *args: ("synthetic-worker",))
    monkeypatch.setattr(
        "subprocess.run", Mock(return_value=subprocess.CompletedProcess([], status, stdout, ""))
    )
    if reason is None:
        assert client.call("up") == {"ready": True}
    else:
        with pytest.raises(ContractError, match="lab_worker_refused_no_retry__" + reason):
            client.call("up")


@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("arm", ("uplink", "baseline"))
def test_all_catalog_faults_both_arms_produce_honest_dry_traces(
    tmp_path: Path, fault: str, arm: str
) -> None:
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    policy = SimulatedPolicy() if arm == "uplink" else ScriptedPolicy()
    row = evaluate(ROOT, client, spec, fault, policy, tmp_path, dry=True)
    assert row["evidence_kind"] == "MOCK" and row["profile"] == LAB_VERSION
    assert not client.created
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    assert records[0]["mode"] == "test" and records[-1]["event"] == "finish"
    assert all("display" in r and "net_state" in r for r in records)
    if fault in {"healthy", "misleading_status"}:
        assert row["changes"] == [] and row["app_resumed"]
        assert row["outcome"] == "healthy_noop"
    if fault == "captive_portal":
        assert row["outcome"] == "reported" and not row["app_resumed"]
    if fault == "site_only_outage":
        assert not row["app_resumed"]
    for i, r in enumerate(records):
        if r.get("phase") == "apply":
            assert records[i - 1]["phase"] == "confirm"


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("inject", {"fault": "shell"}),
        ("apply", {"tool": "shell", "policy": "id"}),
        ("fetch", {"target": 5}),
        ("arm", {"seconds": 21}),
        ("observe", {"argv": ["id"]}),
    ],
)
def test_untrusted_lab_payloads_are_refused(operation: str, payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        request(operation, "0123456789ab", payload)


def test_worker_dry_run_has_no_effects_even_for_destructive_operations() -> None:
    for operation in ("up", "undo", "down"):
        result = dispatch(request(operation, "0123456789ab"), execute=False)
        assert result["effects"] is False


def test_vm_guard_refuses_local_root_and_test_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with pytest.raises(ValueError):
        require_vm(True)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.node", lambda: "lima-uplink-harness")
    with pytest.raises(ValueError, match="forbidden_in_tests"):
        require_vm(True)


def test_host_control_and_model_calls_require_lab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink.contracts import Scope
    from offline_agent.uplink.lab_runner import probe, state
    from offline_agent.uplink.privacy import Redactor

    monkeypatch.setattr("platform.node", lambda: "workstation")
    spy = Mock(side_effect=AssertionError("must not call provider"))
    monkeypatch.setattr("offline_agent.uplink.lab_runner.HttpTransport.post", spy)
    with pytest.raises(ContractError):
        LimaClient("0123456789ab", tmp_path / "worker.py", True).call("up")
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    client.call("up")
    with pytest.raises(ContractError):
        GlimmerPolicy(ROOT, execute=True).choose(
            Scope(), state(client, spec), probe(client, Redactor(b"x" * 32)), []
        )
    spy.assert_not_called()


def test_compact_model_guide_keeps_scoped_grammar_and_stable_cache_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Sequence

    from offline_agent.providers import Message
    from offline_agent.uplink.contracts import Scope, action_schema
    from offline_agent.uplink.lab_runner import probe, state
    from offline_agent.uplink.privacy import Redactor

    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    prompts: list[Sequence[Message]] = []

    def prompt(root: Path, messages: Sequence[Message]) -> str:
        prompts.append(messages)
        return "synthetic-prompt"

    monkeypatch.setattr("offline_agent.uplink.lab_runner.server_owned_prompt", prompt)
    responses = []
    for service in ("one", "two"):
        for content in (
            '{"layer":"dns","evidence":["dns-1"]}',
            json.dumps(
                {
                    "version": 1,
                    "tool": "set_dns",
                    "args": {
                        "snapshot_id": "snapshot-a",
                        "service_id": service,
                        "why": "dns_failed",
                        "evidence_ids": ["dns-1"],
                        "policy": "snapshot",
                    },
                }
            ),
        ):
            responses.append({"content": content, "truncated": False, "stop": True, "timings": {}})
    post = Mock(side_effect=responses)
    monkeypatch.setattr("offline_agent.uplink.lab_runner.HttpTransport.post", post)
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    client.call("up")
    client.call("inject", {"fault": "wrong_dns"})
    oracle = probe(client, Redactor(b"x" * 32))
    scopes = [Scope((service,), (), ("snapshot-a",), ("dns-1",)) for service in ("one", "two")]
    for scope in scopes:
        GlimmerPolicy(ROOT, execute=True).choose(scope, state(client, spec), oracle, [])
    assert prompts[0][0].content == prompts[2][0].content
    assert prompts[1][0].content == prompts[3][0].content
    system = prompts[0][0].content
    assert len(system) < len(json.dumps(action_schema(scopes[0]))) / 2
    assert json.loads(prompts[3][-1].content)["scope"]["services"] == ["two"]
    requests = [call.args[1] for call in post.call_args_list]
    assert requests[1]["grammar"] != requests[3]["grammar"]
    assert all(r["grammar_lazy"] is False and r["temperature"] == 0 for r in requests)
    assert all(call.args[-1] == 15 for call in post.call_args_list)


def test_model_facts_separate_current_drift_from_captured_baseline() -> None:
    from offline_agent.uplink.lab_runner import configuration_facts, policy_state, state

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    client.call("up")
    baseline = configuration_facts(state(client, spec))
    client.call("inject", {"fault": "wrong_dns"})
    changed = state(client, spec)
    changed.raw["irrelevant_kernel_metadata"] = "not model input"
    facts = policy_state(changed, baseline)
    assert facts.raw["known_good_snapshot"] == baseline
    current = facts.raw["current"]
    assert isinstance(current, dict) and current["dns_server"] != baseline["dns_server"]
    assert "addresses" not in facts.raw and "irrelevant_kernel_metadata" not in facts.raw
    assert facts.raw["prior_alternate_online"] is False
    assert "addresses" in changed.raw  # Evidence retained outside the model prompt.


@pytest.mark.parametrize("fault", ("healthy", "misleading_status", "site_only_outage"))
def test_online_oracle_refuses_model_requested_network_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from offline_agent.uplink.contracts import Action, Scope, Tool
    from offline_agent.uplink.lab_runner import Emission

    def harmful(scope: Scope, *args: object) -> Emission:
        action = Action(
            Tool.CYCLE_INTERFACE,
            {
                "snapshot_id": scope.snapshots[0],
                "service_id": scope.services[0],
                "why": "snapshot_drift",
                "evidence_ids": ["dns-1"],
            },
        )
        return Emission(json.dumps(action.as_json()), {})

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    policy = SimulatedPolicy()
    monkeypatch.setattr(policy, "choose", harmful)
    row = evaluate(ROOT, client, spec, fault, policy, tmp_path, dry=True)
    assert row["outcome"] == "healthy_change_refused" and row["changes"] == []
    injection = next(i for i, call in enumerate(client.calls) if call["operation"] == "inject")
    assert not any(call["operation"] == "apply" for call in client.calls[injection + 1 :])
    rows = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    assert not any(r.get("phase") in ("confirm", "apply") for r in rows)
    assert not client.created


def test_provider_timeout_is_recorded_as_attempted_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from offline_agent.providers import ProviderTimeout
    from offline_agent.uplink.model_identity import expected_model

    identity = expected_model(chip="Apple M4 Max")
    identity.update(verification="server_verified", verified_at=time.time())
    policy = SimulatedPolicy()
    monkeypatch.setattr(policy, "choose", Mock(side_effect=ProviderTimeout()))
    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "wrong_dns", policy, tmp_path, model_identity=identity)
    receipt = json.loads(next(tmp_path.glob("wrong_dns-uplink-*.json")).read_text())
    assert receipt["failure"] == "http_timeout" and receipt["model"]["model_used"] is True
    assert receipt["model_calls_attempted"] == 1 and not client.created
    rows = list(read_trace(tmp_path / "traces" / receipt["trace"]))
    errors = [r for r in rows if r.get("phase") == "report"]
    observation = errors[-1]["observation"]
    assert isinstance(observation, dict)
    assert observation["source"] == "model_request_failed_no_response"
    assert errors[-1]["model"] is None


@pytest.mark.parametrize("rejection", ("watchdog", "repeat"))
def test_rejected_returned_model_output_is_retained_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    from offline_agent.uplink.contracts import Scope
    from offline_agent.uplink.lab_runner import Emission, LabState
    from offline_agent.uplink.oracle import OracleResult

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    policy = SimulatedPolicy()
    original = policy.choose
    responses: list[str] = []

    def respond(
        scope: Scope, observed: LabState, oracle: OracleResult, history: object
    ) -> Emission:
        emission = original(scope, observed, oracle, [])
        if rejection == "repeat" and responses:
            emission = Emission(responses[0], {})
        responses.append(emission.text)
        if rejection == "watchdog":
            client.restore()
            client.timer = {"state": "cleared", "reason": "deadline_reverted"}
        return emission

    monkeypatch.setattr(policy, "choose", respond)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "dns_plus_proxy", policy, tmp_path, dry=True)
    receipt = json.loads(next(tmp_path.glob("dns_plus_proxy-uplink-*.json")).read_text())
    assert not receipt["app_resumed"]
    records = list(read_trace(tmp_path / "traces" / receipt["trace"]))
    model_rows = [
        row["model"] for row in records if row.get("event") == "step" and row.get("model")
    ]
    assert len(model_rows) == len(responses)
    assert all(
        isinstance(row, dict) and row["text"] == text
        for row, text in zip(model_rows, responses, strict=True)
    )
    totals = records[-1]["totals"]
    assert isinstance(totals, dict) and totals["model_calls"] == len(responses)


def test_run_ids_cannot_escape_or_reuse_existing_smoke_names() -> None:
    for value in ("../uh-client", "uh-client", "-x", "ABCDEF123456"):
        with pytest.raises(ValueError):
            LabSpec(value)
    spec = LabSpec("0123456789ab")
    assert spec.client != "uh-client" and spec.internet != "uh-inet"
    assert all(len(x) <= 15 for x in spec.interfaces)


def test_failed_setup_never_cleans_up_someone_elses_fixture(tmp_path: Path) -> None:
    class Collision(SimulatedClient):
        def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            if operation == "up":
                self.calls.append(request(operation, self.spec.run_id, payload))
                raise ContractError("fixture_collision")
            raise AssertionError("no cleanup is authorized after a setup collision")

    spec = LabSpec("0123456789ab")
    client = Collision(spec)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "healthy", ScriptedPolicy(), tmp_path, dry=True)
    receipt = next(tmp_path.glob("healthy-baseline-*.json"))
    assert json.loads(receipt.read_text())["failure"] == "fixture_collision"
    assert len(client.calls) == 1


def test_watchdog_recovery_cannot_be_credited_to_agent(tmp_path: Path) -> None:
    class Fired(SimulatedClient):
        injected = False

        def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            result = super().call(operation, payload)
            if operation == "inject":
                self.injected = True
            if self.injected and operation == "observe":
                result["timer"] = {"state": "cleared", "reason": "deadline_reverted"}
            return result

    spec = LabSpec("0123456789ab")
    client = Fired(spec)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "wrong_dns", ScriptedPolicy(), tmp_path, dry=True)
    receipt = json.loads(next(tmp_path.glob("wrong_dns-baseline-*.json")).read_text())
    assert (
        not receipt["app_resumed"]
        and receipt["failure"] == "watchdog_ended_trial_no_recovery_credit"
    )


def test_injection_commands_stay_inside_owned_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    from offline_agent.uplink import lab_worker

    spec = LabSpec("0123456789ab")
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(lab_worker, "run", lambda argv, **kwargs: commands.append(argv))
    monkeypatch.setattr(lab_worker, "write_resolver", lambda *args: None)
    for fault in FAULTS:
        lab_worker.inject(spec, {}, fault)
    assert commands
    for command in commands:
        assert (
            command[:2] == ("ip", "-n")
            and command[2] in {spec.client, spec.internet}
            or (
                command[:3] == ("ip", "netns", "exec")
                and command[3] in {spec.client, spec.internet}
            )
        )
    assert not any("iptables" in command or "nft" in command for command in commands)


@pytest.mark.parametrize("owned", (True, False))
def test_partial_setup_cleanup_requires_this_invocation_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned: bool
) -> None:
    from offline_agent.uplink import lab_worker

    root = tmp_path / "run"
    monkeypatch.setattr(LabSpec, "state", property(lambda _: root))
    state: dict[str, object] = {}
    cleaned: list[bool] = []

    def fail(spec: LabSpec, owner: str) -> dict[str, object]:
        root.mkdir()
        (root / "state.json").write_text("{}")
        state["setup_owner"] = owner if owned else "another-invocation"
        raise ValueError("daemon_failed")

    def cleanup(
        spec: LabSpec, snapshot: dict[str, object], *, partial: bool = False
    ) -> dict[str, object]:
        cleaned.append(partial)
        return {"closed": True}

    monkeypatch.setattr(lab_worker, "setup_owned", fail)
    monkeypatch.setattr(lab_worker, "load", lambda _: state)
    monkeypatch.setattr(lab_worker, "down", cleanup)
    with pytest.raises(ValueError, match="daemon_failed"):
        lab_worker.setup(LabSpec("0123456789ab"))
    assert cleaned == ([True] if owned else [])


def test_partial_cleanup_failure_is_durable_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import lab_worker

    root = tmp_path / "run"
    monkeypatch.setattr(LabSpec, "state", property(lambda _: root))
    state: dict[str, object] = {}
    calls: list[str] = []

    def fail(spec: LabSpec, owner: str) -> dict[str, object]:
        root.mkdir()
        (root / "state.json").write_text("{}")
        state["setup_owner"] = owner
        raise ValueError("daemon_failed")

    def cleanup(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("cleanup")
        raise ValueError("cleanup_failed")

    monkeypatch.setattr(lab_worker, "setup_owned", fail)
    monkeypatch.setattr(lab_worker, "load", lambda _: state)
    monkeypatch.setattr(lab_worker, "down", cleanup)
    with pytest.raises(ValueError, match="cleanup_failed_no_retry"):
        lab_worker.setup(LabSpec("0123456789ab"))
    assert calls == ["cleanup"]
    assert (
        json.loads((root / "state.json").read_text())["cleanup_failure"]
        == "partial_setup_cleanup_failed_no_retry"
    )


def test_model_transport_failure_has_receipt_and_owned_cleanup(tmp_path: Path) -> None:
    from collections.abc import Sequence

    from offline_agent.providers import Message, ProviderTimeout
    from offline_agent.uplink.contracts import Scope
    from offline_agent.uplink.lab_runner import Emission, LabState
    from offline_agent.uplink.oracle import OracleResult

    class FailedPolicy(SimulatedPolicy):
        def choose(
            self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
        ) -> Emission:
            raise ProviderTimeout()

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "wrong_dns", FailedPolicy(), tmp_path, dry=True)
    receipt = json.loads(next(tmp_path.glob("wrong_dns-uplink-*.json")).read_text())
    assert receipt["failure"] == "http_timeout" and not receipt["app_resumed"]
    assert not client.created


def test_watchdog_firing_during_verification_cannot_be_disarmed_into_credit(tmp_path: Path) -> None:
    class FiredDuringProbe(SimulatedClient):
        repair_started = False

        def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            result = super().call(operation, payload)
            if operation == "apply" and (payload or {}).get("tool") == "set_dns":
                self.repair_started = True
            if operation == "fetch" and (payload or {}).get("target") == 3 and self.repair_started:
                self.timer = {"state": "cleared", "reason": "deadline_reverted"}
            return result

    spec = LabSpec("0123456789ab")
    client = FiredDuringProbe(spec)
    with pytest.raises(ContractError, match="stopped_see_receipt"):
        evaluate(ROOT, client, spec, "wrong_dns", ScriptedPolicy(), tmp_path, dry=True)
    receipt = json.loads(next(tmp_path.glob("wrong_dns-baseline-*.json")).read_text())
    assert receipt["failure"] == "watchdog_ended_trial_no_recovery_credit"
    assert not receipt["app_resumed"]
    assert sum(c["operation"] == "undo" for c in client.calls) == 1  # Alternate preflight only.
    assert client.calls[-1]["operation"] == "down"


def test_every_lab_probe_and_app_handshake_preserves_raw_rpc_receipts(tmp_path: Path) -> None:
    from offline_agent.uplink.trace_format import mapping

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    row = evaluate(ROOT, client, spec, "wrong_dns", ScriptedPolicy(), tmp_path, dry=True)
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    captured = sum(
        len(json.loads(str(mapping(r["observation"])["stdout"])))
        for r in records
        if r.get("event") == "step" and mapping(r["observation"])["source"] == "lab_oracle"
    )
    assert captured == sum(c["operation"] in {"resolve", "fetch"} for c in client.calls)


def test_failed_manual_lab_rollback_cannot_be_retried_by_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import lab_worker

    monkeypatch.setattr(LabSpec, "state", property(lambda _: tmp_path))
    state: dict[str, object] = {
        "status": "ready",
        "timer": {"state": "armed"},
        "host_fingerprint": "baseline",
    }
    calls: list[str] = []

    def fail() -> None:
        calls.append("restore")
        raise ValueError("native_restore_failed")

    spec = LabSpec("0123456789ab")
    with pytest.raises(ValueError, match="native_restore_failed"):
        lab_worker.mutate(spec, state, "undo", fail)
    assert state["status"] == "failed" and state["timer"] == {
        "state": "failed",
        "reason": "rollback_failed",
    }
    with pytest.raises(ValueError, match="no_retry"):
        lab_worker.mutate(spec, state, "watchdog_revert", fail)
    assert calls == ["restore"]
