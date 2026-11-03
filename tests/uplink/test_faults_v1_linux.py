"""Namespace-only command and controller contracts, using fakes for every effect."""

import importlib.util
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import Mock

import pytest

from offline_agent.contracts import Json
from offline_agent.uplink import faults_v1_linux as f
from offline_agent.uplink import lab_worker
from offline_agent.uplink.lab_protocol import FAULT_PACKAGES, FAULTS, LAB_VERSION, LabSpec, request
from offline_agent.uplink.lab_runner import probe
from offline_agent.uplink.lab_simulator import SimulatedClient
from offline_agent.uplink.privacy import Redactor


@pytest.mark.parametrize("fault", f.FAULTS)
def test_new_fixtures_require_their_own_setup_and_snapshot(fault: str) -> None:
    assert fault in FAULT_PACKAGES and fault not in FAULTS and len(FAULTS) == 12
    value = request("up", "0123456789ab", {"fault_package": fault})
    assert lab_worker.dispatch(value, execute=False)["effects"] is False
    with pytest.raises(ValueError, match="setup_required"):
        lab_worker.inject(LabSpec("0123456789ab"), {}, fault)


@pytest.mark.parametrize("fault", f.FAULTS)
def test_injection_commands_never_leave_owned_namespace(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Mock()
    s = LabSpec("0123456789ab")
    state: dict[str, object] = {
        "fault_package": fault,
        "fault_package_snapshot": {"stable": True},
        "fault_package_injected": False,
    }
    monkeypatch.setattr(f, "capture", lambda *a: {"stable": True})
    f.inject(s, state, cast(ModuleType, worker))
    calls = [call.args[0] for call in worker.run.call_args_list]
    assert calls and all(
        cmd[:3] == ("ip", "-n", s.client) or cmd[:4] == ("ip", "netns", "exec", s.client)
        for cmd in calls
    )
    assert not any("default" in cmd or "flush" in cmd for cmd in calls)
    if fault == "owned_pf_outbound":
        assert all(f.table(s) in cmd for cmd in calls)
        assert "output" in calls[-1] and "input" not in calls[-1]
    assert state["fault_package_injected"] is True
    worker.atomic_json.assert_called_once()  # Recorded before any possible partial mutation.
    with pytest.raises(ValueError, match="one_attempt"):
        f.inject(s, state, cast(ModuleType, worker))


def test_snapshot_drift_refuses_before_any_linux_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = Mock()
    monkeypatch.setattr(f, "capture", lambda *a: {"changed": True})
    with pytest.raises(ValueError, match="preinject_drift"):
        f.inject(
            LabSpec("0123456789ab"),
            {
                "fault_package": "test_host_blackhole",
                "fault_package_snapshot": {},
                "fault_package_injected": False,
            },
            cast(ModuleType, worker),
        )
    worker.run.assert_not_called()


@pytest.mark.parametrize("fault", f.FAULTS)
def test_only_matching_snapshot_action_can_repair_fixture(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repair = Mock()
    monkeypatch.setattr(f, "restore", repair)
    s = LabSpec("0123456789ab")
    state: dict[str, object] = {"fault_package": fault}
    lab_worker.apply(s, state, f.TOOLS[f.FAULTS.index(fault)], "snapshot")
    repair.assert_called_once()
    with pytest.raises(ValueError, match="action_refused"):
        lab_worker.apply(s, state, "reroute", "alternate")


def test_linux_restore_mismatch_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = Mock()
    monkeypatch.setattr(f, "capture", lambda *a: {"different": True})
    with pytest.raises(ValueError, match="exact_restore_mismatch"):
        f.restore(
            LabSpec("0123456789ab"),
            {"fault_package_snapshot": {"stable": True}, "fault_package_injected": False},
            cast(ModuleType, worker),
        )
    worker.atomic_json.assert_called_once()


def test_filter_lookup_errors_never_count_as_an_empty_table() -> None:
    worker = Mock()
    s = LabSpec("0123456789ab")
    worker.run.return_value = subprocess.CompletedProcess([], 1, "", "No such file or directory")
    assert f.filter_exists(s, cast(ModuleType, worker)) is False
    worker.run.return_value = subprocess.CompletedProcess([], 0, "table inet owned", "")
    assert f.filter_exists(s, cast(ModuleType, worker)) is True
    worker.run.return_value = subprocess.CompletedProcess([], 1, "", "Operation not permitted")
    with pytest.raises(ValueError, match="state_unavailable"):
        f.filter_exists(s, cast(ModuleType, worker))


class FakeClient:
    def __init__(self, *, broken: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.stage = "baseline"
        self.breaks = broken

    def command(self, operation: str, payload: dict[str, object] | None = None) -> tuple[str, ...]:
        return (operation,)

    def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
        self.calls.append((operation, payload))
        if operation == "inject":
            self.stage = "broken" if self.breaks else "baseline"
        if operation in {"apply", "undo"}:
            self.stage = "restored"
        if operation == "fault_package_check":
            return {
                "target": {"ok": self.stage != "broken"},
                "snapshot_match": self.stage != "broken",
            }
        return {"ok": True}


def runner() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts/run_faults_v1_linux.py"
    spec = importlib.util.spec_from_file_location("fault_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def healthy_oracle() -> dict[str, Json]:
    client = SimulatedClient(LabSpec("0123456789ab"))
    client.call("up")
    return probe(client, Redactor(b"x" * 32)).as_json()


@pytest.mark.parametrize("fault", f.FAULTS)
def test_linux_smoke_requires_before_broken_repaired_and_cleanup(fault: str) -> None:
    client = FakeClient()
    result = runner().run_case(client, fault, healthy_oracle)
    assert result["passed"] is True and result["model_calls"] == 0
    operations = [c[0] for c in client.calls]
    assert operations == [
        "up",
        "fault_package_check",
        "arm",
        "inject",
        "fault_package_check",
        "apply",
        "fault_package_check",
        "disarm",
        "down",
    ]


def test_ineffective_fault_and_failed_oracle_stop_without_retry() -> None:
    client = FakeClient(broken=False)
    result = runner().run_case(client, f.FAULTS[0], healthy_oracle)
    assert result["passed"] is False and result["reason"] == "package_fault_not_effective"
    assert [c[0] for c in client.calls].count("inject") == 1
    assert client.calls[-2:] == [("undo", None), ("down", None)]
    client = FakeClient()

    def refused() -> dict[str, Json]:
        raise ValueError("package_online_oracle_failed")

    result = runner().run_case(client, f.FAULTS[0], refused)
    assert result["passed"] is False and not any(c[0] == "inject" for c in client.calls)


def test_smoke_accepts_existing_linux_oracle_without_using_mac_version_gate() -> None:
    client = SimulatedClient(LabSpec("0123456789ab"))
    client.call("up")
    result = probe(client, Redactor(b"x" * 32))
    assert result.version == LAB_VERSION and result.verdict.value == "ONLINE"
    assert result.fresh_online(time.time()) is False
    runner().require_lab_online(result.as_json(), time.time())
    for changed in (
        replace(result, version="uplink-online-v1"),
        replace(result, evidence=result.evidence[:-1]),
        replace(result, checked_at=time.time() - 31),
        replace(result, evidence=(replace(result.evidence[0], ok=False), *result.evidence[1:])),
    ):
        with pytest.raises(ValueError, match="package_online_oracle_failed"):
            runner().require_lab_online(changed.as_json(), time.time())


def test_failed_baseline_oracle_is_saved_in_case_receipt() -> None:
    client = FakeClient()
    document = healthy_oracle()
    document["verdict"] = "OFFLINE"
    result = runner().run_case(client, f.FAULTS[0], lambda: document)
    assert result["passed"] is False and result["before_online"] == document
    assert result["reason"] == "package_online_oracle_failed"
    assert not any(c[0] == "inject" for c in client.calls)
