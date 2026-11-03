"""Flushed, privacy-filtered Uplink run journal. Recording never performs a network change."""

import difflib
import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO, cast

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.contracts import EMPTY_SCOPE, Action, Scope, Tool, validate
from offline_agent.uplink.diagnostics import IF, NS, Vitals, parse_service_order
from offline_agent.uplink.grammar import gbnf
from offline_agent.uplink.model_identity import expected_model, validate_identity
from offline_agent.uplink.oracle import OracleResult
from offline_agent.uplink.plans import ConfirmCard
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.trace_format import PERF_FIELDS, TRACE_VERSION, validate_record

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[3] / "artifacts/uplink/traces"


def iso(value: float) -> str:
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="milliseconds")


def empty_state() -> dict[str, Json]:
    return {
        "interfaces": [],
        "active_route": {"interface_id": None, "service_id": None, "candidate_id": None},
        "latest_oracle": {"verdict": None, "ts": None},
    }


def model_fields(
    text: str, tokens: tuple[int, ...] | None, timings: dict[str, Json], redactor: Redactor
) -> tuple[dict[str, Json], dict[str, Json]]:
    perf: dict[str, Json] = {}
    for key in PERF_FIELDS:
        value = (
            timings.get("cache_n", timings.get("cached_n"))
            if key == "cached_n"
            else timings.get(key)
        )
        if value is not None and (
            type(value) not in (int, float)
            or not math.isfinite(cast(float, value))
            or cast(float, value) < 0
            or key.endswith("_n")
            and type(value) is not int
        ):
            raise ContractError("invalid_llama_timings")
        perf[key] = value
    if tokens is not None and any(type(x) is not int or x < 0 for x in tokens):
        raise ContractError("invalid_model_tokens")
    scrubbed = redactor.scrub(text)
    # Raw token IDs can reconstruct a redacted secret. Omit them in that case.
    model: dict[str, Json] = {
        "text": scrubbed,
        "tokens": list(tokens) if tokens is not None and scrubbed == text else None,
        "redacted": scrubbed != text,
        "provider_timings": {k: v for k, v in timings.items() if k in PERF_FIELDS | {"cache_n"}},
    }
    return model, perf


def config_diff(
    before_id: str,
    before: dict[str, Json],
    after: dict[str, Json] | None,
    *,
    after_id: str | None = None,
    observed: bool = False,
) -> dict[str, Json]:
    old = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    new = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    structured: list[Json] = []
    for key in sorted(before.keys() | (after or {}).keys()):
        previous, following = before.get(key), (after or {}).get(key)
        if previous != following:
            structured.append({"field": key, "before": previous, "after": following})
    return {
        "before_snapshot_id": before_id,
        "after_snapshot_id": after_id,
        "kind": "observed" if observed else "proposed",
        "before": before,
        "after": after,
        "structured": structured,
        "unified": "".join(difflib.unified_diff(old, new, fromfile="before", tofile="after")),
    }


def decision(
    reason: str,
    *,
    accepted: bool = True,
    move: str | None = None,
    confirm: str | None = None,
    timer: dict[str, Json] | None = None,
    reverted: bool = False,
) -> dict[str, Json]:
    return {
        "outcome": "accepted" if accepted else "rejected",
        "reason": reason,
        "confirm_outcome": confirm,
        "timer": timer,
        "auto_revert_triggered": reverted,
        "next_move": move,
    }


def cleared(reason: str) -> dict[str, Json]:
    return {"state": "cleared", "deadline_ts": None, "reason": reason}


