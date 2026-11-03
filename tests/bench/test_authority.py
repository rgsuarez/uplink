"""Exact authority, contradictory evidence and one-use consumption invariants."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import copy
import hashlib
import json
from pathlib import Path

import pytest

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.bench_attempt import Attempt


def test_exact_fresh_authority_accepts_new_pid(manifest, authority_bundle) -> None:
    now, window, owner, richard = authority_bundle
    assert b.authority(window, manifest, "a" * 64, "b" * 64, now, starting=True) == window
    b.approvals(window, owner, richard, now)
    assert window["server_pid"] != c.SERVER_PID


@pytest.mark.parametrize(
    "key,value",
    [
        ("authorized", False),
        ("model_sha256", "0" * 64),
        ("profile_sha256", "0" * 64),
        ("package_sha256", "0" * 64),
        ("run_id", "0" * 12),
        ("server_pid", True),
        ("server_context", 2048),
        ("manual_recovery_confirmed", False),
        ("approval_nonce", None),
        ("oob_interface", "en0"),
        ("kind", "KAYLIN-OK-TOP10-FAULTS"),
    ],
)
def test_missing_or_contradictory_binding_refuses(manifest, authority_bundle, key, value) -> None:
    now, window, _, _ = authority_bundle
    with pytest.raises(c.Refused):
        b.authority(window | {key: value}, manifest, "a" * 64, "b" * 64, now)
    missing = copy.deepcopy(window)
    del missing[key]
    with pytest.raises(c.Refused):
        b.authority(missing, manifest, "a" * 64, "b" * 64, now)


def test_expiry_stops_work_but_preserves_stored_cleanup_authority(
    manifest, authority_bundle
) -> None:
    now, window, owner, richard = authority_bundle
    with pytest.raises(c.Refused, match="outside_window"):
        b.authority(window, manifest, "a" * 64, "b" * 64, now + 1801)
    with pytest.raises(c.Refused, match="outside_launch"):
        b.authority(window, manifest, "a" * 64, "b" * 64, now + 151, starting=True)
    b.authority(window, manifest, "a" * 64, "b" * 64, now + 3600, cleanup=True)
    b.approvals(window, owner, richard, now + 3600, cleanup=True)
    with pytest.raises(c.Refused, match="stale_owner"):
        b.approvals(window, owner, richard, now + 1801)


def test_missing_or_rewritten_owner_refuses(authority_bundle) -> None:
    now, window, owner, richard = authority_bundle
    with pytest.raises(c.Refused):
        b.approvals(window, None, richard, now)
    changed = json.dumps(json.loads(owner) | {"released": False})
    window["owner_handback_sha256"] = hashlib.sha256(changed.encode()).hexdigest()
    with pytest.raises(c.Refused, match="owner_handback"):
        b.approvals(window, changed, richard, now)


@pytest.mark.parametrize(
    "key,value",
    [
        ("private_config_sha256", "d" * 64),
        ("server_context", 8192),
        ("oob_client_ipv4", _public_fixture("@PUBLIC_IPV4_069@")),
    ],
)
def test_richard_approval_binds_every_target_coordinate(authority_bundle, key, value) -> None:
    now, window, owner, richard = authority_bundle
    with pytest.raises(c.Refused, match="exact_richard"):
        b.approvals(window | {key: value}, owner, richard, now)


def test_attempt_and_completion_are_consumed_before_effects(
    tmp_path, manifest, authority_bundle
) -> None:
    _, window, _, _ = authority_bundle
    attempt = Attempt(tmp_path / "attempts", str(manifest["run_id"]))
    attempt.reserve(manifest, window)
    with pytest.raises(c.Refused):
        attempt.reserve(manifest, window)
    attempt.change("reserved", "preflighting", {})
    with pytest.raises(c.Refused):
        attempt.change("reserved", "preflighting", {})
    attempt.admit(
        {"base_verified": True, "pid": window["server_pid"], "context": window["server_context"]},
        window,
    )
    attempt.decision([])
    with pytest.raises(c.Refused, match="replay"):
        attempt.decision([])
    attempt.decision_result({"failure": None, "action": {"tool": "restore_dns"}})
    with pytest.raises(c.Refused):
        attempt.decision([])
    attempt.decision(["restore_dns"])
    attempt.decision_result({"failure": "TimeoutError", "action": None})
    with pytest.raises(c.Refused):
        attempt.decision(["restore_dns"])
    events = [
        json.loads(line)["event"]
        for line in (attempt.path / "audit.jsonl").read_text().splitlines()
    ]
    assert events.index("completion_intent_consumed") < events.index("completion_result")
    assert attempt.state()["lifecycle"] == "stopped"


def test_stopped_attempt_cannot_reuse_nonce_in_new_directory(
    tmp_path, manifest, authority_bundle
) -> None:
    _, window, _, _ = authority_bundle
    first = Attempt(tmp_path / "attempts", str(manifest["run_id"]))
    first.reserve(manifest, window)
    first.stop("preflight_refused")
    second = Attempt(tmp_path / "attempts", "f" * 12)
    with pytest.raises(c.Refused, match="nonce"):
        second.reserve(manifest | {"run_id": "f" * 12}, window)


def test_approval_templates_are_inert(manifest) -> None:
    root = Path(__file__).resolve().parents[2]
    template = c.read(root / "harness/bench/authority.template.json")
    with pytest.raises(c.Refused, match="exact_kaylin"):
        b.authority(template, manifest, "a" * 64, "b" * 64, 1)


@pytest.mark.parametrize("tool", ["report", "give_up", "declare_online", *c.HELD_REPAIRS])
def test_terminal_decision_cannot_request_another_completion(
    tmp_path, manifest, authority_bundle, tool
) -> None:
    _, window, _, _ = authority_bundle
    attempt = Attempt(tmp_path / "attempts", str(manifest["run_id"]))
    attempt.reserve(manifest, window)
    attempt.change("reserved", "preflighting", {})
    attempt.admit({"base_verified": True, "pid": window["server_pid"], "context": 4096}, window)
    attempt.decision([])
    attempt.decision_result({"failure": None, "action": {"tool": tool}})
    with pytest.raises(c.Refused):
        attempt.decision([tool])
    assert attempt.state()["reason"] == "bench_terminal_or_refused_model_decision"
