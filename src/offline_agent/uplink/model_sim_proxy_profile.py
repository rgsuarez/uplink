"""Balanced tool descriptions for the serialized Linux BASE simulator only."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

from offline_agent.contracts import Json
from offline_agent.providers import Message
from offline_agent.uplink import model_sim_profile as parent
from offline_agent.uplink import top10_model as frozen

PROFILE_ID = "snapshot-restore-descriptions-v1"
DESCRIPTIONS = {
    "restore_ipv4": (
        "Restore captured IPv4 configuration and request DHCP when ipv4_usable is false "
        "or ipv4_linklocal is true."
    ),
    "renew_lease": (
        "Request a fresh DHCP lease using captured IPv4 policy when dhcp_lease_present is false."
    ),
    "restore_dns": "Restore only captured DNS configuration when dns_matches_snapshot is false.",
    "restore_gateway": (
        "Restore captured IPv4 routing configuration when gateway_matches_snapshot is false."
    ),
    "restore_proxy": (
        "Restore only captured web and secure proxy configuration "
        "when proxy_matches_snapshot is false."
    ),
    "restore_mtu": "Restore the captured interface MTU when mtu_matches_snapshot is false.",
}


def messages(root: Path, facts: object, oracle: object, history: list[str]) -> list[Message]:
    with patch.dict(frozen.DESCRIPTIONS, DESCRIPTIONS):
        return parent.messages(root, facts, oracle, history)


def generate(
    root: Path, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
) -> dict[str, Json]:
    with patch.dict(frozen.DESCRIPTIONS, DESCRIPTIONS):
        record = parent.generate(root, snapshot_id, facts, oracle, history, timeout)
    record["input_profile"] = PROFILE_ID
    record["rendered_prompt_sha256"] = hashlib.sha256(str(record["prompt"]).encode()).hexdigest()
    return record
