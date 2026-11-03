"""Acceptance and failure pins for recovery. All effects use an injected fake backend."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
from pathlib import Path

import pytest

from offline_agent.contracts import Json
from offline_agent.uplink.real_preflight import Checks
from offline_agent.uplink.real_protocol import REAL_FAULTS, RealSpec, request
from offline_agent.uplink.real_runner import OOBLost, RevertFailed, recover
from offline_agent.uplink.real_simulator import SimulatedLab
from offline_agent.uplink.trace_format import read_trace


@pytest.mark.parametrize("fault", REAL_FAULTS)
def test_dry_recovery_covers_all_faults_with_confirm_and_deadman(
    tmp_path: Path, fault: str
) -> None:
    spec = RealSpec("0123456789ab", fault)
    backend = SimulatedLab(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["recovered"] is True and row["evidence_kind"] == "MOCK"
    assert isinstance(row["model"], dict) and row["model"]["model_used"] is False
    assert backend.calls.index("arm") < backend.calls.index("inject") < backend.calls.index("apply")
    assert backend.calls[-1] == "disarm"
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    assert records[-1]["verdict"] == "ONLINE"
    for i, record in enumerate(records):
        assert record["display"] and record["net_state"]
        if record.get("phase") == "apply":
            assert records[i - 1]["phase"] == "confirm"
            assert record["config_diff"]
    assert len(row["changes"]) == (2 if fault == "dns_and_proxy" else 1)  # type: ignore[arg-type]


def test_denied_confirmation_restores_once_without_apply(tmp_path: Path) -> None:
    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = SimulatedLab(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: False)
    assert not row["recovered"] and row["failure"] == "confirmation_denied"
    assert backend.calls.count("restore") == 1 and "apply" not in backend.calls


def test_oob_failure_stops_without_remote_cleanup_or_retry(tmp_path: Path) -> None:
    class Lost(SimulatedLab):
        def oob(self) -> None:
            super().oob()
            if "inject" in self.calls:
                raise OOBLost("oob_lost_no_retry")

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = Lost(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert not row["recovered"] and row["failure"] == "oob_lost_no_retry"
    assert "restore" not in backend.calls and "apply" not in backend.calls
    assert backend.calls[-1] == "oob"


def test_failed_revert_has_no_second_attempt(tmp_path: Path) -> None:
    class Broken(SimulatedLab):
        def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            if op == "restore":
                self.calls.append(op)
                raise RevertFailed("restore_failed_no_retry")
            return super().call(op, payload)

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = Broken(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: False)
    assert not row["recovered"] and backend.calls.count("restore") == 1
    assert "disarm" not in backend.calls


def test_timed_out_model_request_counts_as_inference_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_preflight_checks: Checks
) -> None:
    import time
    from unittest.mock import Mock

    from offline_agent.contracts import ContractError
    from offline_agent.uplink.model_identity import expected_model

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = SimulatedLab(spec)
    backend.dry = False
    from typing import cast

    from offline_agent.uplink.real_fleet import approval

    backend.authorization = cast(dict[str, Json], {**approval(), "clearance_sha256": "a" * 64})
    identity = expected_model(chip="Apple M4 Max")
    identity.update(verification="server_verified", verified_at=time.time())
    monkeypatch.setattr(backend, "identity", lambda: identity)
    backend.last_model_request_count = 1
    monkeypatch.setattr(backend, "choose", Mock(side_effect=ContractError("http_timeout")))
    receipt = recover(
        backend, spec, tmp_path, confirm=lambda _: True, preflight_checks=fake_preflight_checks
    )
    assert receipt["failure"] == "http_timeout" and receipt["model_calls_attempted"] == 1
    assert isinstance(receipt["model"], dict) and receipt["model"]["model_used"] is True
    assert backend.calls.count("restore") == 1 and "apply" not in backend.calls


def test_watchdog_rejection_retains_the_unexecuted_model_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink.contracts import Action, Tool
    from offline_agent.uplink.lab_runner import Emission

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = SimulatedLab(spec)
    text = json.dumps(Action(Tool.READ_VITALS, {}).as_json())

    def respond(*args: object) -> Emission:
        backend.dns_changed = False
        backend.watchdog = "reverted"
        return Emission(text, {})

    monkeypatch.setattr(backend, "choose", respond)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert not row["recovered"] and row["failure"] == "watchdog_ended_trial_no_recovery_credit"
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    models = [r["model"] for r in records if r.get("event") == "step" and r.get("model")]
    assert len(models) == 1 and isinstance(models[0], dict) and models[0]["text"] == text


def test_watchdog_cannot_earn_recovery_credit(tmp_path: Path) -> None:
    class Fired(SimulatedLab):
        def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            value = super().call(op, payload)
            if op == "observe" and "apply" in self.calls:
                value["watchdog"] = "reverted"
            return value

    spec = RealSpec("0123456789ab", "wrong_dns")
    row = recover(Fired(spec), spec, tmp_path, confirm=lambda _: True)
    assert not row["recovered"] and row["failure"] == "watchdog_ended_trial_no_recovery_credit"


def test_health_failure_never_arms_or_injects(tmp_path: Path) -> None:
    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = SimulatedLab(spec)
    backend.dns_changed = True
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["failure"] == "healthy_wifi_baseline_required"
    assert "arm" not in backend.calls and "inject" not in backend.calls


@pytest.mark.parametrize(
    "op,payload",
    [
        ("apply", {"tool": "shell"}),
        ("prepare", {"fault": "wrong_dns", "seconds": 61}),
        ("inject", {"service": "AX88179B"}),
        ("observe", {"argv": ["sudo", "id"]}),
    ],
)
def test_root_protocol_rejects_scope_expansion(op: str, payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        request(op, "0123456789ab", payload)


def test_cli_dry_three_trials_never_claims_real_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from offline_agent.uplink.real_runner import main

    monkeypatch.setattr("sys.argv", ["uplink-real", "--trials", "3", "--output", str(tmp_path)])
    main()
    result = json.loads((tmp_path / "summary.json").read_text())
    assert result["completed_trials"] == 3
    assert result["three_consecutive_dns_proxy_recoveries"] is False
    assert result["evidence_kind"] == "MOCK"
    capsys.readouterr()


def test_missing_model_dependency_still_persists_refusal_trace(tmp_path: Path) -> None:
    from offline_agent.contracts import ContractError

    class Missing(SimulatedLab):
        def identity(self) -> dict[str, Json]:
            raise ContractError("local_model_server_unavailable")

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = Missing(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["failure"] == "local_model_server_unavailable"
    assert "prepare" not in backend.calls
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    assert records[-1]["verdict"] == "ERROR"


def test_write_intent_survives_indeterminate_apply(tmp_path: Path) -> None:
    class LostDuringApply(SimulatedLab):
        def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            if op == "apply":
                raise OOBLost("oob_result_indeterminate_no_retry")
            return super().call(op, payload)

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = LostDuringApply(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert not row["recovered"] and "restore" not in backend.calls
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    attempts = [r for r in records if r.get("phase") == "apply"]
    assert len(attempts) == 1 and records[-1]["verdict"] == "ERROR"


def test_transport_guard_never_opens_ssh_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from offline_agent.contracts import ContractError
    from offline_agent.uplink.real_transport import LabBackend

    spy = Mock(side_effect=AssertionError("SSH forbidden"))
    monkeypatch.setattr("subprocess.run", spy)
    with pytest.raises(ContractError, match="outside_tests"):
        LabBackend(RealSpec("0123456789ab", "wrong_dns"), execute=True).oob()
    spy.assert_not_called()


def test_card_argv_contains_the_real_closed_request_not_a_stdin_annotation() -> None:
    import shlex

    from offline_agent.uplink.real_transport import LabBackend

    backend = LabBackend(RealSpec("0123456789ab", "wrong_dns"))
    argv = backend.command("apply", {"tool": "set_dns"})
    assert argv[:-1] == backend.targets.oob_ssh and "[stdin-json]" not in argv
    parts = shlex.split(argv[-1])
    assert json.loads(parts[-1])["payload"] == {"tool": "set_dns"}
    assert parts[-2] == "--root-request"


def test_interface_proxy_selection_never_uses_oob_proxy() -> None:
    from offline_agent.uplink.real_bridge import proxies

    value: dict[str, Json] = {
        "__SCOPED__": {
            "en0": {"HTTPSEnable": 1, "HTTPSProxy": _public_fixture("@PUBLIC_IPV4_056@"), "HTTPSPort": 9},
            "en7": {"HTTPSEnable": 0},
        }
    }
    selected = proxies(value, "en0", {})
    assert selected.known and selected.system["https"] == _public_fixture("http://@PUBLIC_IPV4_056@:9")
    assert not proxies(value, "en2", {}).known
    assert not proxies({"__MATCHED__": {}}, "en0", {}).known


def test_http_probe_binds_wifi_and_keeps_curlrc_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from unittest.mock import Mock

    from offline_agent.uplink.oracle import TLS_ENDPOINTS, ProxyRoute, SystemTransport

    spy = Mock(return_value=subprocess.CompletedProcess([], 0, b"\nUPLINK_STATUS:204", b""))
    monkeypatch.setattr("subprocess.run", spy)
    SystemTransport(interface="en0").fetch(
        TLS_ENDPOINTS[1], ProxyRoute("path", "system", None), 0.8
    )
    argv = spy.call_args.args[0]
    assert argv[:4] == ["/usr/bin/curl", "--disable", "--interface", "en0"]
    with pytest.raises(ValueError):
        SystemTransport(interface="en0;id")


def test_root_worker_default_has_no_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from offline_agent.uplink.real_worker import dispatch

    spy = Mock(side_effect=AssertionError("effect forbidden"))
    monkeypatch.setattr("subprocess.run", spy)
    for op in ("arm", "inject", "restore", "observe"):
        assert dispatch(request(op, "0123456789ab"), execute=False)["effects"] is False
    spy.assert_not_called()


def test_root_worker_refuses_test_context_even_if_lab_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.real_worker import require_lab

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with pytest.raises(ValueError, match="forbidden_in_tests"):
        require_lab(True)


def test_detached_launchd_job_uses_root_owned_copy_and_cannot_rearm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plistlib

    from offline_agent.uplink import real_worker

    directory = tmp_path / "0123456789ab"
    directory.mkdir()
    commands: list[tuple[str, ...]] = []

    def command(argv: tuple[str, ...], **kwargs: object) -> str:
        commands.append(argv)
        if argv == ("/bin/launchctl", "list"):
            return (
                "- 0 com.uplink.real.0123456789ab\n"
                if any("bootstrap" in a for a in commands)
                else ""
            )
        return ""

    monkeypatch.setattr(real_worker, "run", command)
    monkeypatch.setattr(real_worker, "boot_id", lambda: "a" * 64)
    state: dict[str, object] = {
        "lifecycle": "prepared",
        "seconds": 45,
        "clock_basis": real_worker.CLOCK_BASIS,
        "boot_id": "a" * 64,
    }
    real_worker.arm(directory, state)
    job = plistlib.loads((directory / "watchdog.plist").read_bytes())
    assert job["RunAtLoad"] is True
    assert job["ProgramArguments"][:2] == ["/usr/bin/python3", "-I"]
    assert job["ProgramArguments"][2] == str(directory / "real_worker.py")
    assert "--watchdog" in job["ProgramArguments"]
    assert commands[1][:3] == ("/bin/launchctl", "bootstrap", "system")
    assert state["ready"] is False
    with pytest.raises(ValueError, match="cannot_rearm"):
        real_worker.arm(directory, state)


def test_failed_root_restore_is_recorded_and_never_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import real_worker

    calls: list[str] = []
    monkeypatch.setattr(real_worker, "read", lambda _: {})

    def broken(directory: Path, operation: str = "read") -> dict[str, object]:
        calls.append(operation)
        raise ValueError("native_unavailable")

    monkeypatch.setattr(real_worker, "native", broken)
    state: dict[str, object] = {"lifecycle": "injected", "watchdog": "armed"}
    with pytest.raises(ValueError, match="restore_failed_no_retry"):
        real_worker.restore(tmp_path, state, "test")
    assert state["lifecycle"] == "rollback_failed"
    with pytest.raises(ValueError, match="already_attempted"):
        real_worker.restore(tmp_path, state, "test")
    assert calls == ["read"]
    assert json.loads((tmp_path / "state.json").read_text())["reason"] == "native_unavailable"


def test_oracle_parser_rejects_stale_or_self_inconsistent_online() -> None:
    import time

    from offline_agent.contracts import ContractError
    from offline_agent.uplink.real_transport import parse_oracle

    value = SimulatedLab(RealSpec("0123456789ab", "wrong_dns")).probe().as_json()
    value["checked_at"] = time.time() - 31
    with pytest.raises(ContractError, match="fresh_complete"):
        parse_oracle(value)
    value["checked_at"] = time.time()
    value["evidence"] = []
    with pytest.raises(ContractError, match="incomplete"):
        parse_oracle(value)


@pytest.mark.parametrize("offset,accepted", ((0.1, True), (1.1, False), (-301, False)))
def test_remote_provenance_waits_for_small_skew_without_loosening_freshness(
    monkeypatch: pytest.MonkeyPatch, offset: float, accepted: bool
) -> None:
    from offline_agent.contracts import ContractError
    from offline_agent.uplink.model_identity import expected_model
    from offline_agent.uplink.real_transport import LabBackend

    clock = [1000.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", sleep)
    record = expected_model(chip="Apple M4 Max")
    record.update(verification="server_verified", verified_at=1000.0 + offset)
    backend = LabBackend(RealSpec("0123456789ab", "wrong_dns"))
    monkeypatch.setattr(backend, "rpc", lambda *args, **kwargs: record)
    if accepted:
        assert backend.identity()["verified_at"] == 1000.0 + offset
        assert sleeps == [pytest.approx(offset)]
    else:
        with pytest.raises(ContractError, match="fresh_server"):
            backend.identity()
        assert sleeps == []


def test_remote_oracle_waits_for_small_skew_and_keeps_its_original_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.real_transport import parse_oracle

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    value = SimulatedLab(RealSpec("0123456789ab", "wrong_dns")).probe().as_json()
    value["checked_at"] = 1000.1
    result = parse_oracle(value)
    assert result.checked_at == 1000.1
    assert result.fresh_online(clock[0])


@pytest.mark.parametrize(
    "text,allowed",
    [
        ("state = running\npid = 123\n", True),
        ("state = waiting\npid = 123\n", False),
        ("state = running\npid = 124\n", False),
        ("", False),
    ],
)
def test_watchdog_readiness_requires_live_launchd_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, allowed: bool
) -> None:
    from offline_agent.uplink import real_worker

    monkeypatch.setattr(real_worker, "run", lambda argv: text)
    monkeypatch.setattr(real_worker, "boot_id", lambda: "a" * 64)
    now = real_worker.steady_time()
    state: dict[str, object] = {
        "watchdog": "armed",
        "ready": True,
        "watchdog_pid": 123,
        "deadline_mono": now + 30,
        "armed_at_mono": now,
        "clock_basis": real_worker.CLOCK_BASIS,
        "boot_id": "a" * 64,
    }
    if allowed:
        real_worker.require_armed(tmp_path, state)
    else:
        with pytest.raises(ValueError, match="process_not_running"):
            real_worker.require_armed(tmp_path, state)


def test_root_restore_uses_only_captured_wifi_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import real_worker

    snapshot: dict[str, object] = {
        "service_id": "synthetic",
        "interface": "en0",
        "protected_sha256": "p",
        "dns_sha256": "good",
        "proxies_sha256": "good",
        "wifi_up": True,
    }
    current = {**snapshot, "dns_sha256": "bad"}
    calls: list[str] = []
    monkeypatch.setattr(real_worker, "read", lambda _: snapshot)

    def native(directory: Path, operation: str = "read") -> dict[str, object]:
        calls.append(operation)
        if operation == "restore":
            current.update(snapshot)
        return current

    monkeypatch.setattr(real_worker, "native", native)
    monkeypatch.setattr(real_worker, "power", lambda _: True)
    monkeypatch.setattr(real_worker, "boot_id", lambda: "a" * 64)
    monkeypatch.setattr(real_worker, "fleet_check", lambda _: {"match": True})
    monkeypatch.setattr(
        real_worker,
        "observe",
        lambda *_: {
            "baseline_equal": current["dns_sha256"] == "good",
            "watchdog": "reverted",
            "fleet_diff_match": True,
        },
    )
    state: dict[str, object] = {"lifecycle": "injected", "watchdog": "armed"}
    row = real_worker.restore(tmp_path, state, "test")
    assert row["baseline_equal"] is True and calls == ["read", "restore"]
    assert state["lifecycle"] == "reverted"


def test_card_metadata_cannot_enable_the_original_u1_executor() -> None:
    from offline_agent.contracts import ContractError
    from offline_agent.uplink.plans import ConfirmCard, execute_change

    card = ConfirmCard("set_dns", (), "snapshot_drift", (), (), "snapshot", executable=True)
    assert card.for_confirmation()["executable"] is True
    with pytest.raises(ContractError, match="u1_changes_never_execute"):
        execute_change(card)


def test_model_can_request_diagnostics_before_restoring(tmp_path: Path) -> None:
    from offline_agent.uplink.contracts import Action, Scope, Tool
    from offline_agent.uplink.lab_runner import Emission
    from offline_agent.uplink.oracle import OracleResult

    class DiagnosticFirst(SimulatedLab):
        first = True

        def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
            if self.first:
                self.first = False
                return Emission(json.dumps(Action(Tool.READ_DNS, {}).as_json()), {})
            assert observed["action_history"]
            return super().choose(scope, observed, oracle)

    spec = RealSpec("0123456789ab", "wrong_dns")
    row = recover(DiagnosticFirst(spec), spec, tmp_path, confirm=lambda _: True)
    assert row["recovered"] is True
    records = list(read_trace(tmp_path / "traces" / str(row["trace"])))
    assert any(r.get("phase") == "diagnose" and isinstance(r.get("model"), dict) for r in records)


def test_failed_watchdog_causes_no_manual_rollback_attempt(tmp_path: Path) -> None:
    class FailedWatchdog(SimulatedLab):
        def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
            result = super().call(op, payload)
            if op == "observe" and "inject" in self.calls:
                result["watchdog"] = "failed"
            return result

    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = FailedWatchdog(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["failure"] == "watchdog_restore_failed_no_retry"
    assert "restore" not in backend.calls and "apply" not in backend.calls


def test_cached_dns_cannot_turn_proxy_repair_into_combined_fault_credit(tmp_path: Path) -> None:
    from dataclasses import replace

    from offline_agent.uplink.oracle import OracleResult, Verdict

    class Cached(SimulatedLab):
        def probe(self) -> OracleResult:
            result = super().probe()
            if self.dns_changed:
                return OracleResult(
                    Verdict.DEGRADED,
                    result.checked_at,
                    tuple(replace(e, ok=True) if e.kind == "dns" else e for e in result.evidence),
                )
            return result

    spec = RealSpec("0123456789ab", "dns_and_proxy")
    backend = Cached(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    assert row["failure"] == "dns_fault_not_observed_possible_resolver_cache"
    assert "choose" not in backend.calls and backend.calls.count("restore") == 1


def test_handled_watchdog_failure_cannot_trigger_launchd_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from offline_agent.uplink import real_worker

    def failed(_: str) -> None:
        raise ValueError("restore_failed_no_retry")

    monkeypatch.setattr(real_worker, "watchdog", failed)
    monkeypatch.setattr("sys.argv", ["real_worker", "--execute", "--watchdog", "0123456789ab"])
    real_worker.main()  # Successful process exit suppresses KeepAlive's crash restart.
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": False, "error": "restore_failed_no_retry"}


def test_every_oracle_call_keeps_a_transport_receipt_and_minimal_config_diff(
    tmp_path: Path,
) -> None:
    import hashlib

    spec = RealSpec("0123456789ab", "dns_and_proxy")
    backend = SimulatedLab(spec)
    row = recover(backend, spec, tmp_path, confirm=lambda _: True)
    path = tmp_path / "traces" / str(row["trace"])
    records = list(read_trace(path))
    raw_probes = [
        r
        for r in records
        if isinstance(observation := r.get("observation"), dict)
        and observation["source"] == "mock_oracle"
    ]
    assert len(raw_probes) == backend.calls.count("probe")
    first_plan = next(r for r in records if r.get("phase") == "plan")
    diff = first_plan["config_diff"]
    assert isinstance(diff, dict)
    before, after = diff["before"], diff["after"]
    assert isinstance(before, dict) and isinstance(after, dict)
    assert set(before) == {"dns", "proxy", "wifi_power"}
    assert before["dns"] != after["dns"] and before["proxy"] == after["proxy"]
    assert diff["before_snapshot_id"] and diff["after_snapshot_id"]
    assert hashlib.sha256(b"True").hexdigest() not in path.read_text()


def test_probe_receipt_redacts_body_and_command_identity() -> None:
    from offline_agent.uplink.privacy import Redactor
    from offline_agent.uplink.real_bridge import probe_receipt
    from offline_agent.uplink.records import RawObservation

    redactor = Redactor(b"s" * 32)
    redactor.handle("host", "private.example.test")
    raw = RawObservation(
        ("/usr/bin/curl", "https://private.example.test/path"),
        "private captive portal content",
        exit_code=0,
        duration_ms=2,
        source="http_probe",
    )
    result = SimulatedLab(RealSpec("0123456789ab", "wrong_dns")).probe()
    receipt = probe_receipt(result, [raw], redactor)
    encoded = json.dumps(receipt, ensure_ascii=False)
    assert "private.example.test" not in encoded and "private captive portal content" not in encoded
    assert "«redacted»" in encoded and "body-" in encoded


def test_probe_wire_refuses_missing_transport_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from offline_agent.contracts import ContractError
    from offline_agent.uplink.real_transport import LabBackend

    backend = LabBackend(RealSpec("0123456789ab", "wrong_dns"))
    oracle = SimulatedLab(backend.spec).probe().as_json()
    monkeypatch.setattr(
        backend, "rpc", lambda *args, **kwargs: {"oracle": oracle, "observations": [{}, {}]}
    )
    with pytest.raises(ContractError, match="invalid_probe_observation"):
        backend.probe()
