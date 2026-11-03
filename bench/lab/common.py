"""Shared helpers for the bench bench. Importing this never loads a model or opens a socket."""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from offline_agent.contracts import ContractError, parse_action
from offline_agent.corpus import Task, load_tasks
from offline_agent.native_protocol import server_owned_prompt
from offline_agent.providers import Message

BASE = "http://ADDRESS_REMOVED:8080"
QUOTA = {
    ("okunishinishi/python-stringcase", False): 6,
    ("synthetic:text-v2", False): 3,
    ("synthetic:records-v2", False): 3,
    ("okunishinishi/python-stringcase", True): 1,
    ("synthetic:text-v2", True): 1,
    ("synthetic:records-v2", True): 1,
}
SUBSET_RULE = (
    "First N tasks per (repository, impossible) group in frozen v2 held-out order: "
    "stringcase 6, text-v2 3, records-v2 3 solvable; 1 impossible per family (15 total)"
)


def subset(root: Path) -> list[Task]:
    """Deterministic 15-task stratified parity subset of the frozen 50-task v2 held-out set."""
    chosen: list[Task] = []
    used: dict[tuple[str, bool], int] = {}
    for task in load_tasks(root, "heldout", 2):  # Verifies the freeze hash list first.
        key = (task.repository, task.impossible)
        if used.get(key, 0) < QUOTA.get(key, 0):
            chosen.append(task)
            used[key] = used.get(key, 0) + 1
    if len(chosen) != 15:
        raise SystemExit("parity_subset_size_mismatch")
    return chosen


def native_prompt(root: Path, messages: list[dict[str, str]], reasoning: str) -> str:
    """b10353 owns BOS on /completion. Keep the native template unchanged."""
    return server_owned_prompt(
        root, [Message(m["role"], m["content"]) for m in messages], reasoning_strength=reasoning
    )


def is_action(text: object) -> bool:
    if not isinstance(text, str) or not text:
        return False
    try:
        parse_action(text)
        return True
    except (ContractError, ValueError):
        return False


def http(method: str, path: str, body: dict | None, timeout: float) -> tuple[int, object, float]:
    """Loopback only, no proxies. Returns (status, parsed-or-text body, seconds)."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    start = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(8_000_000)
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(200_000), exc.code
    except (OSError, urllib.error.URLError) as exc:
        return -1, {"transport_error": type(exc).__name__}, time.monotonic() - start
    elapsed = time.monotonic() - start
    try:
        return status, json.loads(raw.decode()), elapsed
    except (UnicodeError, ValueError):
        return status, raw.decode(errors="replace")[:4000], elapsed


def remaining(deadline: float) -> float:
    return deadline - time.time()
