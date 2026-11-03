"""One-shot unprivileged staging receiver. Standard library only; no archive extraction API."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import time
import types
from datetime import datetime
from pathlib import Path


def checked_members(data: bytes, expected_sha: str) -> dict[str, bytes]:
    if not 0 < len(data) <= 32 * 1024 * 1024:
        raise ValueError("top10_stage_archive_size_refused")
    result: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for item in archive:
            if (
                item.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                or not 0 < item.size <= 32 * 1024 * 1024
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", item.name)
                or item.name.startswith("/")
                or any(p in {"", ".", ".."} for p in item.name.split("/"))
                or item.name in result
                or len(result) >= 1001
            ):
                raise ValueError("top10_stage_archive_member_refused")
            source = archive.extractfile(item)
            if source is None:
                raise ValueError("top10_stage_archive_member_missing")
            result[item.name] = source.read(32 * 1024 * 1024 + 1)
            total += len(result[item.name])
            if len(result[item.name]) != item.size or total > 32 * 1024 * 1024:
                raise ValueError("top10_stage_archive_size_refused")
    name = "harness/top10/FROZEN.json"
    if name not in result or hashlib.sha256(result[name]).hexdigest() != expected_sha:
        raise ValueError("top10_stage_manifest_mismatch")
    manifest = json.loads(result[name])
    files = manifest["files"]
    if not isinstance(files, dict) or set(result) != set(files) | {name}:
        raise ValueError("top10_stage_unlisted_member")
    for member, wanted in files.items():
        if hashlib.sha256(result[member]).hexdigest() != wanted:
            raise ValueError("top10_stage_member_digest_mismatch")
    if any("lab.local" in member or Path(member).name.startswith("GO-BENCH") for member in files):
        raise ValueError("top10_stage_private_member_refused")
    return result


def check_authority(row: dict[str, object], now: float) -> None:
    window = row.get("window")
    owner = row.get("owner")
    approval = row.get("root_approval")
    if not isinstance(window, dict) or not isinstance(owner, str):
        raise ValueError("top10_stage_authority_required")
    if (
        not isinstance(approval, str)
        or not 0 < len(approval.encode()) <= 16_384
        or hashlib.sha256(approval.encode()).hexdigest() != window.get("root_approval_sha256")
    ):
        raise ValueError("top10_stage_richard_approval_required")
    if (
        window.get("authorized") is not True
        or window.get("approved_by") != "Kaylin"
        or window.get("kind") != "KAYLIN-OK-TOP10-FAULTS"
        or window.get("manifest_sha256") != row.get("manifest_sha256")
        or window.get("arm") != "BASE"
        or window.get("server_pid") != 19904
        or (window.get("start"), window.get("end"))
        not in (
            ("2026-10-30T21:30:00-05:00", "2026-10-30T23:10:00-05:00"),
            ("2026-10-31T07:30:00-05:00", "2026-10-31T09:10:00-05:00"),
        )
        or hashlib.sha256(owner.encode()).hexdigest() != window.get("owner_handback_sha256")
    ):
        raise ValueError("top10_stage_exact_release_required")
    start = datetime.fromisoformat(window["start"]).timestamp()
    end = datetime.fromisoformat(window["end"]).timestamp()
    if not start <= now < min(start + 600, end):
        raise ValueError("top10_stage_preflight_window_required")
    release = json.loads(owner)
    if (
        release.get("released") is not True
        or release.get("approved_by") != "Kaylin"
        or release.get("kind") != "TOP10-OWNER-HANDBACK"
        or release.get("manifest_sha256") != row.get("manifest_sha256")
        or release.get("arm") != "BASE"
        or release.get("pid") != 19904
        or release.get("birth") != window.get("server_birth")
        or type(release.get("released_at")) not in (int, float)
        or not start - 1500 <= release["released_at"] <= start - 900
        or release["released_at"] > now
        or now >= release["released_at"] + 1800
    ):
        raise ValueError("top10_stage_owner_release_required")


def approved_module(name: str, members: dict[str, bytes]) -> types.ModuleType:
    """Load only code whose bytes were verified against the operator-approved manifest."""
    path = "src/offline_agent/uplink/" + name + ".py"
    module = types.ModuleType(name)
    module.__file__ = path
    sys.modules[name] = module
    exec(compile(members[path], path, "exec"), module.__dict__)
    return module


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(48 * 1024 * 1024 + 1)
        if len(raw) > 48 * 1024 * 1024:
            raise ValueError("top10_stage_envelope_size_refused")
        row = json.loads(raw)
        if set(row) != {
            "archive",
            "config",
            "manifest_sha256",
            "window",
            "owner",
            "root_approval",
            "oob_proof",
        }:
            raise ValueError("top10_stage_envelope_refused")
        check_authority(row, time.time())
        members = checked_members(
            base64.b64decode(row["archive"], validate=True), row["manifest_sha256"]
        )
        contract = approved_module("top10_contract", members)
        oob = approved_module("top10_oob", members)
        manifest = json.loads(members["harness/top10/FROZEN.json"])
        contract.validate_window(row["window"], manifest, row["manifest_sha256"], time.time())
        contract.validate_root_approval(row["root_approval"], row["window"])
        oob.validate_proof(row["oob_proof"], row["window"], "stage", time.time())
        config_bytes = base64.b64decode(row["config"], validate=True)
        if (
            len(config_bytes) > 8192
            or hashlib.sha256(config_bytes).hexdigest() != row["window"]["private_config_sha256"]
        ):
            raise ValueError("top10_stage_config_mismatch")
        config = json.loads(config_bytes)
        if (
            platform.system() != "Darwin"
            or platform.node().split(".")[0] != config["node"]
            or os.geteuid() == 0
            or int(platform.mac_ver()[0].split(".")[0]) < 15
        ):
            raise ValueError("top10_stage_normal_target_user_required")
        hardware = subprocess.run(
            ("/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        match = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([A-Za-z0-9]+)"', hardware.stdout)
        if hardware.returncode or match is None or match[1] != config["approved_serial"]:
            raise ValueError("top10_stage_device_identity_refused")
        nonce = row["oob_proof"]["qualification_id"]
        measured = subprocess.run(
            ("/usr/bin/python3", "-I", "-B", "-c", oob.TARGET_SCRIPT),
            input=json.dumps(
                {
                    "nonce": nonce,
                    "client": row["window"]["oob_client_ipv4"],
                    "node": config["node"],
                    "serial": config["approved_serial"],
                }
            ),
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        oob.target_ok(contract.parse_json(measured.stdout), row["window"], nonce)
        relative = config["bench_relative"]
        if not re.fullmatch(
            r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", relative
        ) or ".." in relative.split("/"):
            raise ValueError("top10_stage_destination_refused")
        directory = Path.home() / relative / ("top10-" + row["manifest_sha256"][:12])
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError("top10_stage_symlink_refused")
        check_authority(row, time.time())
        oob.validate_proof(row["oob_proof"], row["window"], "stage", time.time())
        directory.mkdir(mode=0o700)  # An existing/partial stage is never overwritten.
        for name, content in members.items():
            destination = directory / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = 0o700 if name.endswith(("uplink-network", "uplink-top10-network")) else 0o600
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        config_path = directory / "src/offline_agent/uplink/lab.local.json"
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(config_bytes)
        sys.path.insert(0, str(directory / "src/offline_agent/uplink"))
        manifest = contract.verify_manifest(directory, directory / "harness/top10/FROZEN.json")
        contract.validate_window(
            row["window"], manifest, row["manifest_sha256"], __import__("time").time()
        )
        print(
            json.dumps(
                {
                    "staged": True,
                    "manifest_sha256": row["manifest_sha256"],
                    "files": len(members),
                    "model_calls": 0,
                }
            )
        )
    except Exception as exc:
        reason = str(exc)
        print(
            json.dumps(
                {
                    "staged": False,
                    "reason": reason
                    if re.fullmatch(r"top10_[a-z0-9_]{1,110}", reason)
                    else type(exc).__name__,
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
