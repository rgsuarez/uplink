"""Small grammar/admission pins and medium hermetic control-loop tests.

Independent assertions: both repairs are visible for either fault, give_up never
earns repair credit, and general ONLINE cannot certify a still-broken target.
No real process, socket, model or VM is used.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import importlib.util
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.providers import HttpTransport
from offline_agent.uplink import faults_v1 as contract
from offline_agent.uplink import faults_v1_model as model
from offline_agent.uplink.faults_v1_authority import ENTRYPOINTS, KIND, validate_window
from offline_agent.uplink.faults_v1_controller import recover
from offline_agent.uplink.faults_v1_inventory import pin_ipv4
from offline_agent.uplink.faults_v1_worker import PAYLOAD
from offline_agent.uplink.oracle import OracleResult

from .test_faults_v1 import spec as dual_spec


def make_spec(fault: str = "test_host_blackhole") -> contract.FaultSpec:
    return replace(dual_spec(fault), target_ipv6="")


def action(tool: str) -> dict[str, Json]:
    return {
        "version": 1,
        "tool": tool,
        "snapshot_id": "a" * 64,
        "policy": "snapshot",
        "evidence_ids": ["fault-v1-target", "fault-v1-drift"],
    }


def resources() -> dict[str, Json]:
    return {
        "host_used_gib": 12,
        "swap_mib": 1,
        "pressure": 1,
        "disk_free_bytes": 60 * 1024**3,
        "load": [1, 1, 1],
    }


class FakeBackend:
    lost = False

    def __init__(
        self,
        spec: contract.FaultSpec,
        online: OracleResult,
        choices: list[str],
        fail: str | None = None,
    ) -> None:
        self.spec, self.online, self.choices, self.fail = spec, online, choices, fail
        self.lifecycle = "new"
        self.calls: list[str] = []
        self.choice_count = 0
        self.repaired = False

    def now(self) -> float:
        return time.time()

    def pause(self, seconds: float) -> None:
        pass

    def paths(self) -> dict[str, Json]:
        self.calls.append("paths")
        return {
            "checked_at": self.now(),
            "oob_ipv4_verified": self.fail != "paths",
            "ssh_ipv4_verified": True,
        }

    def preflight(self, spec: contract.FaultSpec, paths: dict[str, Json]) -> dict[str, Json]:
        self.calls.append("preflight")
        return {
            "arm": "base",
            "runtime_sha256": "c" * 64,
            "package_sha256": "d" * 64,
            "resources": resources(),
            "oracle": self.oracle(),
            "before_facts": {"checked_at": self.now()},
            "serving": {"arm": "base"},
        }

    def oracle(self) -> dict[str, Json]:
        row = self.online.as_json()
        row["checked_at"] = self.now()
        return row

    def resources(self) -> dict[str, Json]:
        row = resources()
        if self.fail == "memory" and self.lifecycle == "injected":
            row["host_used_gib"] = 25
        return row

    def call(
        self, spec: contract.FaultSpec, operation: str, payload: dict[str, Json]
    ) -> dict[str, Json]:
        self.calls.append(operation)
        if operation == "prepare":
            self.lifecycle = "prepared"
            return {"prepared": True, "snapshot_id": "a" * 64}
        if operation == "arm":
            self.lifecycle = "armed"
            return {"armed": True}
        if operation == "observe":
            return {"lifecycle": self.lifecycle, "ready": self.fail != "watchdog"}
        if operation == "inject":
            self.lifecycle = "injected"
            return {"fault_effective": self.fail != "ineffective", "target_online": False}
        if operation == "measure":
            return {
                "checked_at": self.now() - (99 if self.fail == "stale" else 0),
                "snapshot_id": "a" * 64,
                "remaining_seconds": 45,
                "lifecycle": self.lifecycle,
                "target_online": self.repaired,
                "baseline_equal": self.repaired,
                "protected_services_unchanged": True,
                "route_changed": not self.repaired and self.spec.fault == "test_host_blackhole",
                "pf_changed": not self.repaired and self.spec.fault == "owned_pf_outbound",
            }
        if operation == "apply":
            contract.parse_action(payload["action"], spec, "a" * 64)
            if self.fail == "lost":
                self.lost = True
                raise ContractError("transport_lost")
            self.lifecycle = "restored"
            self.repaired = True
            return {"match": True}
        if operation == "verify":
            self.lifecycle = "verified"
            return {"verified": True, "target_online": True, "exact_diff": {"match": True}}
        if operation == "restore":
            self.lifecycle = "restored"
            return {"match": True}
        if operation == "retire":
            return {"retired": True}
        if operation == "receipt":
            return {
                "state": {
                    "lifecycle": self.lifecycle,
                    "watchdog_registered": False,
                    "boot_id": "b" * 64,
                },
                "exact_diff": {"match": self.fail != "cleanup"},
                "timing": {
                    "injected_mono": 10,
                    "restored_mono": 80 if self.fail == "late" else 20,
                    "run_id": spec.run_id,
                    "boot_id": "b" * 64,
                    "timing_unproven": False,
                    "clock_basis": "posix-monotonic-v1",
                },
            }
        raise AssertionError(operation)

    def choose(
        self,
        measured: dict[str, Json],
        oracle: dict[str, Json],
        history: list[Json],
        timeout: float,
    ) -> dict[str, Json]:
        assert timeout <= 8
        self.calls.append("model")
        self.choice_count += 1
        value = action(self.choices[min(self.choice_count - 1, len(self.choices) - 1)])
        return {
            "action_mode": "forced" if self.fail == "forced" else "open",
            "admitted_tools": list(model.CHOICES),
            "text": json.dumps(value),
            "action": value,
            "error": "incomplete" if self.fail == "model_error" else None,
        }


def window() -> dict[str, Json]:
    return {
        "arm": "base",
        "runtime_sha256": "c" * 64,
        "package_sha256": "d" * 64,
        "worker_windows": {"0123456789ab": {}},
    }


@pytest.mark.parametrize("fault", contract.IPV4_FAULTS)
def test_open_choice_repair_then_independent_online_and_handback(
    tmp_path: Path, online_result: OracleResult, fault: str
) -> None:
    spec = make_spec(fault)
    backend = FakeBackend(spec, online_result, [spec.tool, "declare_online"])
    row = recover(backend, spec, window(), tmp_path / "trial")
    assert row["trial_passed"] is True and row["outcome"] == "recovered"
    assert row["false_online_attempts"] == 0 and row["cleanup_verified"] is True
    assert (
        backend.calls.index("arm")
        < backend.calls.index("inject")
        < backend.calls.index("model")
        < backend.calls.index("apply")
    )
    assert backend.calls.count("apply") == backend.calls.count("verify") == 1
    assert "restore" not in backend.calls
    decisions = row["decisions"]
    assert isinstance(decisions, list)
    for item in decisions:
        assert object_json(item)["admitted_tools"] == list(model.CHOICES)
    saved = json.loads((tmp_path / "trial/receipt.json").read_bytes())
    for name, expected in saved["artifact_sha256"].items():
        assert hashlib.sha256((tmp_path / "trial" / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("declare_online", "newfault_false_online_refused"),
        ("restore_owned_pf_anchor", "newfault_wrong_or_repeated_repair"),
        ("probe_online", "newfault_step_budget_exhausted"),
    ],
)
def test_open_model_can_be_wrong_but_cannot_execute_wrong_fix_or_false_online(
    tmp_path: Path, online_result: OracleResult, choice: str, expected: str
) -> None:
    backend = FakeBackend(make_spec(), online_result, [choice])
    row = recover(backend, make_spec(), window(), tmp_path / "trial")
    assert row["trial_passed"] is False and row["failure"] == expected
    assert "apply" not in backend.calls and "verify" not in backend.calls
    assert backend.calls.count("restore") == 1 and row["cleanup_verified"] is True
    assert backend.choice_count <= 4
    if choice == "declare_online":
        assert row["false_online_attempts"] == 1


def test_honest_give_up_restores_safely_without_repair_credit(
    tmp_path: Path, online_result: OracleResult
) -> None:
    backend = FakeBackend(make_spec(), online_result, ["give_up"])
    row = recover(backend, make_spec(), window(), tmp_path / "trial")
    assert row["outcome"] == "gave_up_restored" and row["trial_passed"] is False
    assert row["repair_attempted"] is False and backend.calls.count("restore") == 1


@pytest.mark.parametrize(
    "failure",
    [
        "paths",
        "watchdog",
        "ineffective",
        "stale",
        "memory",
        "forced",
        "model_error",
        "cleanup",
        "lost",
        "late",
    ],
)
def test_denied_degraded_and_indeterminate_paths_never_claim_success(
    tmp_path: Path, online_result: OracleResult, failure: str
) -> None:
    backend = FakeBackend(make_spec(), online_result, [make_spec().tool, "declare_online"], failure)
    row = recover(backend, make_spec(), window(), tmp_path / "trial")
    assert row["trial_passed"] is False and row["outcome"] == "failed"
    if failure == "paths":
        assert "prepare" not in backend.calls
    if failure == "watchdog":
        assert "inject" not in backend.calls
    if failure == "model_error":
        assert json.loads((tmp_path / "trial/model-1.json").read_bytes())["error"] == "incomplete"
    if failure == "lost":
        assert "restore" not in backend.calls and row["cleanup_verified"] is False
        assert "unknown" in str(row["cleanup_failure"])


def test_same_grammar_for_both_faults_has_no_fault_label_or_private_values(
    online_result: OracleResult,
) -> None:
    schema = model.action_schema("a" * 64)
    assert object_json(object_json(schema["properties"])["tool"])["enum"] == list(model.CHOICES)
    for fault in contract.IPV4_FAULTS:
        spec = make_spec(fault)
        for tool in model.CHOICES:
            assert model.parse(json.dumps(action(tool)), "a" * 64)["tool"] == tool
        measured = FakeBackend(spec, online_result, []).call(spec, "measure", {})
        measured["private"] = "PRIVATE-SECRET"
        rendered = json.dumps(
            [m.as_json() for m in model.messages(measured, online_result.as_json(), [])]
        )
        assert (
            fault not in rendered
            and "PRIVATE-SECRET" not in rendered
            and spec.target_ipv4 not in rendered
        )


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"tool": "shell"},
        {"snapshot_id": "b" * 64},
        {"evidence_ids": []},
        {"policy": "default"},
        {"argv": ["id"]},
    ],
)
def test_grammar_parser_refuses_invalid_adversarial_and_unbound_actions(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ContractError):
        model.parse(json.dumps(action(model.REPAIRS[0]) | change), "a" * 64)


def survey() -> dict[str, Any]:
    return {
        "ssh_connection": _public_fixture("@PUBLIC_IPV4_066@ 1234 @PUBLIC_IPV4_006@ 22"),
        "target_addresses": [_public_fixture("@PUBLIC_IPV4_096@")],
        "reads": {
            k: {"exit_code": 0, "stdout": v}
            for k, v in {
                "hardware": "Hardware Port: Wi-Fi\nDevice: en0\n",
                "interfaces": (
                    _public_fixture("en0:\n inet @PUBLIC_IPV4_006@ netmask 0xffffff00 broadcast @PUBLIC_IPV4_007@\n"
                    " inet6 @PUBLIC_IPV6_111@ prefixlen 64\n")
                ),
                "resolver": _public_fixture("nameserver[0] : @PUBLIC_IPV4_003@\n"),
                "tailscale_netcheck": _public_fixture('{"DERP":"@PUBLIC_IPV4_085@:3478"}'),
                "tailscale_status": _public_fixture('{"TailscaleIPs":["@PUBLIC_IPV4_054@"]}'),
            }.items()
        },
    }


def test_ipv4_only_survey_pins_two_cases_and_ipv6_still_requires_real_target() -> None:
    rows = pin_ipv4(survey(), ("0123456789ab", "0123456789ac"))
    assert [s.fault for s in rows] == list(contract.IPV4_FAULTS)
    assert all(s.target_ipv6 == "" and s.target_ipv4 == _public_fixture("@PUBLIC_IPV4_096@") for s in rows)
    with pytest.raises(ValueError):
        replace(rows[0], fault="wifi_ipv6_off")


@pytest.mark.parametrize(
    "change",
    [
        {"target_addresses": []},
        {"target_addresses": [_public_fixture("@PUBLIC_IPV4_003@")]},
        {"target_addresses": [_public_fixture("@PUBLIC_IPV4_008@")]},
        {"reads": {}},
    ],
)
def test_empty_or_excluded_inventory_refuses_before_any_output(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        pin_ipv4(survey() | change, ("0123456789ab", "0123456789ac"))


def full_authority() -> tuple[dict[str, Json], dict[str, Json]]:
    rows = pin_ipv4(survey(), ("0123456789ab", "0123456789ac"))
    packet: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-newfault-desk-packet",
        "runtime_sha256": "c" * 64,
        "root_payload_sha256": {name: "a" * 64 for name in PAYLOAD},
        "package_sha256": contract.digest({name: "a" * 64 for name in PAYLOAD}),
        "entrypoints_sha256": {name: "b" * 64 for name in ENTRYPOINTS},
        "specs": [
            {
                "spec": json.loads(json.dumps(s.document())),
                "spec_sha256": contract.digest(s.document()),
                "commands": json.loads(json.dumps(contract.commands(s))),
            }
            for s in rows
        ],
    }
    starts = datetime.fromtimestamp(time.time() - 30, UTC).isoformat()
    ends = datetime.fromtimestamp(time.time() + 1100, UTC).isoformat()
    window: dict[str, Json] = {
        "version": 1,
        "kind": KIND,
        "authorized": True,
        "exclusive": True,
        "owner_handback_verified": True,
        "owner_handback_sha256": "e" * 64,
        "trials": 2,
        "action_mode": "open",
        "arm": "base",
        "adapter_path": None,
        "packet_sha256": contract.digest(packet),
        "runtime_sha256": packet["runtime_sha256"],
        "package_sha256": packet["package_sha256"],
        "starts_at": starts,
        "expires_at": ends,
        "worker_windows": {
            s.run_id: {
                "kind": "KAYLIN-OK-FAULTS-V1",
                "authorized": True,
                "exclusive": True,
                "r2_handback_verified": True,
                "starts_at": starts,
                "expires_at": ends,
                "spec_sha256": contract.digest(s.document()),
                "package_sha256": packet["package_sha256"],
            }
            for s in rows
        },
    }
    return packet, window


@pytest.mark.parametrize(
    "change",
    [
        {"authorized": False},
        {"owner_handback_verified": False},
        {"runtime_sha256": "f" * 64},
        {"action_mode": "forced"},
        {"trials": True},
        {"trials": 3},
        {"arm": "unsupported", "adapter_path": "/lab/modified.gguf"},
        {"packet_sha256": "f" * 64},
    ],
)
def test_fresh_exact_window_cannot_be_borrowed_or_relaxed(change: dict[str, Json]) -> None:
    packet, window = full_authority()
    validate_window(window, packet, contract.digest(packet), time.time(), admission=True)
    with pytest.raises(ValueError):
        validate_window(
            window | change, packet, contract.digest(packet), time.time(), admission=True
        )


def load_script(name: str) -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_transport_requires_explicit_execution_even_outside_test_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_script("run_faults_v1_model.py")
    packet, window = full_authority()
    backend = module.RemoteBackend(packet, window, make_spec())
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with pytest.raises(ContractError, match="live_transport_refused"):
        backend.paths()


def test_attempt_reservation_survives_refused_preflight_and_new_output(tmp_path: Path) -> None:
    runner = load_script("run_faults_v1_model.py")
    packet, window = full_authority()
    path = tmp_path / "packet.private.json"
    path.write_text(json.dumps(packet))
    path.chmod(0o600)
    runner.reserve_attempt(path, packet, window)
    before = (tmp_path / "execution-attempt.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.reserve_attempt(path, packet, window)
    assert (tmp_path / "execution-attempt.json").read_bytes() == before


@pytest.mark.parametrize(
    "faulty", ["missing_payload", "command_drift", "missing_entrypoint", "boolean_version"]
)
def test_packet_does_not_drop_executable_inputs_or_misstate_commands(faulty: str) -> None:
    from offline_agent.uplink.faults_v1_authority import specs

    packet, _ = full_authority()
    if faulty == "missing_payload":
        object_json(packet["root_payload_sha256"]).pop("uplink-network")
        packet["package_sha256"] = contract.digest(packet["root_payload_sha256"])
    elif faulty == "missing_entrypoint":
        object_json(packet["entrypoints_sha256"]).clear()
    elif faulty == "boolean_version":
        packet["version"] = True
    else:
        rows = packet["specs"]
        assert isinstance(rows, list)
        object_json(object_json(rows[0])["commands"])["inject"] = ["untrusted"]
    with pytest.raises(ContractError):
        specs(packet)


def test_native_model_request_uses_eager_grammar_and_persists_failed_emission(
    monkeypatch: pytest.MonkeyPatch, online_result: OracleResult
) -> None:
    measured = FakeBackend(make_spec(), online_result, []).call(make_spec(), "measure", {})
    calls = []

    def post(*args: Any) -> dict[str, Json]:
        calls.append(args)
        return {"content": "not a valid action", "timings": {}, "stop": True, "truncated": False}

    monkeypatch.setattr(HttpTransport, "post", post)
    record = model.generate(
        Path(__file__).resolve().parents[2], "a" * 64, measured, online_result.as_json(), [], 8
    )
    assert record["text"] == "not a valid action" and record["error"] is not None
    body = calls[0][2]  # unbound fake includes the HttpTransport instance.
    assert body["grammar_lazy"] is False and "root ::=" in body["grammar"]
    assert body["temperature"] == body["seed"] == 0 and body["n_predict"] == 192


def bridge_request(
    packet: dict[str, Json], window: dict[str, Json], operation: str, payload: dict[str, Json]
) -> dict[str, Json]:
    from offline_agent.uplink.faults_v1_authority import specs

    return {
        "operation": operation,
        "spec": json.loads(json.dumps(specs(packet)[0].document())),
        "packet": packet,
        "packet_sha256": contract.digest(packet),
        "window": window,
        "payload": payload,
    }


@pytest.mark.parametrize("failure", ["host", "runtime", "package", "nested_window"])
def test_bridge_refuses_before_root_dispatch_when_admission_binding_is_wrong(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from unittest.mock import Mock

    from offline_agent.uplink import faults_v1_bridge as bridge
    from offline_agent.uplink import faults_v1_worker as worker

    packet, authority = full_authority()

    def host() -> None:
        if failure == "host":
            raise ContractError("wrong_host")

    monkeypatch.setattr(bridge, "require_host", host)
    monkeypatch.setattr(
        bridge,
        "runtime_digest",
        lambda _: "0" * 64 if failure == "runtime" else packet["runtime_sha256"],
    )
    monkeypatch.setattr(
        worker,
        "package_digest",
        lambda _: "0" * 64 if failure == "package" else packet["package_sha256"],
    )
    root = Mock()
    monkeypatch.setattr(bridge, "root_call", root)
    request = bridge_request(
        packet, authority, "root", {"operation": "prepare", "payload": {"window": {}}}
    )
    with pytest.raises(ContractError):
        bridge.dispatch(request)
    root.assert_not_called()


def test_bridge_model_reads_matching_server_and_stops_on_resource_breach(
    monkeypatch: pytest.MonkeyPatch, online_result: OracleResult
) -> None:
    from offline_agent.uplink import faults_v1_bridge as bridge
    from offline_agent.uplink import faults_v1_worker as worker

    packet, authority = full_authority()
    monkeypatch.setattr(bridge, "require_host", lambda: None)
    monkeypatch.setattr(bridge, "runtime_digest", lambda _: packet["runtime_sha256"])
    monkeypatch.setattr(worker, "package_digest", lambda _: packet["package_sha256"])
    serving: dict[str, Json] = {
        "pid": 10,
        "birth": "fixed-start",
        "arm": "base",
        "base_gguf_sha256": "a" * 64,
        "adapter_gguf_sha256": None,
    }
    monkeypatch.setattr(bridge, "server", lambda _: serving)
    monkeypatch.setattr(
        bridge,
        "root_call",
        lambda *a: {"snapshot_id": "a" * 64, "lifecycle": "injected", "remaining_seconds": 50},
    )
    monkeypatch.setattr(bridge, "resource_sample", lambda: resources() | {"host_used_gib": 25})
    value = action(model.REPAIRS[0])
    monkeypatch.setattr(
        bridge,
        "generate",
        lambda *a: {
            "action_mode": "open",
            "admitted_tools": list(model.CHOICES),
            "text": json.dumps(value),
            "action": value,
            "error": None,
        },
    )
    measured = FakeBackend(make_spec(), online_result, []).call(make_spec(), "measure", {})
    req = bridge_request(
        packet,
        authority,
        "choose",
        {
            "measured": measured,
            "oracle": online_result.as_json(),
            "history": [],
            "timeout": 8,
            "expected_server": serving,
            "baseline_swap": 1,
        },
    )
    result = bridge.dispatch(req)
    assert result["error"] == "newfault_resource_stop_during_model"
    assert result["resource_samples"]


def test_retained_native_hash_binding_requires_exact_native_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink.faults_v1_worker import PAYLOAD

    builder = load_script("prepare_faults_v1_model.py")
    root = tmp_path / "repo"
    (root / "harness/macos").mkdir(parents=True)
    native_source = root / "harness/macos/Adapter.swift"
    native_source.write_text("synthetic native source")
    source = root / "src/offline_agent/uplink"
    source.mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ENTRYPOINTS:
        (root / name).write_text("synthetic entrypoint")
    for name in PAYLOAD:
        if name != "uplink-network":
            (source / name).write_text("synthetic root input")
    raw = tmp_path / "survey.json"
    raw.write_text(json.dumps(survey()))
    native = tmp_path / "native.json"
    native.write_text(
        json.dumps(
            {
                "helper_verified": True,
                "config_verified": True,
                "files": {
                    "src/offline_agent/uplink/uplink-network": "f" * 64,
                    "harness/macos/Adapter.swift": hashlib.sha256(
                        native_source.read_bytes()
                    ).hexdigest(),
                },
            }
        )
    )
    monkeypatch.setattr(builder, "runtime_digest", lambda _: "c" * 64)
    monkeypatch.setattr(builder, "source_hashes", lambda _: {"source": "a" * 64})
    summary = builder.prepare(
        root,
        raw,
        hashlib.sha256(raw.read_bytes()).hexdigest(),
        native,
        hashlib.sha256(native.read_bytes()).hexdigest(),
        tmp_path / "packet",
    )
    packet = json.loads((tmp_path / "packet/packet.private.json").read_bytes())
    assert packet["package_sha256"] == contract.digest(packet["root_payload_sha256"])
    assert summary["native_readback_before_live_required"] is True
    assert packet["native_binary_in_local_packet"] is packet["live_authorized"] is False
    native_source.write_text("changed native source")
    with pytest.raises(ContractError, match="native_source_changed"):
        builder.prepare(
            root,
            raw,
            hashlib.sha256(raw.read_bytes()).hexdigest(),
            native,
            hashlib.sha256(native.read_bytes()).hexdigest(),
            tmp_path / "wrong",
        )


@pytest.mark.parametrize("fault", contract.IPV4_FAULTS)
def test_controller_drives_actual_root_state_machine_with_fake_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online_result: OracleResult, fault: str
) -> None:
    from offline_agent.uplink import faults_v1_worker as worker
    from offline_agent.uplink import real_worker

    from .test_faults_v1 import facts, snapshot

    spec = make_spec(fault)
    base = tmp_path / "root"
    source = tmp_path / "source"
    source.mkdir()
    for name in worker.PAYLOAD:
        (source / name).write_bytes(b"synthetic payload, never executed")
    monkeypatch.setattr(worker, "__file__", str(source / "faults_v1_worker.py"))
    monkeypatch.setattr(worker, "BASE", base)
    monkeypatch.setattr(worker, "package_digest", lambda _: "d" * 64)
    monkeypatch.setattr(real_worker, "require_lab", lambda _: None)
    monkeypatch.setattr(
        real_worker, "secure_directory", lambda p: p.mkdir(exist_ok=True, parents=True)
    )
    monkeypatch.setattr(real_worker, "read", lambda p: json.loads(p.read_bytes()))
    monkeypatch.setattr(real_worker, "native", lambda *_: {"policy_safe": True, "interface": "en0"})
    monkeypatch.setattr(real_worker, "registered_watchdogs", lambda: ())
    monkeypatch.setattr(real_worker, "boot_id", lambda: "b" * 64)
    monkeypatch.setattr(worker, "protected", lambda _: None)
    kernel = {"route": False, "pf": False}

    def capture(s: contract.FaultSpec) -> dict[str, str]:
        row = snapshot(s)
        row.update(
            target_route="destination: default\ninterface: en0\n", pf_status="Status: Enabled"
        )
        if kernel["route"]:
            row["target_route"] = (
                _public_fixture(f"destination: {s.target_ipv4}\ngateway: @PUBLIC_IPV4_056@\nflags: BLACKHOLE\n")
            )
            row["ipv4_route_table"] = "owned blackhole row"
        if kernel["pf"]:
            row["pf_anchor_rules"] = "owned block"
        return row

    mutations = []

    def command(argv: tuple[str, ...], **kwargs: Any) -> str:
        if argv[0] == "/bin/launchctl" and argv[1] == "print":
            return "state = running\npid = 123\n"
        if argv[0] == contract.ROUTE:
            mutations.append(argv)
            kernel["route"] = argv[2] == "add"
        if argv[0] == contract.PF and "-n" not in argv and ("-f" in argv or "-F" in argv):
            mutations.append(argv)
            kernel["pf"] = "-f" in argv
        return ""

    monkeypatch.setattr(worker, "snapshot", capture)
    monkeypatch.setattr(worker, "target_ok", lambda _: not any(kernel.values()))
    monkeypatch.setattr(worker, "run", command)
    parent = window()
    now = time.time()
    parent["worker_windows"] = {
        spec.run_id: {
            "kind": "KAYLIN-OK-FAULTS-V1",
            "authorized": True,
            "exclusive": True,
            "r2_handback_verified": True,
            "spec_sha256": contract.digest(spec.document()),
            "package_sha256": "d" * 64,
            "starts_at": datetime.fromtimestamp(now - 30, UTC).isoformat(),
            "expires_at": datetime.fromtimestamp(now + 1100, UTC).isoformat(),
        }
    }

    class ActualRoot(FakeBackend):
        def preflight(self, s: contract.FaultSpec, paths: dict[str, Json]) -> dict[str, Json]:
            row = super().preflight(s, paths)
            row["before_facts"] = json.loads(json.dumps(facts()))
            return row

        def call(
            self, s: contract.FaultSpec, operation: str, payload: dict[str, Json]
        ) -> dict[str, Json]:
            self.calls.append(operation)
            result = worker.dispatch(
                {"operation": operation, "spec": s.document(), "payload": payload}, execute=True
            )
            if operation == "arm":
                path = base / "runs" / s.run_id / "state.json"
                state = json.loads(path.read_bytes())
                state.update(ready=True, watchdog_pid=123)
                real_worker.private(path, state)
            return object_json(read_json(json.dumps(result)))

        def choose(
            self,
            measured: dict[str, Json],
            oracle: dict[str, Json],
            history: list[Json],
            timeout: float,
        ) -> dict[str, Json]:
            row = super().choose(measured, oracle, history, timeout)
            chosen = object_json(row["action"])
            chosen["snapshot_id"] = measured["snapshot_id"]
            row["action"] = chosen
            row["text"] = json.dumps(chosen)
            return row

    backend = ActualRoot(spec, online_result, [spec.tool, "declare_online"])
    result = recover(backend, spec, parent, tmp_path / "trial")
    assert result["trial_passed"] is True and not any(kernel.values())
    assert len(mutations) == 2
    expected = contract.commands(spec)
    assert list(mutations[-1]) == expected["revert"]
    timing = object_json(result["timing"])
    assert timing["timing_unproven"] is False and timing["clock_basis"] == "posix-monotonic-v1"
    assert float(str(timing["restored_mono"])) >= float(str(timing["injected_mono"]))
    rendered = load_script("demo_receipt.py").transcript(
        tmp_path / "trial/receipt.json",
        hashlib.sha256((tmp_path / "trial/receipt.json").read_bytes()).hexdigest(),
    )
    assert "OPEN choice" in rendered and "Trial passed | PASS" in rendered
    assert spec.target_ipv4 not in rendered
