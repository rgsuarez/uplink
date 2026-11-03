"""The live projection must match the adopted simulation without enabling its provider."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import bench_profile as live
from offline_agent.uplink import model_sim_proxy_profile as simulation
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.top10_model import schema

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = "a" * 64


@pytest.fixture
def measured() -> tuple[object, dict[str, object]]:
    value = json.loads((ROOT / "data/model-sim-proxy-fix/probes-v1.json").read_text())["cases"][0]
    value["oracle"]["checked_at"] = time.time()
    return value["facts"], value["oracle"]


def completion(tool: str = "restore_proxy") -> dict[str, object]:
    return {
        "content": json.dumps(
            {
                "version": 1,
                "tool": tool,
                "snapshot_id": SNAPSHOT,
                "evidence_id": "proxy_matches_snapshot",
                "reason": "snapshot_repair",
            }
        ),
        "stop": True,
        "truncated": False,
        "stop_type": "eos",
        "timings": {},
    }


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    measured: tuple[object, dict[str, object]],
    responses: list[object],
) -> tuple[dict[str, object], Mock]:
    monkeypatch.setattr(live.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(live.os, "geteuid", lambda: 501)
    monkeypatch.delenv("UPLINK_TOP10_SANDBOX")  # OS network/exec fence remains in force.
    post = Mock(side_effect=responses)
    monkeypatch.setattr(live.HttpTransport, "post", post)
    return dict(live.generate(ROOT, SNAPSHOT, *measured, [], 8, context=4096)), post


def test_exact_adopted_projection(measured: tuple[object, dict[str, object]]) -> None:
    assert live.messages(ROOT, *measured, []) == simulation.messages(ROOT, *measured, [])
    assert c.file_hash(ROOT / live.PROMPT_PATH) == b.PROMPT_SHA256
    data = json.loads(live.messages(ROOT, *measured, ["probe_online"])[1].content)
    assert set(data) == {"observed", "online_checks", "prior_actions", "available_tools"}
    assert set(data["available_tools"]) == set(c.TOOLS) and len(c.TOOLS) == 13
    assert data["prior_actions"] == ["probe_online"]


def test_one_bounded_completion_full_grammar(
    monkeypatch: pytest.MonkeyPatch, measured: tuple[object, dict[str, object]]
) -> None:
    result, post = invoke(monkeypatch, measured, [{"tokens": [1] * 1000}, completion()])
    assert result["failure"] is None and result["action"]["tool"] == "restore_proxy"
    assert result["request_count"] == 1 and result["tokenize_requests"] == 1
    assert post.call_count == 2
    body = post.call_args.args[1]
    assert body["grammar"] == schema_gbnf(schema(SNAPSHOT))
    assert body["n_predict"] == 192 and body["grammar_lazy"] is False
    assert 0 < post.call_args.args[3] <= 8


@pytest.mark.parametrize(
    "response", [{}, {"tokens": []}, {"tokens": [True]}, {"tokens": [1] * 4000}]
)
def test_invalid_or_oversize_tokenization_never_completes(
    monkeypatch: pytest.MonkeyPatch, measured: tuple[object, dict[str, object]], response: object
) -> None:
    result, post = invoke(monkeypatch, measured, [response])
    assert post.call_count == 1 and result["request_count"] == 0
    assert result["action"] is None and result["failure"] is not None


@pytest.mark.parametrize(
    "response",
    [
        {},
        completion("run_shell"),
        {**completion(), "content": "not-json"},
        {**completion(), "truncated": True},
        TimeoutError(),
    ],
)
def test_failed_or_invalid_completion_retained(
    monkeypatch: pytest.MonkeyPatch, measured: tuple[object, dict[str, object]], response: object
) -> None:
    result, post = invoke(monkeypatch, measured, [{"tokens": [1]}, response])
    assert post.call_count == 2 and result["request_count"] == 1
    assert result["action"] is None and result["failure"] is not None


def test_live_profile_refuses_sandbox_before_transport(
    monkeypatch: pytest.MonkeyPatch, measured: tuple[object, dict[str, object]]
) -> None:
    post = Mock()
    monkeypatch.setattr(live.HttpTransport, "post", post)
    with pytest.raises(c.Refused, match="live_normal_user"):
        live.generate(ROOT, SNAPSHOT, *measured, [], 8, context=4096)
    post.assert_not_called()


def test_stale_measurement_refuses_before_transport(
    monkeypatch: pytest.MonkeyPatch, measured: tuple[object, dict[str, object]]
) -> None:
    measured[1]["checked_at"] = time.time() - 11
    with pytest.raises(c.Refused, match="stale_oracle"):
        invoke(monkeypatch, measured, [])


def test_public_acceptance_and_edge_oracle_hashes_match() -> None:
    for name, digest in b.ORACLES.items():
        assert c.file_hash(ROOT / name) == digest


@pytest.mark.private_host_package
def test_original_private_frozen_package_files_unchanged() -> None:
    parent = c.read(ROOT / "harness/top10/FROZEN.json")
    assert all(
        c.file_hash(ROOT / name) == digest for name, digest in c.obj(parent["files"]).items()
    )


def test_slow_valid_completion_is_retained_but_never_admitted(monkeypatch, measured) -> None:
    monotonic = Mock(side_effect=[0.0, 0.2, 9.0, 9.0])
    monkeypatch.setattr(live.time, "monotonic", monotonic)
    result, post = invoke(monkeypatch, measured, [{"tokens": [1]}, completion()])
    assert post.call_count == 2
    assert result["failure"] == "bench_completion_exceeded_decision_budget"
    assert result["raw_text"] == completion()["content"] and result["action"] is None
