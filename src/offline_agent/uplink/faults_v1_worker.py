"""Opt-in root worker for bounded fault packages; no legacy clearance is accepted.

Uses the existing native identity/protected-service adapter and root persistence
helpers. Each run owns one launchd watchdog, one exact mutation and its rollback.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

if __package__:
    from . import faults_v1 as contract
    from . import real_worker as root_worker
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import faults_v1 as contract  # type: ignore[no-redef,import-not-found]
    import real_worker as root_worker  # type: ignore[no-redef,import-not-found]

BASE = Path("/var/db/uplink-faults-v1")
PAYLOAD = (
    "faults_v1_worker.py",
    "faults_v1.py",
    "real_worker.py",
    "real_protocol.py",
    "real_fleet.py",
    "lab_config.py",
    "lab.local.json",
    "uplink-network",
)
ACTIVE = {"prepared", "armed", "injecting", "injected", "repairing"}


def package_digest(directory: Path) -> str:
    hashes = {}
    for name in PAYLOAD:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("fault_payload_missing_or_symlink")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return contract.digest(hashes)


def run(argv: tuple[str, ...], *, optional: bool = False) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=1, check=False)
    if result.returncode:
        if optional and ("not in table" in result.stderr or "not found" in result.stderr):
            return "ABSENT"
        raise ValueError("fault_read_or_command_failed")
    return result.stdout


def snapshot(spec: contract.FaultSpec) -> dict[str, str]:
    def read(row: tuple[str, tuple[str, ...]]) -> tuple[str, str]:
        key, argv = row
        return key, run(argv, optional=key == "default_ipv6")

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = dict(pool.map(read, contract.snapshot_commands(spec).items()))
    result["target_route"] = run(
        (contract.ROUTE, "-n", "get", "-host", "-ifscope", spec.wifi_interface, spec.target_ipv4)
    )
    # Scope clarification requires only the stable Status token, never uptime.
    status = run((contract.PF, "-s", "info"))
    result["pf_status"] = pf_status_token(status)
    return result


def pf_status_token(status: str) -> str:
    match = re.search(r"^Status: (?:Enabled|Disabled)\b", status, re.M)
    if match is None:
        raise ValueError("pf_status_unparsed")
    return match[0]


def target_ok(spec: contract.FaultSpec) -> bool:
    result = subprocess.run(contract.target_command(spec), capture_output=True, timeout=4)
    return result.returncode == 0 and result.stdout == b"\n204"


def online(value: object) -> None:
    row = contract.obj(value)
    checked = row.get("checked_at")
    if (
        row.get("version") != "uplink-online-v1"
        or row.get("verdict") != "ONLINE"
        or type(checked) not in (int, float)
        or not 0 <= time.time() - float(str(checked)) <= 10
    ):
        raise ValueError("fresh_independent_online_required")
    rows = row.get("evidence")
    ids = {"dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google"}
    if not isinstance(rows, list) or len(rows) != 6:
        raise ValueError("six_online_checks_required")
    evidence = [contract.obj(r) for r in rows]
    if {r.get("id") for r in evidence} != ids or any(
        r.get("ok") is not True or not r.get("path_id") and r.get("kind") != "dns" for r in evidence
    ):
        raise ValueError("six_online_checks_required")


def protected(directory: Path) -> None:
    root_worker.protected(
        root_worker.read(directory / "native.json"), root_worker.native(directory)
    )


def before_validate(spec: contract.FaultSpec, saved: dict[str, str], facts: object) -> None:
    base = {k: saved[k] for k in contract.snapshot_commands(spec)}
    contract.validate_before(spec, base, facts)
    if any(
        line.strip() == "destination: " + spec.target_ipv4
        for line in saved["target_route"].splitlines()
    ):
        raise ValueError("preexisting_test_host_route_refused")
    if not any(
        line.strip() == "interface: " + spec.wifi_interface
        for line in saved["target_route"].splitlines()
    ):
        raise ValueError("target_route_must_use_wifi")
    if spec.fault == "owned_pf_outbound" and (
        not saved["pf_status"].startswith("Status: Enabled")
        or spec.target_ipv4 in run((contract.PF, "-ss"))
    ):
        raise ValueError("pf_disabled_or_target_state_present")


def read_snapshot(directory: Path) -> dict[str, str]:
    value = root_worker.read(directory / "snapshot.json")
    if any(not isinstance(v, str) for v in value.values()):
        raise ValueError("snapshot_strings_required")
    return {k: str(v) for k, v in value.items()}


def remaining(state: dict[str, object]) -> float:
    if state.get("boot_id") != root_worker.boot_id():
        raise ValueError("fault_watchdog_clock_unproven")
    return float(str(state["deadline_mono"])) - root_worker.steady_time()


def require_armed(state: dict[str, object]) -> None:
    if (
        state.get("ready") is not True
        or state.get("lifecycle") not in {"armed", "injected"}
        or remaining(state) < 20
    ):
        raise ValueError("detached_fault_revert_not_ready")


def verify_watchdog(spec: contract.FaultSpec, state: dict[str, object]) -> None:
    pid = state.get("watchdog_pid")
    if type(pid) is not int or pid < 2:
        raise ValueError("detached_fault_pid_unproven")
    status = run(("/bin/launchctl", "print", "system/com.uplink.faults-v1." + spec.run_id))
    if not re.search(r"(?m)^\s*state = running\s*$", status) or not re.search(
        r"(?m)^\s*pid = " + str(pid) + r"\s*$", status
    ):
        raise ValueError("detached_fault_job_not_running")


def restore(directory: Path, state: dict[str, object], reason: str) -> dict[str, object]:
    if state.get("lifecycle") in {"rollback_failed", "restoring"}:
        raise ValueError("fault_rollback_failed_no_retry")
    spec = contract.parse_spec(state["spec"])
    root_worker.audit(directory, "restore", reason)
    state["lifecycle"] = "restoring"
    root_worker.private(directory / "state.json", state)
    try:
        protected(directory)
        saved = read_snapshot(directory)
        current = snapshot(spec)
        command = tuple(cast(list[str], contract.commands(spec)["revert"]))
        if spec.fault == "test_host_blackhole":
            if current["target_route"] != saved["target_route"]:
                if not all(
                    s in current["target_route"]
                    for s in (spec.target_ipv4, _public_fixture("@PUBLIC_IPV4_056@"), "BLACKHOLE")
                ):
                    raise ValueError("foreign_target_route_refused")
                run(command)
        elif spec.fault == "wifi_ipv6_off":
            if current["wifi_info"] != saved["wifi_info"]:
                if "IPv6: Off" not in current["wifi_info"]:
                    raise ValueError("foreign_ipv6_mode_refused")
                run(command)
        else:
            # Always remove our owned block before grading global/status drift.
            # Never fix global drift by reloading the main ruleset or toggling PF.
            run(command)
        diff = contract.restoration(saved, snapshot(spec))
        root_worker.private(directory / "restoration.json", diff)
        if diff["match"] is not True:
            raise ValueError("fault_exact_snapshot_mismatch")
        protected(directory)
        state.update(lifecycle="restored", ready=False, reason=reason)
        if "injected_mono" in state:
            if state.get("boot_id") != root_worker.boot_id():
                raise ValueError("restore_timing_boot_mismatch")
            state["restored_mono"] = root_worker.steady_time()
        root_worker.private(directory / "state.json", state)
        root_worker.audit(directory, "restore", "exact_match")
        return diff
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        reason_label = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z][a-z0-9_]{3,100}", str(exc))
            else type(exc).__name__
        )
        root_worker.private(
            directory / "abort.json",
            {
                "run_id": spec.run_id,
                "reason": reason_label,
                "stop_series": True,
                "operator_notification_required": True,
                "automatic_second_fix_forbidden": True,
            },
        )
        state.update(lifecycle="rollback_failed", ready=False, reason="restore_failed_no_retry")
        root_worker.private(directory / "state.json", state)
        root_worker.audit(directory, "restore", "failed_no_retry")
        raise ValueError("fault_rollback_failed_no_retry") from None


def dispatch(value: object, *, execute: bool = False) -> dict[str, object]:
    row = contract.obj(value)
    if set(row) != {"operation", "spec", "payload"}:
        raise ValueError("fault_request_fields_refused")
    spec = contract.parse_spec(row["spec"])
    payload = contract.obj(row["payload"])
    op = row["operation"]
    expected = {
        "prepare": {"window", "before_facts", "oracle"},
        "arm": set(),
        "inject": set(),
        "apply": {"action"},
        "verify": {"oracle"},
        "restore": set(),
        "retire": set(),
        "observe": set(),
        "measure": set(),
        "receipt": set(),
    }
    if not isinstance(op, str) or op not in expected or set(payload) != expected[op]:
        raise ValueError("fault_operation_refused")
    if not execute:
        return {
            "effects": False,
            "contract": contract.VERSION,
            "commands": contract.commands(spec),
            "operation": op,
        }
    root_worker.require_lab(True)
    if op == "prepare":
        contract.admit_window(
            payload["window"], spec, time.time(), package_digest(Path(__file__).resolve().parent)
        )
        online(payload["oracle"])
    root_worker.secure_directory(BASE)
    root_worker.secure_directory(BASE / "runs")
    directory = BASE / "runs" / spec.run_id
    fd = os.open(BASE / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if op == "prepare":
            source = Path(__file__).resolve().parent
            package = package_digest(source)
            contract.admit_window(payload["window"], spec, time.time(), package)
            online(payload["oracle"])
            if root_worker.registered_watchdogs():
                raise ValueError("legacy_watchdog_present")
            active = BASE / "active.json"
            if active.exists():
                old = root_worker.read(active).get("run_id")
                if not isinstance(old, str) or not re.fullmatch(r"[0-9a-f]{12}", old):
                    raise ValueError("fault_active_run_invalid")
                prior = root_worker.read(BASE / "runs" / old / "state.json")
                if (
                    prior.get("lifecycle") not in {"verified", "restored"}
                    or time.time() - float(str(prior.get("finished_at", 0))) < 120
                ):
                    raise ValueError("prior_fault_run_not_released")
            directory.mkdir(mode=0o700)
            for name in PAYLOAD:
                shutil.copyfile(source / name, directory / name)
                (directory / name).chmod(0o700 if name == "uplink-network" else 0o600)
            if package_digest(directory) != package:
                raise ValueError("fault_payload_copy_mismatch")
            native = root_worker.native(directory)
            if (
                native.get("policy_safe") is not True
                or native.get("interface") != spec.wifi_interface
            ):
                raise ValueError("native_wifi_identity_required")
            saved = snapshot(spec)
            before_validate(spec, saved, payload["before_facts"])
            if not target_ok(spec):
                raise ValueError("target_baseline_failed")
            root_worker.audit(directory, "prepare", "before_state_verified")
            root_worker.private(directory / "native.json", native)
            root_worker.private(directory / "snapshot.json", saved)
            state = {
                "spec": spec.document(),
                "window": payload["window"],
                "package_sha256": package,
                "snapshot_id": contract.digest(saved),
                "lifecycle": "prepared",
                "ready": False,
                "boot_id": root_worker.boot_id(),
                "clock_basis": root_worker.CLOCK_BASIS,
            }
            (directory / "fault.rules").write_text(str(contract.commands(spec)["pf_rule"]))
            (directory / "fault.rules").chmod(0o600)
            if spec.fault == "owned_pf_outbound":
                run((contract.PF, "-n", "-a", spec.anchor, "-f", str(directory / "fault.rules")))
            root_worker.private(directory / "state.json", state)
            root_worker.private(active, {"run_id": spec.run_id})
            return {"prepared": True, "snapshot_id": state["snapshot_id"]}
        state = root_worker.read(directory / "state.json")
        if (
            state.get("spec") != spec.document()
            or root_worker.read(BASE / "active.json").get("run_id") != spec.run_id
        ):
            raise ValueError("fault_state_binding_mismatch")
        if op == "observe":
            return {
                "lifecycle": state["lifecycle"],
                "ready": state["ready"],
                "snapshot_id": state["snapshot_id"],
                "remaining_seconds": remaining(state) if "deadline_mono" in state else None,
                "reason": state.get("reason"),
            }
        if op == "receipt":
            diff_path = directory / "restoration.json"
            timing: dict[str, object] = {
                "run_id": spec.run_id,
                "boot_id": state.get("boot_id"),
                "clock_basis": state.get("clock_basis"),
                "injected_mono": state.get("injected_mono"),
                "restored_mono": state.get("restored_mono"),
                "timing_unproven": not all(k in state for k in ("injected_mono", "restored_mono")),
            }
            return {
                "state": state,
                "exact_diff": root_worker.read(diff_path) if diff_path.exists() else None,
                "timing": timing,
            }
        if op == "measure":
            protected(directory)
            saved, current = read_snapshot(directory), snapshot(spec)
            diff = contract.restoration(saved, current)
            measurement = {
                "checked_at": time.time(),
                "snapshot_id": state["snapshot_id"],
                "lifecycle": state["lifecycle"],
                "remaining_seconds": remaining(state) if "deadline_mono" in state else None,
                "target_online": target_ok(spec),
                "baseline_equal": diff["match"],
                "route_changed": current["target_route"] != saved["target_route"],
                "pf_changed": current["pf_anchor_rules"] != saved["pf_anchor_rules"],
                "protected_services_unchanged": True,
            }
            root_worker.audit(directory, "measure", "pinned_target_and_exact_snapshot")
            root_worker.private(directory / "measurement.json", measurement)
            return measurement
        if op == "restore":
            result = restore(directory, state, "controller_abort")
            state["finished_at"] = time.time()
            root_worker.private(directory / "state.json", state)
            return result
        if op == "retire":
            if state["lifecycle"] not in {"restored", "verified"}:
                raise ValueError("cannot_retire_unrestored_fault_run")
            protected(directory)
            if contract.restoration(read_snapshot(directory), snapshot(spec))["match"] is not True:
                raise ValueError("cannot_retire_snapshot_drift")
            if state.get("watchdog_registered") is True:
                root_worker.audit(directory, "retire", "exact_owned_watchdog")
                run(("/bin/launchctl", "bootout", "system/com.uplink.faults-v1." + spec.run_id))
                state["watchdog_registered"] = False
                root_worker.private(directory / "state.json", state)
            return {"retired": True}
        if op == "arm":
            if state["lifecycle"] != "prepared":
                raise ValueError("fault_cannot_rearm")
            contract.admit_window(state["window"], spec, time.time(), str(state["package_sha256"]))
            root_worker.audit(directory, "arm", "fixed_60_second_deadline")
            state.update(
                lifecycle="armed", deadline_mono=root_worker.steady_time() + contract.SECONDS
            )
            root_worker.private(directory / "state.json", state)
            job = {
                "Label": "com.uplink.faults-v1." + spec.run_id,
                "ProgramArguments": [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    str(directory / "faults_v1_worker.py"),
                    "--watchdog",
                    spec.run_id,
                ],
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "StandardOutPath": "/dev/null",
                "StandardErrorPath": "/dev/null",
            }
            with (directory / "watchdog.plist").open("xb") as f:
                plistlib.dump(job, f)
            run(("/bin/launchctl", "bootstrap", "system", str(directory / "watchdog.plist")))
            state["watchdog_registered"] = True
            root_worker.private(directory / "state.json", state)
            return {"armed": True, "ready": False, "seconds": contract.SECONDS}
        if op == "inject":
            require_armed(state)
            verify_watchdog(spec, state)
            if state["lifecycle"] != "armed":
                raise ValueError("one_fault_attempt_only")
            contract.admit_window(state["window"], spec, time.time(), str(state["package_sha256"]))
            protected(directory)
            if contract.restoration(read_snapshot(directory), snapshot(spec))["match"] is not True:
                raise ValueError("preinject_snapshot_drift")
            root_worker.audit(directory, "inject", spec.fault)
            state["lifecycle"] = "injecting"
            state["injected_mono"] = root_worker.steady_time()
            root_worker.private(directory / "state.json", state)
            argv = list(cast(list[str], contract.commands(spec)["inject"]))
            if spec.fault == "owned_pf_outbound":
                argv[-1] = str(directory / "fault.rules")
            run(tuple(str(a) for a in argv))
            state["lifecycle"] = "injected"
            root_worker.private(directory / "state.json", state)
            if target_ok(spec):
                restore(directory, state, "fault_not_effective")
                raise ValueError("fault_target_not_broken_no_credit")
            return {"fault_effective": True, "target_online": False}
        if op == "apply":
            require_armed(state)
            verify_watchdog(spec, state)
            if state["lifecycle"] != "injected":
                raise ValueError("fault_apply_requires_injection")
            contract.parse_action(payload["action"], spec, str(state["snapshot_id"]))
            root_worker.audit(directory, "apply", "validated_matching_snapshot_action")
            state["applied_action"] = payload["action"]
            root_worker.private(directory / "state.json", state)
            return restore(directory, state, "typed_agent_snapshot_repair")
        if state["lifecycle"] != "restored" or state.get("reason") != "typed_agent_snapshot_repair":
            raise ValueError("watchdog_or_aborted_run_earns_no_repair_credit")
        online(payload["oracle"])
        protected(directory)
        diff = contract.restoration(read_snapshot(directory), snapshot(spec))
        if diff["match"] is not True or not target_ok(spec):
            raise ValueError("fault_final_verification_failed")
        root_worker.audit(directory, "verify", "six_online_target_and_exact_snapshot")
        state.update(lifecycle="verified", finished_at=time.time())
        root_worker.private(directory / "state.json", state)
        run(("/bin/launchctl", "bootout", "system/com.uplink.faults-v1." + spec.run_id))
        state["watchdog_registered"] = False
        root_worker.private(directory / "state.json", state)
        return {"verified": True, "exact_diff": diff, "target_online": True}
    finally:
        os.close(fd)


def watchdog(run_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        raise ValueError("invalid_fault_run_id")
    root_worker.require_lab(True)
    directory = BASE / "runs" / run_id
    while True:
        fd = os.open(BASE / "lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = root_worker.read(directory / "state.json")
            if state["lifecycle"] not in ACTIVE:
                return
            try:
                seconds = remaining(state)
            except ValueError:
                seconds = -1
            if seconds <= 0:
                restore(directory, state, "detached_deadline_revert")
                state["finished_at"] = time.time()
                root_worker.private(directory / "state.json", state)
                return
            state["ready"] = True
            state["watchdog_pid"] = os.getpid()
            root_worker.private(directory / "state.json", state)
        finally:
            os.close(fd)
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watchdog")
    args = parser.parse_args()
    value: object = None
    try:
        if args.watchdog:
            watchdog(args.watchdog)
        else:
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768:
                raise ValueError("fault_request_size_refused")
            value = json.loads(raw)
            print(json.dumps(dispatch(value, execute=args.execute)))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Preserve closed refusal labels without echoing raw payloads or OS diagnostics.
        label = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z][a-z0-9_]{3,100}", str(exc))
            else type(exc).__name__
        )
        if args.execute and isinstance(value, dict) and isinstance(value.get("spec"), dict):
            run_id = value["spec"].get("run_id")
            if isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{12}", run_id):
                directory = BASE / "runs" / run_id
                if directory.exists() and not directory.is_symlink():
                    root_worker.secure_directory(directory)
                    root_worker.audit(directory, "refused", label)
        parser.exit(2, "fault_package_refused:" + label + "\n")


if __name__ == "__main__":
    main()
