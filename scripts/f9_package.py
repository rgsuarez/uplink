"""Build or verify an offline lab rerun bundle. Does not SSH, install, or load weights."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from offline_agent.contracts import object_json, read_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(package: Path) -> int:
    manifest = object_json(read_json((package / "package-manifest.json").read_text()))
    if manifest.get("version") != 1 or manifest.get("protocol_id") != "f9-ondevice-v1":
        raise ValueError("unsupported_package_manifest")
    files = object_json(manifest.get("files"))
    if not files:
        raise ValueError("empty_package_manifest")
    for name, expected in files.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError("invalid_package_hash")
        path = package / name
        if not path.resolve().is_relative_to(package.resolve()) or path.is_symlink():
            raise ValueError("unsafe_package_path")
        if digest(path) != expected:
            raise ValueError("package_file_mismatch:" + name)
    return len(files)


def build(root: Path, output: Path) -> int:
    # Tracked inputs plus the explicitly scoped new F9 files. No artifacts,
    # caches, evidence receipts, environments, weights, credentials, or unrelated untracked files.
    names = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0"))
    names.discard("")
    names.update(str(p.relative_to(root)) for p in (root / "bench/lab").rglob("*") if p.is_file())
    names.update(("scripts/f9_package.py", "configs/f9-ondevice.json"))
    output.mkdir(parents=True, exist_ok=False)
    for name in sorted(names):
        path = root / name
        if path.is_symlink() or any(
            x in path.parts for x in ("__pycache__", "artifacts", "evidence", ".venv", ".cache")
        ):
            continue
        if name.startswith("BRIEF") or name.endswith((".pyc", ".local.json")):
            continue
        target = output / "offline-agent" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    shutil.copytree(
        root / "bench/lab", output / "bench", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy2(root / "bench/lab/run-bench.sh", output / "run-bench.sh")
    files = {
        str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()
    }
    manifest = {
        "version": 1,
        "protocol_id": "f9-ondevice-v1",
        "base_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "model_weights_included": False,
        "files": files,
    }
    # Keep every path/hash and the existing parser size bound; indentation is not evidence.
    (output / "package-manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")) + "\n"
    )
    return verify(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if bool(args.out) == bool(args.verify):
        parser.error("choose --out or --verify")
    count = (
        verify(args.verify) if args.verify else build(Path(__file__).resolve().parents[1], args.out)
    )
    print(f"Verified {count} package files; no inference or remote action")
