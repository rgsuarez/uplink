"""Medium, file-only tests: receipt provenance, truthful labels and privacy.

The pre-existing simulated producer supplies valid V5 traces. These tests must
never contact a host or run a process; the Uplink no_live_effects fixture applies.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.oracle import OracleResult

from .base_receipts import receipt


@pytest.fixture
def renderer() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts/demo_receipt.py"
    spec = importlib.util.spec_from_file_location("demo_receipt", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v5_transcript_uses_recorded_actions_and_hashes(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path, arm="base", mode="forced")
    rendered = renderer.transcript(path, sha(path))
    assert "set_dns; FORCED" in rendered and "restore_proxy; FORCED" in rendered
    assert "MOCK" in rendered and "independent diagnosis" in rendered
    assert sha(path) in rendered
    data = json.loads(path.read_bytes())
    assert data["probe_execution"]["trace_sha256"] in rendered
    assert "R:/revert_seconds" in rendered and "T:/" in rendered
    for check in (
        "DNS / Apple",
        "DNS / Google",
        "HTTPS / Apple",
        "HTTPS / Google",
        "Captive check / Apple",
        "Captive check / Google",
    ):
        assert check in rendered


def test_copies_and_private_fields_cannot_supply_video_evidence(
    renderer: Any, tmp_path: Path
) -> None:
    path = receipt(tmp_path, arm="base", mode="forced")
    data = json.loads(path.read_bytes())
    data["probe_episodes"] = [{"action_mode": "open", "action": {"tool": "untrusted_payload"}}]
    data["non_read_state_returned"] = 999
    data["authorization"] = {"serial": "PRIVATE-DEVICE", "token": "PRIVATE-CREDENTIAL"}
    path.write_text(json.dumps(data))
    rendered = renderer.transcript(path, sha(path))
    assert "FORCED" in rendered and "OPEN choice" not in rendered
    for forbidden in ("untrusted_payload", "PRIVATE-DEVICE", "PRIVATE-CREDENTIAL", "999"):
        assert forbidden not in rendered


def test_open_action_is_distinct_from_forced(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path)
    assert "OPEN choice" in renderer.transcript(path, sha(path))


def test_tampered_trace_or_wrong_receipt_pin_refuses(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path)
    with pytest.raises(ContractError, match="hash_mismatch"):
        renderer.transcript(path, "0" * 64)
    data = json.loads(path.read_bytes())
    trace = path.parent / "traces" / data["trace"]
    trace.write_text(trace.read_text() + "\n")
    with pytest.raises(ContractError, match="digest_mismatch"):
        renderer.transcript(path, sha(path))


def test_missing_oracle_and_time_are_unknown_not_success(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path)
    data = json.loads(path.read_bytes())
    data.pop("oracle")
    data.pop("revert_seconds")
    path.write_text(json.dumps(data))
    rendered = renderer.transcript(path, sha(path))
    assert "Final oracle | UNKNOWN" in rendered
    assert "Revert time | UNKNOWN" in rendered
    assert "DNS / Apple | UNKNOWN" in rendered


def test_online_with_failed_check_is_explicitly_unproven(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path)
    data = json.loads(path.read_bytes())
    data["oracle"]["evidence"][0]["ok"] = False
    path.write_text(json.dumps(data))
    rendered = renderer.transcript(path, sha(path))
    assert "ONLINE claim is UNPROVEN" in rendered and "DNS / Apple | FAIL" in rendered


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "12"])
def test_invalid_numbers_never_become_durations(renderer: Any, value: Any) -> None:
    with pytest.raises(ContractError, match="finite_number"):
        renderer.number(value)


@pytest.mark.parametrize("raw", ["{}", '{"version":1,"version":1}', "[]"])
def test_empty_and_ambiguous_receipts_refuse(renderer: Any, tmp_path: Path, raw: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(raw)
    with pytest.raises(ContractError):
        renderer.transcript(path, sha(path))


def fault_receipt(tmp_path: Path, online: OracleResult, fault: str) -> Path:
    tools = {
        "test_host_blackhole": "restore_test_host_route",
        "wifi_ipv6_off": "restore_wifi_ipv6",
        "owned_pf_outbound": "restore_owned_pf_anchor",
    }
    spec = {
        "run_id": "123456789abc",
        "fault": fault,
        "target_ipv4": _public_fixture("@PUBLIC_IPV4_096@"),
        "target_ipv6": _public_fixture("@PUBLIC_IPV6_107@"),
        "wifi_interface": "en0",
        "protected_networks": [_public_fixture("@PUBLIC_IPV4_004@/8")],
        "tailscale_addresses": [_public_fixture("@PUBLIC_IPV4_053@")],
    }
    # Literal producer response shapes, independent of the renderer.
    documents = {
        "state": {
            "spec": spec,
            "snapshot_id": "a" * 64,
            "lifecycle": "verified",
            "reason": "typed_agent_snapshot_repair",
            "boot_id": "fixture-boot",
        },
        "inject": {"fault_effective": True, "target_online": False},
        "apply_request": {
            "operation": "apply",
            "spec": spec,
            "payload": {
                "action": {
                    "version": 1,
                    "tool": tools[fault],
                    "snapshot_id": "a" * 64,
                    "policy": "snapshot",
                    "evidence_ids": ["fault-v1-target", "fault-v1-drift"],
                }
            },
        },
        "verify": {
            "verified": True,
            "target_online": True,
            "exact_diff": {
                "match": True,
                "before_sha256": "a" * 64,
                "after_sha256": "a" * 64,
                "fields": {"wifi_info": True, "ipv4_route_table": True},
            },
        },
        "oracle": online.as_json(),
        "timing": {
            "run_id": spec["run_id"],
            "boot_id": "fixture-boot",
            "clock_basis": "posix-monotonic-v1",
            "timing_unproven": False,
            "injected_mono": 10.0,
            "restored_mono": 18.125,
        },
    }
    files = {}
    for name, value in documents.items():
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        files[name] = {"path": path.name, "sha256": sha(path)}
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps({"kind": "uplink-faults-v1-demo-input", "version": 1, "files": files})
    )
    return path


@pytest.mark.parametrize("fault", ["test_host_blackhole", "wifi_ipv6_off", "owned_pf_outbound"])
def test_future_fault_worker_receipts_render_without_live_effects(
    renderer: Any, tmp_path: Path, online_result: OracleResult, fault: str
) -> None:
    path = fault_receipt(tmp_path, online_result, fault)
    rendered = renderer.transcript(path, sha(path))
    assert "FORCED single allowed repair" in rendered
    assert "8.125 s" in rendered and "Pinned target restored | PASS" in rendered
    assert "Exact snapshot diff | PASS" in rendered
    assert sha(tmp_path / "state.json") in rendered
    assert sha(tmp_path / "timing.json") in rendered


def test_fault_receipt_missing_evidence_does_not_invent_elapsed_or_success(
    renderer: Any, tmp_path: Path, online_result: OracleResult
) -> None:
    path = fault_receipt(tmp_path, online_result, "test_host_blackhole")
    data = json.loads(path.read_bytes())
    data["files"] = {"state": data["files"]["state"]}
    path.write_text(json.dumps(data))
    rendered = renderer.transcript(path, sha(path))
    assert "Revert time | UNKNOWN" in rendered
    assert "Worker verification recorded | UNKNOWN" in rendered
    assert "Requested action | UNKNOWN" in rendered


@pytest.mark.parametrize("mutation", ["mismatched_run", "bad_diff", "wrong_hash", "path_escape"])
def test_fault_receipt_bindings_refuse_inconsistent_evidence(
    renderer: Any, tmp_path: Path, online_result: OracleResult, mutation: str
) -> None:
    path = fault_receipt(tmp_path, online_result, "owned_pf_outbound")
    data = json.loads(path.read_bytes())
    if mutation == "wrong_hash":
        data["files"]["oracle"]["sha256"] = "0" * 64
    elif mutation == "path_escape":
        data["files"]["state"]["path"] = "../state.json"
    else:
        key = "apply_request" if mutation == "mismatched_run" else "verify"
        member = tmp_path / (key + ".json")
        content = json.loads(member.read_bytes())
        if mutation == "mismatched_run":
            content["spec"]["run_id"] = "000000000000"
        else:
            content["exact_diff"]["fields"]["wifi_info"] = False
        member.write_text(json.dumps(content))
        data["files"][key]["sha256"] = sha(member)
    path.write_text(json.dumps(data))
    with pytest.raises(ContractError):
        renderer.transcript(path, sha(path))


def test_symlink_input_refuses_before_read(renderer: Any, tmp_path: Path) -> None:
    original = tmp_path / "original.json"
    original.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(original)
    with pytest.raises(ContractError, match="symlink"):
        renderer.transcript(link, sha(original))


@pytest.mark.parametrize(
    "change",
    [
        {"boot_id": "other-boot"},
        {"restored_mono": 9.0},
        {"timing_unproven": True},
        {"injected_mono": True},
    ],
)
def test_fault_timing_cannot_cross_clocks_or_invent_interval(
    renderer: Any, tmp_path: Path, online_result: OracleResult, change: dict[str, Any]
) -> None:
    path = fault_receipt(tmp_path, online_result, "test_host_blackhole")
    timing = tmp_path / "timing.json"
    value = json.loads(timing.read_bytes()) | change
    timing.write_text(json.dumps(value))
    manifest = json.loads(path.read_bytes())
    manifest["files"]["timing"]["sha256"] = sha(timing)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ContractError):
        renderer.transcript(path, sha(path))


def test_duplicate_oracle_ids_cannot_mask_a_missing_check(renderer: Any, tmp_path: Path) -> None:
    path = receipt(tmp_path)
    value = json.loads(path.read_bytes())
    value["oracle"]["evidence"][1] = value["oracle"]["evidence"][0]
    path.write_text(json.dumps(value))
    with pytest.raises(ContractError, match="duplicate_or_wrong"):
        renderer.transcript(path, sha(path))


def test_cli_preserves_existing_output_and_emits_closed_reason(
    renderer: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = receipt(tmp_path)
    output = tmp_path / "preserve.md"
    output.write_text("previous artifact")
    monkeypatch.setattr(
        sys,
        "argv",
        ["demo_receipt", "--receipt", str(path), "--sha256", "0" * 64, "--output", str(output)],
    )
    with pytest.raises(SystemExit) as failure:
        renderer.main()
    assert failure.value.code == 2
    assert "demo_receipt_hash_mismatch" in capsys.readouterr().err
    assert output.read_text() == "previous artifact"
    monkeypatch.setattr(
        sys,
        "argv",
        ["demo_receipt", "--receipt", str(path), "--sha256", sha(path), "--output", str(output)],
    )
    with pytest.raises(SystemExit):
        renderer.main()
    assert "FileExistsError" in capsys.readouterr().err
    assert output.read_text() == "previous artifact"
