"""Typecheck native helpers on an approved Mac host; gate staging on the exact bytes.

No helper execution, root, SSH, package install or network configuration operation.
The controller's --verify/--archive paths never invoke the Swift compiler.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import tarfile
import tempfile
from pathlib import Path

VERSION = "uplink-macos-typecheck-v1"
ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def swift_sources(root: Path) -> dict[str, bytes]:
    directory = root / "harness/macos"
    if directory.is_symlink() or any(p.is_symlink() for p in directory.rglob("*")):
        raise ValueError("native_source_symlink_refused")
    paths = sorted(directory.rglob("*.swift"))
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError("native_swift_sources_required")
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}


def source_hashes(sources: dict[str, bytes]) -> dict[str, str]:
    return {name: digest(data) for name, data in sources.items()}


def typecheck(root: Path) -> dict[str, object]:
    if platform.system() != "Darwin" or os.geteuid() == 0:
        raise ValueError("native_typecheck_requires_nonroot_macos")
    sources = swift_sources(root)
    hashes = source_hashes(sources)
    results: dict[str, object] = {}
    passed = True
    # Compiler inputs and module cache live only in a disposable user-owned directory.
    with tempfile.TemporaryDirectory(prefix="uplink-swift-typecheck-") as name:
        temporary = Path(name)
        for path, data in sources.items():
            target = temporary / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        cache = temporary / "module-cache"
        for path in sources:
            command = ["xcrun", "swiftc", "-typecheck", "-module-cache-path", str(cache), path]
            try:
                result = subprocess.run(
                    command,
                    cwd=temporary,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                row: dict[str, object] = {
                    "exit_code": result.returncode,
                    "reason": "passed" if result.returncode == 0 else "native_typecheck_failed",
                    "diagnostic": result.stdout + result.stderr,
                }
            except subprocess.TimeoutExpired:
                row = {"exit_code": None, "reason": "native_typecheck_timeout", "diagnostic": ""}
            except OSError:
                row = {"exit_code": None, "reason": "native_compiler_unavailable", "diagnostic": ""}
            results[path] = row
            if row["exit_code"] != 0:
                passed = False
                break  # No retry and no misleading partial success.
    return {
        "version": VERSION,
        "gate_sha256": digest(Path(__file__).read_bytes()),
        "sources": hashes,
        "compiler": ["xcrun", "swiftc", "-typecheck"],
        "non_root": True,
        "temporary_directory_removed": not temporary.exists(),
        "passed": passed,
        "results": results,
    }


def verify(root: Path, receipt: Path) -> dict[str, str]:
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 1_048_576:
        raise ValueError("native_typecheck_receipt_required")
    value: object = json.loads(receipt.read_bytes())
    hashes = source_hashes(swift_sources(root))
    if (
        not isinstance(value, dict)
        or value.get("version") != VERSION
        or value.get("gate_sha256") != digest(Path(__file__).read_bytes())
        or value.get("sources") != hashes
        or value.get("compiler") != ["xcrun", "swiftc", "-typecheck"]
        or value.get("non_root") is not True
        or value.get("temporary_directory_removed") is not True
        or value.get("passed") is not True
    ):
        raise ValueError("matching_native_typecheck_pass_required")
    results = value.get("results")
    if not isinstance(results, dict) or set(results) != set(hashes):
        raise ValueError("complete_native_typecheck_results_required")
    for result in results.values():
        if (
            not isinstance(result, dict)
            or type(result.get("exit_code")) is not int
            or result["exit_code"] != 0
            or result.get("reason") != "passed"
        ):
            raise ValueError("native_typecheck_exit_zero_required")
    return hashes


def git_payload(root: Path, revision: str) -> dict[str, bytes]:
    # Resolve to a commit before using the revision in any subsequent Git argument.
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
        cwd=root,
        text=True,
    ).strip()
    paths = (
        subprocess.check_output(
            [
                "git",
                "ls-tree",
                "-rz",
                "--name-only",
                commit,
                "--",
                "src",
                "templates",
                "harness/macos",
                "docs/UPLINK-FAULTS.json",
                "scripts/check_macos_swift.py",
            ],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    return {
        path: subprocess.check_output(["git", "show", commit + ":" + path], cwd=root)
        for path in paths
        if path and not path.startswith("src/offline_agent/uplink/sealed_")
    }


def archive(root: Path, receipt: Path, destination: Path, revision: str) -> None:
    hashes = verify(root, receipt)  # Refuse before creating any transferable payload.
    files = git_payload(root, revision)
    native = {
        p: data
        for p, data in files.items()
        if p.startswith("harness/macos/") and p.endswith(".swift")
    }
    if source_hashes(native) != hashes:
        raise ValueError("staging_revision_differs_from_typechecked_source")
    if any(
        Path(p).name in {"lab.local.json", "uplink-network"} or Path(p).name.startswith(".env")
        for p in files
    ):
        raise ValueError("private_or_compiled_staging_file_refused")
    manifest = (
        json.dumps(source_hashes(files), sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    files["source-manifest.json"] = manifest
    files["native-typecheck.json"] = receipt.read_bytes()
    # Exclusive creation prevents replacing a prior prepared payload or following a symlink.
    with destination.open("xb") as output:
        os.chmod(destination, 0o600)
        with tarfile.open(fileobj=output, mode="w") as bundle:
            for path, data in files.items():
                info = tarfile.TarInfo(path)
                info.size, info.mode = len(data), 0o644
                bundle.addfile(info, io.BytesIO(data))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--typecheck", action="store_true")
    mode.add_argument("--verify", type=Path, metavar="RECEIPT")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="New typecheck receipt; never overwritten")
    parser.add_argument("--archive", type=Path, help="New staging tar, only after --verify")
    parser.add_argument("--revision", default="HEAD", help="Pinned Git commit to package")
    args = parser.parse_args()
    if args.typecheck:
        if args.output is None or args.archive is not None:
            parser.error("--typecheck requires --output and excludes --archive")
        # Reserve the receipt before the one-shot compiler operation.
        with args.output.open("x") as output:
            os.chmod(args.output, 0o600)
            result = typecheck(args.root)
            output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print(json.dumps(result))
        raise SystemExit(0 if result["passed"] is True else 1)
    if args.output is not None:
        parser.error("--output is only valid with --typecheck")
    if args.archive is not None:
        archive(args.root, args.verify, args.archive, args.revision)
    else:
        verify(args.root, args.verify)
    print("native staging preflight passed; no transfer performed")


if __name__ == "__main__":
    main()
