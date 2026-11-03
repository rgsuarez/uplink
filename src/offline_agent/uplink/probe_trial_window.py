"""Original base-model runtime identity and resource checks, with no model-switching controller."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.lab_config import bench_root
from offline_agent.uplink.model_identity import file_sha256

ROOT = Path(__file__).resolve().parents[3]


def source_hashes(root: Path) -> dict[str, str]:
    paths = [p for p in (root / "src").rglob("*.py") if not p.name.startswith("sealed_")]
    paths += list((root / "templates").glob("*"))
    paths += list((root / "harness/macos").glob("*.swift"))
    paths += [root / "docs/UPLINK-FAULTS.json", root / "scripts/check_macos_swift.py"]
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise ContractError("probe_runtime_regular_sources_required")
    return {str(p.relative_to(root)): file_sha256(p) for p in sorted(paths)}


def runtime_digest(root: Path = ROOT) -> str:
    return hashlib.sha256(
        json.dumps(source_hashes(root), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_object(path: Path, expected: str | None = None) -> dict[str, Json]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        raise ContractError("probe_bounded_regular_json_required")
    data = path.read_bytes()
    if expected is not None and hashlib.sha256(data).hexdigest() != expected:
        raise ContractError("probe_input_digest_mismatch")
    return object_json(read_json(data.decode(), max_bytes=4_000_000))


def save(path: Path, value: Json) -> None:
    with path.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def timestamp(value: Json) -> float:
    if not isinstance(value, str):
        raise ContractError("probe_window_timestamp_required")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except ValueError as exc:
        raise ContractError("probe_window_timestamp_required") from exc


def capture(argv: list[str]) -> str:
    return subprocess.run(
        argv, capture_output=True, text=True, check=True, timeout=5
    ).stdout.strip()


def birth(pid: int) -> str | None:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=5
    )
    return result.stdout.strip() or None


def processes() -> list[tuple[int, str]]:
    # Process arguments can contain credentials. Observe only PID and executable name.
    text = capture(["/bin/ps", "-axo", "pid=,comm="])
    return [(int(row.split()[0]), Path(row.split(maxsplit=1)[1]).name) for row in text.splitlines()]


def server_pids() -> list[int]:
    return [pid for pid, name in processes() if name == "llama-server"]


def resource_sample() -> dict[str, Json]:
    vm = capture(["/usr/bin/vm_stat"])
    page = re.search(r"page size of (\d+) bytes", vm)
    pages = [
        re.search(r"^" + re.escape(k) + r":\s*(\d+)\.", vm, re.M)
        for k in ("Pages active", "Pages wired down", "Pages occupied by compressor")
    ]
    swap = re.search(r"used = ([0-9.]+)([MG])", capture(["/usr/sbin/sysctl", "vm.swapusage"]))
    if page is None or any(p is None for p in pages) or swap is None:
        raise ContractError("probe_resources_unproven")
    used = sum(int(p[1]) for p in pages if p is not None) * int(page[1]) / 1024**3
    return {
        "host_used_gib": used,
        "swap_mib": float(swap[1]) * (1024 if swap[2] == "G" else 1),
        "pressure": int(capture(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"])),
        "disk_free_bytes": shutil.disk_usage(bench_root()).free,
        "load": list(os.getloadavg()),
    }


def require_resources(row: dict[str, Json], baseline_swap: Json) -> None:
    if (
        float(str(row["host_used_gib"])) > 24
        or row["swap_mib"] != baseline_swap
        or row["pressure"] != 1
        or int(str(row["disk_free_bytes"])) < 50 * 1024**3
    ):
        raise ContractError("probe_resource_stop_restore_base")
