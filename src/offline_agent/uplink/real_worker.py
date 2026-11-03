"""Root-owned, stdlib-only launchd recovery worker. Never import the model/controller here.

After prepare, both this file and its native adapter live under a root-owned 0700 run
folder. No model output, user path, raw shell command or credential enters this protocol.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import cast

if __package__:
    from .real_fleet import (
        FLEET_GETTERS,
        TRIAL_INTERVAL_SECONDS,
        approved_serial,
        require_exception,
        settings_digest,
    )
    from .real_protocol import request, validate_request
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from real_fleet import (  # type: ignore[import-not-found,no-redef]
        FLEET_GETTERS,
        TRIAL_INTERVAL_SECONDS,
        approved_serial,
        require_exception,
        settings_digest,
    )
    from real_protocol import request, validate_request  # type: ignore[import-not-found,no-redef]

BASE = Path("/var/db/uplink-real")
ACTIVE = {"prepared", "armed", "injecting", "injected", "applying"}
CLOCK_BASIS = "posix-monotonic-v1"


def steady_time() -> float:
    # Python 3.9 on macOS gives time.monotonic() a per-process origin. Persisted
    # watchdog deadlines require the system clock shared by every root process.
    try:
        value = time.clock_gettime(time.CLOCK_MONOTONIC)
    except (AttributeError, OSError) as exc:
        raise ValueError("shared_monotonic_clock_unavailable") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("shared_monotonic_clock_invalid")
    return value


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("object_required")
    return cast(dict[str, object], value)


def require_lab(execute: bool) -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise ValueError("real_effects_forbidden_in_tests")
    if not execute or os.geteuid() != 0 or platform.system() != "Darwin":
        raise ValueError("explicit_root_lab_required")
    identity = obj(json.loads(run((str(Path(__file__).with_name("uplink-network")), "identity"))))
    if identity.get("serial") != approved_serial():
        raise ValueError("managed_device_refused")


def secure_directory(path: Path) -> None:
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("private_root_directory_required")


def private(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read(path: Path) -> dict[str, object]:
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("private_root_file_required")
    return obj(json.loads(path.read_text()))


def audit(directory: Path, operation: str, reason: str = "intent") -> None:
    fd = os.open(
        directory / "audit.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        stream.write(
            json.dumps({"ts": time.time(), "operation": operation, "reason": reason}) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def run(argv: tuple[str, ...], *, timeout: float = 1, allow_failure: bool = False) -> str:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
    )
    if result.returncode and not allow_failure:
        raise ValueError("fixed_native_command_failed")
    return result.stdout


@lru_cache(maxsize=1)
def boot_id() -> str:
    value = run(("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")).strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", value):
        raise ValueError("boot_identity_unavailable")
    return hashlib.sha256(value.lower().encode()).hexdigest()


def clock_matches(state: dict[str, object]) -> bool:
    return state.get("clock_basis") == CLOCK_BASIS and state.get("boot_id") == boot_id()


def deadline_remaining(state: dict[str, object]) -> float:
    deadline = state.get("deadline_mono")
    armed_at = state.get("armed_at_mono")
    if (
        not clock_matches(state)
        or type(deadline) not in (int, float)
        or not math.isfinite(cast(float, deadline))
        or type(armed_at) not in (int, float)
        or not math.isfinite(cast(float, armed_at))
        or not 0 < cast(float, deadline) - cast(float, armed_at) <= 60
    ):
        raise ValueError("watchdog_clock_unproven")
    now = steady_time()
    if cast(float, armed_at) > now:
        raise ValueError("watchdog_clock_unproven")
    return cast(float, deadline) - now


def require_cooldown(directory: Path, state: dict[str, object]) -> None:
    if (
        state.get("lifecycle") not in {"reverted", "complete"}
        or state.get("timing_unproven") is True
    ):
        raise ValueError("prior_run_unresolved_no_takeover")
    current_boot = boot_id()
    now = steady_time()
    if (
        state.get("finished_clock_basis") == CLOCK_BASIS
        and state.get("finished_boot_id") == current_boot
    ):
        started = state.get("finished_at_mono")
    else:
        # A legacy or previous-boot timestamp is never interpreted in this clock.
        # Hold a fresh full quiet interval without rewriting the original receipt.
        fleet = state.get("fleet_check")
        if not isinstance(fleet, dict) or fleet.get("match") is not True:
            raise ValueError("prior_terminal_restoration_unproven")
        path = directory / "cooldown-clock.json"
        barrier = read(path) if path.exists() else None
        if barrier is None or barrier.get("boot_id") != current_boot:
            barrier = {"clock_basis": CLOCK_BASIS, "boot_id": current_boot, "started_at_mono": now}
            audit(directory, "cooldown", "new_clock_requires_full_quiet_interval")
            private(path, barrier)
            raise ValueError("trial_cooldown_restarted_after_clock_upgrade")
        if barrier.get("clock_basis") != CLOCK_BASIS:
            raise ValueError("prior_trial_timing_unproven")
        started = barrier.get("started_at_mono")
    if (
        type(started) not in (int, float)
        or not math.isfinite(cast(float, started))
        or not 0 <= cast(float, started) <= now
    ):
        raise ValueError("prior_trial_timing_unproven")
    if now - cast(float, started) < TRIAL_INTERVAL_SECONDS:
        raise ValueError("trial_cooldown_active")


def native(directory: Path, operation: str = "read") -> dict[str, object]:
    args: tuple[str, ...] = (str(directory / "uplink-network"), operation)
    if operation != "read":
        args += (str(directory / "snapshot.json"),)
    return obj(json.loads(run(args)))


def registered_watchdogs() -> tuple[str, ...]:
    names = []
    for line in run(("/bin/launchctl", "list"), timeout=2).splitlines():
        fields = line.split()
        if not fields or not fields[-1].startswith("com.uplink.real."):
            continue
        label = fields[-1]
        if not re.fullmatch(r"com\.uplink\.real\.[0-9a-f]{12}", label):
            raise ValueError("unrecognized_watchdog_job")
        names.append(label.removeprefix("com.uplink.real."))
    return tuple(sorted(names))


def retire_finished_watchdogs() -> dict[str, object]:
    """Unload only this helper's verified terminal jobs; keep the audit files."""
    names = registered_watchdogs()
    if len(names) > 16:
        raise ValueError("watchdog_retirement_batch_limit")
    rows = []
    # Validate the entire batch before stopping any job.
    for name in names:
        directory = BASE / "runs" / name
        state = read(directory / "state.json")
        fleet = state.get("fleet_check")
        if (
            state.get("lifecycle") not in {"reverted", "complete"}
            or state.get("watchdog") not in {"reverted", "disarmed"}
            or not isinstance(fleet, dict)
            or fleet.get("match") is not True
        ):
            raise ValueError("watchdog_job_not_verified_finished")
        require_exception(approved_serial(), state.get("fault"), state.get("authorization"))
        rows.append((directory, state))
    for directory, state in rows:
        audit(directory, "retire", "verified_terminal_job")
        run(("/bin/launchctl", "bootout", "system/com.uplink.real." + directory.name), timeout=2)
        if directory.name in registered_watchdogs():
            raise ValueError("watchdog_retirement_unconfirmed")
        state.update(job_retired=True, job_retired_at=time.time())
        private(directory / "state.json", state)
        audit(directory, "retire", "job_absent")
    remaining = registered_watchdogs()
    if remaining:
        raise ValueError("watchdog_retirement_unconfirmed")
    return {"job_retired": True, "retired_count": len(rows), "registered_jobs": 0}


