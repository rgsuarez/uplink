"""Protocol wiring tests; every host and model operation is replaced before dispatch."""

from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from offline_agent.uplink import bench_bridge as bridge
from offline_agent.uplink import bench_profile as profile
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_oob import Qualifier


def oob(window, scope):
    def probe(kind, nonce, timeout):
        if kind == "relay":
            return {"nonce": nonce, "node": "relay", "cores": 10, "load1": 1, "load5": 1}
        return {
            "nonce": nonce,
            "client": window["oob_client_ipv4"],
            "server": c.OOB_SERVER_IPV4,
            "interface": c.OOB_INTERFACE,
            "address": c.OOB_SERVER_IPV4,
            "host_verified": True,
        }

    return Qualifier().qualify(window, scope, probe, pause=lambda _: None)


def request(manifest, authority_bundle):
    _, window, owner, richard = authority_bundle
    return {
        "operation": "choose",
        "payload": {
            "snapshot_id": "d" * 64,
            "facts": {},
            "oracle": {},
            "history": [],
            "timeout": 8,
            "preflight": {},
        },
        "manifest_sha256": "a" * 64,
        "package_sha256": "b" * 64,
        "window": window,
        "owner": owner,
        "richard": richard,
        "oob": oob(window, manifest["run_id"]),
    }


def local_bindings(monkeypatch, manifest):
    monkeypatch.setattr(bridge.b, "verify_manifest", lambda _: manifest)
    monkeypatch.setattr(
        bridge.c,
        "file_hash",
        lambda p: (
            "a" * 64
            if p.name == "FROZEN.json"
            else "c" * 64
            if p.name == "lab.local.json"
            else "b" * 64
        ),
    )


def host_fakes(monkeypatch):
    monkeypatch.setattr(bridge.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bridge.platform, "mac_ver", lambda: ("15.0", (), ""))
    monkeypatch.setattr(bridge.os, "geteuid", lambda: 501)
    monkeypatch.delenv("UPLINK_TOP10_SANDBOX")  # OS no-network policy stays active.
    monkeypatch.setattr(bridge, "require_host", Mock())
    monkeypatch.setattr(bridge, "management", Mock())
    monkeypatch.setattr(bridge, "proof_and_resources", Mock(return_value={"base_verified": True}))


def test_choose_consumes_intent_before_live_profile(monkeypatch, manifest, authority_bundle):
    assert bridge.generate is profile.generate
    row = request(manifest, authority_bundle)
    local_bindings(monkeypatch, manifest)
    host_fakes(monkeypatch)
    events = []

    def root(op, run_id, payload):
        events.append(op)
        assert run_id == manifest["run_id"]
        return {
            "snapshot_id": "d" * 64,
            "lifecycle": "fault_verified",
            "watchdog_restore": False,
            "controller_cleanup": False,
            "remaining_seconds": 40,
        }

    def generate(*args, **kwargs):
        events.append("generate")
        assert kwargs["context"] == 4096
        return {"request_count": 1, "action": {"tool": "restore_dns"}, "failure": None}

    monkeypatch.setattr(bridge, "root_call", root)
    monkeypatch.setattr(bridge, "generate", generate)
    assert bridge.dispatch(row)["failure"] is None
    assert events == ["status", "reserve_decision", "generate", "decision_result"]


def test_bad_authority_refuses_before_host_probes(monkeypatch, manifest, authority_bundle):
    row = request(manifest, authority_bundle)
    row["window"] = row["window"] | {"authorized": False}
    local_bindings(monkeypatch, manifest)
    host = Mock(side_effect=AssertionError("host touched"))
    monkeypatch.setattr(bridge, "require_host", host)
    with pytest.raises(c.Refused, match="exact_kaylin"):
        bridge.dispatch(row)
    host.assert_not_called()


@pytest.mark.parametrize("mutation", ["missing", "stale", "contradictory"])
def test_bad_oob_refuses_before_root_or_model(monkeypatch, manifest, authority_bundle, mutation):
    row = request(manifest, authority_bundle)
    if mutation == "missing":
        row["oob"] = {}
    elif mutation == "stale":
        row["oob"]["started_at"] = time.time() - 100
        row["oob"]["checked_at"] = time.time() - 91
    else:
        row["oob"]["attempts"][0]["target"]["host_verified"] = False
    local_bindings(monkeypatch, manifest)
    host_fakes(monkeypatch)
    root = Mock(side_effect=AssertionError("root touched"))
    model = Mock(side_effect=AssertionError("model called"))
    monkeypatch.setattr(bridge, "root_call", root)
    monkeypatch.setattr(bridge, "generate", model)
    with pytest.raises(c.Refused):
        bridge.dispatch(row)
    root.assert_not_called()
    model.assert_not_called()
