"""Kaylin 05:50 invariants: preserved definition, held slots and zero Keychain paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_cli import plan
from offline_agent.uplink.top10_engine import Engine
from offline_agent.uplink.top10_kernel import MacKernel

ROOT = Path(__file__).resolve().parents[2]


def test_public_definition_preserves_original_provenance_and_scope() -> None:
    public = json.loads((ROOT / "docs/PUBLIC-EXPORT-MANIFEST.json").read_text())
    original = next(row for row in public["files"]
                    if row["candidate_path"] == "configs/top10-faults.v1.json")
    assert original["source_sha256"] == (
        "224f6a2f9a78be29449d3d52b75f5d2c8ce203efd581bab455f2cb21097c7061"
    )
    assert c.file_hash(ROOT / "configs/top10-faults.v1.json") == c.REGISTRY_SHA256
    assert len(c.cases(ROOT)) == 10
    assert (
        ROOT / "configs/top10-faults.v1.sha256"
    ).read_text() == c.REGISTRY_SHA256 + "  configs/top10-faults.v1.json\n"
    assert c.BENCH_FAULT_IDS == ("F03", "F04", "F05", "F06", "F07", "F09", "F10")
    result = plan()
    assert result["registered_trial_count"] == 30
    assert result["trial_count"] == 21
    held = c.execution_profile()["held_faults"]
    assert isinstance(held, list)
    assert {c.obj(row)["fault_id"] for row in held} == {"F01", "F02", "F08"}


@pytest.mark.parametrize("fault", ["F01", "F02", "F08"])
def test_held_fault_refuses_before_any_mac_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    kernel = MacKernel(tmp_path, {})

    def forbidden() -> dict[str, object]:
        pytest.fail("held fault reached native capture")

    monkeypatch.setattr(kernel, "capture", forbidden)
    with pytest.raises(c.Refused, match="held_or_not_selected"):
        Engine(tmp_path, kernel).prepare("abcdef123456", fault)
    assert plan(fault)["trial_count"] == 0


@pytest.mark.parametrize(
    "operation",
    [
        "can_restore_network",
        "forget_current_profile",
        "restore_saved_network",
        "radio_off",
        "restore_radio",
    ],
)
def test_root_cannot_reach_keychain_or_rejoin_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    kernel = MacKernel(tmp_path, {})
    calls: list[str] = []
    monkeypatch.setattr("os.geteuid", lambda: 0)

    def native(op: str) -> dict[str, object]:
        calls.append(op)
        return {}

    monkeypatch.setattr(kernel, "native", native)
    with pytest.raises(c.Refused, match="keychain_rejoin_or_link_operation_held"):
        kernel.primitive("native", operation, {})
    assert calls == []


def test_native_contains_no_keychain_reader_or_rejoin_api() -> None:
    source = (ROOT / "harness/macos/Top10Network.swift").read_text()
    assert "func enforceOperationPolicy" in source
    assert "for uid: uid_t in [0, 501]" in source
    for forbidden in (
        "CWKeychainFindWiFiPassword",
        "SecKeychain",
        "SecItemCopyMatching",
        "wifi.associate(",
        "scanForNetworks",
        "commitConfiguration",
        "wifi.setPower(",
    ):
        assert forbidden not in source
