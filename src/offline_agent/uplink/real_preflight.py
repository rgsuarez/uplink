"""The single pre-trial chain. No retry, inference, root operation or fault injection.

Live checks operate on an already authorized, native-gated staging payload. The
transport owns attestation and clock alignment; callers must not reconstruct it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink import lab_config
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.model_identity import (
    LLAMA_SERVER_BUILD,
    expected_model,
    validate_identity,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_evidence import require_positive_control
from offline_agent.uplink.real_fleet import load_clearance, validate_authorization
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.remote_settings import RemoteTargets
from offline_agent.uplink.snapshots import write_private_json

if TYPE_CHECKING:
    from offline_agent.uplink.real_runner import RealBackend
    from offline_agent.uplink.real_transport import LabBackend

ROOT = Path(__file__).resolve().parents[3]
STEPS = (
    "oob",
    "model_attestation",
    "llama_build",
    "native_typecheck",
    "helper_build",
    "helper_self_test",
    "clearance",
    "staging_readback",
    "probe_positive_control",
)


class NativeGate(Protocol):
    def verify(self, root: Path, receipt: Path) -> dict[str, str]: ...
    def git_payload(self, root: Path, revision: str) -> dict[str, bytes]: ...
    def typecheck(self, root: Path) -> dict[str, object]: ...


def native_gate(root: Path) -> NativeGate:
    spec = importlib.util.spec_from_file_location(
        "uplink_native_gate", root / "scripts/check_macos_swift.py"
    )
    if spec is None or spec.loader is None:
        raise ContractError("native_checker_required")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(NativeGate, module)


class Checks(Protocol):
    def check(self, step: str) -> dict[str, Json]: ...


class LabChecks:
    """Use the same bounded OOB transport for each unprivileged remote gate."""

    def __init__(self, backend: LabBackend, root: Path, directory: Path, spec: RealSpec) -> None:
        self.backend, self.root, self.directory, self.spec = backend, root, directory, spec
        self.helper_sha256: Json = None
        self.config = load_config()
        self.redactor = Redactor.local(directory)
        self.redactor.handle("serial", self.config.approved_serial)
        self.config_sha256 = hashlib.sha256(lab_config.CONFIG_PATH.read_bytes()).hexdigest()

    def remote(self, step: str) -> dict[str, Json]:
        config = load_config()
        if (
            config != self.config
            or hashlib.sha256(lab_config.CONFIG_PATH.read_bytes()).hexdigest() != self.config_sha256
        ):
            raise ContractError("lab_config_changed_during_preflight")
        command = (
            'cd "$HOME"/'
            + shlex.quote(config.source_relative)
            + ' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$HOME"/'
            + shlex.quote(config.python_relative)
            + " -B -m offline_agent.uplink.real_preflight_worker"
            + " --step "
            + shlex.quote(step)
            + " --run-id "
            + self.spec.run_id
        )
        return self.backend.rpc(
            {},
            argv=(*self.backend.targets.oob_ssh, command),
            timeout=30 if step == "probe_positive_control" else 180,
        )

    def check(self, step: str) -> dict[str, Json]:
        if step == "clearance":
            value = cast(dict[str, Json], load_clearance(self.root))
            if value != self.backend.authorization:
                raise ContractError("clearance_changed_during_preflight")
            return {"clearance_unchanged": True}
        value = self.remote(step)
        if step == "llama_build":
            if value != {"build_info": LLAMA_SERVER_BUILD, "model_path_matches": True}:
                raise ContractError("server_build_binding_unproven")
        elif step == "native_typecheck":
            path = self.directory / (self.spec.run_id + "-native-typecheck.json")
            if path.exists() or path.is_symlink():
                raise ContractError("native_receipt_reuse_refused")
            write_private_json(path, object_json(self.redactor.scrub_serials_json(value)))
            hashes = native_gate(self.root).verify(self.root, path)
            return {"checked_sources": len(hashes), "passed": True}
        elif step == "helper_build":
            digest = value.get("helper_sha256")
            if (
                set(value) != {"helper_sha256", "exit_code"}
                or type(value.get("exit_code")) is not int
                or value["exit_code"] != 0
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ContractError("native_helper_compile_failed")
            self.helper_sha256 = digest
        elif step == "helper_self_test":
            if (
                value != {"self_test": "passed", "network_reads": 0, "network_writes": 0}
                or type(value.get("network_reads")) is not int
                or type(value.get("network_writes")) is not int
            ):
                raise ContractError("native_helper_self_test_failed")
        elif step == "staging_readback":
            files = native_gate(self.root).git_payload(self.root, "HEAD")
            expected = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            if not files or any(
                (self.root / name).read_bytes() != data for name, data in files.items()
            ):
                raise ContractError("committed_staging_source_required")
            if (
                value.get("sources") != expected
                or value.get("helper_sha256") != self.helper_sha256
                or self.helper_sha256 is None
                or value.get("config_sha256") != self.config_sha256
                or value.get("owned_private_staging") is not True
            ):
                raise ContractError("staging_readback_mismatch")
            return {
                "source_files": len(expected),
                "sources_match": True,
                "helper_matches": True,
                "config_matches": True,
                "owned_private_staging": True,
            }
        elif step == "probe_positive_control":
            return require_positive_control(value)
        else:
            raise ContractError("unknown_preflight_step")
        return value


@dataclass
class PreflightResult:
    identity: dict[str, Json]
    authorization: dict[str, Json] | None
    oob_ok: bool
    failure: Exception | None
    receipt: dict[str, Json]


def reason(exc: Exception) -> str:
    # Never persist arbitrary process output, private paths or config values.
    text = str(exc)
    return text if re.fullmatch(r"[a-z][a-z0-9_]{0,159}", text) else type(exc).__name__


def run_preflight(
    backend: RealBackend,
    spec: RealSpec,
    directory: Path,
    *,
    root: Path = ROOT,
    checks: Checks | None = None,
) -> PreflightResult:
    """Run once, persist intent/results, return the refusal to the existing abort path."""
    from offline_agent.uplink.real_runner import OOBLost

    path = directory / (spec.run_id + "-preflight.json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Reserve before effects. An interrupted or refused run cannot be silently replayed.
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        stream.write('{"status":"reserved"}\n')
    identity = expected_model(provider="mock" if backend.dry else "llama_cpp", chip="unmeasured")
    result = PreflightResult(
        identity,
        None,
        False,
        None,
        {
            "version": 1,
            "kind": "uplink-preflight",
            "run_id": spec.run_id,
            "evidence_kind": "MOCK" if backend.dry else "lab_preflight",
            "status": "running",
            "steps": [],
            "failure": None,
            "trial_prepared": False,
            "inference_requests": 0,
        },
    )
    events: list[Json] = []
    result.receipt["steps"] = events
    redactor = Redactor.local(directory)

    def persist() -> None:
        write_private_json(path, object_json(redactor.scrub_serials_json(result.receipt)))

    step = "local_authorization"
    try:
        if not backend.dry:
            redactor.register_lab_identity()
            if backend.authorization is None:
                backend.authorization = cast(dict[str, Json], load_clearance(root))
            result.authorization = cast(
                dict[str, Json], validate_authorization(backend.authorization)
            )
            if checks is None:
                checks = LabChecks(cast("LabBackend", backend), root, directory, spec)
        for step in STEPS:
            if step == "probe_positive_control" and not spec.probe_scripts:
                continue
            if backend.dry and step not in {"oob", "model_attestation"}:
                continue
            event: dict[str, Json] = {"step": step, "status": "started"}
            events.append(event)
            persist()
            if step == "oob":
                backend.oob()
                result.oob_ok = True
            elif step == "model_attestation":
                # LabBackend.identity owns the bounded clock wait. Never inline it here.
                result.identity = backend.identity()
                validate_identity(result.identity, live=not backend.dry)
                event["result"] = result.identity
            else:
                assert checks is not None
                event["result"] = checks.check(step)
            event["status"] = "passed"
            persist()
        # Building may take time. Do not extend, refresh or retry an expired proof.
        step = "final_freshness"
        validate_identity(result.identity, live=not backend.dry)
        result.receipt["status"] = "passed"
    except (ContractError, ValueError, OSError, subprocess.SubprocessError) as exc:
        result.failure = exc
        if isinstance(exc, OOBLost):
            result.oob_ok = False
        result.receipt.update(status="refused", failed_step=step, failure=reason(exc))
        if events and isinstance(events[-1], dict) and events[-1]["status"] == "started":
            events[-1]["status"] = "refused"
    result.receipt = object_json(redactor.scrub_serials_json(result.receipt))
    persist()
    return result


class BoundaryReached(BaseException):
    """An audit hook stopped the request before process creation."""


def rehearse_boundary(spec: RealSpec, root: Path, directory: Path, targets: RemoteTargets) -> None:
    """CLI-only, permanent fail-closed process/socket barrier around the real chain."""
    from offline_agent.uplink.real_transport import LabBackend

    expected = (*targets.oob_ssh, "true")
    reached = 0

    def audit(event: str, args: tuple[object, ...]) -> None:
        nonlocal reached
        if event == "subprocess.Popen" and tuple(cast(list[str], args[1])) == expected:
            reached += 1
            raise BoundaryReached()
        if event.startswith("socket.") or event in {
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.fork",
            "os.forkpty",
        }:
            raise RuntimeError("unexpected_effect_blocked")

    sys.addaudithook(audit)
    try:
        result = run_preflight(
            LabBackend(spec, execute=True, targets=targets), spec, directory, root=root
        )
    except BoundaryReached:
        if reached != 1:
            raise RuntimeError("unexpected_boundary_count") from None
        path = directory / (spec.run_id + "-preflight.json")
        stopped = object_json(read_json(path.read_text()))
        stopped.update(status="boundary_stopped", desk_rehearsal=True)
        stopped["steps"] = [{"step": "oob", "status": "stopped_before_process_creation"}]
        write_private_json(path, stopped)
        receipt: dict[str, Json] = {
            "status": "PASS_STOPPED_BEFORE_FIRST_SSH",
            "entrypoint": "run_preflight",
            "stopped_before_command": list(expected),
            "audit_hook_stop_before_process_creation": True,
            "processes_started": 0,
            "sockets_opened": 0,
            "remote_contacts": 0,
            "trials_run": 0,
            "retries": 0,
        }
        write_private_json(directory / "boundary-result.json", receipt)
        print(json.dumps(receipt, sort_keys=True))
    else:
        raise ContractError("boundary_not_reached__" + str(result.receipt.get("failure")))