def interface(snapshot: dict[str, object]) -> str:
    value = snapshot.get("interface")
    if not isinstance(value, str) or not re.fullmatch(r"en[0-9]+", value) or value == "en7":
        raise ValueError("only_wifi_interface_allowed")
    return value


def power(snapshot: dict[str, object]) -> bool:
    text = run(("/usr/sbin/networksetup", "-getairportpower", interface(snapshot))).strip()
    if not text.endswith((": On", ": Off")):
        raise ValueError("wifi_power_unavailable")
    return text.endswith(": On")


def set_power(snapshot: dict[str, object], enabled: bool) -> None:
    run(
        (
            "/usr/sbin/networksetup",
            "-setairportpower",
            interface(snapshot),
            "on" if enabled else "off",
        )
    )


def protected(snapshot: dict[str, object], current: dict[str, object]) -> None:
    if any(
        snapshot.get(k) != current.get(k)
        for k in (
            "serial",
            "service_id",
            "interface",
            "protected_sha256",
            "location_sha256",
            "service_order_sha256",
        )
    ):
        raise ValueError("wifi_identity_or_protected_service_changed")


def fleet_settings() -> dict[str, str]:
    values = {}
    for getter in FLEET_GETTERS:
        try:
            values[getter] = run(("/usr/sbin/networksetup", getter, "Wi-Fi"))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise ValueError("fleet_settings_unavailable_" + getter.removeprefix("-")) from exc
    return values


