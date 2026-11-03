"""Bounded, fault-blind model contract tested using a synthetic HTTP transport."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import time
from pathlib import Path

import pytest

from offline_agent.contracts import Json
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.top10_model import generate, messages, parse
from offline_agent.uplink.top10_score import require_fresh, simulated_portal_visible

ROOT = Path(__file__).resolve().parents[2]


def oracle() -> dict[str, object]:
    return dict(
        OracleResult(
            Verdict.OFFLINE,
            time.time(),
            tuple(
                Evidence(
                    name,
                    "dns"
                    if name.startswith("dns")
                    else "tls"
                    if name.startswith("tls")
                    else "portal",
                    False,
                    "synthetic_failure",
                    "path-synthetic",
                )
                for name in c.CHECK_IDS
            ),
        ).as_json()
    )


def facts() -> dict[str, bool]:
    return {
        name: name
        not in {
            "portal_redirect_seen",
            "ipv4_linklocal",
            "dns_matches_snapshot",
            "configured_dns_ok",
        }
        for name in c.FACTS
    }


def action() -> dict[str, Json]:
    return {
        "version": 1,
        "tool": "restore_dns",
        "snapshot_id": "a" * 64,
        "evidence_id": "configured_dns_ok",
        "reason": "snapshot_repair",
    }


@pytest.mark.parametrize(
    "changes,failed",
    [
        ({}, False),
        ({"stop": False}, True),
        ({"truncated": True}, True),
        ({"stop_type": "limit"}, True),
        ({"stopped_limit": True}, True),
        ({"content": "not a parseable action"}, True),
    ],
)
def test_generation_keeps_raw_output_and_refuses_incomplete_response(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Json], failed: bool
) -> None:
    response: dict[str, Json] = {
        "content": json.dumps(action()),
        "stop": True,
        "truncated": False,
        "stop_type": "eos",
        "timings": {},
    }
    response.update(changes)
    requests: list[dict[str, Json]] = []

    class Transport:
        def post(
            self, url: str, body: dict[str, Json], headers: dict[str, str], timeout: float
        ) -> dict[str, Json]:
            assert url == _public_fixture("http://@PUBLIC_IPV4_056@:8080/completion")
            assert timeout <= 8
            requests.append(body)
            return response

    monkeypatch.setattr("offline_agent.uplink.top10_model.HttpTransport", Transport)
    result = generate(ROOT, "a" * 64, facts(), oracle(), [], 1)
    assert len(requests) == 1
    assert requests[0]["n_predict"] == 192
    assert result["raw_text"] == response["content"]
    assert (result["failure"] is not None) is failed
    assert result["admitted_tools"] == list(c.TOOLS)


def test_projection_has_no_fault_or_expected_answer() -> None:
    value = messages(ROOT, facts(), oracle(), ["probe_online"])
    content = value[-1].content
    row = json.loads(content)
    assert set(row) == {"observed", "online_checks", "prior_actions", "available_tools"}
    assert set(row["available_tools"]) == set(c.TOOLS)
    assert all(fault not in content for fault in c.FAULT_IDS)


def test_unknown_or_wrong_snapshot_action_refuses() -> None:
    with pytest.raises(ValueError):
        parse(json.dumps(action() | {"tool": "shell", "argv": ["sudo"]}), "a" * 64)
    with pytest.raises(ValueError):
        parse(json.dumps(action()), "b" * 64)


def test_stale_negative_oracle_cannot_drive_a_decision() -> None:
    value = oracle()
    value["checked_at"] = time.time() - 11
    with pytest.raises(c.Refused, match="stale_oracle"):
        require_fresh(value, time.time())
    with pytest.raises(c.Refused, match="stale_oracle"):
        messages(ROOT, facts(), value, [])


def test_simulated_portal_requires_unchanged_oracle_proxy_and_redirect_evidence() -> None:
    value = oracle()
    assert simulated_portal_visible(value, time.time()) is False
    rows = value["evidence"]
    assert isinstance(rows, list)
    for raw in rows:
        row = c.obj(raw)
        if row["kind"] in {"portal", "tls"}:
            row["proxy_used"] = "proxy-" + "a" * 24
        if row["kind"] == "portal":
            row["status"] = 302
    assert simulated_portal_visible(value, time.time()) is True
    for raw in rows:
        row = c.obj(raw)
        row["proxy_used"] = "direct"
    assert simulated_portal_visible(value, time.time()) is False
