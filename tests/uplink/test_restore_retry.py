"""A changed route permits one evidence-backed restore retry, never a retry loop."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import time
from pathlib import Path

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import Action, Scope, Tool
from offline_agent.uplink.lab_protocol import PROXY_URL, LabSpec
from offline_agent.uplink.lab_runner import Emission, GlimmerPolicy, LabState, evaluate
from offline_agent.uplink.lab_simulator import SimulatedClient
from offline_agent.uplink.layer_policy import evidence_facts
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.probe_policy import permitted_actions
from offline_agent.uplink.repair_budget import mutation_key, snapshot_retry_allowed
from offline_agent.uplink.trace_format import mapping as trace_object
from offline_agent.uplink.trace_format import read_trace

ROOT = Path(__file__).resolve().parents[2]
SCOPE = Scope(("service-a",), ("candidate-a",), ("snapshot-a",), ("dns-1",))


@pytest.mark.parametrize("tool", ("set_dns", "restore_proxy"))
@pytest.mark.parametrize(
    "history,index,allowed",
    [
        (("restore", "reroute"), 1, True),
        (("restore",), None, False),
        (("restore", "reroute"), None, False),
        (("reroute", "restore"), 0, False),
        (("restore", "reroute", "restore"), 1, False),
        (("restore", "reroute"), 0, False),
        (("restore", "reroute"), 2, False),
        (("restore", "reroute"), -1, False),
        (("restore", "reroute"), True, False),
    ],
)
def test_snapshot_retry_requires_one_restore_before_a_verified_route(
    tool: str, history: tuple[str, ...], index: int | None, allowed: bool
) -> None:
    attempted = tuple(tool if item == "restore" else item for item in history)
    keys = tuple(mutation_key(t, "primary", {"proxy": "changed"}) for t in attempted)
    key = mutation_key(tool, "alternate", {"proxy": "changed"})
    assert (
        snapshot_retry_allowed(tool, attempted, index, attempt_keys=keys, current_key=key)
        is allowed
    )
    assert not snapshot_retry_allowed("cycle_interface", attempted, index)
    assert not snapshot_retry_allowed("reroute", attempted, index)


def oracle(passing: int) -> OracleResult:
    kinds = ("dns", "dns", "tls", "tls", "portal", "portal")
    ids = ("dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google")
    return OracleResult(
        Verdict.ONLINE if passing == 6 else Verdict.OFFLINE if passing == 0 else Verdict.DEGRADED,
        1,
        tuple(
            Evidence(name, kind, n < passing, "fixture", "path-a", 1, "host-a", 204, "direct")
            for n, (name, kind) in enumerate(zip(ids, kinds, strict=True))
        ),
    )


def test_policy_requires_drift_and_keeps_mac_snapshot_profile_single_attempt() -> None:
    facts: dict[str, Json] = {"snapshot-proxy": {"changed": True}}

    def permitted(scope: Scope, observed: dict[str, Json]) -> tuple[Tool, ...]:
        return permitted_actions(
            scope,
            observed,
            oracle(2),
            alternate_qualified=False,
            attempted=("restore_proxy", "reroute"),
            verified_route_change_at=1,
            retryable_restores=("restore_proxy",),
        )

    assert permitted(SCOPE, facts) == (Tool.RESTORE_PROXY,)
    assert permitted(SCOPE, {}) == (Tool.REPORT, Tool.GIVE_UP)
    mac = Scope(
        SCOPE.services, (), SCOPE.snapshots, SCOPE.evidence, profile="lab_dns_proxy_snapshot"
    )
    assert permitted(mac, facts) == (Tool.REPORT, Tool.GIVE_UP)


def test_qualified_route_precedes_proxy_restore_when_primary_path_is_unavailable() -> None:
    facts: dict[str, Json] = {"link": {"up": False}, "snapshot-proxy": {"changed": True}}
    assert permitted_actions(SCOPE, facts, oracle(0), alternate_qualified=True, attempted=()) == (
        Tool.REROUTE,
    )
    # A proxy-only outage still restores the proxy. Missing evidence is not a failed path.
    facts["link"] = {"up": True}
    assert permitted_actions(SCOPE, facts, oracle(2), alternate_qualified=True, attempted=()) == (
        Tool.RESTORE_PROXY,
    )
    assert permitted_actions(
        SCOPE, facts, OracleResult(Verdict.OFFLINE, 1, ()), alternate_qualified=True, attempted=()
    ) == (Tool.RESTORE_PROXY,)


@pytest.mark.parametrize("fault", ("interface_down", "isp_down_saved_alternate"))
def test_primary_outage_plus_stale_proxy_recovers_without_undoing_route_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    class Compound(SimulatedClient):
        def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            result = super().call(operation, payload)
            if operation == "inject":
                self.proxy = _public_fixture("http://@PUBLIC_IPV4_086@:9")
            return result

    spec = LabSpec("0123456789ab")
    client = Compound(spec)
    policy = GlimmerPolicy(ROOT, execute=False, probe_scripts=True)

    def choose(scope: Scope, observed: LabState, result: OracleResult, history: object) -> Emission:
        attempted = observed.raw["action_history"]
        index = observed.raw.get("verified_route_change_at")
        assert isinstance(attempted, list)
        tools = permitted_actions(
            scope,
            evidence_facts(observed.raw, result),
            result,
            alternate_qualified=True,
            attempted=tuple(str(x) for x in attempted),
            verified_route_change_at=index if type(index) is int else None,
        )
        return action(scope, tools[0])

    monkeypatch.setattr(policy, "choose", choose)
    result = evaluate(ROOT, client, spec, fault, policy, tmp_path, dry=True)
    assert result["changes"] == ["reroute", "restore_proxy"]
    assert result["outcome"] == "recovered" and result["app_resumed"] is True
    assert result["restore_retries"] == []
    assert sum(c["operation"] == "arm" for c in client.calls) == 2


def action(scope: Scope, tool: Tool, *, policy: str = "snapshot") -> Emission:
    args: dict[str, Json] = {
        "snapshot_id": scope.snapshots[0],
        "why": "snapshot_drift",
        "evidence_ids": ["dns-1"],
    }
    if tool == Tool.REROUTE:
        args.update(candidate_id=scope.candidates[0], why="alternate_route")
    else:
        args.update(service_id=scope.services[0], policy=policy)
    return Emission(json.dumps(Action(tool, args).as_json()), {})


@pytest.mark.parametrize(
    "case",
    (
        "success",
        "no_route_change",
        "no_drift",
        "non_snapshot",
        "third_attempt",
        "expired",
        "retry_fails",
    ),
)
def test_runtime_retry_has_readback_budget_deadline_and_rollback_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    class RouteDrift(SimulatedClient):
        injected = False
        stage = 0

        def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            result = super().call(operation, payload)
            if operation == "inject":
                self.injected, self.stage = True, 0
            if operation == "apply" and self.injected:
                self.stage += 1
                if payload and payload["tool"] == "reroute":
                    self.proxy = PROXY_URL if case == "no_drift" else _public_fixture("http://@PUBLIC_IPV4_086@:9")
                    if case == "no_route_change":
                        self.gateway = _public_fixture("@PUBLIC_IPV4_081@")
            if operation == "undo":
                self.injected = False
            return result

    spec = LabSpec("0123456789ab")
    client = RouteDrift(spec)
    policy = GlimmerPolicy(ROOT, execute=False, probe_scripts=True)
    choices = 0

    def choose(scope: Scope, observed: LabState, result: OracleResult, history: object) -> Emission:
        nonlocal choices
        choices += 1
        if choices == 3 and case == "expired":
            client.timer = {"state": "armed", "deadline": time.time() - 1}
        return action(
            scope,
            Tool.REROUTE if choices == 2 else Tool.RESTORE_PROXY,
            policy="off" if choices == 3 and case == "non_snapshot" else "snapshot",
        )

    def measure(*args: object, **kwargs: object) -> OracleResult:
        if not client.injected:
            return oracle(6)
        # Explicit synthetic contract: a partially useful restore, then a better
        # route that reintroduces proxy drift. These are not live network results.
        passing = (0, 1, 4, 5 if case == "third_attempt" else 4 if case == "retry_fails" else 6)[
            client.stage
        ]
        return oracle(passing)

    monkeypatch.setattr(policy, "choose", choose)
    monkeypatch.setattr("offline_agent.uplink.lab_runner.probe", measure)
    if case in {"success", "retry_fails"}:
        result = evaluate(ROOT, client, spec, "stale_proxy", policy, tmp_path, dry=True)
        assert result["outcome"] == ("recovered" if case == "success" else "reported_after_revert")
    else:
        with pytest.raises(ContractError, match="stopped_see_receipt"):
            evaluate(ROOT, client, spec, "stale_proxy", policy, tmp_path, dry=True)
        result = json.loads(next(tmp_path.glob("stale_proxy-uplink-*.json")).read_text())
        assert result["failure"] == (
            "watchdog_deadline_expired_no_recovery_credit"
            if case == "expired"
            else "repeat_mutation_refused"
        )
    assert result["app_resumed"] is (case == "success")
    changes = result["changes"]
    assert isinstance(changes, list) and changes.count("restore_proxy") <= 2
    assert sum(c["operation"] == "arm" for c in client.calls) == 2
    assert not client.created
    records = list(read_trace(tmp_path / "traces" / str(result["trace"])))
    timers = [
        str(trace_object(trace_object(r["decision"])["timer"])["deadline_ts"])
        for r in records
        if r.get("phase") == "confirm"
    ]
    assert len(set(timers)) == 1
    if case in {"success", "third_attempt", "retry_fails"}:
        retries = result["restore_retries"]
        assert isinstance(retries, list) and len(retries) == 1
        retry = retries[0]
        assert isinstance(retry, dict)
        assert retry["tool"] == "restore_proxy" and retry["verified_route_change_at"] == 1
        previous, current = retry["previous_key"], retry["current_key"]
        assert isinstance(previous, dict) and isinstance(current, dict)
        assert previous["target_path"] != current["target_path"]
        assert isinstance(current["state_sha256"], str) and len(current["state_sha256"]) == 64
        assert any(
            trace_object(r["decision"]).get("reason")
            == "snapshot_restore_retry_after_verified_route"
            for r in records
            if r.get("event") == "step"
        )
    else:
        assert result["restore_retries"] == []
    if case == "retry_fails":
        assert any(r.get("phase") == "revert" for r in records)


@pytest.mark.parametrize("context", ("new_path", "same_path", "used_key", "no_keys", "full_budget"))
def test_retry_key_is_action_path_and_state_with_hard_episode_cap(context: str) -> None:
    prior = mutation_key("restore_proxy", "primary-interface", {"proxy": "stale"})
    route = mutation_key("reroute", "primary-interface", {"proxy": "restored"})
    current = mutation_key("restore_proxy", "alternate-interface", {"proxy": "stale"})
    attempted: tuple[str, ...] = ("restore_proxy", "reroute")
    keys: tuple[tuple[str, str, str], ...] = (prior, route)
    if context == "same_path":
        current = mutation_key("restore_proxy", "primary-interface", {"proxy": "different"})
    elif context == "used_key":
        current = prior
    elif context == "no_keys":
        keys = ()
    elif context == "full_budget":
        attempted += ("read_vitals",) * 6
        keys += (mutation_key("read_vitals", "primary-interface", {}),) * 6
    assert snapshot_retry_allowed(
        "restore_proxy", attempted, 1, attempt_keys=keys, current_key=current
    ) is (context == "new_path")
    assert mutation_key("restore_proxy", "primary-interface", {"b": 2, "a": 1}) == mutation_key(
        "restore_proxy", "primary-interface", {"a": 1, "b": 2}
    )
