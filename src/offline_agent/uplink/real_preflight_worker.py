"""Unprivileged, one-shot build checks on an explicitly staged lab checkout."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path, PurePosixPath

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink import lab_config
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.model_identity import GGUF_NAME, LLAMA_SERVER_BUILD
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_evidence import POSITIVE_CONTROL, require_positive_control
from offline_agent.uplink.real_bridge import require_host
from offline_agent.uplink.real_preflight import native_gate, reason
from offline_agent.uplink.snapshots import write_private_json

ROOT = Path(__file__).resolve().parents[3]
REMOTE_STEPS = (
    "llama_build",
    "native_typecheck",
    "helper_build",
    "helper_self_test",
    "staging_readback",
    "probe_positive_control",
)


def owned_directory(path: Path) -> None:
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
    ):
        raise ContractError("owned_private_staging_required")


def regular(path: Path, root: Path) -> bytes:
    if root not in path.parents or any(p.is_symlink() for p in (path, *path.parents)):
        raise ContractError("staging_symlink_refused")
    if not path.is_file() or path.stat().st_uid != os.getuid():
        raise ContractError("staging_file_owner_mismatch")
    return path.read_bytes()


def sources(root: Path) -> dict[str, Json]:
    manifest = object_json(read_json(regular(root / "source-manifest.json", root).decode()))
    if not manifest:
        raise ContractError("staging_manifest_required")
    for name, digest in manifest.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name:
            raise ContractError("staging_manifest_path_refused")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ContractError("staging_manifest_digest_refused")
        if hashlib.sha256(regular(root / name, root)).hexdigest() != digest:
            raise ContractError("staging_source_mismatch")
    return manifest


def transition(directory: Path, step: str, previous: str | None) -> None:
    state = directory / "state.json"
    if previous is not None:
        value = object_json(read_json(regular(state, directory).decode()))
        if value != {"state": previous + "_passed"}:
            raise ContractError("preflight_operation_not_repeatable")
    elif state.exists():
        raise ContractError("preflight_operation_not_repeatable")
    write_private_json(state, {"state": step + "_started"})


def perform(step: str, run_id: str, *, root: Path = ROOT) -> dict[str, Json]:
    require_host()
    config = load_config()
    if os.geteuid() == 0 or root != Path.home() / config.source_relative:
        raise ContractError("preflight_requires_owned_nonroot_staging")
    if step not in REMOTE_STEPS or not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ContractError("invalid_preflight_operation")
    owned_directory(root)
    if step == "llama_build":
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(_public_fixture("http://@PUBLIC_IPV4_056@:8080/props"), timeout=3) as response:
            props = object_json(read_json(response.read(262145).decode(), max_bytes=262144))
        if (
            props.get("build_info") != LLAMA_SERVER_BUILD
            or not isinstance(props.get("model_path"), str)
            or Path(str(props["model_path"])).resolve() != (config.bench_root / GGUF_NAME).resolve()
        ):
            raise ContractError("server_build_binding_unproven")
        return {"build_info": LLAMA_SERVER_BUILD, "model_path_matches": True}

    parent = root / ".uplink-preflight"
    parent.mkdir(exist_ok=True, mode=0o700)
    owned_directory(parent)
    directory = parent / run_id
    if step == "native_typecheck":
        directory.mkdir(mode=0o700)  # Never reuse an earlier failed or interrupted attempt.
    owned_directory(directory)
    redactor = Redactor.local(directory)
    redactor.handle("serial", config.approved_serial)

    def receipt(path: Path, value: dict[str, Json]) -> None:
        write_private_json(path, object_json(redactor.scrub_serials_json(value)))

    prior = {
        "native_typecheck": None,
        "helper_build": "native_typecheck",
        "helper_self_test": "helper_build",
        "staging_readback": "helper_self_test",
        "probe_positive_control": "staging_readback",
    }
    transition(directory, step, prior[step])
    try:
        manifest = sources(root)
        helper = directory / "uplink-network"
        value: dict[str, Json]
        if step == "native_typecheck":
            gate = native_gate(root)
            value = object_json(read_json(json.dumps(gate.typecheck(root))))
            receipt_path = directory / "native-typecheck.json"
            receipt(receipt_path, value)
            gate.verify(root, receipt_path)
        elif step == "helper_build":
            native_gate(root).verify(root, directory / "native-typecheck.json")
            built = subprocess.run(
                ["xcrun", "swiftc", "-O", "harness/macos/UplinkNetwork.swift", "-o", str(helper)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            # Preserve compiler evidence privately; stdout only carries the closed receipt.
            receipt(
                directory / "build.json",
                {
                    "exit_code": built.returncode,
                    "diagnostic": built.stdout + built.stderr,
                },
            )
            if built.returncode:
                raise ContractError("native_helper_compile_failed")
            value = {
                "exit_code": 0,
                "helper_sha256": hashlib.sha256(regular(helper, root)).hexdigest(),
            }
            receipt(directory / "helper.json", value)
        elif step == "helper_self_test":
            built_receipt = object_json(
                read_json(regular(directory / "helper.json", root).decode())
            )
            if hashlib.sha256(regular(helper, root)).hexdigest() != built_receipt.get(
                "helper_sha256"
            ):
                raise ContractError("native_helper_digest_mismatch")
            tested = subprocess.run(
                [str(helper), "self-test"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            receipt(
                directory / "self-test.json",
                {"exit_code": tested.returncode, "stdout": tested.stdout, "stderr": tested.stderr},
            )
            value = object_json(read_json(tested.stdout))
            if (
                tested.returncode
                or value != {"self_test": "passed", "network_reads": 0, "network_writes": 0}
                or type(value.get("network_reads")) is not int
                or type(value.get("network_writes")) is not int
            ):
                raise ContractError("native_helper_self_test_failed")
            target = root / "src/offline_agent/uplink/uplink-network"
            if target.is_symlink() or (target.exists() and target.stat().st_uid != os.getuid()):
                raise ContractError("native_helper_target_refused")
            # Publish only the self-tested binary. No network settings or root state change.
            os.replace(helper, target)
        elif step == "staging_readback":
            value = {
                "sources": manifest,
                "helper_sha256": hashlib.sha256(
                    regular(root / "src/offline_agent/uplink/uplink-network", root)
                ).hexdigest(),
                "config_sha256": hashlib.sha256(regular(lab_config.CONFIG_PATH, root)).hexdigest(),
                "owned_private_staging": True,
            }
        else:
            from offline_agent.uplink.real_probe_scripts import execute

            # The fixed positive control uses the actual Wi-Fi capture and sandbox
            # path, but a separate ledger so it cannot consume the trial's budget.
            value = execute(
                POSITIVE_CONTROL,
                run_id,
                time.time() + 20,
                directory,
                redactor,
            )
            receipt(directory / "probe-positive-control.json", value)
            require_positive_control(value)
        write_private_json(directory / "state.json", {"state": step + "_passed"})
        return object_json(redactor.scrub_serials_json(value))
    except (ContractError, ValueError, OSError, subprocess.SubprocessError) as exc:
        write_private_json(
            directory / "state.json", {"state": step + "_refused", "reason": reason(exc)}
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, choices=REMOTE_STEPS)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        value = perform(args.step, args.run_id)
    except (ContractError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": reason(exc)}))
        raise SystemExit(1) from exc
    print(json.dumps({"ok": True, "result": value}))


if __name__ == "__main__":
    main()
