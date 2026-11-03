"""Typed dispatch and value-blind trace boundary shared by a future local model loop."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import CHANGES, DNS_PRESETS, Action, Scope, Tool, parse_action
from offline_agent.uplink.diagnostics import Vitals
from offline_agent.uplink.oracle import OnlineOracle
from offline_agent.uplink.plans import ConfirmCard, PlanContext, plan_change
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.snapshots import Snapshot, diff_snapshot, snapshot_state
from offline_agent.uplink.trace import RunTrace, cleared, config_diff, decision


@dataclass(frozen=True)
class Outcome:
    data: dict[str, Json]
    card: ConfirmCard | None = field(default=None, repr=False)


@dataclass
class Session:
    vitals: Vitals
    oracle: OnlineOracle
    redactor: Redactor
    snapshot: Snapshot | None = None
    plan_context: PlanContext | None = field(default=None, repr=False)
    checkpoint: Snapshot | None = None
    trace: list[dict[str, Json]] = field(default_factory=list)
    state: str = "active"
    trace_directory: Path | None = None
    model_name: str | None = None
    provider: str | None = None
    writer: RunTrace = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.writer = RunTrace(
            self.redactor,
            directory=self.trace_directory,
            mode="plan",
            scope=self.scope(),
            vitals=self.vitals,
            latest_oracle=self.oracle.latest,
            model_name=self.model_name,
            provider=self.provider,
        )
        self.writer.update_state(self.vitals, self.oracle.latest)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.writer.finish("ERROR" if kind else None)

    def close(self) -> None:
        self.writer.finish()
        self.state = "closed"

    def dispatch(
        self,
        text: str,
        *,
        model_text: str | None = None,
        model_tokens: tuple[int, ...] | None = None,
        timings: dict[str, Json] | None = None,
    ) -> Outcome:
        if self.writer.closed:
            raise ContractError("session_terminal_start_new_observation")
        action: Action | None = None
        try:
            action = parse_action(text, self.scope())
            outcome = self._dispatch(text)
        except ContractError as exc:
            self.writer.step(
                "report",
                action,
                choice=decision(str(exc), accepted=False),
                model_text=model_text,
                model_tokens=model_tokens,
                timings=timings,
                scope=self.scope(),
            )
            raise
        self.writer.update_state(self.vitals, self.oracle.latest)
        if outcome.card is not None:
            assert self.checkpoint is not None
            proposed = self._proposal(action)
            change = config_diff(self.checkpoint.id, self.checkpoint.state, proposed)
            move = "Reroute" if action.tool == Tool.REROUTE else "Repair"
            self.writer.step(
                "plan",
                action,
                change=change,
                card=outcome.card,
                choice=decision("plan_only", move=move),
                scope=self.scope(),
                model_text=model_text,
                model_tokens=model_tokens,
                timings=timings,
            )
            self.writer.step(
                "confirm",
                action,
                change=change,
                card=outcome.card,
                scope=self.scope(),
                choice=decision(
                    "confirmation_not_requested_u1",
                    move=move,
                    confirm="not_requested_u1",
                    timer=cleared("u1_execution_disabled"),
                ),
            )
        else:
            phase = (
                "verify"
                if action.tool == Tool.PROBE_ONLINE
                else "report"
                if self.state != "active"
                else "diagnose"
            )
            self.writer.step(
                phase,
                action,
                oracle=self.oracle.latest if action.tool == Tool.PROBE_ONLINE else None,
                choice=decision("accepted"),
                scope=self.scope(),
                model_text=model_text,
                model_tokens=model_tokens,
                timings=timings,
            )
        if self.state != "active":
            self.writer.finish(
                "REPORTED"
                if self.state == "report"
                else "GAVE_UP"
                if self.state == "give_up"
                else None
            )
        return outcome

    def _proposal(self, action: Action) -> dict[str, Json] | None:
        from offline_agent.contracts import object_json

        assert self.checkpoint is not None and self.plan_context is not None
        proposed = object_json(json.loads(json.dumps(self.checkpoint.state)))
        if action.tool == Tool.SET_DNS:
            sid, policy = action.text("service_id"), action.text("policy")
            servers = (
                self.plan_context.service(sid, previous=True).dns
                if policy == "snapshot"
                else DNS_PRESETS.get(policy, ())
            )
            service = object_json(object_json(proposed["services"])[sid])
            service.update(dns=list(servers), dns_mode="manual" if servers else "dhcp")
            return proposed
        if action.tool == Tool.SET_SERVICE_ORDER:
            proposed["service_order"] = list(action.texts("service_ids"))
            return proposed
        # Nontrivial runtime effects remain unknown while the executor is disabled.
        return None

    def scope(self) -> Scope:
        evidence = tuple(o.id for o in self.vitals.observations)
        if self.oracle.latest:
            evidence += tuple(e.id for e in self.oracle.latest.evidence)
        snapshot_ids = () if self.snapshot is None else (self.snapshot.id,)
        if self.checkpoint is not None:
            snapshot_ids = (*snapshot_ids, self.checkpoint.id)
        return Scope(
            tuple(s.id for s in self.vitals.services),
            tuple(c.id for c in self.vitals.candidates if c.eligible),
            tuple(dict.fromkeys(snapshot_ids)),
            tuple(dict.fromkeys(evidence)),
        )

    def _dispatch(self, text: str) -> Outcome:
        if self.state != "active":
            raise ContractError("session_terminal_start_new_observation")
        action = parse_action(text, self.scope())
        data: dict[str, Json]
        card = None
        public = self.vitals.as_json()
        if action.tool in CHANGES:
            if self.oracle.latest is not None and self.oracle.latest.fresh_online(time.time()):
                raise ContractError("healthy_network_change_refused")
            if self.plan_context is None or self.checkpoint is None:
                raise ContractError("prechange_snapshot_and_bindings_required")
            if (
                self.checkpoint.online
                or self.checkpoint.id != self.plan_context.snapshot_id
                or self.checkpoint.state != snapshot_state(self.vitals)
                or not 0 <= time.time() - self.checkpoint.created_at <= 30
            ):
                raise ContractError("prechange_checkpoint_stale_or_mismatched")
            card = plan_change(action, self.plan_context)
            data = card.for_trace(self.redactor)
        elif action.tool == Tool.PROBE_ONLINE:
            data = self.oracle.probe().as_json()
        elif action.tool == Tool.DECLARE_ONLINE:
            data = self.oracle.declare_online().as_json()
        elif action.tool == Tool.DIFF_SNAPSHOT:
            if self.snapshot is None or action.text("snapshot_id") != self.snapshot.id:
                raise ContractError("snapshot_missing")
            data = {
                "snapshot_id": self.snapshot.id,
                "changes": list(diff_snapshot(self.snapshot, self.vitals)),
            }
        elif action.tool in {Tool.REPORT, Tool.GIVE_UP}:
            data = action.as_json()
        elif action.tool == Tool.READ_VITALS:
            data = public
        elif action.tool == Tool.READ_DNS:
            data = {
                "system_dns": public["system_dns"],
                "services": [
                    {"id": s.id, "dns": list(s.dns), "mode": s.dns_mode}
                    for s in self.vitals.services
                ],
            }
        elif action.tool == Tool.READ_PROXY:
            services = public["services"]
            assert isinstance(services, list)
            data = {
                "services": services,
                "pac": self.oracle.proxies.pac,
                "autodiscovery": self.oracle.proxies.autodiscovery,
                "proxy_settings_known": self.oracle.proxies.known,
            }
        elif action.tool == Tool.READ_ROUTES:
            data = {"routes": public["routes"], "default_interface": self.vitals.default_interface}
        elif action.tool == Tool.LIST_ROUTE_CANDIDATES:
            data = {"candidates": public["candidates"]}
        else:
            raise ContractError("unsupported_action")
        entry: dict[str, Json] = {"version": 1, "action": action.as_json(), "result": data}
        # A trace has only normalized evidence or HMAC'd argv. The local UI owns the raw card.
        import json

        if not self.redactor.scan(json.dumps(entry))["passed"]:
            raise ContractError("trace_privacy_refused")
        self.trace.append(entry)
        if action.tool in {Tool.DECLARE_ONLINE, Tool.REPORT, Tool.GIVE_UP}:
            self.state = action.tool.value
        return Outcome(data, card)
