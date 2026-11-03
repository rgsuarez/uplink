"""Private lab coordinates, shared by the controller and the detached root worker.

No config is shipped with live device authority. The file is staged beside the
helpers, then copied into the root-owned run directory before any fault.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import stat
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path, PurePosixPath

CONFIG_PATH = Path(__file__).with_name("lab.local.json")


@dataclass(frozen=True)
class LabConfig:
    version: int
    node: str
    oob_host: str
    wifi_host: str
    bench_relative: str
    source_relative: str
    python_relative: str
    approved_serial: str
    approved_by: str
    approved_at: str
    fleet_clearance_at: str
    clearance_file: str

    @property
    def bench_root(self) -> Path:
        return Path.home() / self.bench_relative

    @property
    def bridge_command(self) -> str:
        return (
            'cd "$HOME"/'
            + shlex.quote(self.source_relative)
            + ' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$HOME"/'
            + shlex.quote(self.python_relative)
            + " -B -m offline_agent.uplink.real_bridge"
        )


def parse_config(value: object) -> LabConfig:
    keys = {field.name for field in fields(LabConfig)}
    if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int:
        raise ValueError("lab_config_schema_refused")
    if value["version"] != 1 or any(
        not isinstance(value[key], str) or not value[key] for key in keys - {"version"}
    ):
        raise ValueError("lab_config_values_refused")
    for key in ("node", "oob_host", "wifi_host"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,253}", value[key]):
            raise ValueError("lab_config_host_refused")
    if value["oob_host"] == value["wifi_host"]:
        raise ValueError("distinct_oob_and_wifi_routes_required")
    for key in ("bench_relative", "source_relative", "python_relative"):
        path = PurePosixPath(value[key])
        if (
            path.is_absolute()
            or ".." in path.parts
            or str(path) != value[key]
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*", value[key])
            or value[key] == "."
        ):
            raise ValueError("lab_config_relative_path_required")
    if not re.fullmatch(r"[a-zA-Z0-9]{8,32}", value["approved_serial"]):
        raise ValueError("lab_config_serial_required")
    if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,127}\.md", value["clearance_file"]):
        raise ValueError("lab_config_clearance_filename_refused")
    for key in ("approved_at", "fleet_clearance_at"):
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", value[key]
        ):
            raise ValueError("lab_config_approval_time_required")
        try:
            datetime.fromisoformat(value[key].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("lab_config_approval_time_required") from exc
    if len(value["approved_by"]) > 128 or any(ord(c) < 32 for c in value["approved_by"]):
        raise ValueError("lab_config_approver_refused")
    return LabConfig(**value)


def load_config(path: Path | None = None) -> LabConfig:
    path = CONFIG_PATH if path is None else path
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid not in {0, os.getuid(), Path(__file__).stat().st_uid}
                or not 0 < info.st_size <= 8192
            ):
                raise ValueError("private_lab_config_required")
            content = stream.read(8193)
            if len(content) > 8192:
                raise ValueError("lab_config_size_refused")
    except OSError as exc:
        raise ValueError("private_lab_config_required") from exc
    try:
        value: object = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("lab_config_json_refused") from exc
    return parse_config(value)


def configured_host() -> bool:
    """No config or a malformed config must never admit a live host."""
    try:
        return platform.node().split(".")[0] == load_config().node
    except ValueError:
        return False


def bench_root() -> Path:
    # A neutral default supports help and dry runs only. Live paths require config.
    if not CONFIG_PATH.exists():
        return Path.home() / "lab-bench"
    return load_config().bench_root
