"""Hypothesis, generated read-only probe program, revised hypothesis, typed repair.

The old one-shot layer label does not determine which repair can be attempted.
Measured drift, live alternate evidence and prior independent path qualification do.
The independent acceptance oracle remains the only authority for ONLINE.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.native_protocol import EOT
from offline_agent.providers import Message
from offline_agent.uplink.contracts import Scope, Tool, enum, parse_action
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.layer_policy import action_guide, evidence_facts, layer_action_schema
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.probe_choices import targeted_wifi_probes, wifi_repair_schema
from offline_agent.uplink.probe_protocol import program_schema
from offline_agent.uplink.repair_budget import RESTORES, verified_route_in_history

if TYPE_CHECKING:
    from offline_agent.uplink.lab_runner import Emission, GlimmerPolicy, LabState


def hypothesis_schema(ids: tuple[str, ...]) -> dict[str, Json]:
    return {
        "type": "object",
        "properties": {
            "layer": enum(("link", "ip", "dns", "proxy", "captive", "upstream")),
            "evidence": {"type": "array", "items": enum(ids), "minItems": 1, "maxItems": 1},
        },
        "required": ["layer", "evidence"],
        "additionalProperties": False,
    }


def probe_facts(result: dict[str, Json]) -> tuple[dict[str, Json], bool]:
    rows = result.get("rows")
    facts: dict[str, Json] = {}
    ping, dns = False, False
    if result.get("status") != "completed" or not isinstance(rows, list):
        return {
            "probe-refusal": {"status": result.get("status"), "reason": result.get("reason")}
        }, False
    for index, item in enumerate(rows):
        row = object_json(item)
        probe = object_json(row.get("probe"))
        ok = row.get("ok") is True and row.get("status") == "completed"
        facts["probe-" + str(index + 1)] = {
            "probe": probe,
            "ok": ok,
            "status": row.get("status"),
            "http_status": row.get("status_code", row.get("http_status")),
            "elapsed_ms": row.get("elapsed_ms"),
        }
        ping |= probe.get("op") == "ping_gateway" and probe.get("path") == "alternate" and ok
        dns |= probe.get("op") == "dns_query" and probe.get("resolver") == "alternate" and ok
    return facts, ping and dns


def changed(facts: dict[str, Json], label: str) -> bool:
    row = facts.get("snapshot-" + label)
    return isinstance(row, dict) and row.get("changed") is True


def primary_path_unavailable(facts: dict[str, Json], oracle: OracleResult) -> bool:
    """Do not restore a proxy first when measured primary transport is unavailable."""
    link = facts.get("link")
    if isinstance(link, dict) and link.get("up") is False:
        return True
    dns = [row for row in oracle.evidence if row.kind == "dns"]
    return not changed(facts, "dns") and len(dns) == 2 and not any(row.ok for row in dns)


def require_route_tests(program: dict[str, Json]) -> None:
    """A prior route candidate needs both fresh checks before a safe decision exists."""
    fields = object_json(program["properties"])
    variants = object_json(object_json(fields["probes"])["items"])["oneOf"]
    assert isinstance(variants, list)
    selected: list[Json] = []
    for op, field in (("ping_gateway", "path"), ("dns_query", "resolver")):
        variant = next(
            object_json(row)
            for row in variants
            if object_json(object_json(object_json(row)["properties"])["op"])["const"] == op
        )
        arguments = object_json(variant["properties"])
        arguments[field] = {"const": "alternate"}
        if op == "ping_gateway":
            arguments["count"], arguments["interval_ms"] = {"const": 1}, {"const": 200}
        selected.append(variant)
    fields["probes"] = {
        "type": "array",
        "prefixItems": selected,
        "items": False,
        "minItems": 2,
        "maxItems": 2,
    }


def permitted_actions(
    scope: Scope,
    facts: dict[str, Json],
    oracle: OracleResult,
    *,
    alternate_qualified: bool,
    attempted: tuple[str, ...],
    verified_route_change_at: int | None = None,
    retryable_restores: tuple[str, ...] = (),
) -> tuple[Tool, ...]:
    """Refuse premature handoff while an untried, measured reversible option exists."""
    if oracle.verdict == Verdict.ONLINE:
        return (Tool.DECLARE_ONLINE,)
    route_available = alternate_qualified and bool(scope.candidates) and "reroute" not in attempted
    if route_available and primary_path_unavailable(facts, oracle):
        return (Tool.REROUTE,)
    can_retry = scope.profile == "general"
    dns = [row for row in oracle.evidence if row.kind == "dns"]
    # Reroute deliberately selects that path's resolver. Do not undo working DNS
    # merely because it differs from the original primary-path snapshot.
    route_dns_verified = (
        verified_route_in_history(attempted, verified_route_change_at)
        and len(dns) == 2
        and all(row.ok for row in dns)
    )
    if (
        changed(facts, "dns")
        and not route_dns_verified
        and (Tool.SET_DNS.value not in attempted or can_retry and "set_dns" in retryable_restores)
    ):
        return (Tool.SET_DNS,)
    if changed(facts, "proxy") and (
        Tool.RESTORE_PROXY.value not in attempted
        or can_retry
        and "restore_proxy" in retryable_restores
    ):
        return (Tool.RESTORE_PROXY,)
    if route_available:
        return (Tool.REROUTE,)
    link = facts.get("link")
    if (
        scope.profile == "general"
        and isinstance(link, dict)
        and link.get("up") is False
        and Tool.CYCLE_INTERFACE.value not in attempted
    ):
        return (Tool.CYCLE_INTERFACE,)
    return (Tool.REPORT, Tool.GIVE_UP)


def open_actions(
    scope: Scope,
    facts: dict[str, Json],
    oracle: OracleResult,
    *,
    alternate_qualified: bool,
    attempted: tuple[str, ...],
    verified_route_change_at: int | None = None,
    retryable_restores: tuple[str, ...] = (),
) -> tuple[Tool, ...]:
    """Expose every implemented reversible choice justified by observed evidence.

    The grammar enforces safety and typed arguments, not a preferred repair order.
    Read-only inspection and an honest handoff are always valid choices.
    """
    tools = [
        Tool.READ_VITALS,
        Tool.READ_DNS,
        Tool.READ_PROXY,
        Tool.READ_ROUTES,
        Tool.LIST_ROUTE_CANDIDATES,
        Tool.PROBE_ONLINE,
        Tool.DIFF_SNAPSHOT,
        Tool.REPORT,
        Tool.GIVE_UP,
    ]
    if oracle.verdict == Verdict.ONLINE:
        return (*tools, Tool.DECLARE_ONLINE)
    if not scope.snapshots or not scope.services or not scope.evidence:
        return tuple(tools)
    dns = [row for row in oracle.evidence if row.kind == "dns"]
    route_dns_verified = (
        verified_route_in_history(attempted, verified_route_change_at)
        and len(dns) == 2
        and all(row.ok for row in dns)
    )
    for tool, label in ((Tool.SET_DNS, "dns"), (Tool.RESTORE_PROXY, "proxy")):
        if (
            changed(facts, label)
            and not (label == "dns" and route_dns_verified)
            and (
                tool.value not in attempted
                or (scope.profile == "general" and tool.value in retryable_restores)
            )
        ):
            tools.append(tool)
    if alternate_qualified and scope.candidates and Tool.REROUTE.value not in attempted:
        tools.append(Tool.REROUTE)
    if scope.profile == "general":
        for tool, label, field in (
            (Tool.CYCLE_INTERFACE, "link", "up"),
            (Tool.RENEW_LEASE, "lease", "present"),
        ):
            row = facts.get(label)
            if isinstance(row, dict) and row.get(field) is False and tool.value not in attempted:
                tools.append(tool)
    return tuple(tools)


def choose_with_probes(
    policy: GlimmerPolicy,
    scope: Scope,
    observed: LabState,
    oracle: OracleResult,
    *,
    history: Sequence[Message] = (),
) -> Emission:
    from offline_agent.uplink.lab_runner import Emission

    if policy.probe_executor is None:
        raise ContractError("probe_executor_required")
    begun = time.monotonic()
    policy.probe_result = {}
    prior_reads: list[Json] = []
    if policy.action_mode == "open":
        for message in history:
            if message.role != "assistant":
                continue  # Raw observations may contain evaluator-only fault identity.
            prior_action = parse_action(message.content.removesuffix(EOT), scope)
            if prior_action.tool in {
                Tool.READ_VITALS,
                Tool.READ_DNS,
                Tool.READ_PROXY,
                Tool.READ_ROUTES,
                Tool.LIST_ROUTE_CANDIDATES,
                Tool.PROBE_ONLINE,
                Tool.DIFF_SNAPSHOT,
            }:
                prior_reads.append(prior_action.tool.value)
        prior_reads = prior_reads[-8:]
    facts = evidence_facts(observed.raw, oracle)
    alternate = observed.raw.get("prior_alternate_online") is True and bool(scope.candidates)
    program = object_json(
        read_json(json.dumps(program_schema(policy.probe_profile, alternate=alternate)))
    )
    # Short programs leave room for the next layer in a compound repair episode.
    object_json(object_json(program["properties"])["probes"])["maxItems"] = 3
    targeted_choices: list[Json] = []
    if policy.probe_profile == "lab_wifi" and oracle.verdict != Verdict.ONLINE:
        targeted_choices = targeted_wifi_probes(program, facts)
    if (
        alternate
        and oracle.verdict != Verdict.ONLINE
        and (
            primary_path_unavailable(facts, oracle)
            or not any(changed(facts, label) for label in ("dns", "proxy"))
        )
    ):
        require_route_tests(program)
    before_schema: dict[str, Json] = {
        "type": "object",
        "properties": {
            "hypothesis_before": hypothesis_schema(tuple(facts)),
            "program": program,
        },
        "required": ["hypothesis_before", "program"],
        "additionalProperties": False,
    }
    catalog = json.dumps(
        program
        if policy.probe_profile == "lab_wifi"
        else program_schema(policy.probe_profile, alternate=alternate),
        separators=(",", ":"),
    )
    measurement_guide = (
        "Use targeted_probe_choices and cite their supplied evidence IDs: DNS resolve, "
        "HTTPS through the current effective proxy route, or captive check. "
        "For DNS drift choose dns_query; for proxy drift with working DNS choose "
        "https_head; for possible interception choose captive_check. These are "
        "measurements, not proof of a fault or repair. On unhealthy Wi-Fi, read_state "
        "alone is not an admissible diagnostic program. "
        if policy.probe_profile == "lab_wifi"
        else "DNS/proxy snapshot drift: test configured DNS or the configured proxy TCP endpoint. "
    )
    before = policy.generate(
        "diagnosis",
        [
            Message(
                "system",
                "Test a network hypothesis with 1-3 read-only DSL probes. Return hypothesis_before "
                "and program, exactly as the schema. No code or shell. Cite one "
                "supplied evidence ID. "
                "Each probe timeout_ms=1000; ping count=1 interval_ms=200. "
                + measurement_guide
                + "When DNS and proxy are unchanged, primary is unhealthy, and "
                "prior_alternate_online "
                "is true: MUST test ping_gateway(path=alternate) AND "
                "dns_query(name=one,resolver=alternate). "
                "A reachable alternate is a reversible option even if the primary "
                "layer is uncertain. "
                "If the link is down, or unchanged DNS fails on both names, test that "
                "alternate before restoring a proxy on an unavailable primary path. "
                "Healthy: use read_state. Keep probes short; eight probes per entire episode. "
                "Allowed program schema: " + catalog,
            ),
            Message(
                "user",
                json.dumps(
                    {
                        "oracle": oracle.verdict.value,
                        "evidence": facts,
                        **(
                            {"targeted_probe_choices": targeted_choices}
                            if policy.probe_profile == "lab_wifi"
                            else {}
                        ),
                        "prior_alternate_online": alternate,
                        "previous_actions": observed.raw.get("action_history", []),
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
        schema_gbnf(before_schema),
    )
    raw = object_json(read_json(before.text.removesuffix(EOT)))
    if not Draft202012Validator(before_schema).is_valid(raw):
        policy.records[-1].failure = "probe_plan_schema_refused"
        raise ContractError("probe_plan_schema_refused")
    policy.probe_result = {
        "hypothesis_before": raw["hypothesis_before"],
        "script_verbatim": before.text,
        "program": raw["program"],
    }
    result = policy.probe_executor(object_json(raw["program"]))
    policy.probe_result["execution"] = result
    if result.get("settings_unchanged") is False:
        raise ContractError("probe_settings_changed_no_retry")
    if result.get("wifi_route_unchanged") is False:
        raise ContractError("probe_wifi_route_changed_no_retry")
    measured, live_alternate = probe_facts(result)
    facts.update(measured)
    # Historical independent qualification alone is insufficient after a fault.
    qualified = alternate and live_alternate and result.get("settings_unchanged") is True
    policy.probe_result["alternate_qualified"] = qualified
    action_history = observed.raw.get("action_history", [])
    attempted = (
        tuple(x for x in action_history if isinstance(x, str))
        if isinstance(action_history, list)
        else ()
    )
    route_change = observed.raw.get("verified_route_change_at")
    route_change_at = route_change if type(route_change) is int else None
    retries = observed.raw.get("retryable_restores", [])
    if not isinstance(retries, list) or not all(
        isinstance(x, str) and x in RESTORES for x in retries
    ):
        raise ContractError("invalid_restore_retry_context")
    open_mode = policy.action_mode == "open"
    tools = (open_actions if open_mode else permitted_actions)(
        scope,
        facts,
        oracle,
        alternate_qualified=qualified,
        attempted=attempted,
        verified_route_change_at=route_change_at,
        retryable_restores=tuple(str(x) for x in retries),
    )
    policy.probe_result.update(
        action_mode=policy.action_mode, admitted_tools=[t.value for t in tools]
    )
    if open_mode:
        policy.probe_result["previous_read_actions"] = prior_reads
    after_schema: dict[str, Json] = {
        "type": "object",
        "properties": {
            "hypothesis_after": hypothesis_schema(tuple(facts)),
            "action": layer_action_schema(scope, tools, snapshot_only=open_mode),
        },
        "required": ["hypothesis_after", "action"],
        "additionalProperties": False,
    }
    if policy.probe_profile == "lab_wifi":
        after_schema = wifi_repair_schema(after_schema, scope, tools, facts)
    after = policy.generate(
        "action",
        [
            Message(
                "system",
                (
                    "Revise the hypothesis from actual probe results, then choose "
                    "one typed action. "
                    "Return hypothesis_after and action. Cite one supplied hypothesis evidence ID. "
                    "For action.evidence_ids cite one original oracle ID from scope.evidence. "
                    "Restore changed DNS first, then changed proxy, using policy=snapshot. "
                    "A previously qualified alternate with fresh successful alternate "
                    "gateway and DNS "
                    "probes must be tried with reroute before handoff when the primary fails. "
                    "The hypothesis label never blocks a measured reversible action. "
                    "A snapshot restore can repeat once only after a verified route "
                    "change "
                    "and newly observed drift; the existing deadline still applies. "
                    "Report is admissible only after such actions are exhausted or no "
                    "option qualifies. "
                    "Only the independent oracle can authorize declare_online. All args required; "
                    "$ placeholders mean exact supplied scope values. "
                    if not open_mode
                    else "Revise the hypothesis using measured probe results, then choose "
                    "one typed action. "
                    "Return hypothesis_after and action. Cite one supplied hypothesis evidence ID. "
                    "For action.evidence_ids cite one original oracle ID from scope.evidence. "
                    "Choose among all admitted safe actions, further read-only inspection, report, "
                    "or give_up. No repair order is prescribed. Prefer the smallest useful change. "
                    "Current evidence and probe results are already supplied. Previous read-only "
                    "actions are listed; read again only for information you still lack. "
                    "Do not repeat a read when the evidence is unchanged. "
                    "A restore uses policy=snapshot; no arbitrary resolver or proxy is permitted. "
                    "Only the independent oracle permits declare_online. An ONLINE network still "
                    "needs the separate application handshake. The fixed watchdog and mutation "
                    "budget do not reset. All arguments are required; $ placeholders use supplied "
                    "scope values. "
                )
                + (
                    "For Wi-Fi set_dns, hypothesis_after.layer must be dns and action.evidence_ids "
                    "must cite DNS oracle evidence. For restore_proxy, layer must be proxy, "
                    "hypothesis evidence must cite proxy drift or completed HTTPS measurement, "
                    "and action.evidence_ids must cite TLS oracle evidence, never dns-1. "
                    "A successful DNS check does not explain a remaining proxy repair. "
                    if policy.probe_profile == "lab_wifi"
                    else ""
                )
                + action_guide(scope, tools, snapshot_only=open_mode),
            ),
            Message(
                "user",
                json.dumps(
                    {
                        "oracle": oracle.verdict.value,
                        "hypothesis_before": raw["hypothesis_before"],
                        "evidence": facts,
                        "alternate_qualified": qualified,
                        "scope": {
                            "services": scope.services,
                            "candidates": scope.candidates,
                            "snapshots": scope.snapshots,
                            "evidence": scope.evidence,
                        },
                        "previous_actions": list(attempted),
                        **({"previous_read_actions": prior_reads} if open_mode else {}),
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
        schema_gbnf(after_schema),
    )
    value = object_json(read_json(after.text.removesuffix(EOT)))
    if not Draft202012Validator(after_schema).is_valid(value):
        policy.records[-1].failure = "probe_action_schema_refused"
        raise ContractError("probe_action_schema_refused")
    action = parse_action(json.dumps(value["action"]), scope, tools)
    policy.probe_result.update(
        hypothesis_after=value["hypothesis_after"],
        action=action.as_json(),
        action_verbatim=after.text,
        decision_ms=(time.monotonic() - begun) * 1000,
    )
    return Emission(json.dumps(action.as_json()), after.timings, raw_text=after.text)
