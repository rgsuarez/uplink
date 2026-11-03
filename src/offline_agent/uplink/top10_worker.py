"""Snapshot-only root RPC and detached watchdog. BASE model code never runs here."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from . import top10_contract as c
    from .top10_attempt import Attempt
    from .top10_engine import Engine, label, steady
    from .top10_kernel import MacKernel, SandboxKernel
    from .top10_oob import validate_proof
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import top10_contract as c  # type: ignore[no-redef,import-not-found]
    from top10_attempt import Attempt  # type: ignore[no-redef,import-not-found]
    from top10_engine import Engine, label, steady  # type: ignore[no-redef,import-not-found]
    from top10_kernel import MacKernel, SandboxKernel  # type: ignore[no-redef,import-not-found]
    from top10_oob import validate_proof  # type: ignore[no-redef,import-not-found]

BASE = Path("/private/var/db/uplink-top10")
PAYLOAD = (
    "top10_worker.py",
    "top10_engine.py",
    "top10_kernel.py",
    "top10_injectors.py",
    "top10_contract.py",
    "top10_attempt.py",
    "top10_portal.py",
    "top10_oob.py",
    "uplink-top10-network",
    "lab.local.json",
)


def run(argv: tuple[str, ...], *, optional: bool = False) -> str:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
    )
    if result.returncode:
        if optional:
            return ""
        raise c.Refused("top10_control_command_refused")
    return result.stdout


def boot_id() -> str:
    return hashlib.sha256(run(("/usr/sbin/sysctl", "-n", "kern.boottime")).encode()).hexdigest()


def server_guard(window: dict[str, object]) -> None:
    birth = run(("/bin/ps", "-p", str(c.SERVER_PID), "-o", "lstart="))
    command = run(("/bin/ps", "-p", str(c.SERVER_PID), "-o", "command="))
    argv = command.strip().split(maxsplit=1)
    if not argv or Path(argv[0]).name != "llama-server" or birth.strip() != window["server_birth"]:
        raise c.Refused("top10_pid_19904_identity_changed")


def secure(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise c.Refused("top10_root_symlink_refused")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != 0 or info.st_mode & 0o077:
        raise c.Refused("top10_private_root_directory_required")


def require_root(execute: bool) -> None:
    if (
        not execute
        or platform.system() != "Darwin"
        or os.geteuid() != 0
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        raise c.Refused("top10_explicit_target_root_required")


def install(
    root: Path,
    run_id: str,
    fault_id: str,
    window: dict[str, object],
    manifest: dict[str, object],
    manifest_sha: str,
    root_approval: object,
    oob_proof: object,
) -> Engine:
    c.require_bench_fault(fault_id)
    c.validate_root_approval(root_approval, window)
    qualified = validate_proof(oob_proof, window, run_id, time.time())
    source = root / "src/offline_agent/uplink"
    if c.file_hash(source / "lab.local.json") != window["private_config_sha256"]:
        raise c.Refused("top10_private_config_binding_changed")
    server_guard(window)
    registered_slots = manifest["slots"]
    if not isinstance(registered_slots, list):
        raise c.Refused("top10_slots_required")
    slot = next((c.obj(s) for s in registered_slots if c.obj(s).get("run_id") == run_id), None)
    if slot is None:
        raise c.Refused("top10_slot_not_registered")
    attempt = Attempt(BASE / "suites", manifest_sha)
    attempt.require_slot(slot, manifest)
    if attempt.state().get("window_sha256") != c.digest(window):
        raise c.Refused("top10_reserved_window_changed")
    secure(BASE)
    secure(BASE / "runs")
    global_fd = os.open(BASE / "prepare.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(global_fd, fcntl.LOCK_EX)
        for child in (BASE / "runs").iterdir():
            if child.is_dir() and (child / "state.json").exists():
                previous = c.read(child / "state.json", private=True)
                if previous.get("lifecycle") not in {"closed"}:
                    raise c.Refused("top10_prior_trial_not_handed_back")
        slots = manifest.get("slots")
        if not isinstance(slots, list):
            raise c.Refused("top10_slots_required")
        for slot in c.selected_slots(manifest):
            registered = c.obj(slot)
            if registered["run_id"] == run_id:
                break
            prior = BASE / "runs" / str(registered["run_id"]) / "state.json"
            if not prior.is_file():
                raise c.Refused("top10_frozen_order_not_ready")
            prior_state = c.read(prior, private=True)
            if (
                prior_state.get("lifecycle") != "closed"
                or c.obj(prior_state.get("binding")).get("manifest_sha256") != manifest_sha
            ):
                raise c.Refused("top10_frozen_order_not_ready")
        # Observe only label/PID, never a process argument list.
        for line in run(("/bin/launchctl", "list")).splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[0].isdigit() and fields[2].startswith("com.uplink."):
                raise c.Refused("top10_another_uplink_job_active")
        directory = BASE / "runs" / run_id
        directory.mkdir(mode=0o700)  # No reuse, overwrite, retry or caller-provided output root.
        attempt.record_oob(qualified)
        c.audit(directory, "install_intent", {"manifest_sha256": manifest_sha, "run_id": run_id})
        for name in PAYLOAD:
            origin = source / name
            if name != "lab.local.json" and c.obj(manifest["files"]).get(
                "src/offline_agent/uplink/" + name
            ) != c.file_hash(origin):
                raise c.Refused("top10_root_payload_not_frozen")
            content = c.read_bytes(
                origin, maximum=32 * 1024 * 1024, private=name == "lab.local.json"
            )
            target = directory / name
            fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o700 if name == "uplink-top10-network" else 0o600,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        hashes = {name: c.file_hash(directory / name) for name in PAYLOAD}
        c.save(directory / "payload-hashes.json", hashes, exclusive=True)
        c.save(directory / "window.json", window, exclusive=True)
        c.save(directory / "manifest.json", manifest, exclusive=True)
        c.save(directory / "root-approval.json", {"raw": root_approval}, exclusive=True)
        c.save(directory / "oob-proof.json", qualified, exclusive=True)
        engine = Engine(directory, MacKernel(directory, window))
        engine.prepare(
            run_id, fault_id, binding={"manifest_sha256": manifest_sha, "boot_id": boot_id()}
        )
        return engine
    finally:
        fcntl.flock(global_fd, fcntl.LOCK_UN)
        os.close(global_fd)


def load_engine(run_id: str, *, cleanup: bool = False) -> Engine:
    c.text(run_id, r"[0-9a-f]{12}")
    directory = BASE / "runs" / run_id
    if not directory.is_dir():
        raise c.Refused("top10_root_run_not_prepared")
    secure(directory)
    hashes = c.read(directory / "payload-hashes.json", private=True)
    if set(hashes) != set(PAYLOAD) or any(
        c.file_hash(directory / name) != wanted for name, wanted in hashes.items()
    ):
        raise c.Refused("top10_root_payload_changed")
    window = c.read(directory / "window.json", private=True)
    manifest = c.read(directory / "manifest.json", private=True)
    c.validate_root_approval(
        c.read(directory / "root-approval.json", private=True).get("raw"), window
    )
    engine = Engine(directory, MacKernel(directory, window))
    state = engine.state()
    binding = c.obj(state["binding"])
    c.validate_window(
        window, manifest, str(binding["manifest_sha256"]), time.time(), cleanup=cleanup
    )
    if not cleanup:
        validate_proof(
            c.read(directory / "oob-proof.json", private=True), window, run_id, time.time()
        )
        if binding.get("boot_id") != boot_id():
            raise c.Refused("top10_same_boot_required")
        server_guard(window)
    return engine


def arm_live(engine: Engine) -> dict[str, object]:
    state = engine.arm()
    run_id = str(state["run_id"])
    label_name = "com.uplink.top10." + run_id
    path = engine.directory / "watchdog.plist"
    value = {
        "Label": label_name,
        "ProgramArguments": [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(engine.directory / "top10_worker.py"),
            "--execute",
            "--watchdog",
            run_id,
        ],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(engine.directory / "watchdog.stdout"),
        "StandardErrorPath": str(engine.directory / "watchdog.stderr"),
    }
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(value, stream)
    c.audit(engine.directory, "launchd_arm", {"label": label_name})
    run(("/bin/launchctl", "bootstrap", "system", str(path)))
    for _ in range(50):
        state = engine.state()
        if state.get("ready") is True:
            status = run(("/bin/launchctl", "print", "system/" + label_name))
            if not re.search(r"(?m)^\s*state = running\s*$", status) or not re.search(
                r"(?m)^\s*pid = " + str(state["watchdog_pid"]) + r"\s*$", status
            ):
                raise c.Refused("top10_launchd_pid_not_verified")
            return engine.public(state)
        time.sleep(0.1)
    raise c.Refused("top10_watchdog_not_ready")


def retire_live(engine: Engine) -> dict[str, object]:
    state = engine.state()
    if state["lifecycle"] != "closed":
        raise c.Refused("top10_verified_close_required")
    name = "system/com.uplink.top10." + str(state["run_id"])
    c.audit(engine.directory, "retire_watchdog", {"run_id": state["run_id"]})
    run(("/bin/launchctl", "bootout", name), optional=True)
    if run(("/bin/launchctl", "print", name), optional=True):
        raise c.Refused("top10_watchdog_retirement_unproven")
    return {"retired": True, "run_id": state["run_id"], "model_pid_touched": False}


def request_restore(engine: Engine) -> dict[str, object]:
    """The detached process owns cleanup too; SSH loss cannot kill its writer."""
    state = engine.state()
    if state["lifecycle"] in {"restored", "closed"}:
        return engine.public(state)
    if state.get("ready") is not True and state["lifecycle"] in {"snapshotted", "armed"}:
        # No fault could pass the ready gate. Verify/release this untouched snapshot.
        return engine.restore(actor="controller_cleanup")
    with engine.locked():
        state = engine.state()
        if state["lifecycle"] in c.ACTIVE:
            c.audit(engine.directory, "detached_cleanup_requested", {})
            state.update(restore_requested=True, controller_cleanup=True)
            c.save(engine.directory / "state.json", state)
        elif state["lifecycle"] not in {"restoring", "restored", "closed"}:
            raise c.Refused("top10_failed_restore_requires_operator")
    until = time.monotonic() + c.RESTORE_SECONDS + 3
    while time.monotonic() < until:
        state = engine.state()
        if state["lifecycle"] in {"restored", "closed"}:
            return engine.public(state)
        if state["lifecycle"] == "restore_failed":
            raise c.Refused("top10_failed_restore_requires_operator")
        time.sleep(0.02 if engine.kernel.sandbox else 0.1)
    raise c.Refused("top10_detached_cleanup_unverified_no_retry")


def dispatch(value: object, *, execute: bool) -> dict[str, object]:
    require_root(execute)
    row = c.obj(value)
    if set(row) != {"version", "operation", "run_id", "payload"} or row["version"] != 1:
        raise c.Refused("top10_root_request_refused")
    run_id = c.text(row["run_id"], r"[0-9a-f]{12}")
    operation = row["operation"]
    payload = c.obj(row["payload"])
    if operation in {
        "reserve_suite",
        "begin_preflight",
        "admit_suite",
        "finish_slot",
        "stop_suite",
        "suite_status",
    }:
        if set(payload) != {
            "source_root",
            "manifest_sha256",
            "window",
            "detail",
            "root_approval",
            "oob_proof",
        }:
            raise c.Refused("top10_suite_fields_refused")
        root = Path(str(payload["source_root"]))
        path = root / "harness/top10/FROZEN.json"
        manifest = c.verify_manifest(root, path)
        manifest_sha = c.file_hash(path)
        if manifest_sha != payload["manifest_sha256"]:
            raise c.Refused("top10_manifest_binding_changed")
        window = c.validate_window(
            payload["window"],
            manifest,
            manifest_sha,
            time.time(),
            cleanup=operation in {"finish_slot", "stop_suite", "suite_status"},
        )
        c.validate_root_approval(payload["root_approval"], window)
        if operation == "reserve_suite":
            validate_proof(payload["oob_proof"], window, "preflight", time.time())
        secure(BASE)
        secure(BASE / "suites")
        attempt = Attempt(BASE / "suites", manifest_sha)
        detail = c.obj(payload["detail"])
        if operation == "reserve_suite":
            if detail:
                raise c.Refused("top10_empty_payload_required")
            state = attempt.reserve(window, manifest)
            attempt.record_oob(c.obj(payload["oob_proof"]))
            return state
        if attempt.state().get("window_sha256") != c.digest(window):
            raise c.Refused("top10_reserved_window_changed")
        if operation == "begin_preflight":
            if detail:
                raise c.Refused("top10_empty_payload_required")
            return attempt.begin_preflight()
        if operation == "suite_status":
            if detail:
                raise c.Refused("top10_empty_payload_required")
            return attempt.state() | {
                "preflight": c.read(attempt.path / "preflight.json", private=True)
                if (attempt.path / "preflight.json").is_file()
                else None
            }
        if operation == "admit_suite":
            return attempt.admit(detail)
        if operation == "finish_slot":
            return attempt.finish(detail, manifest)
        return attempt.stop(c.text(detail.get("reason"), r"[A-Za-z][A-Za-z0-9_]{0,119}"))
    if operation == "prepare":
        if set(payload) != {
            "source_root",
            "manifest_sha256",
            "window",
            "fault_id",
            "root_approval",
            "oob_proof",
        }:
            raise c.Refused("top10_prepare_fields_refused")
        root = Path(str(payload["source_root"]))
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise c.Refused("top10_source_path_refused")
        path = root / "harness/top10/FROZEN.json"
        manifest = c.verify_manifest(root, path)
        manifest_sha = c.file_hash(path)
        if manifest_sha != payload["manifest_sha256"]:
            raise c.Refused("top10_manifest_binding_changed")
        window = c.validate_window(
            payload["window"], manifest, manifest_sha, time.time(), new_trial=True
        )
        slots = manifest.get("slots")
        if not isinstance(slots, list):
            raise c.Refused("top10_slots_required")
        slot = next((c.obj(s) for s in slots if c.obj(s).get("run_id") == run_id), None)
        if slot is None or slot.get("fault_id") != payload["fault_id"]:
            raise c.Refused("top10_slot_not_registered")
        c.require_bench_fault(slot.get("fault_id"))
        engine = install(
            root,
            run_id,
            str(slot["fault_id"]),
            window,
            manifest,
            manifest_sha,
            payload["root_approval"],
            payload["oob_proof"],
        )
        return engine.public(engine.state())
    cleanup = operation in {"observe", "restore", "close", "retire", "status"}
    engine = load_engine(run_id, cleanup=cleanup)
    if operation == "apply" and set(payload) == {"tool", "snapshot_id"}:
        return engine.apply(str(payload["tool"]), str(payload["snapshot_id"]))
    if payload:
        raise c.Refused("top10_empty_payload_required")
    if operation == "arm":
        return arm_live(engine)
    if operation == "inject":
        return engine.inject()
    if operation == "verify":
        return engine.verify_fault()
    if operation == "observe":
        return engine.observe()
    if operation == "restore":
        return request_restore(engine)
    if operation == "close":
        return engine.close()
    if operation == "retire":
        return retire_live(engine)
    if operation == "status":
        return engine.public(engine.state())
    raise c.Refused("top10_operation_refused")


def sandbox_engine(directory: Path) -> Engine:
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise c.Refused("top10_sandbox_symlink_refused")
    kernel = SandboxKernel(directory)
    if c.read(directory / "sandbox-marker.json", private=True) != {
        "kind": "fake-kernel-no-network",
        "version": 1,
    }:
        raise c.Refused("top10_fake_kernel_marker_required")
    return Engine(directory, kernel)


def start_sandbox(directory: Path, run_id: str, fault_id: str, *, scale: float = 1) -> Engine:
    c.number(scale, 1, 50)
    directory.mkdir(mode=0o700, parents=True)
    c.save(
        directory / "sandbox-marker.json",
        {"kind": "fake-kernel-no-network", "version": 1},
        exclusive=True,
    )
    c.save(
        directory / "clock.json",
        {"now": 1000.0, "anchor": steady(), "scale": scale},
        exclusive=True,
    )
    kernel = SandboxKernel(directory)
    kernel.initialize()
    engine = Engine(directory, kernel)
    engine.prepare(run_id, fault_id)
    engine.arm()
    with (directory / "watchdog.log").open("xb") as log:
        os.fchmod(log.fileno(), 0o600)
        process = subprocess.Popen(
            (
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--sandbox-watchdog",
                str(directory),
            ),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            start_new_session=True,
        )
    for _ in range(100):
        if engine.state().get("ready") is True:
            return engine
        if process.poll() is not None:
            raise c.Refused("top10_sandbox_watchdog_failed")
        time.sleep(0.02)
    raise c.Refused("top10_sandbox_watchdog_not_ready")


def watchdog(engine: Engine) -> None:
    portal = None
    try:
        state = engine.state()
        if not engine.kernel.sandbox and state["fault_id"] == "F07":
            if __package__:
                from .top10_portal import listener
            else:
                from top10_portal import listener  # type: ignore[no-redef,import-not-found]
            portal = listener()
        port = int(portal.server_port) if portal else 53421 if engine.kernel.sandbox else 0
        engine.mark_ready(os.getpid(), portal_port=port)
        while True:
            state = engine.state()
            if state["lifecycle"] in {"restored", "closed", "restore_failed"}:
                break
            if state.get("restore_requested") is True:
                engine.restore(actor="controller_cleanup")
                break
            if engine.remaining(state) <= 0:
                engine.restore(actor="watchdog")
                break
            if portal:
                portal.handle_request()
            else:
                time.sleep(0.02 if engine.kernel.sandbox else 0.05)
    except BaseException as exc:
        c.audit(engine.directory, "watchdog_failure", {"reason": label(exc)})
        try:
            engine.restore(actor="watchdog")
        except Exception as restore_exc:
            c.audit(engine.directory, "watchdog_restore_failure", {"reason": label(restore_exc)})
        raise
    finally:
        if portal:
            portal.server_close()
        c.save(
            engine.directory / "watchdog-exited.json",
            {"pid": os.getpid(), "run_id": engine.state()["run_id"], "at": time.time()},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watchdog")
    parser.add_argument("--sandbox-watchdog", type=Path)
    args = parser.parse_args()
    try:
        if args.sandbox_watchdog is not None:
            if args.execute or args.watchdog:
                raise c.Refused("top10_separate_sandbox_required")
            watchdog(sandbox_engine(args.sandbox_watchdog))
        elif args.watchdog:
            require_root(args.execute)
            watchdog(load_engine(args.watchdog, cleanup=True))
        else:
            data = sys.stdin.buffer.read(2_000_001)
            if len(data) > 2_000_000:
                raise c.Refused("top10_request_too_large")
            print(json.dumps(dispatch(c.parse_json(data), execute=args.execute), allow_nan=False))
    except Exception as exc:
        print("top10_worker_refused:" + label(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
