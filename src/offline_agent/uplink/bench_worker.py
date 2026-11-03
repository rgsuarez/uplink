"""Reduced F04 root boundary and detached watchdog. No model code executes as root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import sys
import time
from pathlib import Path

if __package__:
    from . import bench_contract as b
    from . import top10_contract as c
    from . import top10_worker as w
    from .bench_attempt import Attempt, lock
    from .top10_engine import Engine, label
    from .top10_kernel import MacKernel
    from .top10_oob import validate_proof
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_contract as b  # type: ignore[no-redef,import-not-found]
    import top10_contract as c  # type: ignore[no-redef,import-not-found]
    import top10_worker as w  # type: ignore[no-redef,import-not-found]
    from bench_attempt import Attempt, lock  # type: ignore[no-redef,import-not-found]
    from top10_engine import Engine, label  # type: ignore[no-redef,import-not-found]
    from top10_kernel import MacKernel  # type: ignore[no-redef,import-not-found]
    from top10_oob import validate_proof  # type: ignore[no-redef,import-not-found]

BASE = b.BASE
CLEANUP = {"restore", "observe", "close", "retire", "status", "finish", "stop", "attempt_status"}


def server_guard(window: dict[str, object]) -> None:
    pid = str(window["server_pid"])
    born = w.run(("/bin/ps", "-p", pid, "-o", "lstart=")).strip()
    # The short comm name can be changed by the server. Inspect only the first
    # executable token of full command output; never log the argument string.
    command = w.run(("/bin/ps", "-p", pid, "-o", "command=")).strip().split(maxsplit=1)
    if born != window["server_birth"] or not command or Path(command[0]).name != "llama-server":
        raise c.Refused("bench_approved_server_identity_changed")


def saved(run_id: str, *, cleanup: bool) -> tuple[Attempt, dict[str, object], dict[str, object]]:
    attempt = Attempt(BASE / "attempts", run_id)
    w.secure(attempt.path)
    data = c.read(attempt.path / "binding.json", private=True)
    manifest = b.validate_registration(data["manifest"])
    window = b.authority(
        data["window"],
        manifest,
        str(data["manifest_sha256"]),
        str(data["package_sha256"]),
        time.time(),
        cleanup=cleanup,
    )
    b.approvals(window, data["owner"], data["richard"], time.time(), cleanup=cleanup)
    if attempt.state().get("window_sha256") != c.digest(window) or manifest["run_id"] != run_id:
        raise c.Refused("bench_reserved_binding_changed")
    if not cleanup:
        server_guard(window)
    return attempt, data, window


def load_engine(run_id: str, *, cleanup: bool = False) -> Engine:
    _, data, window = saved(run_id, cleanup=cleanup)
    directory = BASE / "runs" / run_id
    w.secure(directory)
    files = c.obj(c.obj(data["manifest"])["files"])
    for name in b.ROOT_FILES:
        if c.file_hash(directory / name) != files.get("src/offline_agent/uplink/" + name):
            raise c.Refused("bench_root_payload_changed")
    if c.file_hash(directory / "lab.local.json") != window["private_config_sha256"]:
        raise c.Refused("bench_private_config_changed")
    engine = Engine(directory, MacKernel(directory, window))
    state = engine.state()
    if (
        state.get("run_id") != run_id
        or state.get("fault_id") != "F04"
        or c.obj(state.get("binding")).get("manifest_sha256") != data["manifest_sha256"]
    ):
        raise c.Refused("bench_root_slot_changed")
    if not cleanup:
        if c.obj(state["binding"]).get("boot_id") != w.boot_id():
            raise c.Refused("bench_same_boot_required")
        validate_proof(c.read(directory / "oob.json", private=True), window, run_id, time.time())
    return engine


def copy_payload(source: Path, target: Path, expected: str, *, executable: bool = False) -> None:
    if source.name == "lab.local.json":
        # sudo executes as root while the staged approved configuration belongs
        # to the invoking operator. Read the checked descriptor, never chmod it.
        uid = int(c.text(os.environ.get("SUDO_UID"), r"[0-9]{1,10}"))
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise c.Refused("bench_private_config_symlink_refused")
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, uid}
                or info.st_mode & 0o077
                or info.st_size > 65536
            ):
                raise c.Refused("bench_private_config_owner_or_mode_refused")
            content = stream.read(65537)
    else:
        content = c.read_bytes(source, maximum=32 * 1024 * 1024)
    if hashlib.sha256(content).hexdigest() != expected:
        raise c.Refused("bench_copy_bytes_not_approved")
    fd = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700 if executable else 0o600
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def require_clear() -> None:
    # Historical evidence is observed, never reused or changed.
    for base in (BASE / "runs", Path("/private/var/db/uplink-top10/runs")):
        if base.exists():
            for child in base.iterdir():
                if (
                    child.is_dir()
                    and c.read(child / "state.json", private=True).get("lifecycle") != "closed"
                ):
                    raise c.Refused("bench_prior_trial_not_handed_back")
    for line in w.run(("/bin/launchctl", "list")).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2].startswith("com.uplink."):
            raise c.Refused("bench_prior_uplink_job_not_retired")


def prepare(run_id: str, proof: object) -> dict[str, object]:
    attempt, data, window = saved(run_id, cleanup=False)
    if attempt.state()["lifecycle"] != "running":
        raise c.Refused("bench_preflight_required")
    if time.time() + 150 + 600 + 300 > b.timestamp(window["end"]):
        raise c.Refused("bench_insufficient_handback_reserve")
    qualified = validate_proof(proof, window, run_id, time.time())
    old_proof = c.obj(data["oob"])
    old_attempts, attempts = old_proof["attempts"], qualified["attempts"]
    if not isinstance(old_attempts, list) or not isinstance(attempts, list):
        raise c.Refused("bench_oob_attempts_required")
    if old_proof["qualification_id"] == qualified["qualification_id"] or {
        c.obj(v)["nonce"] for v in old_attempts
    }.intersection(c.obj(v)["nonce"] for v in attempts):
        raise c.Refused("bench_oob_nonce_replay_refused")
    source_root = Path(str(data["source_root"]))
    manifest = b.verify_manifest(source_root)
    if manifest != data["manifest"]:
        raise c.Refused("bench_source_registration_changed")
    with lock(BASE / "prepare.lock"):
        require_clear()
        w.secure(BASE / "runs")
        directory = BASE / "runs" / run_id
        directory.mkdir(mode=0o700)  # Partial install also consumes the slot.
        c.audit(
            directory,
            "install_intent",
            {"run_id": run_id, "manifest_sha256": data["manifest_sha256"]},
        )
        files = c.obj(manifest["files"])
        source = source_root / "src/offline_agent/uplink"
        for name in b.ROOT_FILES:
            copy_payload(
                source / name,
                directory / name,
                str(files["src/offline_agent/uplink/" + name]),
                executable=name == "uplink-top10-network",
            )
        copy_payload(
            source / "lab.local.json",
            directory / "lab.local.json",
            str(window["private_config_sha256"]),
        )
        c.save(directory / "oob.json", qualified, exclusive=True)
        engine = Engine(directory, MacKernel(directory, window))
        return engine.prepare(
            run_id,
            "F04",
            binding={"manifest_sha256": data["manifest_sha256"], "boot_id": w.boot_id()},
        )


def arm(engine: Engine, server_pid: object) -> dict[str, object]:
    state = engine.arm()
    run_id = str(state["run_id"])
    name = "com.uplink.bench-reduced." + run_id
    path = engine.directory / "watchdog.plist"
    value = {
        "Label": name,
        "ProgramArguments": [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(engine.directory / "bench_worker.py"),
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
    c.audit(engine.directory, "watchdog_install_intent", {"label": name})
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    w.run(("/bin/launchctl", "bootstrap", "system", str(path)))
    for _ in range(50):
        state = engine.state()
        if state.get("ready") is True:
            status = w.run(("/bin/launchctl", "print", "system/" + name))
            if (
                state["watchdog_pid"] == server_pid
                or not re.search(r"(?m)^\s*state = running\s*$", status)
                or not re.search(r"(?m)^\s*pid = " + str(state["watchdog_pid"]) + r"\s*$", status)
            ):
                raise c.Refused("bench_watchdog_pid_not_verified")
            return engine.public(state)
        time.sleep(0.1)
    raise c.Refused("bench_watchdog_not_ready")


def retire(engine: Engine) -> dict[str, object]:
    if engine.state()["lifecycle"] != "closed":
        raise c.Refused("bench_verified_close_required")
    name = "com.uplink.bench-reduced." + str(engine.state()["run_id"])
    c.audit(engine.directory, "watchdog_retirement_intent", {"label": name})
    w.run(("/bin/launchctl", "bootout", "system/" + name), optional=True)
    # A successful exhaustive list proves absence. A failed print is not proof.
    listing = w.run(("/bin/launchctl", "list"))
    if not listing.startswith("PID\tStatus\tLabel\n") or any(
        line.split()[-1:] == [name] for line in listing.splitlines()[1:]
    ):
        raise c.Refused("bench_watchdog_retirement_unproven")
    result = {"retired": True, "run_id": engine.state()["run_id"], "model_pid_touched": False}
    c.audit(engine.directory, "watchdog_retired", result)
    c.save(engine.directory / "retired.json", result)
    return result


def reserve(payload: dict[str, object], run_id: str) -> dict[str, object]:
    if set(payload) != {
        "source_root",
        "manifest_sha256",
        "package_sha256",
        "window",
        "owner",
        "richard",
        "oob",
    }:
        raise c.Refused("bench_reservation_fields_refused")
    root = Path(str(payload["source_root"]))
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise c.Refused("bench_source_path_refused")
    manifest = b.verify_manifest(root)
    manifest_sha = c.file_hash(root / b.MANIFEST_PATH)
    package_sha = c.file_hash(root.parent / ("bench-reduced-" + run_id + ".tar"))
    if (
        manifest["run_id"] != run_id
        or manifest_sha != payload["manifest_sha256"]
        or package_sha != payload["package_sha256"]
    ):
        raise c.Refused("bench_reservation_binding_changed")
    window = b.authority(
        payload["window"], manifest, manifest_sha, package_sha, time.time(), starting=True
    )
    b.approvals(window, payload["owner"], payload["richard"], time.time())
    validate_proof(payload["oob"], window, "preflight", time.time())
    w.secure(BASE)
    w.secure(BASE / "attempts")
    attempt = Attempt(BASE / "attempts", run_id)
    state = attempt.reserve(manifest, window)
    c.save(attempt.path / "binding.json", payload | {"manifest": manifest}, exclusive=True)
    return state


def dispatch(value: object, *, execute: bool) -> dict[str, object]:
    w.require_root(execute)
    if "UPLINK_TOP10_SANDBOX" in os.environ:
        raise c.Refused("bench_live_worker_refuses_sandbox")
    row = c.obj(value)
    if (
        set(row) != {"version", "operation", "run_id", "payload"}
        or type(row["version"]) is not int
        or row["version"] != 1
    ):
        raise c.Refused("bench_root_request_refused")
    run_id = c.text(row["run_id"], r"[0-9a-f]{12}")
    op, payload = c.text(row["operation"], r"[a-z_]{1,32}"), c.obj(row["payload"])
    if op == "reserve":
        return reserve(payload, run_id)
    attempt, _, window = saved(run_id, cleanup=op in CLEANUP)
    if (
        op in {"prepare", "arm", "inject", "apply", "reserve_decision"}
        and attempt.state().get("lifecycle") != "running"
    ):
        raise c.Refused("bench_stopped_attempt_cannot_mutate")
    if op == "attempt_status" and not payload:
        return attempt.state() | {
            "preflight": c.read(attempt.path / "preflight.json", private=True)
            if (attempt.path / "preflight.json").is_file()
            else None
        }
    if op == "begin_preflight" and not payload:
        return attempt.change("reserved", "preflighting", {})
    if op == "admit":
        return attempt.admit(payload, window)
    if op == "reserve_decision" and set(payload) == {"history"}:
        engine = load_engine(run_id)
        engine.require_active(engine.state(), minimum=18)
        return attempt.decision(payload["history"])
    if op == "decision_result":
        return attempt.decision_result(payload)
    if op == "stop" and set(payload) == {"reason"}:
        return attempt.stop(str(payload["reason"]))
    if op == "prepare" and set(payload) == {"oob"}:
        return prepare(run_id, payload["oob"])
    engine = load_engine(run_id, cleanup=op in CLEANUP)
    if op == "finish":
        if (
            engine.state()["lifecycle"] != "closed"
            or c.read(engine.directory / "retired.json", private=True).get("retired") is not True
        ):
            raise c.Refused("bench_root_handback_unproven")
        return attempt.finish(payload)
    if op == "apply" and set(payload) == {"tool", "snapshot_id"}:
        state = attempt.state()
        history = state["history"]
        if (
            state["lifecycle"] != "running"
            or not isinstance(history, list)
            or not history
            or history[-1] != payload["tool"]
        ):
            raise c.Refused("bench_model_action_not_recorded")
        return engine.apply(str(payload["tool"]), str(payload["snapshot_id"]))
    if payload:
        raise c.Refused("bench_empty_payload_required")
    if op == "arm":
        return arm(engine, window["server_pid"])
    if op == "inject":
        return engine.inject()
    if op == "observe":
        return engine.observe()
    if op == "status":
        return engine.public(engine.state())
    if op == "restore":
        return w.request_restore(engine)
    if op == "close":
        return engine.close()
    if op == "retire":
        return retire(engine)
    raise c.Refused("bench_root_operation_refused")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watchdog")
    args = parser.parse_args()
    try:
        if args.watchdog:
            w.require_root(args.execute)
            if "UPLINK_TOP10_SANDBOX" in os.environ:
                raise c.Refused("bench_live_worker_refuses_sandbox")
            w.watchdog(load_engine(c.text(args.watchdog, r"[0-9a-f]{12}"), cleanup=True))
        else:
            data = sys.stdin.buffer.read(2_000_001)
            if len(data) > 2_000_000:
                raise c.Refused("bench_request_too_large")
            print(json.dumps(dispatch(c.parse_json(data), execute=args.execute), allow_nan=False))
    except Exception as exc:
        print("bench_worker_refused:" + label(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
