"""Build an offline one-off package from committed bytes; never contact or launch a model."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def prepare(root: Path, target: Path) -> dict[str, object]:
    if target.exists() or not target.absolute().is_relative_to(root / "artifacts"):
        raise ValueError("new_private_artifact_directory_required")
    if any(p.is_symlink() for p in (target.absolute(), *target.absolute().parents)):
        raise ValueError("output_symlink_refused")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    paths = (
        git(
            root,
            "ls-tree",
            "-rz",
            "--name-only",
            commit,
            "--",
            "src",
            "templates",
            "schemas",
            "bench/adhoc/prime_cpp",
            "configs/prime-cpp-oneoff.json",
            "scripts/prime_cpp_once.py",
        )
        .decode()
        .split("\0")
    )
    names = [n for n in paths if n and not n.startswith("src/offline_agent/uplink/sealed_")]
    required = {
        "configs/prime-cpp-oneoff.json",
        "scripts/prime_cpp_once.py",
        "bench/adhoc/prime_cpp/oracle.py.txt",
        "bench/adhoc/prime_cpp/primes.cpp",
    }
    if not required.issubset(names):
        raise ValueError("commit_package_inputs_before_preparing")
    if any(Path(n).name.startswith(".env") or n.endswith(".local.json") for n in names):
        raise ValueError("private_configuration_in_package_refused")
    files = {n: git(root, "show", commit + ":" + n) for n in names}
    if any((root / n).read_bytes() != b for n, b in files.items()):
        raise ValueError("committed_source_bytes_required")
    plan = json.loads(files["configs/prime-cpp-oneoff.json"])
    original = plan["original"]
    for name, key in (("primes.cpp", "source_sha256"), ("oracle.py.txt", "oracle_sha256")):
        if hashlib.sha256(files["bench/adhoc/prime_cpp/" + name]).hexdigest() != original[key]:
            raise ValueError("original_task_bytes_mismatch")
    registration_bytes = (root / "configs/f10-ondevice.json").read_bytes()
    registration_sha = "ff41ad3c71823a67c4abc4834c06c4adf08af5cc4e5ab3bc5e642b8f4b24ce53"
    if (
        hashlib.sha256(registration_bytes).hexdigest() != registration_sha
        or (root / "configs/f10-ondevice.sha256").read_text()
        != registration_sha + "  configs/f10-ondevice.json\n"
    ):
        raise ValueError("frozen_registration_anchor_required")
    registration = json.loads(registration_bytes)
    frozen = registration["protected_sha256"]
    for name, expected in frozen.items():
        path = "bench/lab/" + Path(name).name if name.startswith("bench/") else name
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen_input_changed")
    overlay = plan["workspace_overlay"]
    if (
        overlay.get("file") != "src/offline_agent/workspace.py"
        or overlay.get("original_rule") != 'if name.endswith(".py"):'
        or overlay.get("packaged_rule") != 'if name == "primes.cpp":'
    ):
        raise ValueError("single_cpp_file_overlay_required")
    workspace = str(overlay["file"])
    before = files[workspace]
    old, new = str(overlay["original_rule"]).encode(), str(overlay["packaged_rule"]).encode()
    if before.count(old) != 1 or hashlib.sha256(before).hexdigest() != frozen[workspace]:
        raise ValueError("exact_frozen_workspace_overlay_required")
    files[workspace] = before.replace(old, new, 1)
    sandbox = "src/offline_agent/sandbox.py"
    sandbox_before = files[sandbox]
    sandbox_old = b'        "/Library/Apple",'
    sandbox_new = sandbox_old + b'\n        "/Library/Developer/CommandLineTools",'
    if (
        sandbox_before.count(sandbox_old) != 1
        or hashlib.sha256(sandbox_before).hexdigest() != frozen[sandbox]
        or plan.get("sandbox_overlay", {}).get("file") != sandbox
        or plan["sandbox_overlay"].get("original_rule") != sandbox_old.decode()
        or plan["sandbox_overlay"].get("packaged_rule") != sandbox_new.decode()
    ):
        raise ValueError("exact_compiler_read_overlay_required")
    files[sandbox] = sandbox_before.replace(sandbox_old, sandbox_new, 1)
    manifest = {
        "version": 1,
        "kind": "prime-cpp-oneoff-package",
        "source_commit": commit,
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
        "overlay": {
            "path": workspace,
            "original_sha256": hashlib.sha256(before).hexdigest(),
            "packaged_sha256": hashlib.sha256(files[workspace]).hexdigest(),
            "scope": "only_original_primes_cpp_editable",
        },
        "sandbox_overlay": {
            "path": sandbox,
            "original_sha256": hashlib.sha256(sandbox_before).hexdigest(),
            "packaged_sha256": hashlib.sha256(files[sandbox]).hexdigest(),
            "scope": "read_existing_CommandLineTools_only",
        },
        "frozen_inputs_verified_unchanged": len(frozen),
        "benchmark_result": False,
    }
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    files["package-manifest.json"] = encoded
    target.mkdir(parents=True, mode=0o700)
    archive = target / "prime-cpp-once.tar"
    with archive.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                tar.addfile(info, io.BytesIO(data))
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if {m.name for m in members} != set(files) or any(not m.isfile() for m in members):
            raise ValueError("archive_members_mismatch")
        for member in members:
            extracted = tar.extractfile(member)
            if extracted is None or extracted.read() != files[member.name]:
                raise ValueError("archive_readback_mismatch")
    (target / "package-manifest.json").write_bytes(encoded)
    result = {
        "source_commit": commit,
        "files": len(files),
        "bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "package_manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "readback_verified": True,
        "frozen_inputs_unchanged": len(frozen),
        "workspace_overlay": manifest["overlay"],
        "sandbox_overlay": manifest["sandbox_overlay"],
        "model_calls": 0,
        "host_contacts": 0,
        "compilations": 0,
        "fake_tests_run": False,
        "execution_authorized": False,
    }
    (target / "preparation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.output.absolute())))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Refused: " + type(exc).__name__ + "\n")


if __name__ == "__main__":
    main()
