"""Sealed fixture invariants. No model, socket, SSH, namespace or root execution.

Pure contracts are small tests; temporary-file and mocked-worker tests are medium.
Assertions pin section 6.1 mechanisms, not an observed model answer.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import copy
import hashlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from offline_agent.uplink import lab_worker, sealed_linux, sealed_qualify, sealed_registry
from offline_agent.uplink.lab_protocol import FAULTS, PROXY_URL, LabSpec, request
from offline_agent.uplink.lab_stage import SEALED_FILES, SEALED_WORKERS, WorkerBundle

ROOT = Path(__file__).resolve().parents[2]


def chosen(code: str) -> dict[str, object]:
    path = ROOT / "harness/linux/sealed" / (code + ".json")
    return {"fixture": code, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize(
    "code,flags,actions",
    [
        ("P04", "TTFFTT", ["restore_proxy"]),
        ("D09", "FFFFFF", ["set_dns"]),
        ("X02", "FFFFTT", ["set_dns", "reroute"]),
        ("G02", "FFFFFF", ["reroute"]),
        ("I04", "TTFFFF", ["reroute"]),
        ("I02", "FFFFFF", ["reroute"]),
        ("X03", "TTFFFF", ["restore_proxy"]),
        ("X04", "FFTTFF", ["set_dns"]),
        ("C04", "TTTFTF", []),
        ("P07", "TTFFFF", []),
        ("F05", "TTTTTT", []),
        ("V03", "TTTTTT", []),
    ],
)
def test_sealed_descriptor_matches_catalog_mechanism(
    code: str, flags: str, actions: list[str]
) -> None:
    fixture = sealed_registry.load(chosen(code))
    stages = fixture["stages"]
    assert isinstance(stages, list)
    assert stages[0]["flags"] == flags
    assert [s["after"] for s in stages[1:]] == actions
    assert code not in FAULTS
    assert fixture["role"] == ("control" if code in {"F05", "V03"} else "fault")


@pytest.mark.parametrize(
    "payload",
    [
        {"fixture": "../../outside", "sha256": "0" * 64},
        {"fixture": "P04", "sha256": "bad"},
        {"fixture": "P04", "sha256": "0" * 64, "shell": "id"},
        {"fixture": "R04", "sha256": "0" * 64},
    ],
)
def test_request_refuses_unsealed_or_arbitrary_operations(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        request("up", "0123456789ab", payload)


def test_tampered_missing_and_symlinked_fixture_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "P04.json"
    selected = chosen("P04")
    monkeypatch.setattr(sealed_registry, "fixture_path", lambda _: path)
    with pytest.raises(ValueError, match="file_refused"):
        sealed_registry.load(selected)
    path.write_text("{}\n")
    with pytest.raises(ValueError, match="hash_mismatch"):
        sealed_registry.load(selected)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    monkeypatch.setattr(sealed_registry, "fixture_path", lambda _: link)
    with pytest.raises(ValueError, match="file_refused"):
        sealed_registry.load(selected)


def test_default_worker_bundle_excludes_sealed_cases() -> None:
    source = ROOT / "src/offline_agent/uplink"
    normal = WorkerBundle.read(source)
    sealed = WorkerBundle.read(source, sealed=True)
    assert not set(SEALED_FILES) & normal.hashes.keys()
    assert not set(SEALED_WORKERS) & normal.hashes.keys()
    assert set(SEALED_FILES) <= sealed.hashes.keys()
    assert set(SEALED_WORKERS) <= sealed.hashes.keys()
    assert normal.directory != sealed.directory
    assert len(sealed.sources) == len(sealed.hashes)


def test_live_fixture_cannot_execute_on_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    setup = Mock(side_effect=AssertionError("must not create a namespace"))
    monkeypatch.setattr(lab_worker, "setup", setup)
    with pytest.raises(ValueError, match="explicit_root_execution"):
        lab_worker.dispatch(request("up", "0123456789ab", chosen("P04")), execute=True)
    setup.assert_not_called()


def test_qualifier_is_inert_without_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatch = Mock(side_effect=AssertionError("no worker invocation"))
    monkeypatch.setattr(lab_worker, "dispatch", dispatch)
    receipt = sealed_qualify.qualify(chosen("P04"), "0123456789ab")
    assert receipt["effects"] is False and receipt["model_calls"] == 0
    dispatch.assert_not_called()


@pytest.fixture
def fixture_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[LabSpec, Mock]:
    state = tmp_path / "state"
    resolver = tmp_path / "resolver"
    state.mkdir()
    resolver.mkdir()
    monkeypatch.setattr(LabSpec, "state", property(lambda _: state))
    monkeypatch.setattr(LabSpec, "resolver_directory", property(lambda _: resolver))
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"nftables": []}', ""))
    monkeypatch.setattr(lab_worker, "run", runner)
    monkeypatch.setattr(lab_worker, "atomic_json", Mock())
    monkeypatch.setattr(lab_worker, "audit", Mock())
    monkeypatch.setattr(lab_worker, "renew", Mock())
    monkeypatch.setattr(sealed_linux, "signal_dns", Mock())
    return LabSpec("0123456789ab"), runner


@pytest.mark.parametrize("code", sealed_registry.CODES)
def test_every_injection_stays_inside_owned_namespaces(
    fixture_io: tuple[LabSpec, Mock], monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    spec, runner = fixture_io
    saved = {"fixture_baseline": True}
    monkeypatch.setattr(sealed_linux, "snapshot", lambda *_: saved)
    monkeypatch.setattr(sealed_linux, "capture", lambda *_: copy.deepcopy(saved))
    state: dict[str, object] = {
        "sealed_selection": chosen(code),
        "sealed_injection_started": False,
        "proxy": PROXY_URL,
        "http_proxy": PROXY_URL,
    }
    sealed_linux.inject(spec, state)
    assert state["sealed_injection_started"] is True
    for call in runner.call_args_list:
        argv = call.args[0]
        if argv[:3] == ("ip", "netns", "exec"):
            assert argv[3] in {spec.client, spec.internet}
        else:
            assert argv[:2] == ("ip", "-n") and argv[2] in {spec.client, spec.internet}
        assert not any(word in argv for word in ("en7", "sshd", "pfctl", "iptables", "flush"))
    if code == "P04":
        assert state["proxy"] == sealed_linux.DEAD_PROXY and state["http_proxy"] == PROXY_URL
    if code == "P07":
        assert (
            state["proxy"] == PROXY_URL
            and state["process_proxy_override"] == sealed_linux.DEAD_PROXY
        )
    if code == "X02":
        rules = [c.args[0] for c in runner.call_args_list if "rule" in c.args[0]]
        assert len(rules) == 1
        command = rules[0]
        index = command.index("length")
        assert command[index - 1 : index + 3] == ("ip", "length", ">", "1400")
        assert "oifname" in command and spec.interfaces[1] in command
    if code == "I04":
        assert (spec.state / "dhcp-options").read_text() == _public_fixture("3,@PUBLIC_IPV4_084@\n")
        assert 'via "$router"' in sealed_linux.lease_hook(spec)
    if code == "I02":
        assert any("67" in c.args[0] and "drop" in c.args[0] for c in runner.call_args_list)
    if code == "G02":
        assert any(
            "permanent" in c.args[0] and sealed_linux.BAD_MAC in c.args[0]
            for c in runner.call_args_list
        )


def test_injection_duplicate_and_baseline_drift_refuse_before_effect(
    fixture_io: tuple[LabSpec, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, runner = fixture_io
    for already in (True, None):
        with pytest.raises(ValueError, match="never_retried"):
            sealed_linux.inject(spec, {"sealed_injection_started": already})
    monkeypatch.setattr(sealed_linux, "snapshot", lambda *_: {"config": "before"})
    monkeypatch.setattr(sealed_linux, "capture", lambda *_: {"config": "changed"})
    with pytest.raises(ValueError, match="before_state_mismatch"):
        sealed_linux.inject(spec, {"sealed_injection_started": False})
    runner.assert_not_called()


def test_proxy_restore_preserves_process_override_and_exact_http_mode(
    fixture_io: tuple[LabSpec, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _ = fixture_io
    monkeypatch.setattr(sealed_linux, "snapshot", lambda *_: {"proxy": PROXY_URL, "http_proxy": ""})
    state: dict[str, object] = {
        "sealed_selection": chosen("P07"),
        "proxy": "wrong",
        "http_proxy": "wrong",
        "process_proxy_override": sealed_linux.DEAD_PROXY,
    }
    assert sealed_linux.apply_snapshot(spec, state, "restore_proxy", "snapshot")
    assert state == {
        "sealed_selection": chosen("P07"),
        "proxy": PROXY_URL,
        "http_proxy": "",
        "process_proxy_override": sealed_linux.DEAD_PROXY,
    }
    with pytest.raises(ValueError, match="snapshot_policy"):
        sealed_linux.apply_snapshot(spec, state, "restore_proxy", "off")


@pytest.mark.parametrize("code", ["F05", "V03"])
def test_control_refuses_even_typed_mutations(fixture_io: tuple[LabSpec, Mock], code: str) -> None:
    spec, runner = fixture_io
    with pytest.raises(ValueError, match="control_mutation_refused"):
        sealed_linux.apply_snapshot(
            spec,
            {"sealed_selection": chosen(code), "sealed_injection_started": True},
            "reroute",
            "alternate",
        )
    runner.assert_not_called()


@pytest.mark.parametrize(
    "hostname,path,expected",
    [
        ("one.uplink.test", "/ok", 204),
        ("two.uplink-alt.test", "/ok", 302),
        ("one.uplink.test", "/portal", 204),
        ("two.uplink-alt.test", "/portal", 302),
        ("two.uplink-alt.test", "/app", 204),
        ("", "/portal", 204),
    ],
)
def test_walled_garden_affects_only_second_check_name(
    tmp_path: Path, hostname: str, path: str, expected: int
) -> None:
    from offline_agent.uplink.lab_servers import Handler

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"stub": "walled_second", "sealed_selection": chosen("C04")}))
    handler = SimpleNamespace(
        state=state,
        path=path,
        headers={"Host": hostname},
        send_response=Mock(),
        send_header=Mock(),
        end_headers=Mock(),
        command="GET",
        wfile=io.BytesIO(),
    )
    Handler.do_GET(cast(Handler, handler))
    handler.send_response.assert_called_once_with(expected)


def test_snapshot_hash_refuses_missing_and_changed_bytes(fixture_io: tuple[LabSpec, Mock]) -> None:
    spec, _ = fixture_io
    path = spec.state / "sealed-snapshot.json"
    with pytest.raises(ValueError, match="snapshot_required"):
        sealed_linux.snapshot(spec, {})
    saved = {"resolver": "captured"}
    path.write_text(json.dumps(saved))
    state: dict[str, object] = {"sealed_snapshot_sha256": sealed_linux.digest(saved)}
    assert sealed_linux.snapshot(spec, state) == saved
    path.write_text(json.dumps({"resolver": "changed"}))
    with pytest.raises(ValueError, match="snapshot_changed"):
        sealed_linux.snapshot(spec, state)


def test_environment_override_uses_process_environment_not_system_snapshot(
    fixture_io: tuple[LabSpec, Mock],
) -> None:
    spec, runner = fixture_io
    runner.return_value = subprocess.CompletedProcess([], 7, "\nSTATUS:000", "")
    state: dict[str, object] = {
        "sealed_selection": chosen("P07"),
        "proxy": PROXY_URL,
        "http_proxy": PROXY_URL,
        "process_proxy_override": sealed_linux.DEAD_PROXY,
    }
    lab_worker.fetch(spec, state, 0)
    argv = runner.call_args.args[0]
    assert "env" in argv and "https_proxy=" + sealed_linux.DEAD_PROXY in argv
    assert "--proxy" not in argv and state["proxy"] == PROXY_URL


def test_split_proxy_fetch_keeps_portal_direct(fixture_io: tuple[LabSpec, Mock]) -> None:
    spec, runner = fixture_io
    runner.return_value = subprocess.CompletedProcess([], 0, "\nSTATUS:204", "")
    state: dict[str, object] = {
        "sealed_selection": chosen("X04"),
        "proxy": PROXY_URL,
        "http_proxy": "",
        "process_proxy_override": None,
    }
    lab_worker.fetch(spec, state, 2)
    argv = runner.call_args.args[0]
    assert argv[argv.index("--proxy") + 1] == ""
    lab_worker.fetch(spec, state, 0)
    argv = runner.call_args.args[0]
    assert argv[argv.index("--proxy") + 1] == PROXY_URL


def test_signature_requires_every_network_check_and_separate_application() -> None:
    good: dict[str, object] = {
        "ok": True,
        "status": 204,
        "body_matches": True,
        "tls_verified": True,
    }
    rows: list[dict[str, object]] = [
        {"ok": True},
        {"ok": True},
        *(copy.deepcopy(good) for _ in range(5)),
    ]
    assert sealed_linux.signature(rows)["verdict"] == "ONLINE"
    rows[6]["ok"] = False
    assert sealed_linux.signature(rows)["verdict"] == "ONLINE"
    assert sealed_linux.signature(rows)["app"] is False
    rows[3]["tls_verified"] = False
    assert sealed_linux.signature(rows)["verdict"] == "DEGRADED"
    rows[5].update(ok=False, status=302)
    assert sealed_linux.signature(rows)["verdict"] == "CAPTIVE"
    with pytest.raises(ValueError, match="complete_oracle"):
        sealed_linux.signature([])


def test_ipv6_resolver_read_is_linux_only_and_does_not_bind_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink import probe_execution
    from offline_agent.uplink.probe_protocol import ProbeRefused, validate_program

    program = validate_program(
        {
            "version": 1,
            "probes": [
                {"op": "dns_query", "name": "one", "resolver": "configured", "timeout_ms": 1000}
            ],
        },
        "linux_lab",
    )
    command = Mock(return_value={"exit_code": 1, "stdout": "", "ok": False})
    monkeypatch.setattr(probe_execution, "command", command)
    context: dict[str, object] = {
        "profile": "linux_lab",
        "names": ["one.uplink.test", "two.uplink-alt.test"],
        "resolvers": {"configured": _public_fixture("@PUBLIC_IPV6_102@")},
        "sources": {"current": _public_fixture("@PUBLIC_IPV4_082@")},
    }
    result = probe_execution.primitive(program[0], context, 1)
    assert result["ok"] is False
    argv = command.call_args.args[0]
    assert _public_fixture("@@PUBLIC_IPV6_102@") in argv and "-b" not in argv
    context["profile"] = "lab_wifi"
    with pytest.raises(ProbeRefused, match="linux_namespace"):
        probe_execution.primitive(program[0], context, 1)


def test_partial_repair_is_not_online_or_a_model_handoff() -> None:
    for code, flags, verdict in (("X03", "TTFFTT", "DEGRADED"), ("X04", "TTTTFF", "CAPTIVE")):
        fixture = sealed_registry.load(chosen(code))
        stages = fixture["stages"]
        assert isinstance(stages, list)
        assert stages[-1]["flags"] == flags and stages[-1]["verdict"] == verdict
        terminal = fixture["terminal"]
        assert isinstance(terminal, dict) and terminal["tool"] == "report"
        with pytest.raises(ValueError, match="signature_mismatch"):
            sealed_qualify.check_stage(
                {"flags": "TTTTTT", "verdict": "ONLINE", "app": True}, stages[-1]
            )


def test_mechanism_proof_refuses_healthy_no_fault_and_missing_reads() -> None:
    with pytest.raises(ValueError, match="evidence_missing"):
        sealed_qualify.check_mechanism("F05", {})
    with pytest.raises(ValueError, match="filter_not_observed"):
        sealed_qualify.check_mechanism("X02", {"configuration": {}, "filtered_packets": 0})
    with pytest.raises(ValueError, match="small_tcp"):
        sealed_qualify.check_mechanism(
            "X02", {"configuration": {}, "filtered_packets": 2, "primary_tcp_443_ok": False}
        )


def test_exact_revert_failure_persists_diff_and_blocks_cleanup(
    fixture_io: tuple[LabSpec, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, runner = fixture_io
    saved: dict[str, object] = {
        "resolver": _public_fixture("nameserver @PUBLIC_IPV4_081@\n"),
        "proxy": PROXY_URL,
        "http_proxy": PROXY_URL,
        "process_proxy_override": None,
        "stub": "healthy",
        "network": {},
    }
    monkeypatch.setattr(sealed_linux, "snapshot", lambda *_: saved)
    monkeypatch.setattr(sealed_linux, "capture", lambda *_: {**saved, "resolver": "wrong"})
    state: dict[str, object] = {"sealed_injection_started": True, "sealed_restored": False}
    with pytest.raises(ValueError, match="exact_revert_mismatch"):
        sealed_linux.after_restore(spec, state)
    writer = lab_worker.atomic_json
    assert isinstance(writer, Mock)
    assert writer.call_args.args[1]["fields"]["resolver"] is False
    with pytest.raises(ValueError, match="verified_revert_required"):
        lab_worker.down(spec, state)
    runner.assert_not_called()


def test_failed_rollback_cannot_be_retried(fixture_io: tuple[LabSpec, Mock]) -> None:
    spec, _ = fixture_io
    effect = Mock(side_effect=AssertionError("never retry"))
    with pytest.raises(ValueError, match="no_retry"):
        lab_worker.mutate(spec, {"status": "failed"}, "undo", effect)
    effect.assert_not_called()
