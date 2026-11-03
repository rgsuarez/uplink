"""Contract rehearsal: real controller/worker code over fake SSH, clock and compiler."""

import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink import (
    lab_config,
    real_preflight,
    real_preflight_worker,
    real_probe_scripts,
)
from offline_agent.uplink.model_identity import GGUF_NAME, LLAMA_SERVER_BUILD, expected_model
from offline_agent.uplink.real_fleet import load_clearance, validate_authorization
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_runner import OOBLost, append_abort, recover
from offline_agent.uplink.real_transport import LabBackend
from offline_agent.uplink.remote_settings import RemoteTargets

from .probe_receipts import returned_probe

SPEC = RealSpec("0123456789ab", "dns_and_proxy", 55, True)
ORDER = [
    "oob",
    "model_attestation",
    "llama_build",
    "native_typecheck",
    "helper_build",
    "helper_self_test",
    "clearance",
    "staging_readback",
    "probe_positive_control",
]


class FakeBackend(LabBackend):
    # Only replace the process prohibition for this injected transport. Keep the
    # actual oob/rpc/identity code, including wait_for_receipt_clock, under test.
    def guard(self) -> None:
        if self.lost:
            raise OOBLost("oob_lost_no_retry")
        validate_authorization(self.authorization)


class FakeTransport:
    def __init__(self, root: Path, backend: FakeBackend) -> None:
        self.root, self.backend = root, backend
        self.events: list[str] = []
        self.processes: list[tuple[str, ...]] = []
        self.clock = 1_800_000_000.0
        self.sleeps: list[float] = []
        self.skew = 0.0
        self.bad_sha = False
        self.fail: str | None = None
        self.malformed: str | None = None
        self.build_elapsed = 0.0
        self.reply_patch: dict[str, dict[str, Json]] = {}
        self.native_exit = 0
        self.build_exit = 0
        self.self_test: dict[str, Json] = {
            "self_test": "passed",
            "network_reads": 0,
            "network_writes": 0,
        }
        self.probe_result = returned_probe()
        self.probe_calls: list[Path] = []

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.clock += delay

    def run(
        self, argv: tuple[str, ...] | list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(argv)
        self.processes.append(command)
        if command == (*self.backend.targets.oob_ssh, "true"):
            if not self.events:
                self.events.append("oob")
            return subprocess.CompletedProcess(argv, 255 if self.fail == "oob" else 0, "", "")
        if command[0] == "ssh":
            if "real_preflight_worker" in command[-1]:
                parts = shlex.split(command[-1])
                step = parts[parts.index("--step") + 1]
                self.events.append(step)
                if self.malformed == step:
                    return subprocess.CompletedProcess(argv, 0, "not-json", "")
                try:
                    if self.fail == step:
                        raise ContractError(step + "_failed")
                    value = real_preflight_worker.perform(step, SPEC.run_id, root=self.root)
                    value.update(self.reply_patch.get(step, {}))
                except (ContractError, ValueError) as exc:
                    return subprocess.CompletedProcess(
                        argv, 1, json.dumps({"ok": False, "error": str(exc)}), ""
                    )
            else:
                assert json.loads(str(kwargs["input"])) == {"bridge": "attest"}
                self.events.append("model_attestation")
                if self.fail == "model_attestation":
                    return subprocess.CompletedProcess(
                        argv, 1, '{"ok":false,"error":"attestation_failed"}', ""
                    )
                value = expected_model(provider="llama_cpp", chip="Apple M4 Max")
                value.update(verification="server_verified", verified_at=self.clock + self.skew)
                if self.bad_sha:
                    value["gguf_sha256"] = "0" * 64
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"ok": True, "result": value}), ""
            )
        if command[:2] == ("xcrun", "swiftc"):
            if "-O" in command:
                if not self.build_exit:
                    Path(command[-1]).write_bytes(b"synthetic-native-helper")
                self.clock += self.build_elapsed
                return subprocess.CompletedProcess(argv, self.build_exit, "", "synthetic compiler")
            else:
                assert "-typecheck" in command
            return subprocess.CompletedProcess(argv, self.native_exit, "", "")
        if command[-1] == "self-test":
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.self_test), "")
        raise AssertionError("unexpected_process_in_preflight")


