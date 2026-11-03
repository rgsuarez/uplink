"""One-use reduced-run ledger. Reserve before probes; persist intent before effects."""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator
from pathlib import Path

if __package__:
    from . import bench_contract as b
    from . import top10_contract as c
else:
    import bench_contract as b  # type: ignore[no-redef,import-not-found]
    import top10_contract as c  # type: ignore[no-redef,import-not-found]


@contextlib.contextmanager
def lock(path: Path) -> Iterator[None]:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise c.Refused("bench_ledger_symlink_refused")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Attempt:
    def __init__(self, base: Path, run_id: str) -> None:
        self.base = base
        self.path = base / c.text(run_id, r"[0-9a-f]{12}")

    def state(self) -> dict[str, object]:
        return c.read(self.path / "attempt.json", private=True)

    def save(self, state: dict[str, object], *, exclusive: bool = False) -> None:
        c.save(self.path / "attempt.json", state, exclusive=exclusive)
        b.sync_directory(self.path)

    def reserve(self, manifest: dict[str, object], window: dict[str, object]) -> dict[str, object]:
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock(self.base / "reserve.lock"):
            for prior_path in self.base.iterdir():
                if prior_path.is_dir():
                    prior = c.read(prior_path / "attempt.json", private=True)
                    if (
                        prior.get("approval_nonce") == window["approval_nonce"]
                        or prior.get("registration_nonce") == manifest["registration_nonce"]
                        or prior.get("lifecycle") not in {"finished", "stopped"}
                    ):
                        raise c.Refused("bench_prior_attempt_or_nonce_not_reusable")
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise c.Refused("bench_attempt_already_consumed") from exc
            state = {
                "kind": b.KIND,
                "lifecycle": "reserved",
                "run_id": manifest["run_id"],
                "registration_nonce": manifest["registration_nonce"],
                "approval_nonce": window["approval_nonce"],
                "window_sha256": c.digest(window),
                "manifest_sha256": window["manifest_sha256"],
                "decisions": 0,
                "history": [],
                "decision_pending": False,
                "reserved_at": time.time(),
            }
            c.audit(self.path, "attempt_consumed_before_preflight", state)
            self.save(state, exclusive=True)
            b.sync_directory(self.base)
            b.sync_directory(self.base.parent)
            return state

    def change(self, expected: str, following: str, detail: object) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            if state["lifecycle"] != expected:
                raise c.Refused("bench_attempt_transition_refused")
            c.audit(self.path, following, detail)
            state["lifecycle"] = following
            self.save(state)
            return state

    def admit(self, proof: dict[str, object], window: dict[str, object]) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            if (
                state["lifecycle"] != "preflighting"
                or proof.get("base_verified") is not True
                or proof.get("pid") != window["server_pid"]
                or proof.get("context") != window["server_context"]
            ):
                raise c.Refused("bench_preflight_not_admitted")
            c.audit(self.path, "preflight_admitted", {"proof_sha256": c.digest(proof)})
            c.save(self.path / "preflight.json", proof, exclusive=True)
            state["lifecycle"] = "running"
            self.save(state)
            return state

    def decision(self, history: object) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            count = state["decisions"]
            if (
                state["lifecycle"] != "running"
                or state["decision_pending"] is not False
                or type(count) is not int
                or count >= c.MAX_DECISIONS
                or history != state["history"]
            ):
                raise c.Refused("bench_decision_replay_or_budget_refused")
            c.audit(self.path, "completion_intent_consumed", {"decision": count + 1})
            state.update(decisions=count + 1, decision_pending=True)
            self.save(state)
            return state

    def decision_result(self, result: dict[str, object]) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            if state["lifecycle"] != "running" or state["decision_pending"] is not True:
                raise c.Refused("bench_completion_not_reserved")
            c.audit(self.path, "completion_result", {"result_sha256": c.digest(result)})
            c.save(
                self.path / ("model-" + str(state["decisions"]) + ".json"), result, exclusive=True
            )
            action = result.get("action")
            tool = c.obj(action).get("tool") if isinstance(action, dict) else None
            if result.get("failure") is not None or tool not in c.TOOLS:
                state["lifecycle"] = "stopped"
                state["reason"] = "bench_completion_failed_or_unparsed"
            else:
                history = state["history"]
                if not isinstance(history, list):
                    raise c.Refused("bench_history_unparsed")
                state["history"] = [*history, tool]
                if tool in history or tool in (
                    *c.HELD_REPAIRS,
                    "report",
                    "give_up",
                    "declare_online",
                ):
                    state["lifecycle"] = "stopped"
                    state["reason"] = "bench_terminal_or_refused_model_decision"
            state["decision_pending"] = False
            self.save(state)
            return state

    def stop(self, reason: str) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            c.audit(self.path, "attempt_stop", {"reason": c.text(reason, r"[A-Za-z0-9_]{1,120}")})
            if state["lifecycle"] != "finished":
                state.update(lifecycle="stopped", reason=reason)
                self.save(state)
            return state

    def finish(self, receipt: dict[str, object]) -> dict[str, object]:
        with lock(self.path / "lock"):
            state = self.state()
            if (
                state["lifecycle"] not in {"running", "stopped"}
                or receipt.get("kind") != b.KIND
                or receipt.get("run_id") != self.path.name
                or receipt.get("planned_trials") != 1
                or type(receipt.get("planned_trials")) is not int
                or receipt.get("fault_id") != "F04"
                or type(receipt.get("trial")) is not int
                or receipt.get("trial") != 1
                or receipt.get("evidence_kind") != "BASE"
                or receipt.get("model_actions") != state["history"]
                or receipt.get("top10_complete") is not False
            ):
                raise c.Refused("bench_single_trial_receipt_required")
            c.audit(self.path, "receipt_intent", {"receipt_sha256": c.digest(receipt)})
            c.save(self.path / "receipt.json", receipt, exclusive=True)
            state.update(lifecycle="finished", receipt_sha256=c.digest(receipt))
            self.save(state)
            return state