class RunTrace:
    def __init__(
        self,
        redactor: Redactor,
        *,
        directory: Path | None = None,
        mode: str = "diagnose",
        scope: Scope = EMPTY_SCOPE,
        model_name: str | None = None,
        vitals: Vitals | None = None,
        latest_oracle: OracleResult | None = None,
        provider: str | None = None,
        model_identity: dict[str, Json] | None = None,
        authorization: dict[str, Json] | None = None,
        wall: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if mode not in {"diagnose", "plan", "dry_run", "test", "linux_eval", "real_eval"}:
            raise ContractError("u1_trace_execution_mode_refused")
        self.redactor, self.mode, self.scope = redactor, mode, scope
        self.model_name = model_name
        self.model_identity = (
            validate_identity(model_identity)
            if model_identity is not None
            else expected_model(provider=provider, active_name=model_name)
        )
        self.wall, self.clock = wall, clock
        self.started = clock()
        self.run_id = "uplink-" + uuid.uuid4().hex
        self.path = (directory or DEFAULT_DIRECTORY) / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = empty_state()
        self.step_count = 0
        self.model_calls = 0
        self.prompt_n: int | None = 0
        self.predicted_n: int | None = 0
        self.closed = False
        self._plan: dict[str, Json] | None = None
        self._approved = False
        self._applied = False
        self._interfaces: dict[str, bool | None] = {}
        self._services: dict[str, str | None] = {}
        self._default: str | None = None
        if vitals is not None:
            self.update_state(vitals, latest_oracle)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        # Evaluation receipts keep a run-local path, while the replay inbox exposes
        # the same flushed file at the canonical location required by the trace contract.
        canonical = DEFAULT_DIRECTORY / self.path.name
        if self.path.resolve() != canonical.resolve() and self.path.resolve().is_relative_to(
            DEFAULT_DIRECTORY.parent.resolve()
        ):
            try:
                DEFAULT_DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.link(self.path, canonical)
            except OSError:
                os.close(fd)
                raise
        self.stream: TextIO = os.fdopen(fd, "w")
        self._write(
            {
                "event": "start",
                "schema_version": 1,
                "model": self.model_identity,
                "grammar_sha256": hashlib.sha256(gbnf(scope).encode()).hexdigest(),
                "mode": mode,
                "authorization": authorization,
            }
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.finish("ERROR" if kind is not None else None)

    def _write(self, row: dict[str, Json]) -> None:
        if self.closed:
            raise ContractError("trace_already_finished")
        row.setdefault(
            "display",
            self.redactor.display(
                f"Uplink {row['event']}: "
                f"{self.mode if row['event'] == 'start' else row.get('verdict', '')}"
            ),
        )
        row = {
            **row,
            "trace_version": TRACE_VERSION,
            "run_id": self.run_id,
            "ts": iso(self.wall()),
            "elapsed_ms": round(max(0.0, (self.clock() - self.started) * 1000), 3),
            "net_state": self.state,
        }
        # Typed metadata may contain the live authorization. Its serial must be
        # pseudonymized too, without rewriting keys that define the trace schema.
        row = object_json(self.redactor.scrub_serials_json(row))
        validate_record(row)
        encoded = json.dumps(row, ensure_ascii=True, allow_nan=False)
        scan = self.redactor.scan_trace(encoded)
        if not scan["passed"]:
            from offline_agent.uplink.snapshots import write_private_json

            write_private_json(self.path.parent.parent / f"{self.run_id}-redaction.json", scan)
            raise ContractError("trace_privacy_refused_before_flush")
        self.stream.write(encoded + "\n")
        self.stream.flush()

    def update_state(self, vitals: Vitals, oracle: OracleResult | None = None) -> None:
        self._interfaces = {i.id: i.up for i in vitals.interfaces}
        self._default = vitals.default_interface
        self._services = {s.id: s.interface for s in vitals.services}
        self._refresh_state()
        route = object_json(self.state["active_route"])
        candidates = [
            c
            for c in vitals.candidates
            if c.interface == self._default and c.network_id is None and c.available
        ]
        route["candidate_id"] = candidates[0].id if len(candidates) == 1 else None
        if oracle is not None:
            self.state["latest_oracle"] = {
                "verdict": oracle.verdict.value,
                "ts": iso(oracle.checked_at),
            }

    def _refresh_state(self) -> None:
        self.state["interfaces"] = [
            {
                "id": self.redactor.handle("interface", name, remember=False),
                "up": up,
                "state": "unknown" if up is None else "up" if up else "down",
            }
            for name, up in sorted(self._interfaces.items())
        ]
        services = [sid for sid, iface in self._services.items() if iface == self._default]
        self.state["active_route"] = {
            "interface_id": self.redactor.handle("interface", self._default, remember=False)
            if self._default
            else None,
            "service_id": services[0] if len(services) == 1 else None,
            "candidate_id": None,
        }

    def diagnostic(self, observation: RawObservation) -> None:
        import re

        argv, text = observation.argv, observation.stdout
        self.redactor.learn_command(argv, text)
        if argv == (IF, "-l"):
            self._interfaces.update(
                {name: None for name in text.split() if re.fullmatch(r"[a-z][a-z0-9]{0,14}", name)}
            )
        elif len(argv) == 2 and argv[0] == IF:
            flags = re.search(r"flags=\d+<([^>]+)>", text)
            self._interfaces[argv[1]] = "UP" in flags[1].split(",") if flags else None
        elif argv == (NS, "-listnetworkserviceorder"):
            self._services = {
                self.redactor.handle("service", name): iface
                for name, iface in parse_service_order(text)
            }
        elif argv == ("/sbin/route", "-n", "get", "default"):
            match = re.search(r"interface:\s*([a-z][a-z0-9]*)", text)
            self._default = match[1] if match else None
        self._refresh_state()
        tool = (
            Tool.READ_DNS
            if any("dns" in x for x in argv)
            else (
                Tool.READ_PROXY
                if any("proxy" in x for x in argv)
                else Tool.READ_ROUTES
                if argv[0] in {"/sbin/route", "/usr/sbin/netstat"}
                else Tool.READ_VITALS
            )
        )
        self.step(
            "diagnose",
            Action(tool, {}),
            observation=observation,
            choice=decision(observation.source, accepted=observation.source == "available"),
        )

    def oracle_result(self, result: OracleResult) -> dict[str, Json]:
        self.state["latest_oracle"] = {
            "verdict": result.verdict.value,
            "ts": iso(result.checked_at),
        }
        return {
            "verdict": result.verdict.value,
            "ts": iso(result.checked_at),
            "dns": [e.as_json() for e in result.evidence if e.kind == "dns"],
            "https": [e.as_json() for e in result.evidence if e.kind == "tls"],
            "captive": [e.as_json() for e in result.evidence if e.kind == "portal"],
            "latency_ms": sum(e.latency_ms for e in result.evidence),
        }

    def step(
        self,
        phase: str,
        action: Action | None,
        *,
        observation: RawObservation | None = None,
        change: dict[str, Json] | None = None,
        oracle: OracleResult | None = None,
        choice: dict[str, Json] | None = None,
        card: ConfirmCard | None = None,
        model_text: str | None = None,
        model_tokens: tuple[int, ...] | None = None,
        timings: dict[str, Json] | None = None,
        scope: Scope | None = None,
    ) -> None:
        if model_text is not None and not self.model_name:
            raise ContractError("model_identity_required_for_emission")
        if action is not None:
            validate(action, scope or self.scope)
        choice = choice or decision("observed")
        timer = choice.get("timer")
        pending, approved, applied = self._plan, self._approved, self._applied
        if phase == "plan":
            if applied:
                raise ContractError("unresolved_watchdog_before_new_plan")
            pending = action.as_json() if action else None
            approved = applied = False
        if phase == "confirm":
            if action is None or action.as_json() != pending:
                raise ContractError("confirmation_requires_matching_plan")
            approved = choice["confirm_outcome"] == "approved"
            if self.mode not in {"test", "linux_eval", "real_eval"} and (
                approved or isinstance(timer, dict) and timer.get("state") == "armed"
            ):
                raise ContractError("u1_confirmation_cannot_arm_or_approve")
        if phase == "apply":
            if (
                self.mode not in {"test", "linux_eval", "real_eval"}
                or not approved
                or action is None
                or action.as_json() != pending
                or not isinstance(timer, dict)
                or timer.get("state") != "armed"
            ):
                raise ContractError("apply_requires_confirm_and_armed_watchdog")
            applied = True
        if phase == "revert" and (
            self.mode not in {"test", "linux_eval", "real_eval"} or not applied
        ):
            raise ContractError("revert_requires_applied_test_fixture")
        if phase in {"verify", "revert"} and applied:
            if not isinstance(timer, dict):
                raise ContractError("verification_requires_watchdog_state")
            if timer.get("state") == "cleared" or (
                phase == "verify"
                and choice.get("outcome") == "accepted"
                and choice.get("reason") == "partial_improvement_watchdog_retained"
            ):
                applied = approved = False
        raw = observation or RawObservation((), source="controller")
        observed: dict[str, Json] = {
            "argv": [self.redactor.scrub(x) for x in raw.argv],
            "stdout": self.redactor.handle("body", raw.stdout, remember=False)
            if raw.source == "http_probe" and raw.stdout
            else self.redactor.scrub(raw.stdout),
            "stderr": self.redactor.scrub(raw.stderr),
            "exit_code": raw.exit_code,
            "duration_ms": round(raw.duration_ms, 3),
            "source": self.redactor.scrub(raw.source),
        }
        model, perf = (
            model_fields(model_text, model_tokens, timings or {}, self.redactor)
            if model_text is not None
            else (None, None)
        )
        previous_state = json.loads(json.dumps(self.state))
        from offline_agent.uplink.display import command

        shown = (
            "; ".join(command(argv) for argv in card.argv)
            if card
            else command(raw.argv)
            if raw.argv
            else json.dumps(action.as_json())
            if action
            else "Rejected action"
        )
        record: dict[str, Json] = {
            "event": "step",
            "display": self.redactor.display(shown),
            "step": self.step_count + 1,
            "phase": phase,
            "action": action.as_json() if action else None,
            "observation": observed,
            "config_diff": self.redactor.scrub_json(change),
            "oracle": self.oracle_result(oracle) if oracle else None,
            "model": model,
            "decision": self.redactor.scrub_json(choice),
            "perf": perf,
            "confirm_card": card.for_journal(self.redactor) if card else None,
        }
        try:
            self._write(record)
        except (ValueError, TypeError):
            self.state = previous_state
            raise
        self.step_count += 1
        self._plan, self._approved, self._applied = pending, approved, applied
        if perf:
            self.model_calls += 1
            self.prompt_n = (
                self.prompt_n + cast(int, perf["prompt_n"])
                if self.prompt_n is not None and perf["prompt_n"] is not None
                else None
            )
            self.predicted_n = (
                self.predicted_n + cast(int, perf["predicted_n"])
                if self.predicted_n is not None and perf["predicted_n"] is not None
                else None
            )

    def finish(self, verdict: str | None = None) -> None:
        if self.closed:
            return
        if self._applied:
            verdict = "ERROR"
        latest = object_json(self.state["latest_oracle"])["verdict"]
        verdict = verdict or (
            str(latest) if latest is not None else "PLAN_ONLY" if self.mode == "plan" else "UNKNOWN"
        )
        try:
            self._write(
                {
                    "event": "finish",
                    "verdict": verdict,
                    "totals": {
                        "steps": self.step_count,
                        "model_calls": self.model_calls,
                        "prompt_n": self.prompt_n,
                        "predicted_n": self.predicted_n,
                        "elapsed_ms": round((self.clock() - self.started) * 1000, 3),
                    },
                }
            )
        finally:
            self.stream.close()
            self.closed = True