def fleet_check(directory: Path) -> dict[str, object]:
    saved = obj(read(directory / "fleet-snapshot.json").get("settings"))
    if set(saved) != set(FLEET_GETTERS) or any(not isinstance(v, str) for v in saved.values()):
        raise ValueError("complete_fleet_settings_required")
    current = fleet_settings()
    matches = {key: current[key] == saved[key] for key in FLEET_GETTERS}
    return {
        "match": all(matches.values()),
        "fields": matches,
        "sha256": settings_digest(current),
        "checked_at": time.time(),
    }


def stop_run(directory: Path, state: dict[str, object], reason: str) -> None:
    audit(directory, "abort", reason)
    state.update(lifecycle="rollback_failed", watchdog="failed", ready=False, reason=reason)
    private(directory / "state.json", state)
    raise ValueError("restore_failed_no_retry")


def mark_finished(state: dict[str, object]) -> None:
    injected = state.get("injected_at_mono")
    try:
        finished: float | None = steady_time()
        current_boot: str | None = boot_id()
    except ValueError:
        finished, current_boot = None, None
        state["timing_unproven"] = True
    state.update(
        finished_at=time.time(),
        finished_at_mono=finished,
        finished_clock_basis=CLOCK_BASIS if finished is not None else None,
        finished_boot_id=current_boot,
    )
    state["baseline_restored_after_s"] = (
        finished - cast(float, injected)
        if finished is not None
        and current_boot is not None
        and state.get("clock_basis") == CLOCK_BASIS
        and state.get("boot_id") == current_boot
        and type(injected) in (int, float)
        and math.isfinite(cast(float, injected))
        and 0 <= cast(float, injected) <= finished
        else None
    )
    if injected is not None and state["baseline_restored_after_s"] is None:
        state["timing_unproven"] = True


def observe(directory: Path, state: dict[str, object]) -> dict[str, object]:
    snapshot = read(directory / "snapshot.json")
    current = native(directory)
    try:
        protected(snapshot, current)
    except ValueError:
        stop_run(directory, state, "wifi_identity_or_protected_service_changed")
    up = power(snapshot)
    route = run(("/sbin/route", "-n", "get", "default"), allow_failure=True)
    match = re.search(r"interface:\s*(en[0-9]+)", route)
    flags = run(("/sbin/ifconfig", "-a"))
    interfaces = [
        {"name": name, "up": "UP" in bits.split(",")}
        for name, bits in re.findall(r"(?m)^([a-z][a-z0-9]{0,14}): flags=\d+<([^>]+)>", flags)
    ]
    if not interfaces:
        raise ValueError("interface_state_unavailable")
    dns_changed = snapshot["dns_sha256"] != current["dns_sha256"]
    proxy_changed = snapshot["proxies_sha256"] != current["proxies_sha256"]
    fleet = state.get("fleet_check")
    return {
        "wifi_up": up,
        "wifi_interface": interface(snapshot),
        "active_interface": match[1] if match else None,
        "interfaces": interfaces,
        "wifi_default_route": bool(match and match[1] == interface(snapshot)),
        "config": {
            "dns": current["dns_sha256"],
            "proxy": current["proxies_sha256"],
            "wifi_power": up,
        },
        "dns_changed": dns_changed,
        "proxy_changed": proxy_changed,
        "baseline_equal": not dns_changed and not proxy_changed and up == snapshot["wifi_up"],
        "protected_services_unchanged": True,
        "watchdog": state.get("watchdog", "unarmed"),
        "watchdog_ready": state.get("ready", False),
        "deadline": state.get("deadline", 0),
        "fleet_check": fleet,
        "fleet_diff_match": fleet.get("match") if isinstance(fleet, dict) else None,
        "restore_duration_ms": state.get("restore_duration_ms"),
        "finished_at": state.get("finished_at"),
        "injected_at": state.get("injected_at"),
        "baseline_restored_after_s": state.get("baseline_restored_after_s"),
        "reason": state.get("reason"),
        "timing_unproven": state.get("timing_unproven", False),
        "clock_basis": state.get("clock_basis"),
        "authorization": state.get("authorization"),
    }


