"""Versioned prompt-only overlay for serialized Linux simulation workers.

The frozen generator, grammar, parser and scorer are reused unchanged. This module
is not connected to the live bridge. Its temporary renderer replacement is scoped
to one synchronous model call inside the standalone simulation process.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

from offline_agent.contracts import Json
from offline_agent.providers import Message
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_model as frozen

PROFILE_ID = "authorized-repair-v1"
PROMPT_PATH = "harness/model-sim/authorized-repair-v1.txt"
FROZEN_MESSAGES = frozen.messages


def messages(root: Path, facts: object, oracle: object, history: list[str]) -> list[Message]:
    original = FROZEN_MESSAGES(root, facts, oracle, history)
    text = (root / PROMPT_PATH).read_text().strip()
    if not text.startswith(original[0].content + "\n\n"):
        raise c.Refused("model_sim_original_safety_instructions_required")
    return [Message("system", text), original[1]]


def generate(
    root: Path, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
) -> dict[str, Json]:
    if sys.platform != "linux" or os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        raise c.Refused("model_sim_profile_requires_linux_sandbox")
    with patch.object(frozen, "messages", messages):
        record = frozen.generate(root, snapshot_id, facts, oracle, history, timeout)
    record["input_profile"] = PROFILE_ID
    record["parent_frozen_prompt_sha256"] = record["prompt_sha256"]
    record["prompt_sha256"] = hashlib.sha256((root / PROMPT_PATH).read_bytes()).hexdigest()
    return record
