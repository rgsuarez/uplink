"""Pinned model provenance, with distinct expected, file-verified and live-server evidence."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import hashlib
import json
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.lab_config import bench_root, configured_host
from offline_agent.uplink.snapshots import write_private_json

MODEL_NAME = "Muse Glimmer 30B"
GGUF_NAME = "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
GGUF_SHA256 = "4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e"
SOURCE_REPO = "meta-models/Muse-Glimmer-30B-GGUF"
LLAMA_BUILD = "10353 (f8def7fe1)"
LLAMA_SERVER_BUILD = "b10353-f8def7fe1"
MODEL_KEYS = {
    "name",
    "gguf_filename",
    "gguf_sha256",
    "source_repo",
    "llama_cpp_build",
    "host_chip",
    "provider",
    "active_model_name",
    "verification",
    "verified_at",
    "model_used",
}


@lru_cache(maxsize=1)
def host_chip() -> str:
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"),
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            chip = result.stdout.strip()
            if result.returncode == 0 and re.fullmatch(r"Apple [A-Za-z0-9 ]{1,40}", chip):
                return chip
        except (OSError, subprocess.SubprocessError):
            pass
    return "unmeasured"


def expected_model(
    *, provider: str | None = None, active_name: str | None = None, chip: str | None = None
) -> dict[str, Json]:
    return {
        "name": MODEL_NAME,
        "gguf_filename": GGUF_NAME,
        "gguf_sha256": GGUF_SHA256,
        "source_repo": SOURCE_REPO,
        "llama_cpp_build": LLAMA_BUILD,
        "host_chip": chip or host_chip(),
        "provider": provider,
        "active_model_name": active_name,
        "verification": "expected",
        "verified_at": None,
        "model_used": False,
    }


def validate_identity(value: dict[str, Json], *, live: bool = False) -> dict[str, Json]:
    if set(value) != MODEL_KEYS:
        raise ContractError("complete_model_provenance_required")
    fixed = {
        "name": MODEL_NAME,
        "gguf_filename": GGUF_NAME,
        "gguf_sha256": GGUF_SHA256,
        "source_repo": SOURCE_REPO,
        "llama_cpp_build": LLAMA_BUILD,
    }
    if any(value[k] != v for k, v in fixed.items()):
        raise ContractError("model_provenance_pin_mismatch")
    if not isinstance(value["host_chip"], str) or value["verification"] not in {
        "expected",
        "file_verified",
        "server_verified",
        "fixture",
    }:
        raise ContractError("invalid_model_provenance")
    if live:
        timestamp = value["verified_at"]
        if (
            value["verification"] != "server_verified"
            or value["host_chip"] != "Apple M4 Max"
            or type(timestamp) not in (int, float)
            or not 0 <= time.time() - float(cast(float, timestamp)) <= 300
        ):
            raise ContractError("fresh_server_model_proof_required")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def attest(model: Path, binary: Path, *, require_server: bool = True) -> dict[str, Json]:
    if platform.system() != "Darwin" or not configured_host():
        raise ContractError("model_attestation_requires_lab")
    if model.name != GGUF_NAME or model.is_symlink() or not model.is_file() or not binary.is_file():
        raise ContractError("pinned_model_files_required")
    if file_sha256(model) != GGUF_SHA256:
        raise ContractError("gguf_digest_mismatch")
    record = expected_model(provider="llama_cpp", active_name=MODEL_NAME)
    record.update(verification="file_verified", verified_at=time.time())
    if require_server:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(_public_fixture("http://@PUBLIC_IPV4_056@:8080/props"), timeout=3) as response:
                props = object_json(read_json(response.read(262145).decode(), max_bytes=262144))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ContractError("local_model_server_unavailable") from exc
        served = props.get("model_path")
        if not isinstance(served, str) or Path(served).resolve() != model.resolve():
            raise ContractError("server_model_binding_unproven")
        # Verify the process actually serving requests. Starting a second llama process
        # for --version can stall during device initialization while the model is loaded.
        if props.get("build_info") != LLAMA_SERVER_BUILD:
            raise ContractError("server_build_binding_unproven")
        record["verification"] = "server_verified"
    else:
        try:
            result = subprocess.run(
                (str(binary), "--version"), capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContractError("llama_build_probe_failed") from exc
        if result.returncode or "version: " + LLAMA_BUILD not in result.stdout + result.stderr:
            raise ContractError("llama_build_mismatch")
    return validate_identity(record, live=require_server)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=bench_root() / GGUF_NAME)
    parser.add_argument(
        "--binary",
        type=Path,
        default=bench_root() / "llama-b10353/llama-b10353/llama-server",
    )
    parser.add_argument("--file-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = attest(args.model, args.binary, require_server=not args.file_only)
    write_private_json(args.output, record)
    print(json.dumps(record))


if __name__ == "__main__":
    main()