def require_armed(directory: Path, state: dict[str, object], *, minimum: float = 0) -> None:
    if state.get("watchdog") != "armed" or state.get("ready") is not True:
        raise ValueError("independent_watchdog_not_ready")
    if deadline_remaining(state) <= minimum:
        raise ValueError("watchdog_deadline_reached")
    # launchd remains the owner; a recorded PID alone is not readiness evidence.
    live = run(("/bin/launchctl", "print", "system/com.uplink.real." + directory.name))
    pid = state.get("watchdog_pid")
    if (
        type(pid) is not int
        or pid < 1
        or not re.search(r"(?m)^\s*state = running\s*$", live)
        or not re.search(r"(?m)^\s*pid = " + str(pid) + r"\s*$", live)
    ):
        raise ValueError("watchdog_process_not_running")


def restore(directory: Path, state: dict[str, object], reason: str) -> dict[str, object]:
    if state.get("lifecycle") in {"rollback_started", "rollback_failed", "reverted"}:
        raise ValueError("rollback_already_attempted_no_retry")
    audit(directory, "restore", reason)
    state.update(lifecycle="rollback_started", ready=False)
    private(directory / "state.json", state)
    started = time.perf_counter()
    try:
        snapshot = read(directory / "snapshot.json")
        current = native(directory)
        protected(snapshot, current)
        power_changed = power(snapshot) != snapshot["wifi_up"]
        if power_changed and state.get("authorization") is not None:
            raise ValueError("wifi_power_drift_outside_approved_scope")
        if any(snapshot[k] != current[k] for k in ("dns_sha256", "proxies_sha256")):
            native(directory, "restore")
        if power_changed:
            set_power(snapshot, snapshot["wifi_up"] is True)
        fleet = fleet_check(directory)
        state["fleet_check"] = fleet
        if fleet["match"] is not True:
            raise ValueError("fleet_settings_diff_mismatch")
        state.update(
            restore_duration_ms=(time.perf_counter() - started) * 1000,
        )
        mark_finished(state)
        state.update(watchdog="reverted", lifecycle="reverted")
        result = observe(directory, state)
        if result["baseline_equal"] is not True:
            raise ValueError("snapshot_restore_mismatch")
        audit(directory, "restore", "verified")
        private(directory / "state.json", state)
        return result
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if not re.fullmatch(r"[a-zA-Z0-9_]+", reason):
            reason = "restore_failed_no_retry"
        state.update(
            watchdog="failed",
            lifecycle="rollback_failed",
            reason=reason,
            baseline_restored_after_s=None,
        )
        private(directory / "state.json", state)
        audit(directory, "restore", "failed_no_retry")
        raise ValueError("restore_failed_no_retry") from None


