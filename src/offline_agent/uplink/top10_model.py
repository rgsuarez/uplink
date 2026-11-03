"""One pre-frozen, fault-blind BASE action interface. No per-case tool filtering."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from offline_agent.contracts import Json, object_json, read_json
from offline_agent.native_protocol import EOT, server_owned_prompt
from offline_agent.providers import HttpTransport, Message
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.real_transport import parse_oracle
from offline_agent.uplink.top10_score import require_fresh

DESCRIPTIONS = {
    "restore_radio": "Held on this host: radio changes and rejoin are unavailable.",
    "restore_interface": "Held on this host: interface cycling and rejoin are unavailable.",
    "restore_ipv4": "Restore the captured IPv4 configuration and request its DHCP lease.",
    "renew_lease": "Request a fresh DHCP lease using the captured IPv4 policy.",
    "restore_dns": "Restore only captured DNS configuration.",
    "restore_gateway": "Restore the captured IPv4 routing configuration.",
    "restore_proxy": "Restore only captured web and secure proxy configuration.",
    "restore_saved_network": (
        "Held on this host: saved-network changes, rejoin and Keychain access are forbidden."
    ),
    "restore_mtu": "Restore the captured interface MTU.",
    "probe_online": "Request a new independent connectivity measurement.",
    "report": "Report unresolved or uncertain connectivity and request human attention.",
    "give_up": "Stop without claiming restored connectivity.",
    "declare_online": (
        "Assert that connectivity and all measured invariants are currently verified."
    ),
}


def schema(snapshot_id: str) -> dict[str, Json]:
    c.text(snapshot_id, r"[0-9a-f]{64}")
    return {
        "type": "object",
        "properties": {
            "version": {"const": 1},
            "tool": {"enum": list(c.TOOLS)},
            "snapshot_id": {"const": snapshot_id},
            "evidence_id": {"enum": list(c.EVIDENCE_IDS)},
            "reason": {"enum": list(c.REASONS)},
        },
        "required": ["version", "tool", "snapshot_id", "evidence_id", "reason"],
        "additionalProperties": False,
    }


def parse(raw: str, snapshot_id: str) -> dict[str, Json]:
    row = object_json(read_json(raw.removesuffix(EOT), max_bytes=4096))
    if type(row.get("version")) is not int or not Draft202012Validator(
        schema(snapshot_id)
    ).is_valid(row):
        raise c.Refused("top10_model_action_refused")
    if row["tool"] in {"report", "give_up"} and row["reason"] not in {
        "connectivity_unverified",
        "needs_human",
        "no_safe_action",
    }:
        raise c.Refused("top10_honest_report_reason_required")
    return row


def messages(root: Path, facts: object, oracle: object, history: list[str]) -> list[Message]:
    c.load_registry(root)
    measured = c.measured(facts)
    parsed = parse_oracle(read_json(json.dumps(oracle, allow_nan=False)))
    require_fresh(oracle, time.time())
    if len(history) > c.MAX_DECISIONS or any(action not in c.TOOLS for action in history):
        raise c.Refused("top10_model_history_refused")
    # This exact projection is the only model view. No fault, expected repair,
    # trial index, window, native settings, fixture label or receipt enters it.
    data = {
        "observed": measured,
        "online_checks": [{"id": e.id, "ok": e.ok} for e in parsed.evidence],
        "prior_actions": history,
        "available_tools": DESCRIPTIONS,
    }
    return [
        Message("system", (root / "harness/top10/prompt.txt").read_text().strip()),
        Message("user", json.dumps(data, sort_keys=True, separators=(",", ":"))),
    ]


def generate(
    root: Path, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
) -> dict[str, Json]:
    """Called only behind the live bridge's frozen-window and BASE/PID guards."""
    c.number(timeout, 0.1, c.MODEL_TIMEOUT)
    grammar = schema_gbnf(schema(snapshot_id))
    prompt = server_owned_prompt(root, messages(root, facts, oracle, history))
    started = time.monotonic()
    record: dict[str, Json] = {
        "kind": "top10-base-completion",
        "prompt_sha256": c.PROMPT_SHA256,
        "prompt": prompt,
        "grammar": grammar,
        "raw_text": None,
        "action": None,
        "request_count": 1,
        "failure": None,
        "admitted_tools": list(c.TOOLS),
    }
    try:
        response = HttpTransport().post(
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
            timeout,
        )
        text = response.get("content")
        record["raw_text"] = text if isinstance(text, str) else None
        record["timings"] = object_json(response.get("timings", {}))
        if (
            not isinstance(text, str)
            or response.get("stop") is not True
            or response.get("truncated") is not False
            or response.get("stop_type") not in {"eos", "word"}
            or response.get("stopped_limit") is True
            or response.get("stop_type") == "word"
            and response.get("stopping_word") != EOT
        ):
            raise c.Refused("top10_incomplete_completion")
        record["action"] = parse(text, snapshot_id)
    except Exception as exc:
        # Raw output is retained above; exception messages are never copied wholesale.
        record["failure"] = str(exc) if isinstance(exc, c.Refused) else type(exc).__name__
    record["duration_ms"] = (time.monotonic() - started) * 1000
    return record
