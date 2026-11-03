"""Canonical, one-use suite ledger, independent of controller output directories."""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from . import top10_contract as c
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]


class Attempt:
    def __init__(self, base: Path, manifest_sha: str) -> None:
        c.text(manifest_sha, r"[0-9a-f]{64}")
        self.path = base / manifest_sha

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise c.Refused("top10_attempt_symlink_refused")
        fd = os.open(self.path / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def state(self) -> dict[str, object]:
        return c.read(self.path / "state.json", private=True)

    def reserve(self, window: dict[str, object], manifest: dict[str, object]) -> dict[str, object]:
        selected = c.selected_slots(manifest)
        held = c.held_slots(manifest)
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise c.Refused("top10_attempt_symlink_refused")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise c.Refused("top10_suite_attempt_already_consumed") from exc
        state: dict[str, object] = {
            "version": c.VERSION,
            "lifecycle": "reserved",
            "next_slot": 0,
            "manifest_sha256": self.path.name,
            "window_sha256": c.digest(window),
            "reserved_at": time.time(),
            "reason": None,
            "registered_trials": 30,
            "planned_trials": len(selected),
            "held_trials": len(held),
        }
        c.audit(self.path, "suite_reserved_before_preflight", state)
        c.save(self.path / "window.json", window, exclusive=True)
        c.save(self.path / "held-slots.json", {"slots": held}, exclusive=True)
        c.save(self.path / "state.json", state, exclusive=True)
        return state

    def admit(self, proof: dict[str, object]) -> dict[str, object]:
        with self.locked():
            state = self.state()
            if state["lifecycle"] != "preflighting":
                raise c.Refused("top10_suite_admission_not_repeatable")
            if proof.get("base_verified") is not True or proof.get("pid") != c.SERVER_PID:
                raise c.Refused("top10_suite_preflight_unproven")
            c.audit(self.path, "suite_preflight_passed", {"proof_sha256": c.digest(proof)})
            c.save(self.path / "preflight.json", proof, exclusive=True)
            state["lifecycle"] = "running"
            c.save(self.path / "state.json", state)
            return state

    def record_oob(self, proof: dict[str, object]) -> None:
        """Consume the qualified scope and challenge IDs before any trial mutation."""
        scope = c.text(proof.get("scope"), r"preflight|[0-9a-f]{12}")
        attempts = proof.get("attempts")
        if not isinstance(attempts, list):
            raise c.Refused("top10_oob_attempts_required")
        nonces = {c.obj(row).get("nonce") for row in attempts}
        with self.locked():
            for path in self.path.glob("oob-*.json"):
                prior = c.read(path, private=True)
                prior_attempts = prior.get("attempts")
                if not isinstance(prior_attempts, list):
                    raise c.Refused("top10_prior_oob_evidence_invalid")
                if (
                    prior.get("scope") == scope
                    or prior.get("qualification_id") == proof.get("qualification_id")
                    or nonces.intersection(c.obj(row).get("nonce") for row in prior_attempts)
                ):
                    raise c.Refused("top10_oob_qualification_replay_refused")
            c.audit(
                self.path,
                "oob_qualification_consumed",
                {
                    "scope": scope,
                    "proof_sha256": c.digest(proof),
                },
            )
            c.save(self.path / ("oob-" + scope + ".json"), proof, exclusive=True)

    def require_slot(self, slot: dict[str, object], manifest: dict[str, object]) -> None:
        state = self.state()
        slots = c.selected_slots(manifest)
        index = state.get("next_slot")
        if (
            state.get("lifecycle") != "running"
            or type(index) is not int
            or not 0 <= index < len(slots)
            or slots[index] != slot
        ):
            raise c.Refused("top10_frozen_order_not_ready")

    def begin_preflight(self) -> dict[str, object]:
        with self.locked():
            state = self.state()
            if state["lifecycle"] != "reserved":
                raise c.Refused("top10_preflight_attempt_already_consumed")
            c.audit(self.path, "preflight_invocation_consumed", {})
            state["lifecycle"] = "preflighting"
            c.save(self.path / "state.json", state)
            return state

    def finish(self, receipt: dict[str, object], manifest: dict[str, object]) -> dict[str, object]:
        with self.locked():
            slots = c.selected_slots(manifest)
            slot = {k: receipt.get(k) for k in ("fault_id", "trial", "run_id")}
            self.require_slot(slot, manifest)
            state = self.state()
            safe = (
                receipt.get("cleanup_verified") is True
                and receipt.get("false_online") == 0
                and receipt.get("failure") is None
                and receipt.get("unparsed_model_output") is False
                and receipt.get("outcome") in {"recovered", "honest_report"}
            )
            c.audit(
                self.path,
                "slot_finished",
                {"slot": slot, "safe": safe, "receipt_sha256": c.digest(receipt)},
            )
            c.save(self.path / (str(slot["run_id"]) + ".json"), receipt, exclusive=True)
            state["next_slot"] = int(str(state["next_slot"])) + 1
            state["lifecycle"] = (
                ("complete" if state["next_slot"] == len(slots) else "running")
                if safe
                else "stopped"
            )
            state["reason"] = None if safe else "top10_trial_not_safe_to_continue"
            c.save(self.path / "state.json", state)
            return state

    def stop(self, reason: str) -> dict[str, object]:
        c.text(reason, r"[A-Za-z][A-Za-z0-9_]{0,119}")
        with self.locked():
            state = self.state()
            c.audit(self.path, "suite_stopped", {"reason": reason})
            if state["lifecycle"] != "complete":
                state.update(lifecycle="stopped", reason=reason)
                c.save(self.path / "state.json", state)
            return state
