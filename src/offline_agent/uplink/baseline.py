"""Deterministic troubleshooter baseline: evidence-driven proposals, never execution."""

import json

from offline_agent.contracts import Json, object_json
from offline_agent.uplink.contracts import Action, Scope, parse_action
from offline_agent.uplink.diagnostics import Vitals
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.snapshots import Snapshot


def next_action(
    vitals: Vitals,
    oracle: OracleResult,
    scope: Scope,
    *,
    now: float,
    last_good: Snapshot | None,
    prechange_snapshot_id: str | None,
) -> Action:
    """One bounded decision. The caller still owns consent, watchdog and rollback.

    Prior online state is a repair target, not the undo checkpoint. Ambiguous drift
    or unavailable evidence ends in a report; there is no reset-all fallback.
    """
    tool = "report"
    evidence: list[Json] = list(scope.evidence[:1])
    args: dict[str, Json] = {
        "component": "unknown",
        "physical_step": "wait",
        "evidence_ids": evidence,
    }
    if oracle.fresh_online(now):
        tool, args = "declare_online", {}
    elif oracle.verdict == Verdict.CAPTIVE:
        args.update(component="portal", physical_step="open_portal")
    elif last_good and last_good.online and prechange_snapshot_id in scope.snapshots:
        previous = object_json(last_good.state["services"])
        drifts = []
        for service in vitals.services:
            old = previous.get(service.id)
            if not isinstance(old, dict) or not service.enabled:
                continue
            if (
                service.dns_mode != "unavailable"
                and old["dns_mode"] != "unavailable"
                and (list(service.dns) != old["dns"] or service.dns_mode != old["dns_mode"])
            ):
                drifts.append((service.id, "set_dns", "dns_failed"))
            raw_services = vitals.as_json()["services"]
            assert isinstance(raw_services, list)
            current = next(
                object_json(x) for x in raw_services if object_json(x)["id"] == service.id
            )
            if current["proxies"] != old["proxies"] and all(
                p.enabled is not None for p in service.proxies
            ):
                drifts.append((service.id, "restore_proxy", "proxy_failed"))
        if drifts and len({sid for sid, _, _ in drifts}) == 1:
            sid, tool, reason = drifts[0]
            args = {
                "snapshot_id": prechange_snapshot_id,
                "why": reason,
                "evidence_ids": evidence,
                "service_id": sid,
                "policy": "snapshot",
            }
        elif not drifts:
            alternate = next(
                (
                    c
                    for c in vitals.candidates
                    if c.available
                    and c.eligible
                    and c.interface != vitals.default_interface
                    and "verified_online_snapshot" in c.evidence
                ),
                None,
            )
            if alternate:
                tool = "reroute"
                args = {
                    "snapshot_id": prechange_snapshot_id,
                    "why": "alternate_route",
                    "evidence_ids": evidence,
                    "candidate_id": alternate.id,
                }
    if not evidence and tool != "declare_online":
        tool, args = "give_up", {"reason": "insufficient_evidence"}
    return parse_action(json.dumps({"version": 1, "tool": tool, "args": args}), scope)
