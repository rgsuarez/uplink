"""Proxy wording may change, but facts, parser, tool admission and safety may not."""

import copy
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from offline_agent.uplink import model_sim_profile as parent
from offline_agent.uplink import model_sim_proxy_profile as profile
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_model as frozen
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.top10_score import positive_claim_supported

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "a" * 64


@pytest.fixture
def measured():
    case = json.loads((ROOT / "data/model-sim-proxy-fix/probes-v1.json").read_text())["cases"][0]
    case["oracle"]["checked_at"] = time.time()
    return case["facts"], case["oracle"]


def completion(tool="restore_proxy", reason="snapshot_repair", snapshot=SNAPSHOT):
    return {
        "content": json.dumps(
            {
                "version": 1,
                "tool": tool,
                "snapshot_id": snapshot,
                "evidence_id": "proxy_matches_snapshot",
                "reason": reason,
            }
        ),
        "stop": True,
        "truncated": False,
        "stop_type": "eos",
        "timings": {},
    }


def test_overlay_changes_only_nonheld_descriptions(measured):
    original = parent.messages(ROOT, *measured, [])
    revised = profile.messages(ROOT, *measured, [])
    assert revised[0] == original[0]
    before, after = json.loads(original[1].content), json.loads(revised[1].content)
    assert set(before) == set(after)
    for field in ("observed", "online_checks", "prior_actions"):
        assert before[field] == after[field]
    changed = {
        k
        for k in before["available_tools"]
        if before["available_tools"][k] != after["available_tools"][k]
    }
    assert changed == {
        "restore_ipv4",
        "renew_lease",
        "restore_dns",
        "restore_gateway",
        "restore_proxy",
        "restore_mtu",
    }
    assert not changed.intersection(c.HELD_REPAIRS)
    assert c.HELD_REPAIRS == ("restore_radio", "restore_interface", "restore_saved_network")
    assert parent.PROMPT_PATH == "harness/model-sim/authorized-repair-v1.txt"
    assert parent.PROFILE_ID == "authorized-repair-v1"


def invoke(measured, response):
    descriptions = copy.deepcopy(frozen.DESCRIPTIONS)
    renderer = frozen.messages
    with (
        patch.object(parent.sys, "platform", "linux"),
        patch.dict(parent.os.environ, {"UPLINK_TOP10_SANDBOX": "1"}),
        patch.object(frozen.HttpTransport, "post", return_value=response) as post,
    ):
        result = profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    assert descriptions == frozen.DESCRIPTIONS and frozen.messages is renderer
    assert parent.PROFILE_ID == "authorized-repair-v1"
    assert parent.PROMPT_PATH == "harness/model-sim/authorized-repair-v1.txt"
    return result, post


def test_proxy_restore_uses_full_frozen_grammar_and_budget(measured):
    result, post = invoke(measured, completion())
    assert result["action"]["tool"] == "restore_proxy" and result["failure"] is None
    assert result["admitted_tools"] == list(c.TOOLS)
    body = post.call_args.args[1]
    assert body["grammar"] == schema_gbnf(frozen.schema(SNAPSHOT))
    assert (body["n_predict"], body["seed"], body["temperature"]) == (192, 0, 0)
    assert body["grammar_lazy"] is False and post.call_args.args[3] == 8


@pytest.mark.parametrize(
    "response",
    [
        {**completion(), "content": "not JSON"},
        completion(tool="run_shell"),
        completion(snapshot="b" * 64),
        {**completion(), "stop_type": "limit", "truncated": True},
        {},
    ],
)
def test_invalid_or_empty_completion_remains_refused(measured, response):
    result, _ = invoke(measured, response)
    assert result["action"] is None and result["failure"] is not None


def test_report_available_and_false_online_not_credited(measured):
    result, _ = invoke(measured, completion("report", "no_safe_action"))
    assert result["action"]["tool"] == "report"
    result, _ = invoke(measured, completion("declare_online", "verified"))
    assert result["action"]["tool"] == "declare_online"
    facts, oracle = measured
    assert not positive_claim_supported(
        {"facts": facts, "measurement_state_unchanged": True, "checked_at": time.time()},
        oracle,
        time.time(),
    )


def test_failure_restores_overlay_and_non_sandbox_refuses(measured):
    before = copy.deepcopy(frozen.DESCRIPTIONS)
    with (
        patch.object(parent.sys, "platform", "linux"),
        patch.dict(parent.os.environ, {"UPLINK_TOP10_SANDBOX": "1"}),
        patch.object(frozen.HttpTransport, "post", side_effect=TimeoutError),
    ):
        result = profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    assert result["failure"] == "TimeoutError" and result["action"] is None
    assert before == frozen.DESCRIPTIONS and frozen.messages is parent.FROZEN_MESSAGES
    with patch.object(parent.sys, "platform", "darwin"), pytest.raises(c.Refused):
        profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    with (
        patch.object(parent.sys, "platform", "linux"),
        patch.dict(parent.os.environ, {"UPLINK_TOP10_SANDBOX": "0"}),
        pytest.raises(c.Refused),
    ):
        profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    assert (
        before == frozen.DESCRIPTIONS
        and parent.PROMPT_PATH == "harness/model-sim/authorized-repair-v1.txt"
    )
