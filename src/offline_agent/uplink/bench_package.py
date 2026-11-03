"""Build or inspect a deterministic reduced package offline. Never stages or contacts hosts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import tarfile
from pathlib import Path

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import top10_contract as c

ROOT = Path(__file__).resolve().parents[3]
NEW_FILES = b.NEW_FILES


def registration(root: Path) -> dict[str, object]:
    if c.file_hash(root / "harness/top10/FROZEN.json") != b.PARENT_SHA256:
        raise c.Refused("bench_parent_anchor_changed")
    parent = c.read(root / "harness/top10/FROZEN.json")
    files = c.obj(parent["files"])
    for name, expected in files.items():
        if c.file_hash(root / name) != expected:
            raise c.Refused("bench_frozen_dependency_changed")
    files = files | {name: c.file_hash(root / name) for name in NEW_FILES}
    profile = {
        name: files[name]
        for name in (
            "src/offline_agent/uplink/bench_profile.py",
            "harness/bench/prompt.txt",
            "harness/bench/descriptions.json",
            "templates/glimmer_native.jinja",
            "templates/tokenizer_config.json",
        )
    }
    return b.validate_registration(
        {
            "kind": b.KIND,
            "status": "SEALED",
            "run_id": secrets.token_hex(6),
            "registration_nonce": secrets.token_hex(16),
            "fault_id": "F04",
            "trial": 1,
            "planned_trials": 1,
            "arm": "BASE",
            "profile_id": b.PROFILE_ID,
            "prompt_sha256": b.PROMPT_SHA256,
            "model_sha256": b.MODEL_SHA256,
            "model_bytes": b.MODEL_BYTES,
            "profile_sha256": c.digest(profile),
            "parent_sha256": b.PARENT_SHA256,
            "oracles": b.ORACLES,
            "limits": b.LIMITS,
            "privileged_write_set": b.WRITE_SET,
            "files": files,
        }
    )


def verify_archive(root: Path, archive_path: Path) -> str:
    manifest = b.verify_manifest(root)
    members = c.obj(manifest["files"]) | {b.MANIFEST_PATH: c.file_hash(root / b.MANIFEST_PATH)}
    if (
        archive_path.is_symlink()
        or not archive_path.is_file()
        or archive_path.stat().st_size > 16_000_000
    ):
        raise c.Refused("bench_bounded_regular_archive_required")
    with tarfile.open(archive_path, "r:") as archive:
        if archive.getnames() != sorted(members):
            raise c.Refused("bench_archive_members_changed")
        for item in archive:
            mode = (
                0o700 if item.name.endswith(("uplink-network", "uplink-top10-network")) else 0o600
            )
            if not item.isfile() or item.size > 4_000_000 or item.mode != mode:
                raise c.Refused("bench_archive_member_type_or_mode_refused")
            source = archive.extractfile(item)
            if source is None or hashlib.sha256(source.read()).hexdigest() != members[item.name]:
                raise c.Refused("bench_archive_content_changed")
    return c.file_hash(archive_path)


def package(root: Path, output: Path) -> str:
    manifest = b.verify_manifest(root)
    if output.name != "bench-reduced-" + str(manifest["run_id"]) + ".tar":
        raise c.Refused("bench_archive_name_must_bind_run_id")
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise c.Refused("bench_archive_symlink_refused")
    members = c.obj(manifest["files"]) | {b.MANIFEST_PATH: c.file_hash(root / b.MANIFEST_PATH)}
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w") as archive:
        for name, expected in sorted(members.items()):
            data = c.read_bytes(root / name, maximum=4_000_000)
            if hashlib.sha256(data).hexdigest() != expected:
                raise c.Refused("bench_source_changed_while_packaging")
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = (
                0o700 if name.endswith(("uplink-network", "uplink-top10-network")) else 0o600
            )
            archive.addfile(item, io.BytesIO(data))
    return verify_archive(root, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seal", action="store_true")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.seal:
        # Never silently reseal: a change requires a new review, run ID and package.
        c.save(ROOT / b.MANIFEST_PATH, registration(ROOT), exclusive=True)
    manifest = b.verify_manifest(ROOT)
    result = {
        "manifest_sha256": c.file_hash(ROOT / b.MANIFEST_PATH),
        "run_id": manifest["run_id"],
        "profile_sha256": manifest["profile_sha256"],
        "model_sha256": b.MODEL_SHA256,
        "prompt_sha256": b.PROMPT_SHA256,
        "target_contacts": 0,
        "model_calls": 0,
    }
    if args.output_directory:
        result["package_sha256"] = package(
            ROOT, args.output_directory / ("bench-reduced-" + str(manifest["run_id"]) + ".tar")
        )
    if args.verify:
        result["package_sha256"] = verify_archive(ROOT, args.verify)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