@pytest.fixture
def transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    home = tmp_path / "home"
    root = home / "lab-bench/uplink-u1"
    root.mkdir(parents=True, mode=0o700)
    inputs = {
        "scripts/check_macos_swift.py": (
            real_preflight.ROOT / "scripts/check_macos_swift.py"
        ).read_bytes(),
        "harness/macos/UplinkNetwork.swift": b'print("fake compiler input")\n',
        "src/offline_agent/uplink/fixture.py": b"# synthetic source\n",
    }
    for name, content in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / "source-manifest.json").write_text(
        json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()})
    )
    config = root / "src/offline_agent/uplink/lab.local.json"
    config.write_bytes(lab_config.CONFIG_PATH.read_bytes())
    config.chmod(0o600)
    monkeypatch.setattr(lab_config, "CONFIG_PATH", config)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(real_preflight_worker, "require_host", lambda: None)
    gate = real_preflight.native_gate(root)
    monkeypatch.setattr(gate, "git_payload", lambda *_: dict(inputs))
    monkeypatch.setattr(real_preflight, "native_gate", lambda _: gate)
    monkeypatch.setattr(real_preflight_worker, "native_gate", lambda _: gate)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("os.geteuid", lambda: 501)
    backend = FakeBackend(SPEC, execute=True, targets=RemoteTargets("lab-oob", "lab-wifi"))
    fake = FakeTransport(root, backend)

    def execute_probe(
        program: dict[str, Json], run_id: str, deadline: float, root: Path, redactor: object
    ) -> dict[str, Json]:
        assert program == {"version": 1, "probes": [{"op": "read_state", "timeout_ms": 1000}]}
        assert run_id == SPEC.run_id and deadline == fake.clock + 20
        fake.probe_calls.append(root)
        return fake.probe_result

    monkeypatch.setattr(real_probe_scripts, "execute", execute_probe)
    monkeypatch.setattr("subprocess.run", fake.run)
    monkeypatch.setattr(time, "time", lambda: fake.clock)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    props = {"build_info": LLAMA_SERVER_BUILD, "model_path": str(home / "lab-bench" / GGUF_NAME)}
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.read.return_value = json.dumps(props).encode()
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr("urllib.request.build_opener", lambda *_: opener)
    clearance = root / lab_config.load_config().clearance_file
    clearance.write_text("Fleet OK 2026-10-27 primary Wi-Fi\n")
    loader = load_clearance

    def load(path: Path) -> dict[str, object]:
        if fake.events:
            fake.events.append("clearance")
            if fake.fail == "clearance":
                raise ValueError("fleet_clearance_scope_unrecognized")
        return loader(path)

    monkeypatch.setattr(real_preflight, "load_clearance", load)
    return fake


def test_fake_transport_full_chain(transport: FakeTransport, tmp_path: Path) -> None:
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.failure is None and result.receipt["status"] == "passed"
    assert transport.events == ORDER
    assert [row["step"] for row in cast(list[dict[str, Json]], result.receipt["steps"])] == ORDER
    assert result.receipt["trial_prepared"] is False and result.receipt["inference_requests"] == 0
    assert (
        transport.root / "src/offline_agent/uplink/uplink-network"
    ).read_bytes() == b"synthetic-native-helper"
    assert sum("-typecheck" in argv for argv in transport.processes) == 1
    assert sum("-O" in argv for argv in transport.processes) == 1
    assert sum("self-test" in argv for argv in transport.processes) == 1
    assert all("sudo" not in argv for argv in transport.processes)
    assert transport.probe_calls == [transport.root / ".uplink-preflight" / SPEC.run_id]


@pytest.mark.parametrize("milliseconds", (0, 1, 46, 100, 250, 500))
def test_pinned_identity_waits_out_remote_clock_lead(
    transport: FakeTransport,
    tmp_path: Path,
    milliseconds: int,
) -> None:
    transport.skew = milliseconds / 1000
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.failure is None
    assert sum(transport.sleeps) == pytest.approx(transport.skew, abs=1e-6)
    assert len(transport.sleeps) == (1 if milliseconds else 0)
    assert transport.events.count("model_attestation") == 1


@pytest.mark.parametrize("skew", (-301.0, 1.001))
def test_stale_or_excessively_future_proof_refuses_without_retry(
    transport: FakeTransport,
    tmp_path: Path,
    skew: float,
) -> None:
    transport.skew = skew
    row = recover(
        transport.backend, SPEC, tmp_path / "result", confirm=lambda _: True, root=transport.root
    )
    assert row["failure"] == "fresh_server_model_proof_required"
    assert transport.events == ORDER[:2] and not transport.sleeps
    assert row["hard_stop"] is True and row["injection_state"] == "not_requested"
    assert row["rollback_verified"] is False and row["model_calls_attempted"] == 0


