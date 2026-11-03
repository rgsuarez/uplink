"""Measured reversible options outrank layer labels, with unchanged acceptance oracles."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import io
import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import Json
from offline_agent.uplink import lab_worker
from offline_agent.uplink.contracts import Scope, Tool
from offline_agent.uplink.lab_protocol import request
from offline_agent.uplink.lab_runner import Emission, GlimmerPolicy, LabState
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.probe_execution import connect_socket, macos_sandbox
from offline_agent.uplink.probe_policy import choose_with_probes, permitted_actions, probe_facts
from offline_agent.uplink.probe_protocol import ProbeRefused
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_protocol import request as real_request

ROOT = Path(__file__).resolve().parents[2]
SCOPE = Scope(("service-a",), ("candidate-a",), ("snapshot-a",), ("dns-1",))


def offline() -> OracleResult:
    return OracleResult(Verdict.OFFLINE, 1, ())


def alternate_result() -> dict[str, Json]:
    return {
        "status": "completed",
        "settings_unchanged": True,
        "rows": [
            {
                "probe": {"op": "ping_gateway", "path": "alternate"},
                "ok": True,
                "status": "completed",
            },
            {
                "probe": {"op": "dns_query", "resolver": "alternate"},
                "ok": True,
                "status": "completed",
            },
        ],
    }


@pytest.mark.parametrize("layer", ["link", "ip", "dns", "proxy", "captive", "upstream"])
def test_layer_label_cannot_block_qualified_reroute_or_allow_early_handoff(layer: str) -> None:
    facts: dict[str, Json] = {"layer": layer, "link": {"up": layer != "link"}}
    assert permitted_actions(SCOPE, facts, offline(), alternate_qualified=True, attempted=()) == (
        Tool.REROUTE,
    )
    assert Tool.REPORT not in permitted_actions(
        SCOPE, facts, offline(), alternate_qualified=True, attempted=()
    )


def test_cycle_is_second_choice_only_for_measured_link_failure() -> None:
    facts: dict[str, Json] = {"link": {"up": False}}
    assert permitted_actions(
        SCOPE, facts, offline(), alternate_qualified=True, attempted=("reroute",)
    ) == (Tool.CYCLE_INTERFACE,)
    assert permitted_actions(
        SCOPE, facts, offline(), alternate_qualified=True, attempted=("reroute", "cycle_interface")
    ) == (Tool.REPORT, Tool.GIVE_UP)
    facts["link"] = {"up": True}
    assert Tool.CYCLE_INTERFACE not in permitted_actions(
        SCOPE, facts, offline(), alternate_qualified=False, attempted=()
    )


def test_snapshot_drift_order_and_healthy_noop_are_explicit() -> None:
    facts: dict[str, Json] = {
        "snapshot-dns": {"changed": True},
        "snapshot-proxy": {"changed": True},
    }
    assert permitted_actions(SCOPE, facts, offline(), alternate_qualified=True, attempted=()) == (
        Tool.SET_DNS,
    )
    assert permitted_actions(
        SCOPE, facts, offline(), alternate_qualified=True, attempted=("set_dns",)
    ) == (Tool.RESTORE_PROXY,)
    assert permitted_actions(
        SCOPE, facts, OracleResult(Verdict.ONLINE, 1, ()), alternate_qualified=True, attempted=()
    ) == (Tool.DECLARE_ONLINE,)
    mac = Scope(
        SCOPE.services, (), SCOPE.snapshots, SCOPE.evidence, profile="lab_dns_proxy_snapshot"
    )
    assert Tool.CYCLE_INTERFACE not in permitted_actions(
        mac, {"link": {"up": False}}, offline(), alternate_qualified=True, attempted=()
    )


@pytest.mark.parametrize("missing", ["ping", "dns", "all", "failed"])
def test_alternate_requires_both_fresh_successful_probe_types(missing: str) -> None:
    result = alternate_result()
    rows = result["rows"]
    assert isinstance(rows, list)
    if missing == "ping":
        result["rows"] = rows[1:]
    elif missing == "dns":
        result["rows"] = rows[:1]
    elif missing == "all":
        result["rows"] = []
    else:
        result["status"] = "timeout"
    assert probe_facts(result)[1] is False


def test_pre_and_post_hypotheses_raw_program_and_typed_repair_stay_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = {
        "hypothesis_before": {"layer": "upstream", "evidence": ["snapshot-dns"]},
        "program": {
            "version": 1,
            "probes": [
                {
                    "op": "ping_gateway",
                    "path": "alternate",
                    "count": 1,
                    "interval_ms": 200,
                    "timeout_ms": 1000,
                },
                {"op": "dns_query", "name": "one", "resolver": "alternate", "timeout_ms": 1000},
            ],
        },
    }
    action: dict[str, Json] = {
        "version": 1,
        "tool": "reroute",
        "args": {
            "snapshot_id": "snapshot-a",
            "candidate_id": "candidate-a",
            "why": "alternate_route",
            "evidence_ids": ["dns-1"],
        },
    }
    # A wrong post-probe layer label does not hide the correctly proven route option.
    after = {"hypothesis_after": {"layer": "dns", "evidence": ["probe-2"]}, "action": action}
    policy = GlimmerPolicy(ROOT, execute=True, probe_scripts=True)
    generate = Mock(side_effect=[Emission(json.dumps(before), {}), Emission(json.dumps(after), {})])
    monkeypatch.setattr(policy, "generate", generate)
    executor = Mock(return_value=alternate_result())
    policy.probe_executor = executor
    observed = LabState(
        {
            "current": {
                "primary_up": True,
                "lease_present": True,
                "default_gateway": "synthetic",
                "dns_server": "same",
                "proxy_url": "same",
            },
            "known_good_snapshot": {"dns_server": "same", "proxy_url": "same"},
            "prior_alternate_online": True,
        },
        "",
        "",
        True,
        True,
        None,
    )
    result = choose_with_probes(policy, SCOPE, observed, offline())
    assert json.loads(result.text) == action and result.raw_text == json.dumps(after)
    assert policy.probe_result["script_verbatim"] == json.dumps(before)
    assert policy.probe_result["hypothesis_after"] == after["hypothesis_after"]
    assert "isp_down_saved_alternate" not in str(generate.call_args_list)
    assert "mtu_black_hole" not in str(generate.call_args_list)
    executor.assert_called_once_with(before["program"])


@pytest.mark.parametrize("seconds,flag", [(55, False), (56, True), (60, True), (True, True)])
def test_extended_linux_watchdog_requires_probe_profile_and_stays_below_sixty(
    seconds: int, flag: bool
) -> None:
    with pytest.raises(ValueError):
        request(
            "arm", "0123456789ab", {"seconds": seconds, **({"probe_scripts": True} if flag else {})}
        )


def test_explicit_probe_profiles_preserve_default_watchdog_limits() -> None:
    assert request("arm", "0123456789ab", {"seconds": 55, "probe_scripts": True})
    assert RealSpec("0123456789ab", "dns_and_proxy").seconds == 40
    assert real_request(
        "prepare", "0123456789ab", {"fault": "dns_and_proxy", "seconds": 55, "probe_scripts": True}
    )
    with pytest.raises(ValueError):
        real_request("prepare", "0123456789ab", {"fault": "dns_and_proxy", "seconds": 55})
    with pytest.raises(ValueError):
        RealSpec("0123456789ab", "dns_and_proxy", 56, True)


def test_command_failure_keeps_stderr_argv_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    argv = ("busybox", "udhcpc", "-n")
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess(argv, 1, "", "lease refused")),
    )
    with pytest.raises(lab_worker.LabCommandFailed) as error:
        lab_worker.run(argv)
    assert error.value.details == {
        "argv": list(argv),
        "exit_code": 1,
        "stdout": "",
        "stderr": "lease refused",
    }


def test_mac_tcp_is_interface_bound_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_public_fixture("@PUBLIC_IPV4_066@"), 443))],
    )
    monkeypatch.setattr(socket, "if_nametoindex", lambda iface: 4)
    stream = Mock()
    monkeypatch.setattr(socket, "socket", Mock(return_value=stream))
    assert connect_socket({"interface": "en0"}, ("fixed.test", 443), 1, (_public_fixture("@PUBLIC_IPV4_072@"), 0)) is stream
    stream.setsockopt.assert_called_once_with(socket.IPPROTO_IP, 25, 4)
    stream.bind.assert_called_once_with((_public_fixture("@PUBLIC_IPV4_072@"), 0))
    calls = [call[0] for call in stream.method_calls]
    assert calls.index("setsockopt") < calls.index("connect")
    assert calls.index("bind") < calls.index("connect")


def test_mac_sandbox_has_no_settings_commands_or_writable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink import lab_config

    config = json.loads(lab_config.CONFIG_PATH.read_text())
    config["node"] = "probe-fixture-29"
    lab_config.CONFIG_PATH.write_text(json.dumps(config))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "probe-fixture-29.local")
    monkeypatch.setattr("os.geteuid", lambda: 501)
    argv = macos_sandbox(tmp_path)
    profile = argv[2]
    assert "(deny default)" in profile
    assert '(allow file-write* (literal "/dev/null"))' in profile
    assert "file-write" not in profile.replace('(allow file-write* (literal "/dev/null"))', "")
    assert "sudo" not in profile and "networksetup" not in argv
    assert argv[3:7] == (str(Path(sys.executable).resolve()), "-I", "-S", "-B")


def test_mac_sandbox_uses_base_interpreter_without_opening_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "runtime/bin/python3"
    base.parent.mkdir(parents=True)
    base.write_text("synthetic interpreter, never executed")
    virtual = tmp_path / "private-project/.venv/bin/python"
    virtual.parent.mkdir(parents=True)
    virtual.symlink_to(base)
    (virtual.parent.parent / "pyvenv.cfg").write_text("synthetic private configuration")
    monkeypatch.setattr(sys, "executable", str(virtual))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("os.geteuid", lambda: 501)
    argv = macos_sandbox(tmp_path / "episode")
    assert argv[3:7] == (str(base), "-I", "-S", "-B")
    assert str(virtual.parent.parent) not in argv[2]
    assert "(deny default)" in argv[2]
    assert '(allow file-write* (literal "/dev/null"))' in argv[2]


@pytest.mark.parametrize("host", ("lab", "unlisted-fixture", "probe-fixture-290"))
def test_mac_sandbox_refuses_host_not_named_in_private_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    from offline_agent.uplink import lab_config

    config = json.loads(lab_config.CONFIG_PATH.read_text())
    config["node"] = "probe-fixture-29"
    lab_config.CONFIG_PATH.write_text(json.dumps(config))
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: host)
    monkeypatch.setattr("os.geteuid", lambda: 501)
    with pytest.raises(ProbeRefused, match="unprivileged_lab_probe_required"):
        macos_sandbox(tmp_path)


@pytest.mark.parametrize("config_state", ("missing", "invalid", "public"))
def test_mac_sandbox_refuses_unavailable_private_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_state: str
) -> None:
    from offline_agent.uplink import lab_config

    if config_state == "missing":
        monkeypatch.setattr(lab_config, "CONFIG_PATH", tmp_path / "missing-config.json")
    elif config_state == "invalid":
        lab_config.CONFIG_PATH.write_text("{}")
    else:
        lab_config.CONFIG_PATH.chmod(0o644)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("os.geteuid", lambda: 501)
    with pytest.raises(ProbeRefused, match="unprivileged_lab_probe_required"):
        macos_sandbox(tmp_path)


@pytest.mark.parametrize("system,uid", (("Darwin", 0), ("Linux", 501)))
def test_mac_sandbox_keeps_platform_and_privilege_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str, uid: int
) -> None:
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("os.geteuid", lambda: uid)
    with pytest.raises(ProbeRefused, match="unprivileged_lab_probe_required"):
        macos_sandbox(tmp_path)


@pytest.mark.parametrize("oracle", ["ONLINE", "OFFLINE"])
def test_overlay_uses_recorded_independent_verdict_without_strengthening_it(oracle: str) -> None:
    from offline_agent.uplink.probe_overlay import overlay

    from .probe_receipts import returned_measurement

    execution = returned_measurement()
    result = overlay(
        {"probe_enabled": False, "probe_episodes": []},
        [
            {"oracle": {"verdict": oracle, "evidence": []}},
            {
                "step": 1,
                "observation": {
                    "source": "generated_probe_episode",
                    "exit_code": 0,
                    "stdout": json.dumps(
                        {"program": execution["requested"], "execution": execution}
                    ),
                },
            },
        ],
    )
    assert result["independent_online_check"] == {"verdict": oracle, "evidence": []}
    assert result["probe_enabled"] is True


def test_overlay_refuses_missing_probe_or_oracle_evidence() -> None:
    from offline_agent.contracts import ContractError
    from offline_agent.uplink.probe_overlay import overlay

    with pytest.raises(ContractError):
        overlay({}, [])
    with pytest.raises(ContractError):
        overlay({"probe_episodes": [{}]}, [])


@pytest.mark.parametrize(
    "mode,path,status",
    [
        ("healthy", "/ok", 204),
        ("captive", "/portal", 200),
        ("site_down", "/app", 503),
    ],
)
def test_fixture_head_preserves_get_status_without_returning_body(
    tmp_path: Path,
    mode: str,
    path: str,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.lab_servers import Handler

    instance = Handler.__new__(Handler)
    instance.state = tmp_path / "state.json"
    instance.state.write_text(json.dumps({"stub": mode}))
    instance.path, instance.command = path, "HEAD"
    instance.wfile = io.BytesIO()
    sent = Mock()
    monkeypatch.setattr(instance, "send_response", sent)
    monkeypatch.setattr(instance, "send_header", Mock())
    monkeypatch.setattr(instance, "end_headers", Mock())
    instance.do_HEAD()
    sent.assert_called_once_with(status)
    assert instance.wfile.getvalue() == b""
    instance.command = "GET"
    sent.reset_mock()
    instance.do_GET()
    sent.assert_called_once_with(status)
    assert bool(instance.wfile.getvalue()) == (status != 204)


def test_overlay_text_has_only_receipt_timing_and_independent_verdict() -> None:
    from offline_agent.uplink.probe_overlay import render

    text = render(
        {
            "decisions": [
                {
                    "decision_ms": 123.5,
                    "settings_unchanged": True,
                    "hypothesis_before": {"layer": "dns"},
                    "hypothesis_after": {"layer": "upstream"},
                    "action": {"tool": "reroute"},
                    "probe_results": [],
                }
            ],
            "independent_online_check": {"verdict": "OFFLINE"},
        }
    )
    assert "123.5 ms" in text and "dns -> upstream" in text and "Action: reroute" in text
    assert text.endswith("Independent check: OFFLINE")


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("wrong_dns", "recovered"),
        ("dns_plus_proxy", "recovered"),
        ("healthy", "healthy_noop"),
        ("site_only_outage", "site_report_required"),
    ],
)
def test_probe_controller_finishes_from_independent_app_check_without_extra_model_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    expected: str,
) -> None:
    from offline_agent.uplink.lab_protocol import LabSpec
    from offline_agent.uplink.lab_runner import ScriptedPolicy, evaluate
    from offline_agent.uplink.lab_simulator import SimulatedClient

    spec = LabSpec("0123456789ab")
    client = SimulatedClient(spec)
    policy = GlimmerPolicy(ROOT, execute=False, probe_scripts=True)
    choose = Mock(side_effect=ScriptedPolicy().choose)
    monkeypatch.setattr(policy, "choose", choose)
    result = evaluate(ROOT, client, spec, fault, policy, tmp_path, dry=True)
    assert result["outcome"] == expected
    assert result["app_resumed"] == (expected in {"recovered", "healthy_noop"})
    assert choose.call_count == (2 if fault == "dns_plus_proxy" else 1)
    assert sum(row["operation"] == "arm" for row in client.calls) == 2
    assert client.created is False


@pytest.mark.parametrize("invalid", ["missing_dns", "primary_ping", "duplicate_ping"])
def test_route_decision_program_requires_both_fresh_alternate_checks(invalid: str) -> None:
    from jsonschema import Draft202012Validator

    from offline_agent.contracts import object_json, read_json
    from offline_agent.uplink.grammar import schema_gbnf
    from offline_agent.uplink.probe_policy import require_route_tests
    from offline_agent.uplink.probe_protocol import program_schema

    schema = object_json(read_json(json.dumps(program_schema("linux_lab", alternate=True))))
    require_route_tests(schema)
    ping = {
        "op": "ping_gateway",
        "path": "alternate",
        "count": 1,
        "interval_ms": 200,
        "timeout_ms": 1000,
    }
    dns = {"op": "dns_query", "name": "one", "resolver": "alternate", "timeout_ms": 1000}
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"version": 1, "probes": [ping, dns]})
    rows = (
        [ping]
        if invalid == "missing_dns"
        else [{**ping, "path": "current"}, dns]
        if invalid == "primary_ping"
        else [ping, ping]
    )
    assert not validator.is_valid({"version": 1, "probes": rows})
    grammar = schema_gbnf(schema)
    assert "alternate" in grammar and "read_state" not in grammar
