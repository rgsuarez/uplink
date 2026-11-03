"""Freeze and package the reviewed desk build. Never contacts or loads a model."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import tarfile
from pathlib import Path

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.model_identity import GGUF_NAME, GGUF_SHA256, LLAMA_SERVER_BUILD
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_model import schema

ROOT = Path(__file__).resolve().parents[3]


def freeze(
    root: Path, *, previous: Path | None = None, previous_sha: str | None = None
) -> dict[str, object]:
    c.load_registry(root)
    files = {name: c.file_hash(root / name) for name in sorted(c.runtime_files(root))}
    document: dict[str, object] = {
        "version": c.VERSION,
        "status": "FROZEN",
        "arm": "BASE",
        "server_pid": c.SERVER_PID,
        "registry_sha256": c.REGISTRY_SHA256,
        "prompt_sha256": c.PROMPT_SHA256,
        "oracle_sha256": c.ORACLE_SHA256,
        "edge_oracle_sha256": c.EDGE_ORACLE_SHA256,
        "execution_profile": c.execution_profile(),
        "files": files,
        "slots": [
            {"fault_id": fault, "trial": trial, "run_id": secrets.token_hex(6)}
            for fault in c.FAULT_IDS
            for trial in (1, 2, 3)
        ],
        "model": {
            "name": GGUF_NAME,
            "sha256": GGUF_SHA256,
            "build": LLAMA_SERVER_BUILD,
            "n_ctx": 4096,
            "slots": 1,
            "adapters": [],
            "temperature": 0,
            "seed": 0,
            "max_completion_tokens": 192,
            "reasoning_strength": "low",
        },
        "grammar_zero_snapshot_sha256": hashlib.sha256(
            schema_gbnf(schema("0" * 64)).encode()
        ).hexdigest(),
        "native_target": "arm64-apple-macos15.0",
        "base_model_calls_before_freeze": 0,
        "portal_evidence_class": "local_portal_simulation",
    }
    path = root / "harness/top10/FROZEN.json"
    if previous is not None:
        if (
            previous_sha is None
            or c.file_hash(previous) != previous_sha
            or c.file_hash(path) != previous_sha
        ):
            raise c.Refused("top10_exact_previous_manifest_required")
        old = c.read(previous)
        if (
            old.get("registry_sha256") != c.REGISTRY_SHA256
            or old.get("oracle_sha256") != c.ORACLE_SHA256
            or old.get("prompt_sha256") != c.PROMPT_SHA256
        ):
            raise c.Refused("top10_original_definition_changed")
        c.selected_slots(old)
        document["slots"] = old["slots"]
        document["supersedes_manifest_sha256"] = previous_sha
        document["revision_authority"] = "Amanda 2026-10-29 05:50 CT; Kaylin re-review required"
    c.save(path, document, exclusive=previous is None)
    c.verify_manifest(root, path)
    return document


def package(root: Path, output: Path) -> str:
    manifest_path = root / "harness/top10/FROZEN.json"
    manifest = c.verify_manifest(root, manifest_path)
    files = c.obj(manifest["files"])
    members = dict(files) | {"harness/top10/FROZEN.json": c.file_hash(manifest_path)}
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target, tarfile.open(fileobj=target, mode="w") as archive:
        for name, wanted in sorted(members.items()):
            content = c.read_bytes(root / name, maximum=32 * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != wanted:
                raise c.Refused("top10_package_source_changed")
            item = tarfile.TarInfo(name)
            item.size = len(content)
            item.mode = (
                0o700 if name.endswith(("uplink-network", "uplink-top10-network")) else 0o600
            )
            archive.addfile(item, io.BytesIO(content))
    with tarfile.open(output, "r:") as archive:
        if archive.getnames() != sorted(members):
            raise c.Refused("top10_package_member_mismatch")
        for item in archive:
            source = archive.extractfile(item)
            if (
                not item.isfile()
                or source is None
                or hashlib.sha256(source.read()).hexdigest() != members[item.name]
            ):
                raise c.Refused("top10_package_readback_failed")
    return c.file_hash(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--revise-from", type=Path)
    parser.add_argument("--previous-sha256")
    args = parser.parse_args()
    try:
        if args.freeze:
            freeze(ROOT, previous=args.revise_from, previous_sha=args.previous_sha256)
        elif args.revise_from or args.previous_sha256:
            raise c.Refused("top10_explicit_freeze_revision_required")
        if args.output is not None:
            print(json.dumps({"package_sha256": package(ROOT, args.output)}))
        if args.freeze or args.output:
            print(
                json.dumps(
                    {
                        "manifest_sha256": c.file_hash(ROOT / "harness/top10/FROZEN.json"),
                        "base_model_calls": 0,
                    }
                )
            )
        else:
            print(json.dumps({"mode": "PLAN_ONLY", "target_contacts": 0, "base_model_calls": 0}))
    except Exception as exc:
        print(json.dumps({"refused": label(exc)}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