def test_sha_mismatch_refuses_before_build_or_prepare(
    transport: FakeTransport, tmp_path: Path
) -> None:
    transport.bad_sha = True
    row = recover(
        transport.backend, SPEC, tmp_path / "result", confirm=lambda _: True, root=transport.root
    )
    assert row["failure"] == "model_provenance_pin_mismatch"
    assert transport.events == ORDER[:2]
    assert row["hard_stop"] is True and row["injection_state"] == "not_requested"


@pytest.mark.parametrize("step", ORDER)
def test_each_failed_gate_aborts_before_prepare_and_preserves_history(
    transport: FakeTransport,
    tmp_path: Path,
    step: str,
) -> None:
    transport.fail = step
    output = tmp_path / "result"
    row = recover(transport.backend, SPEC, output, confirm=lambda _: True, root=transport.root)
    assert row["hard_stop"] is True and row["recovered"] is False
    assert row["injection_state"] == "not_requested" and row["revert_seconds"] is None
    assert row["model_calls_attempted"] == 0 and row["watchdog_jobs_retired"] is None
    assert transport.events == ORDER[: ORDER.index(step) + 1]
    preflight = json.loads((output / (SPEC.run_id + "-preflight.json")).read_text())
    assert preflight["status"] == "refused" and preflight["failed_step"] == step
    assert preflight["failure"] and preflight["trial_prepared"] is False
    abort = transport.root / "ABORT-U1.md"
    abort.write_text("previous refusal\n")
    append_abort(transport.root, SPEC, str(row["failure"]), output)
    assert abort.read_text().startswith("previous refusal\n")
    assert str(row["failure"]) in abort.read_text()
    # Nothing prepares/arms/injects, and there is nothing to restore on a refused gate.
    assert not any("--root-request" in " ".join(argv) for argv in transport.processes)


def test_empty_transport_reply_is_terminal(transport: FakeTransport, tmp_path: Path) -> None:
    transport.malformed = "llama_build"
    row = recover(
        transport.backend, SPEC, tmp_path / "result", confirm=lambda _: True, root=transport.root
    )
    assert row["failure"] == "oob_result_indeterminate_no_retry" and row["oob_ok"] is False
    assert transport.events == ORDER[:3]


def test_proof_expiring_during_build_is_not_refreshed(
    transport: FakeTransport, tmp_path: Path
) -> None:
    transport.build_elapsed = 301
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.receipt["failed_step"] == "final_freshness"
    assert result.receipt["failure"] == "fresh_server_model_proof_required"
    assert transport.events == ORDER and not transport.sleeps


def test_attempt_receipt_cannot_be_reused(transport: FakeTransport, tmp_path: Path) -> None:
    output = tmp_path / "result"
    result = real_preflight.run_preflight(transport.backend, SPEC, output, root=transport.root)
    assert result.failure is None
    before = list(transport.processes)
    with pytest.raises(FileExistsError):
        real_preflight.run_preflight(transport.backend, SPEC, output, root=transport.root)
    assert transport.processes == before


def test_worker_cannot_retry_build_after_failure(transport: FakeTransport) -> None:
    # A missing prerequisite or a previously started build is never inferred passed.
    worker = real_preflight_worker
    worker.perform("native_typecheck", SPEC.run_id, root=transport.root)
    directory = transport.root / ".uplink-preflight" / SPEC.run_id
    (directory / "state.json").write_text('{"state":"helper_build_started"}')
    with pytest.raises(ContractError, match="not_repeatable"):
        worker.perform("helper_build", SPEC.run_id, root=transport.root)
    assert not any("-O" in argv for argv in transport.processes)