def prepare(directory: Path, payload: dict[str, object]) -> dict[str, object]:
    active_path = BASE / "active.json"
    if active_path.exists():
        previous = read(active_path).get("run_id")
        if not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{12}", previous):
            raise ValueError("invalid_active_run")
        previous_directory = BASE / "runs" / previous
        previous_state = read(previous_directory / "state.json")
        require_cooldown(previous_directory, previous_state)
    retire_finished_watchdogs()
    directory.mkdir(mode=0o700)  # Never overwrite an existing run or snapshot.
    source = Path(__file__).resolve().parent
    for name in (
        "real_worker.py",
        "real_protocol.py",
        "real_fleet.py",
        "lab_config.py",
        "lab.local.json",
        "uplink-network",
    ):
        item = source / name
        if item.is_symlink() or not item.is_file():
            raise ValueError("staged_helper_required")
        target = directory / name
        shutil.copyfile(item, target)
        target.chmod(0o700 if name == "uplink-network" else 0o600)
    snapshot = native(directory)
    if snapshot.get("policy_safe") is not True:
        raise ValueError("managed_or_complex_proxy_refused")
    enrollment = run(("/usr/bin/profiles", "status", "-type", "enrollment"))
    managed = "MDM enrollment: No" not in enrollment or "Enrolled via DEP: Yes" in enrollment
    authorization = payload.get("authorization")
    if managed or authorization is not None:
        authorization = require_exception(
            snapshot.get("serial"), payload.get("fault"), authorization
        )
    snapshot["wifi_up"] = power(snapshot)
    if snapshot["wifi_up"] is not True:
        raise ValueError("initial_wifi_power_required")
    # Dynamic proxy state is not rollback configuration. It may contain unrelated scopes.
    snapshot.pop("effective_proxy", None)
    audit(directory, "prepare", "intent")
    private(directory / "snapshot.json", snapshot)
    settings = fleet_settings()
    private(
        directory / "fleet-snapshot.json",
        {"settings": settings, "sha256": settings_digest(settings)},
    )
    state: dict[str, object] = {
        "lifecycle": "prepared",
        "watchdog": "unarmed",
        "clock_basis": CLOCK_BASIS,
        "boot_id": boot_id(),
        **payload,
        "authorization": authorization,
        "fleet_check": {
            "match": True,
            "fields": {key: True for key in FLEET_GETTERS},
            "sha256": settings_digest(settings),
            "checked_at": time.time(),
        },
    }
    audit(directory, "prepare", "snapshot_persisted")
    private(directory / "state.json", state)
    private(active_path, {"run_id": directory.name})
    return observe(directory, state)


