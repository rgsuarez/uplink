"""One shared open grammar for route/PF, with bounded native model completion.

The model never receives the injected fault name or private network settings.
Both repairs, another measurement, honest give_up and declare_online remain
available. Independent controller/root checks decide whether an action is safe.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import re
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.native_protocol import EOT, server_owned_prompt
from offline_agent.providers import HttpTransport, Message, ProviderError
from offline_agent.uplink.grammar import schema_gbnf

VERSION = "uplink-newfault-open-v1"
REPAIRS = ("restore_test_host_route", "restore_owned_pf_anchor")
CHOICES = (*REPAIRS, "probe_online", "give_up", "declare_online")
EVIDENCE = ("fault-v1-target", "fault-v1-drift")


def action_schema(snapshot_id: str) -> dict[str, Json]:
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ContractError("newfault_snapshot_id_required")
    return {
        "type": "object",
        "properties": {
            "version": {"const": 1},
            "tool": {"enum": list(CHOICES)},
            "snapshot_id": {"const": snapshot_id},
            "policy": {"const": "snapshot"},
            "evidence_ids": {"const": list(EVIDENCE)},
        },
        "required": ["version", "tool", "snapshot_id", "policy", "evidence_ids"],
        "additionalProperties": False,
    }


def parse(text: str, snapshot_id: str) -> dict[str, Json]:
    row = object_json(read_json(text.removesuffix(EOT), max_bytes=4096))
    if type(row.get("version")) is not int or not Draft202012Validator(
        action_schema(snapshot_id)
    ).is_valid(row):
        raise ContractError("newfault_action_refused")
    return row


def observation(value: Json) -> dict[str, Json]:
    row = object_json(value)
    keys = {
        "target_online",
        "baseline_equal",
        "route_changed",
        "pf_changed",
        "protected_services_unchanged",
    }
    if any(type(row.get(k)) is not bool for k in keys):
        raise ContractError("newfault_measured_observation_required")
    return {k: row[k] for k in sorted(keys)}


def messages(measured: Json, oracle: Json, history: Json) -> list[Message]:
    facts = observation(measured)
    rows = object_json(oracle).get("evidence")
    if not isinstance(rows, list):
        raise ContractError("newfault_oracle_evidence_required")
    checks: list[Json] = []
    for raw in rows:
        row = object_json(raw)
        if (
            row.get("id")
            not in {"dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google"}
            or type(row.get("ok")) is not bool
        ):
            raise ContractError("newfault_oracle_check_refused")
        checks.append({"id": row["id"], "ok": row["ok"]})
    if not isinstance(history, list) or len(history) > 4 or any(x not in CHOICES for x in history):
        raise ContractError("newfault_history_refused")
    return [
        Message(
            "system",
            "Choose one bounded action from measured evidence. "
            "restore_test_host_route removes only the owned changed test-host route. "
            "restore_owned_pf_anchor removes only the owned outbound block. "
            "No other mutation is possible. Use give_up when a safe repair is unsupported. "
            "Use probe_online to measure again. Declare online only when the pinned target, "
            "exact baseline and every independent ONLINE check pass. Global ONLINE alone "
            "does not prove a single destination recovered. Do not repeat a repair. "
            "Output only the grammar-locked action.",
        ),
        Message(
            "user",
            json.dumps(
                {"observed": facts, "online_checks": checks, "prior_actions": history},
                sort_keys=True,
            ),
        ),
    ]


def generate(
    root: Path, snapshot_id: str, measured: Json, oracle: Json, history: Json, timeout: float
) -> dict[str, Json]:
    if not 0 < timeout <= 8:
        raise ContractError("newfault_model_timeout_refused")
    grammar = schema_gbnf(action_schema(snapshot_id))
    prompt = server_owned_prompt(root, messages(measured, oracle, history))
    started = time.monotonic()
    record: dict[str, Json] = {
        "version": VERSION,
        "action_mode": "open",
        "admitted_tools": list(CHOICES),
        "text": None,
        "action": None,
        "grammar": grammar,
        "prompt": prompt,
        "timings": {},
        "error": None,
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
        content = response.get("content")
        record["text"] = content if isinstance(content, str) else None
        record["timings"] = object_json(response.get("timings", {}))
        if (
            not isinstance(content, str)
            or response.get("stop") is not True
            or response.get("truncated") is not False
        ):
            raise ContractError("newfault_incomplete_completion")
        record["action"] = parse(content, snapshot_id)
    except (ContractError, ProviderError) as exc:
        label = str(exc)
        record["error"] = (
            label if re.fullmatch(r"[a-z][a-z0-9_]{0,119}", label) else type(exc).__name__
        )
    record["duration_ms"] = (time.monotonic() - started) * 1000
    return record
