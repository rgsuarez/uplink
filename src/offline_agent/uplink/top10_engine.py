"""One audited trial state machine. No model/controller dependency enters root code."""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from . import top10_contract as c
    from . import top10_injectors as plans
    from .top10_kernel import Kernel, MacKernel, SandboxKernel
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]
    import top10_injectors as plans  # type: ignore[no-redef,import-not-found]
    from top10_kernel import Kernel, MacKernel, SandboxKernel  # type: ignore[no-redef,import-not-found]


def steady() -> float:
    try:
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    except (AttributeError, OSError) as exc:
        raise c.Refused("top10_shared_monotonic_clock_unavailable") from exc


class Engine:
    def __init__(self, directory: Path, kernel: Kernel) -> None:
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise c.Refused("top10_run_symlink_refused")
        self.directory, self.kernel = directory, kernel

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        descriptor = os.open(self.directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def state(self) -> dict[str, object]:
        return c.read(self.directory / "state.json", private=True)

    def snapshot(self) -> dict[str, object]:
        value = c.read(self.directory / "snapshot.json", private=True)
        if c.digest(value) != self.state().get("snapshot_id"):
            raise c.Refused("top10_snapshot_changed")
        return value

    def now(self) -> float:
        if self.kernel.sandbox:
            clock = c.read(self.directory / "clock.json", private=True)
            base = c.number(clock.get("now"), 0, 1e12)
            anchor = c.number(clock.get("anchor"), 0, 1e12)
            scale = c.number(clock.get("scale"), 1, 50)
            return base + max(0.0, steady() - anchor) * scale
        return steady()

    def remaining(self, state: dict[str, object]) -> float:
        return c.number(state.get("deadline"), 0, 1e12) - self.now()

    @contextlib.contextmanager
    def mutation_budget(self, state: dict[str, object]) -> Iterator[None]:
        """Release the mutation lock before the detached watchdog deadline.

        Probes run outside this lock. Each primitive shares one aggregate deadline;
        a sequence cannot renew its budget on every subprocess invocation.
        """
        if isinstance(self.kernel, MacKernel):
            self.kernel.command_deadline = time.monotonic() + min(
                12.0, max(0.0, self.remaining(state) - 1.0)
            )
        try:
            yield
        finally:
            if isinstance(self.kernel, MacKernel):
                self.kernel.command_deadline = None

    def prepare(
        self, run_id: str, fault_id: str, *, binding: dict[str, object] | None = None
    ) -> dict[str, object]:
        c.text(run_id, r"[0-9a-f]{12}")
        if not self.kernel.sandbox:
            c.require_bench_fault(fault_id)
        if fault_id not in c.FAULT_IDS or (self.directory / "state.json").exists():
            raise c.Refused("top10_new_registered_trial_required")
        c.audit(self.directory, "prepare_intent", {"run_id": run_id, "fault_id": fault_id})
        snapshot = self.kernel.capture()
        c.save(self.directory / "snapshot.json", snapshot, exclusive=True)
        state: dict[str, object] = {
            "version": c.VERSION,
            "run_id": run_id,
            "fault_id": fault_id,
            "lifecycle": "reserved",
            "snapshot_id": c.digest(snapshot),
            "ready": False,
            "watchdog_pid": None,
            "watchdog_restore": False,
            "controller_cleanup": False,
            "model_actions": [],
            "completed_model_actions": [],
            "binding": binding or {},
            "evidence_kind": "FAKE_KERNEL" if self.kernel.sandbox else "lab_wifi",
            "created_at": time.time(),
            "last_reason": None,
            "portal_port": 0,
        }
        c.save(self.directory / "state.json", state, exclusive=True)
        try:
            self.kernel.admit(snapshot, fault_id)
            facts = self.kernel.facts(snapshot)
            c.save(self.directory / "baseline-facts.json", facts, exclusive=True)
            if not c.baseline_ok(facts):
                raise c.Refused("top10_baseline_measurements_not_healthy")
            c.transition(self.directory, state, "snapshotted", "safe_baseline_captured")
        except Exception as exc:
            c.transition(self.directory, state, "closed", "prepare_refused_no_injection")
            c.audit(self.directory, "failure", {"reason": label(exc), "phase": "prepare"})
            raise
        return self.public(state)

    def arm(self) -> dict[str, object]:
        with self.locked():
            state = self.state()
            state["armed_at"] = self.now()
            state["deadline"] = self.now() + c.WATCHDOG_SECONDS
            c.transition(self.directory, state, "armed", "detached_watchdog_requested")
            return self.public(state)

    def mark_ready(self, pid: int, *, portal_port: int = 0) -> None:
        if pid < 2 or pid == c.SERVER_PID:
            raise c.Refused("top10_watchdog_pid_refused")
        with self.locked():
            state = self.state()
            if state["lifecycle"] != "armed" or self.remaining(state) <= 5:
                raise c.Refused("top10_watchdog_ready_too_late")
            c.audit(
                self.directory,
                "watchdog_ready",
                {"pid": pid, "deadline": state["deadline"], "portal_port": portal_port},
            )
            state.update(ready=True, watchdog_pid=pid, portal_port=portal_port)
            c.save(self.directory / "state.json", state)

    def require_active(self, state: dict[str, object], *, minimum: float = 0) -> None:
        if (
            state.get("lifecycle") not in {"armed", "fault_verified", "deciding"}
            or state.get("ready") is not True
            or type(state.get("watchdog_pid")) is not int
            or state.get("watchdog_pid") == c.SERVER_PID
            or state.get("watchdog_restore") is not False
            or state.get("controller_cleanup") is not False
            or self.remaining(state) < minimum
        ):
            raise c.Refused("top10_detached_restore_not_ready_or_expired")

    def inject(self) -> dict[str, object]:
        previous = self.state()
        self.require_active(previous, minimum=20)
        snapshot = self.snapshot()
        self._port(previous)
        # Admission and readback must never delay the independent restore lock.
        self.kernel.admit(snapshot, str(previous["fault_id"]))
        if not self.kernel.facts(snapshot)["protected_services_unchanged"]:
            raise c.Refused("top10_management_or_protected_drift")
        with self.locked():
            state = self.state()
            if state != previous or state["lifecycle"] != "armed":
                raise c.Refused("top10_injection_not_repeatable")
            self.require_active(state, minimum=20)
            self._port(state)
            c.transition(self.directory, state, "injecting", "exact_registered_injection")
            try:
                with self.mutation_budget(state):
                    plans.inject(self.kernel, str(state["fault_id"]), snapshot)
            except Exception as exc:
                state["last_reason"] = label(exc)
                c.audit(self.directory, "injection_failure", {"reason": label(exc)})
                c.save(self.directory / "state.json", state)
                raise
        # Measurement does not hold the mutation lock across external probe latency.
        return self.verify_fault()

    def verify_fault(self) -> dict[str, object]:
        previous = self.state()
        self._port(previous)
        facts = self.kernel.facts(self.snapshot())
        effective = self.kernel.negative_control(str(previous["fault_id"]), facts)
        with self.locked():
            state = self.state()
            if (
                state != previous
                or state["lifecycle"] not in {"injecting", "fault_verified", "deciding"}
                or self.remaining(state) <= 0
            ):
                raise c.Refused("top10_fault_measurement_expired")
            c.audit(self.directory, "negative_control", {"effective": effective, "facts": facts})
            if not effective:
                raise c.Refused("top10_fault_effect_not_proven")
            if state["lifecycle"] == "injecting":
                c.transition(
                    self.directory, state, "fault_verified", "independent_negative_control"
                )
            return self.public(state) | {
                "fault_effective": True,
                "facts": facts,
                "checked_at": time.time(),
            }

    def apply(self, action: str, snapshot_id: str) -> dict[str, object]:
        if action not in c.REPAIRS:
            raise c.Refused("top10_closed_snapshot_action_required")
        if not self.kernel.sandbox and action in c.HELD_REPAIRS:
            raise c.Refused("top10_radio_rejoin_or_saved_network_action_held")
        with self.locked():
            state = self.state()
            self.require_active(state, minimum=18)
            if (
                state["lifecycle"] not in {"fault_verified", "deciding"}
                or snapshot_id != state["snapshot_id"]
            ):
                raise c.Refused("top10_action_snapshot_mismatch")
            actions = state.get("model_actions")
            if (
                not isinstance(actions, list)
                or action in actions
                or len(actions) >= c.MAX_DECISIONS
            ):
                raise c.Refused("top10_repeated_or_excess_action")
            snapshot = self.snapshot()
            self._port(state)
            c.audit(
                self.directory, "model_action_intent", {"tool": action, "snapshot_id": snapshot_id}
            )
            actions.append(action)
            c.transition(self.directory, state, "deciding", "snapshot_action_reserved")
            try:
                with self.mutation_budget(state):
                    plans.repair(self.kernel, action, snapshot)
            except Exception as exc:
                state["last_reason"] = label(exc)
                c.audit(self.directory, "action_failure", {"tool": action, "reason": label(exc)})
                c.save(self.directory / "state.json", state)
                raise
            c.audit(self.directory, "model_action_complete", {"tool": action})
            completed = state["completed_model_actions"]
            if not isinstance(completed, list):
                raise c.Refused("top10_action_ledger_invalid")
            completed.append(action)
            c.save(self.directory / "state.json", state)
            return self.public(state)

    def observe(self) -> dict[str, object]:
        state = self.state()
        self._port(state)
        facts = self.kernel.facts(self.snapshot())
        latest = self.state()
        return self.public(latest) | {
            "facts": facts,
            "checked_at": time.time(),
            "measurement_state_unchanged": state == latest,
        }

    def restore(self, *, actor: str) -> dict[str, object]:
        if actor not in {"controller_cleanup", "watchdog"}:
            raise c.Refused("top10_restore_actor_required")
        with self.locked():
            state = self.state()
            if state["lifecycle"] in {"restored", "closed"}:
                return self.public(state)
            if state["lifecycle"] == "restoring":
                # The prior holder has released/died: never silently retry a
                # partially completed restoration or call it handed back.
                state["last_reason"] = "top10_interrupted_restore_requires_operator"
                c.transition(self.directory, state, "restore_failed", str(state["last_reason"]))
                raise c.Refused(str(state["last_reason"]))
            if state["lifecycle"] == "restore_failed":
                raise c.Refused("top10_failed_restore_requires_operator")
            if state["lifecycle"] not in c.ACTIVE:
                raise c.Refused("top10_no_restorable_snapshot")
            started = self.now()
            state["watchdog_restore" if actor == "watchdog" else "controller_cleanup"] = True
            state["restore_started_at"] = time.time()
            state["restore_actor_pid"] = os.getpid()
            c.transition(self.directory, state, "restoring", actor)
            self._port(state)
            if isinstance(self.kernel, MacKernel):
                self.kernel.command_deadline = time.monotonic() + c.RESTORE_SECONDS
            try:
                facts = plans.restore(self.kernel, self.snapshot())
                # DHCP and association are asynchronous. Only bounded readback waits.
                until = time.monotonic() + (0 if self.kernel.sandbox else 5)
                while not c.baseline_ok(facts) and time.monotonic() < until:
                    time.sleep(0.2)
                    facts = self.kernel.facts(self.snapshot())
                duration = self.now() - started
                if not c.baseline_ok(facts) or duration > c.RESTORE_SECONDS:
                    raise c.Refused("top10_snapshot_restore_not_verified")
                state["restore_seconds"] = duration
                state["restoration_facts"] = facts
                c.transition(
                    self.directory,
                    state,
                    "restored",
                    "persistent_baseline_and_connectivity_invariants",
                )
            except BaseException as exc:
                state["last_reason"] = label(exc)
                c.transition(self.directory, state, "restore_failed", label(exc))
                raise
            finally:
                if isinstance(self.kernel, MacKernel):
                    self.kernel.command_deadline = None
            return self.public(state)

    def close(self) -> dict[str, object]:
        with self.locked():
            state = self.state()
            if state["lifecycle"] == "closed":
                return self.public(state)
            if state["lifecycle"] != "restored":
                raise c.Refused("top10_restoration_required_before_close")
            c.transition(self.directory, state, "closed", "verified_handback")
            return self.public(state)

    def advance(self, seconds: float) -> None:
        if not isinstance(self.kernel, SandboxKernel):
            raise c.Refused("top10_fake_clock_only")
        c.number(seconds, 0, 120)
        with self.locked():
            clock = c.read(self.directory / "clock.json", private=True)
            c.save(
                self.directory / "clock.json",
                {"now": self.now() + seconds, "anchor": steady(), "scale": clock["scale"]},
            )

    def _port(self, state: dict[str, object]) -> None:
        if isinstance(self.kernel, (MacKernel, SandboxKernel)):
            self.kernel.portal_port = int(c.number(state.get("portal_port", 0), 0, 65535))

    def public(self, state: dict[str, object]) -> dict[str, object]:
        keys = (
            "version",
            "run_id",
            "fault_id",
            "lifecycle",
            "snapshot_id",
            "ready",
            "watchdog_pid",
            "watchdog_restore",
            "controller_cleanup",
            "model_actions",
            "completed_model_actions",
            "evidence_kind",
            "last_reason",
            "restore_seconds",
        )
        return {k: state.get(k) for k in keys} | {
            "remaining_seconds": max(0.0, self.remaining(state)) if "deadline" in state else 0,
        }


def label(exc: BaseException) -> str:
    value = str(exc)
    return value if re.fullmatch(r"[a-z][a-z0-9_]{0,119}", value) else type(exc).__name__
