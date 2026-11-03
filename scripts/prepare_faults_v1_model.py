"""Bind saved survey, runtime and previously verified native bytes without host contact."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.faults_v1 import commands, digest
from offline_agent.uplink.faults_v1_authority import ENTRYPOINTS, hash_value
from offline_agent.uplink.faults_v1_inventory import pin_ipv4
from offline_agent.uplink.faults_v1_worker import PAYLOAD
from offline_agent.uplink.probe_trial_window import read_object, runtime_digest, save, source_hashes

ROOT = Path(__file__).resolve().parents[1]


def prepare(
    root: Path,
    survey_path: Path,
    survey_sha: str,
    native_manifest: Path,
    native_sha: str,
    output: Path,
) -> dict[str, Json]:
    survey = read_object(survey_path, survey_sha)
    native = read_object(native_manifest, native_sha)
    if native.get("helper_verified") is not True or native.get("config_verified") is not True:
        raise ContractError("newfault_prior_native_verification_required")
    files = object_json(native.get("files"))
    native_hash = files.get("src/offline_agent/uplink/uplink-network")
    hash_value(native_hash)
    # Retained binary provenance must bind the very same native source files.
    native_sources = list((root / "harness/macos").rglob("*.swift"))
    if not native_sources or any(p.is_symlink() or not p.is_file() for p in native_sources):
        raise ContractError("newfault_native_sources_required")
    for path in native_sources:
        if files.get(str(path.relative_to(root))) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ContractError("newfault_native_source_changed")
    specs = pin_ipv4(survey, (uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]))
    payload: dict[str, Json] = {}
    for name in PAYLOAD:
        if name == "uplink-network":
            payload[name] = native_hash
            continue
        path = root / "src/offline_agent/uplink" / name
        if path.is_symlink() or not path.is_file():
            raise ContractError("newfault_root_source_missing")
        payload[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    value: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-newfault-desk-packet",
        "live_authorized": False,
        "runtime_sha256": runtime_digest(root),
        "runtime_files": dict(source_hashes(root)),
        "root_payload_sha256": payload,
        "entrypoints_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ENTRYPOINTS
        },
        "package_sha256": digest(payload),
        "native_binary_sha256": native_hash,
        "native_provenance_sha256": native_sha,
        "native_binding_basis": "prior_verified_binary_hash_requires_exact_live_readback",
        "native_binary_in_local_packet": False,
        "survey_sha256": survey_sha,
        "survey_is_current_live_admission": False,
        "specs": [
            {
                "spec": json.loads(json.dumps(s.document())),
                "spec_sha256": digest(s.document()),
                "commands": json.loads(json.dumps(commands(s))),
            }
            for s in specs
        ],
    }
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ContractError("newfault_new_packet_directory_required")
    output.mkdir(mode=0o700, parents=True)
    save(output / "packet.private.json", value)
    summary: dict[str, Json] = {
        "kind": "uplink-newfault-packet-binding",
        "version": 1,
        "packet_sha256": digest(value),
        "packet_file_sha256": hashlib.sha256(
            (output / "packet.private.json").read_bytes()
        ).hexdigest(),
        "runtime_sha256": value["runtime_sha256"],
        "package_sha256": value["package_sha256"],
        "native_binary_sha256": native_hash,
        "native_readback_before_live_required": True,
        "survey_sha256": survey_sha,
        "live_authorized": False,
        "specs": [
            {"run_id": s.run_id, "fault": s.fault, "spec_sha256": digest(s.document())}
            for s in specs
        ],
    }
    save(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", required=True, type=Path)
    parser.add_argument("--survey-sha256", required=True)
    parser.add_argument("--native-manifest", required=True, type=Path)
    parser.add_argument("--native-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    target = args.output.absolute()
    if not target.is_relative_to(ROOT / "artifacts") or ".." in target.parts:
        parser.error("new private artifact directory required")
    result = prepare(
        ROOT,
        args.survey,
        args.survey_sha256,
        args.native_manifest,
        args.native_manifest_sha256,
        target,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