def arm(directory: Path, state: dict[str, object]) -> dict[str, object]:
    if state.get("lifecycle") != "prepared":
        raise ValueError("cannot_rearm_or_extend_deadline")
    if not clock_matches(state):
        raise ValueError("watchdog_clock_unproven")
    if registered_watchdogs():
        raise ValueError("prior_watchdog_jobs_require_retirement")
    seconds = int(str(state["seconds"]))
    begun = steady_time()
    audit(directory, "arm")
    state.update(
        lifecycle="armed",
        watchdog="armed",
        ready=False,
        deadline=time.time() + seconds,
        armed_at_mono=begun,
        deadline_mono=begun + seconds,
    )
    private(directory / "state.json", state)
    label = "com.uplink.real." + directory.name
    job = {
        "Label": label,
        "ProgramArguments": [
            "/usr/bin/python3",
            "-I",
            str(directory / "real_worker.py"),
            "--execute",
            "--watchdog",
            directory.name,
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    plist = directory / "watchdog.plist"
    with plist.open("xb") as stream:
        plistlib.dump(job, stream)
    plist.chmod(0o600)
    run(("/bin/launchctl", "bootstrap", "system", str(plist)))
    if registered_watchdogs() != (directory.name,):
        raise ValueError("exactly_one_watchdog_required")
    return state


def dispatch(value: object, *, execute: bool) -> dict[str, object]:
    command = validate_request(value)
    if not execute:
        return {"effects": False, "request": command, "target": "lab_wifi_only"}
    require_lab(execute)
    secure_directory(BASE)
    secure_directory(BASE / "runs")
    directory = BASE / "runs" / str(command["run_id"])
    lock = os.open(BASE / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        op = str(command["operation"])
        payload = obj(command["payload"])
        if op == "prepare":
            return prepare(directory, payload)
        if not directory.is_dir():
            raise ValueError("unknown_run")
        secure_directory(directory)
        state = read(directory / "state.json")
        if read(BASE / "active.json").get("run_id") != directory.name:
            raise ValueError("not_the_active_run")
        if op == "arm":
            arm(directory, state)
        elif op == "observe":
            return observe(directory, state)
        elif op == "retire":
            return retire_finished_watchdogs()
        elif op == "restore":
            return restore(directory, state, "controller_aborted")
        elif op == "disarm":
            require_armed(directory, state)
            if not 0 <= time.time() - float(str(payload["verified_at"])) <= 10:
                raise ValueError("fresh_oracle_required")
            if observe(directory, state)["baseline_equal"] is not True:
                stop_run(directory, state, "cannot_disarm_unrestored_configuration")
            fleet = fleet_check(directory)
            if fleet["match"] is not True:
                state["fleet_check"] = fleet
                stop_run(directory, state, "fleet_settings_diff_mismatch")
            audit(directory, "disarm", "fresh_oracle_and_exact_snapshot")
            state.update(
                watchdog="disarmed",
                lifecycle="complete",
                ready=False,
                fleet_check=fleet,
            )
            mark_finished(state)
            private(directory / "state.json", state)
            return observe(directory, state)
        elif op in {"inject", "apply"}:
            require_armed(directory, state, minimum=4)
            if op == "inject" and state.get("lifecycle") != "armed":
                raise ValueError("one_fault_per_trial")
            if op == "apply" and state.get("lifecycle") not in {"injected", "applying"}:
                raise ValueError("apply_requires_injected_trial")
            snapshot = read(directory / "snapshot.json")
            try:
                protected(snapshot, native(directory))
            except ValueError:
                stop_run(directory, state, "wifi_identity_or_protected_service_changed")
            action = str(state["fault"]) if op == "inject" else str(payload["tool"])
            if state.get("authorization") is not None and action not in {
                "wrong_dns",
                "stale_proxy",
                "dns_and_proxy",
                "set_dns",
                "restore_proxy",
            }:
                raise ValueError("managed_exception_dns_proxy_only")
            audit(directory, op, action)
            state["lifecycle"] = "injecting" if op == "inject" else "applying"
            state["fleet_check"] = None
            if op == "inject":
                state.update(injected_at=time.time(), injected_at_mono=steady_time())
            private(directory / "state.json", state)
            if action == "wifi_power_off":
                set_power(snapshot, False)
            elif action == "cycle_interface":
                # The only power recovery is restoring the captured on state, never cycling OOB.
                set_power(snapshot, True)
            else:
                native(directory, action)
            state["lifecycle"] = "injected"
            private(directory / "state.json", state)
            return observe(directory, state)
        else:
            raise ValueError("watchdog_has_dedicated_entry")
    finally:
        os.close(lock)
    # Release the lock so the detached worker can prove readiness.
    until = steady_time() + 2
    while steady_time() < until:
        state = read(directory / "state.json")
        if state.get("ready") is True:
            return observe(directory, state)
        time.sleep(0.05)
    raise ValueError("watchdog_readiness_unproven_no_injection")


def watchdog(run_id: str) -> None:
    request("watchdog", run_id)
    require_lab(True)
    directory = BASE / "runs" / run_id
    secure_directory(directory)
    while True:
        lock = os.open(BASE / "lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read(directory / "state.json")
            if state.get("lifecycle") not in ACTIVE:
                return
            try:
                remaining = deadline_remaining(state)
            except ValueError:
                state.update(timing_unproven=True, reason="watchdog_clock_unproven")
                restore(directory, state, "watchdog_clock_unproven")
                return
            if remaining <= 0:
                restore(directory, state, "deadline_expired")
                return
            if not state.get("ready") or state.get("watchdog_pid") != os.getpid():
                state["ready"] = True
                state["watchdog_pid"] = os.getpid()
                audit(directory, "watchdog", "detached_ready")
                private(directory / "state.json", state)
        finally:
            os.close(lock)
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watchdog")
    args = parser.parse_args()
    try:
        if args.watchdog:
            if not args.execute:
                raise ValueError("explicit_watchdog_execution_required")
            watchdog(args.watchdog)
            return
        value = json.loads(sys.stdin.read(16385))
        result = dispatch(value, execute=args.execute)
        print(json.dumps({"ok": True, "result": result}))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({"ok": False, "error": reason}))
        if args.watchdog:
            # A handled rollback/refusal must not activate launchd's crash restart.
            # Unexpected process death can restart, but the persisted deadline/attempt
            # marker still forbids extending the trial or retrying a started rollback.
            return
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
