"""A probe is bounded data; generated code never crosses the execution boundary."""

import json
import os
import resource
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from offline_agent.uplink import probe_execution as execution
from offline_agent.uplink.probe_protocol import (
    ProbeBudget,
    ProbeRefused,
    fields,
    parse_program,
    program_schema,
    validate_program,
)


def program(op: str = "read_state", **values: object) -> dict[str, object]:
    return {"version": 1, "probes": [{"op": op, "timeout_ms": 1000, **values}]}


@pytest.mark.parametrize("profile", ["linux_lab", "lab_wifi"])
def test_every_allowlisted_probe_has_a_closed_schema(profile: str) -> None:
    for op, arguments in fields(profile, True).items():
        value = program(op, **{k: v[0] for k, v in arguments.items()})
        assert validate_program(value, profile, alternate=True)[0].op == op
    assert program_schema(profile)["additionalProperties"] is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"version": True, "probes": []},
        {"version": 1, "probes": []},
        {"version": 1, "probes": [program()["probes"]]},
        program("sudo"),
        program("read_state", argv=["touch", "file"]),
        program("read_state", timeout_ms=True),
        program("read_state", timeout_ms=6000),
        program("dns_query", name="arbitrary.example", resolver="configured"),
        program("dns_query", name="one", resolver="https://example.com"),
        program("tcp_connect", endpoint="one", path="alternate"),
        program("ping_gateway", path="current", count=21, interval_ms=200),
        program("ping_gateway", path="current", count=20, interval_ms=100),
        program("ping_gateway", path="current", count=20, interval_ms=200, timeout_ms=1000),
        {"version": 1, "probes": [{"op": "read_state", "timeout_ms": 500}] * 9},
        {"version": 1, "probes": [{"op": "read_state", "timeout_ms": 5000}] * 7},
    ],
)
def test_refuses_code_unknown_targets_privilege_and_excess_budget(value: object) -> None:
    with pytest.raises(ProbeRefused):
        validate_program(value, "linux_lab")


def test_profile_does_not_advertise_an_unqualified_alternate() -> None:
    value = program("ping_gateway", path="alternate", count=1, interval_ms=200)
    with pytest.raises(ProbeRefused):
        validate_program(value, "linux_lab")
    assert validate_program(value, "linux_lab", alternate=True)
    with pytest.raises(ProbeRefused):
        validate_program(value, "lab_wifi", alternate=True)
    with pytest.raises(ProbeRefused):
        validate_program(program(), "other")


@pytest.mark.parametrize("value", ["{", "x" * 16385, "null"])
def test_parser_records_invalid_input_as_refusal(value: str) -> None:
    with pytest.raises(ProbeRefused):
        parse_program(value, "linux_lab")


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempted", -1),
        ("attempted", 9),
        ("attempted", True),
        ("reserved_ms", -1),
        ("reserved_ms", 30001),
        ("elapsed_ms", -1),
        ("elapsed_ms", float("nan")),
        ("elapsed_ms", float("inf")),
    ],
)
def test_persisted_budget_cannot_reset_or_invent_capacity(field: str, value: object) -> None:
    with pytest.raises(ProbeRefused):
        ProbeBudget.read({**ProbeBudget().as_json(), field: value})


def test_budget_is_shared_across_scripts_and_reserved_before_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sandbox(value: dict[str, object], directory: Path, timeout: float) -> dict[str, object]:
        assert json.loads((tmp_path / "budget.json").read_text())["attempted"] >= 1
        return {"status": "completed", "uid": 65534, "rows": []}

    monkeypatch.setattr(execution, "execute_sandbox", sandbox)
    context: dict[str, object] = {"profile": "linux_lab"}
    for _ in range(8):
        assert (
            execution.episode(program(), context, tmp_path, limit_ms=5000)["status"] == "completed"
        )
    refused = execution.episode(program(), context, tmp_path, limit_ms=5000)
    assert refused["status"] == "refused" and refused["reason"] == "probe_episode_budget_exceeded"
    assert json.loads((tmp_path / "budget.json").read_text())["attempted"] == 8


def test_script_timeout_and_missing_sandbox_stay_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "execute_sandbox", Mock(side_effect=FileNotFoundError()))
    result = execution.episode(program(), {"profile": "lab_wifi"}, tmp_path, limit_ms=5000)
    assert result["status"] == "refused" and result["budget"]["attempted"] == 1  # type: ignore[index]
    monkeypatch.setattr(
        execution, "execute_sandbox", Mock(return_value={"status": "timeout", "rows": []})
    )
    assert (
        execution.episode(program(), {"profile": "lab_wifi"}, tmp_path, limit_ms=5000)["status"]
        == "timeout"
    )


def test_probe_timeout_is_recorded_without_running_a_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 65534)
    monkeypatch.setattr(resource, "setrlimit", Mock())
    monkeypatch.setattr(signal, "signal", Mock())
    timer = Mock()
    monkeypatch.setattr(signal, "setitimer", timer)
    monkeypatch.setattr(execution, "primitive", Mock(side_effect=TimeoutError()))
    result = execution.child({"context": {"profile": "linux_lab"}, "program": program()})
    assert result["rows"][0]["status"] == "timeout"  # type: ignore[index]
    assert timer.call_args.args[1] == 0


def test_probe_child_refuses_root_before_any_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    primitive = Mock()
    monkeypatch.setattr(execution, "primitive", primitive)
    with pytest.raises(ProbeRefused, match="unprivileged"):
        execution.child({"context": {"profile": "linux_lab"}})
    primitive.assert_not_called()


def test_no_live_child_under_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 65534)
    with pytest.raises(ProbeRefused, match="forbidden_in_tests"):
        execution.child({"context": {"profile": "linux_lab"}})


def test_sandbox_spawn_timeout_kills_its_own_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "macos_sandbox", lambda directory: ("sandbox-fixture",))
    proc = Mock(pid=99999)
    proc.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 1), ("", "")]
    popen = MagicMock()
    popen.return_value.__enter__.return_value = proc
    monkeypatch.setattr(subprocess, "Popen", popen)
    kill = Mock()
    monkeypatch.setattr(os, "killpg", kill)
    result = execution.execute_sandbox({"context": {"profile": "lab_wifi"}}, tmp_path, 1)
    assert result["status"] == "timeout"
    kill.assert_called_once_with(99999, signal.SIGKILL)


def test_no_root_sandbox_result_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "macos_sandbox", lambda directory: ("sandbox-fixture",))
    proc = Mock(returncode=0)
    proc.communicate.return_value = ('{"uid":0,"rows":[]}', "")
    popen = MagicMock()
    popen.return_value.__enter__.return_value = proc
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(ProbeRefused, match="privilege_drop_unproven"):
        execution.execute_sandbox({"context": {"profile": "lab_wifi"}}, tmp_path, 1)