@pytest.mark.parametrize(
    "step,change",
    [
        ("llama_build", {"build_info": "wrong-build"}),
        ("native_typecheck", {"passed": False}),
        ("native_typecheck", {"sources": {}}),
        ("helper_build", {"exit_code": True}),
        ("helper_build", {"helper_sha256": "invalid"}),
        ("helper_self_test", {"network_reads": True}),
        ("staging_readback", {"sources": {}}),
        ("staging_readback", {"helper_sha256": "0" * 64}),
        ("staging_readback", {"config_sha256": "0" * 64}),
        ("staging_readback", {"owned_private_staging": False}),
        ("probe_positive_control", {"rows": []}),
        ("probe_positive_control", {"uid": 0}),
        ("probe_positive_control", {"settings_unchanged": False}),
    ],
)
def test_step_receipts_are_validated_not_just_success_flags(
    transport: FakeTransport,
    tmp_path: Path,
    step: str,
    change: dict[str, Json],
) -> None:
    transport.reply_patch[step] = change
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.failure is not None and result.receipt["failed_step"] == step
    assert transport.events == ORDER[: ORDER.index(step) + 1]


@pytest.mark.parametrize("step", ("native_typecheck", "helper_build", "helper_self_test"))
def test_native_failures_preserve_previous_helper_and_record_terminal_state(
    transport: FakeTransport,
    tmp_path: Path,
    step: str,
) -> None:
    helper = transport.root / "src/offline_agent/uplink/uplink-network"
    helper.write_bytes(b"previous-helper")
    if step == "native_typecheck":
        transport.native_exit = 1
    elif step == "helper_build":
        transport.build_exit = 1
    else:
        transport.self_test["network_writes"] = 1
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.receipt["failed_step"] == step
    assert helper.read_bytes() == b"previous-helper"
    state = json.loads(
        (transport.root / ".uplink-preflight" / SPEC.run_id / "state.json").read_text()
    )
    assert state["state"] == step + "_refused" and state["reason"]
    assert transport.events == ORDER[: ORDER.index(step) + 1]
    if step == "helper_self_test":
        log = transport.root / ".uplink-preflight" / SPEC.run_id / "self-test.json"
        assert json.loads(json.loads(log.read_text())["stdout"])["network_writes"] == 1


def test_missing_clearance_refuses_before_ssh(transport: FakeTransport, tmp_path: Path) -> None:
    (transport.root / lab_config.load_config().clearance_file).unlink()
    result = real_preflight.run_preflight(
        transport.backend, SPEC, tmp_path / "result", root=transport.root
    )
    assert result.receipt["failed_step"] == "local_authorization"
    assert result.receipt["failure"] == "fleet_clearance_required_before_fault"
    assert not transport.processes


def test_config_drift_blocks_next_remote_call(transport: FakeTransport, tmp_path: Path) -> None:
    checks = real_preflight.LabChecks(transport.backend, transport.root, tmp_path, SPEC)
    content = json.loads(lab_config.CONFIG_PATH.read_text())
    content["source_relative"] = "different/staging"
    lab_config.CONFIG_PATH.write_text(json.dumps(content))
    with pytest.raises(ContractError, match="config_changed"):
        checks.remote("llama_build")
    assert not transport.processes


@pytest.mark.parametrize(
    "change",
    [
        {"status": "refused", "reason": "unprivileged_lab_probe_required", "rows": []},
        {"status": "timeout", "rows": []},
        {"rows": []},
        {"settings_unchanged": False},
        {"wifi_route_unchanged": False},
        {"after_sha256": "b" * 64},
    ],
)
def test_real_probe_positive_control_refuses_trial_before_prepare(
    transport: FakeTransport, tmp_path: Path, change: dict[str, Json]
) -> None:
    transport.probe_result.update(change)
    row = recover(
        transport.backend, SPEC, tmp_path / "result", confirm=lambda _: True, root=transport.root
    )
    assert row["injection_state"] == "not_requested"
    assert row["hard_stop"] is True and row["recovered"] is False
    assert str(row["failure"]).startswith("probe_positive_control_")
    assert row["model_calls_attempted"] == 0
    assert not any("--root-request" in " ".join(argv) for argv in transport.processes)
    directory = transport.root / ".uplink-preflight" / SPEC.run_id
    assert json.loads((directory / "state.json").read_text())["state"] == (
        "probe_positive_control_refused"
    )
    assert json.loads((directory / "probe-positive-control.json").read_text()) == (
        transport.probe_result
    )


def test_nonprobe_trial_keeps_existing_preflight_without_probe_control(
    transport: FakeTransport, tmp_path: Path
) -> None:
    spec = RealSpec(SPEC.run_id, "dns_and_proxy", 40, False)
    result = real_preflight.run_preflight(
        transport.backend, spec, tmp_path / "result", root=transport.root
    )
    assert result.failure is None and transport.events == ORDER[:-1]
    assert not transport.probe_calls
