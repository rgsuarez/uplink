"""Explicit base-only serving evidence, separate from the frozen base-model identity."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.model_identity import GGUF_NAME, GGUF_SHA256, LLAMA_SERVER_BUILD

VERSION = "uplink-probe-serving-v1"


def binding(value: Json) -> dict[str, Json]:
    row = object_json(value)
    if (
        set(row) != {"arm", "adapter_path"}
        or not isinstance(row["arm"], str)
        or row["arm"] != "base"
    ):
        raise ContractError("probe_serving_binding_required")
    if row["adapter_path"] is not None:
        raise ContractError("base_adapter_must_be_absent")
    return row


def get_json(endpoint: str) -> Json:
    if endpoint not in {"props", "lora-adapters", "slots"}:
        raise ContractError("serving_read_endpoint_only")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(
        _public_fixture("http://@PUBLIC_IPV4_056@:8080/") + endpoint, timeout=3
    ) as response:
        return read_json(response.read(262145).decode(), max_bytes=262144)


def require_serving(expected: Json, *, model_path: str | None = None) -> dict[str, Json]:
    wanted = binding(expected)
    props, adapters = object_json(get_json("props")), get_json("lora-adapters")
    path = props.get("model_path")
    if (
        not isinstance(path, str)
        or Path(path).name != GGUF_NAME
        or model_path is not None
        and path != model_path
        or props.get("build_info") != LLAMA_SERVER_BUILD
        or type(props.get("total_slots")) is not int
        or props["total_slots"] != 1
    ):
        raise ContractError("probe_server_base_or_build_mismatch")
    if adapters != []:
        raise ContractError("probe_base_requires_empty_adapters")
    from offline_agent.uplink.probe_trial_window import runtime_digest

    return {
        "version": VERSION,
        "arm": wanted["arm"],
        "base_gguf_sha256": GGUF_SHA256,
        "adapter_gguf_sha256": None,
        "adapter_count": len(cast(list[Json], adapters)),
        "build": LLAMA_SERVER_BUILD,
        "checked_at": time.time(),
        "runtime_sha256": runtime_digest(),
        "base_hash_basis": "separate_model_attestation",
        "serving_binding_sha256": hashlib.sha256(
            json.dumps(wanted, sort_keys=True).encode()
        ).hexdigest(),
    }
