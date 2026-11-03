"""The revised prompt changes instructions without weakening the frozen boundary."""

import copy
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from offline_agent.uplink import model_sim_profile as profile
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_injectors as plans
from offline_agent.uplink import top10_model as frozen
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.top10_backends import SandboxBackend
from offline_agent.uplink.top10_kernel import SandboxKernel, initial_fake_state
from offline_agent.uplink.top10_score import positive_claim_supported

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "a" * 64


@pytest.fixture
def measured():
    before = initial_fake_state()
    state = [copy.deepcopy(before)]
    snapshot = {"kernel": before}
    kernel = SandboxKernel(Path("/unused-profile-test"))
    with (
        patch.object(kernel, "state", side_effect=lambda: copy.deepcopy(state[0])),
        patch.object(
            c, "save", side_effect=lambda _path, value: state.__setitem__(0, copy.deepcopy(value))
        ),
        patch.object(c, "audit"),
    ):
        plans.inject(kernel, "F04", snapshot)
        facts = kernel.facts(snapshot)
    backend = SandboxBackend(Path("/unused-profile-test"), {"fault_id": "F04", "trial": 1})
    engine = Mock()
    engine.observe.return_value = {"facts": facts}
    with patch.object(backend, "require_engine", return_value=engine):
        oracle = backend.oracle()
    return facts, oracle


def completion(tool="restore_dns", reason="snapshot_repair", snapshot=SNAPSHOT):
    action = {
        "version": 1,
        "tool": tool,
        "snapshot_id": snapshot,
        "evidence_id": "dns_matches_snapshot",
        "reason": reason,
    }
    return {
        "content": json.dumps(action),
        "stop": True,
        "truncated": False,
        "stop_type": "eos",
        "timings": {},
    }


def invoke(measured, response):
    facts, oracle = measured
    with (
        patch.object(profile.sys, "platform", "linux"),
        patch.dict(profile.os.environ, {"UPLINK_TOP10_SANDBOX": "1"}),
        patch.object(frozen.HttpTransport, "post", return_value=response) as post,
    ):
        record = profile.generate(ROOT, SNAPSHOT, facts, oracle, [], 8)
    return record, post


def test_prompt_only_overlay_preserves_projection_grammar_and_request_budget(measured):
    facts, oracle = measured
    original = frozen.messages
    baseline_messages = original(ROOT, facts, oracle, [])
    revised = profile.messages(ROOT, facts, oracle, [])
    assert revised[1] == baseline_messages[1]
    assert revised[0].content.startswith(baseline_messages[0].content + "\n\n")
    record, post = invoke(measured, completion())
    assert record["action"]["tool"] == "restore_dns"
    assert record["failure"] is None
    assert (
        record["prompt_sha256"]
        == hashlib.sha256((ROOT / profile.PROMPT_PATH).read_bytes()).hexdigest()
    )
    assert record["parent_frozen_prompt_sha256"] == c.PROMPT_SHA256
    assert frozen.messages is original
    body = post.call_args.args[1]
    assert body["grammar"] == schema_gbnf(frozen.schema(SNAPSHOT))
    assert body["n_predict"] == 192 and body["seed"] == 0 and body["temperature"] == 0
    assert body["grammar_lazy"] is False and post.call_args.args[3] == 8
    assert record["admitted_tools"] == list(c.TOOLS)


@pytest.mark.parametrize(
    "response",
    [
        {**completion(), "content": "not JSON"},
        completion(tool="run_shell"),
        completion(snapshot="b" * 64),
        {**completion(), "stop_type": "limit", "truncated": True},
    ],
)
def test_invalid_model_outputs_remain_refused(measured, response):
    record, _ = invoke(measured, response)
    assert record["action"] is None and record["failure"] is not None
    assert frozen.messages is profile.FROZEN_MESSAGES


def test_report_remains_selectable_and_unsupported_online_still_fails_oracle(measured):
    record, _ = invoke(measured, completion(tool="report", reason="no_safe_action"))
    assert record["action"]["tool"] == "report"
    record, _ = invoke(measured, completion(tool="declare_online", reason="verified"))
    assert record["action"]["tool"] == "declare_online"
    facts, oracle = measured
    assert not positive_claim_supported(
        {"facts": facts, "measurement_state_unchanged": True, "checked_at": time.time()},
        oracle,
        time.time(),
    )


def test_missing_measurements_and_non_linux_execution_fail_before_http(measured):
    with (
        patch.object(profile.sys, "platform", "linux"),
        patch.dict(profile.os.environ, {"UPLINK_TOP10_SANDBOX": "1"}),
        patch.object(frozen.HttpTransport, "post") as post,
    ):
        with pytest.raises(ValueError):
            profile.generate(ROOT, SNAPSHOT, {}, measured[1], [], 8)
        with pytest.raises(ValueError):
            profile.generate(ROOT, SNAPSHOT, measured[0], {}, [], 8)
        post.assert_not_called()
    with (
        patch.object(profile.sys, "platform", "darwin"),
        pytest.raises(c.Refused, match="requires_linux_sandbox"),
    ):
        profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    assert frozen.messages is profile.FROZEN_MESSAGES


def test_transport_failure_is_recorded_and_renderer_is_restored(measured):
    with (
        patch.object(profile.sys, "platform", "linux"),
        patch.dict(profile.os.environ, {"UPLINK_TOP10_SANDBOX": "1"}),
        patch.object(frozen.HttpTransport, "post", side_effect=TimeoutError),
    ):
        record = profile.generate(ROOT, SNAPSHOT, *measured, [], 8)
    assert record["action"] is None and record["failure"] == "TimeoutError"
    assert frozen.messages is profile.FROZEN_MESSAGES
