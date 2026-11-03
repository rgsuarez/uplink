"""One extra snapshot restore only after a controller-verified route transition."""

import hashlib
import json

from offline_agent.contracts import Json

RESTORES = frozenset({"set_dns", "restore_proxy"})
MAX_MUTATIONS = 8
AttemptKey = tuple[str, str, str]


def mutation_key(tool: str, target_path: str, state: dict[str, Json]) -> AttemptKey:
    """Hash normalized configuration only, excluding clocks, counters and model output."""
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode())
    return tool, target_path, digest.hexdigest()


def key_document(key: AttemptKey) -> dict[str, Json]:
    return {"action": key[0], "target_path": key[1], "state_sha256": key[2]}


def verified_route_in_history(attempted: tuple[str, ...], index: int | None) -> bool:
    return type(index) is int and 0 <= index < len(attempted) and attempted[index] == "reroute"


def snapshot_retry_allowed(
    tool: str,
    attempted: tuple[str, ...],
    verified_route_change_at: int | None,
    *,
    attempt_keys: tuple[AttemptKey, ...] = (),
    current_key: AttemptKey | None = None,
) -> bool:
    """The index is issued by the controller after route readback and oracle improvement."""
    if (
        tool not in RESTORES
        or attempted.count(tool) != 1
        or len(attempted) >= MAX_MUTATIONS
        or len(attempt_keys) != len(attempted)
        or current_key is None
        or current_key[0] != tool
        or current_key in attempt_keys
        or any(key[0] != action for key, action in zip(attempt_keys, attempted, strict=True))
        or not verified_route_in_history(attempted, verified_route_change_at)
    ):
        return False
    assert verified_route_change_at is not None
    first = attempted.index(tool)
    return first < verified_route_change_at and attempt_keys[first][1] != current_key[1]
