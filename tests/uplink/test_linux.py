import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.contracts import Scope
from offline_agent.uplink.linux import (
    FAULTS,
    LinuxAdapter,
    execute_plan,
    injection_plan,
    require_execution,
    validate_plan,
)
from offline_agent.uplink.portal import response


@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_dry_run_roundtrips_without_spawning(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = Mock(side_effect=AssertionError("injection must never run"))
    monkeypatch.setattr("subprocess.run", spawn)
    monkeypatch.setattr("subprocess.Popen", spawn)
    plan = injection_plan(fault, 7)
    assert validate_plan(json.dumps(plan.as_json())) == plan
    assert not plan.as_json()["executable"] and len(plan.namespaces) == 3
    assert any("dnsmasq" in step.argv for step in plan.services)
    assert any("tinyproxy" in step.argv for step in plan.services)
    assert plan.blockers
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "fault,seed", [("shell", 0), ("healthy", -1), ("healthy", 10000), ("healthy", True)]
)
def test_invalid_fault_or_seed_refused(fault: str, seed: int) -> None:
    with pytest.raises(ContractError):
        injection_plan(fault, seed)


def test_arbitrary_command_in_plan_refused() -> None:
    plan = injection_plan("healthy")
    bad = replace(plan, setup=(replace(plan.setup[0], argv=("sh", "-c", "id")),))
    with pytest.raises(ContractError, match="drift"):
        validate_plan(json.dumps(bad.as_json()))


@pytest.mark.parametrize(
    "execute,system,uid,under_test,reason",
    [
        (False, "Linux", 0, False, "explicit_execute"),
        (True, "Darwin", 0, False, "real_linux"),
        (True, "Linux", 501, False, "root_required"),
        (True, "Linux", 0, True, "never_executes_in_tests"),
    ],
)
def test_injector_preconditions(
    execute: bool, system: str, uid: int, under_test: bool, reason: str
) -> None:
    with pytest.raises(ContractError, match=reason):
        require_execution(execute=execute, system=system, uid=uid, under_test=under_test)


def test_even_linux_root_explicit_execute_is_inert_u1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    spy = Mock(side_effect=AssertionError("no executor may spawn"))
    monkeypatch.setattr("subprocess.run", spy)
    with pytest.raises(ContractError, match="never_executes_in_tests"):
        execute_plan(injection_plan("wrong_dns"), execute=True)
    # Simulate a non-pytest caller to pin the independent, unconditional U1 veto.
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with pytest.raises(ContractError, match="no_executor"):
        execute_plan(injection_plan("wrong_dns"), execute=True)
    spy.assert_not_called()


def test_linux_adapter_uses_shared_contract() -> None:
    adapter = LinuxAdapter(Scope())
    action = {"version": 1, "tool": "read_vitals", "args": {}}
    assert adapter.plan(json.dumps(action))["action"] == action
    with pytest.raises(ContractError):
        adapter.plan('{"version":1,"tool":"shell","args":{"argv":["id"]}}')


def test_pure_portal_stub_has_success_denial_and_invalid_cases() -> None:
    assert response("healthy").status == 204
    assert (
        response("captive_portal").status == 200
        and b"Success" not in response("captive_portal").body
    )
    assert response("site_only_outage").status == 503
    with pytest.raises(ValueError):
        response("arbitrary")
