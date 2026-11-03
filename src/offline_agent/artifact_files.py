"""Existing local artifact-path and bounded JSON readers."""

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from offline_agent.contracts import Json, object_json, read_json


def run_directory(root: Path, category: str) -> Path:
    stamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d-%H%M%S")
    directory = root / "artifacts" / category / f"{stamp}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def json_file(path: Path) -> dict[str, Json]:
    if path.stat().st_size > 16_000_000:
        raise ValueError("receipt_size_limit")
    return object_json(read_json(path.read_text(), max_bytes=16_000_000))
