"""Live balanced BASE profile. No simulation provider or global renderer replacement."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import os
import platform
import time
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.native_protocol import EOT, server_owned_prompt
from offline_agent.providers import HttpTransport, Message
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_model import messages as original_messages
from offline_agent.uplink.top10_model import parse, schema

PROFILE_ID = "snapshot-restore-descriptions-v1"
PROMPT_PATH = "harness/bench/prompt.txt"
DESCRIPTIONS_PATH = "harness/bench/descriptions.json"


def messages(root: Path, facts: object, oracle: object, history: list[str]) -> list[Message]:
    original = original_messages(root, facts, oracle, history)
    prompt = (root / PROMPT_PATH).read_text().strip()
    if not prompt.startswith(original[0].content + "\n\n"):
        raise c.Refused("bench_original_safety_instructions_required")
    data = c.parse_json(original[1].content)
    descriptions = c.read(root / DESCRIPTIONS_PATH)
    if set(descriptions) != set(c.REPAIRS) - set(c.HELD_REPAIRS):
        raise c.Refused("bench_balanced_description_set_required")
    if any(not isinstance(v, str) or not v for v in descriptions.values()):
        raise c.Refused("bench_description_text_required")
    data["available_tools"] = c.obj(data["available_tools"]) | descriptions
    return [
        Message("system", prompt),
        Message("user", json.dumps(data, sort_keys=True, separators=(",", ":"))),
    ]


def generate(
    root: Path,
    snapshot_id: str,
    facts: object,
    oracle: object,
    history: list[str],
    timeout: float,
    *,
    context: int,
) -> dict[str, Json]:
    # Live caller additionally verifies package, approvals, physical host, BASE,
    # root snapshot, OOB and budgets. A sandbox marker never enables this path.
    if platform.system() != "Darwin" or os.geteuid() == 0 or "UPLINK_TOP10_SANDBOX" in os.environ:
        raise c.Refused("bench_live_normal_user_required")
    c.number(timeout, 0.1, c.MODEL_TIMEOUT)
    if type(context) is not int or not 4096 <= context <= 8192:
        raise c.Refused("bench_approved_context_required")
    prompt = server_owned_prompt(root, messages(root, facts, oracle, history))
    grammar = schema_gbnf(schema(snapshot_id))
    started = time.monotonic()
    record: dict[str, Json] = {
        "kind": "bench-reduced-base-completion-v1",
        "input_profile": PROFILE_ID,
        "prompt_sha256": c.file_hash(root / PROMPT_PATH),
        "rendered_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "grammar_sha256": hashlib.sha256(grammar.encode()).hexdigest(),
        "prompt": prompt,
        "grammar": grammar,
        "raw_text": None,
        "action": None,
        "request_count": 0,
        "tokenize_requests": 0,
        "failure": None,
        "admitted_tools": list(c.TOOLS),
    }
    try:
        transport = HttpTransport()
        record["tokenize_requests"] = 1
        tokens = transport.post(
            _public_fixture("http://@PUBLIC_IPV4_056@:8080/tokenize"),
            {"content": prompt, "add_special": True},
            {},
            min(2.0, timeout),
        ).get("tokens")
        if not isinstance(tokens, list) or not tokens or any(type(t) is not int for t in tokens):
            raise c.Refused("bench_token_count_unparsed")
        record["prompt_tokens"] = len(tokens)
        if len(tokens) + 192 > context:
            raise c.Refused("bench_prompt_does_not_fit_approved_context")
        remaining = timeout - (time.monotonic() - started)
        if remaining < 0.1:
            raise c.Refused("bench_tokenization_exhausted_decision_budget")
        record["request_count"] = 1
        response = transport.post(
            _public_fixture("http://@PUBLIC_IPV4_056@:8080/completion"),
            {
                "prompt": prompt,
                "grammar": grammar,
                "grammar_lazy": False,
                "n_predict": 192,
                "temperature": 0,
                "seed": 0,
                "stop": [EOT],
                "stream": False,
                "cache_prompt": True,
                "id_slot": 0,
            },
            {},
            remaining,
        )
        raw = response.get("content")
        record["raw_text"] = raw if isinstance(raw, str) else None
        record["timings"] = object_json(response.get("timings", {}))
        if time.monotonic() - started > timeout:
            raise c.Refused("bench_completion_exceeded_decision_budget")
        if (
            not isinstance(raw, str)
            or response.get("stop") is not True
            or response.get("truncated") is not False
            or response.get("stop_type") not in {"eos", "word"}
            or response.get("stopped_limit") is True
            or response.get("stop_type") == "word"
            and response.get("stopping_word") != EOT
        ):
            raise c.Refused("top10_incomplete_completion")
        record["action"] = parse(raw, snapshot_id)
    except Exception as exc:
        record["failure"] = label(exc)
    record["duration_ms"] = (time.monotonic() - started) * 1000
    return record
