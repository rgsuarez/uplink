"""Inert macOS plans. This module deliberately contains no subprocess executor."""

import ipaddress
import re
from dataclasses import dataclass, field

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.contracts import CHANGES, DNS_PRESETS, Action, Scope, Tool, validate
from offline_agent.uplink.privacy import Redactor

NETWORKSETUP = "/usr/sbin/networksetup"


@dataclass(frozen=True)
class ServiceSettings:
    id: str
    name: str = field(repr=False)
    interface: str
    dns: tuple[str, ...] = ()
    # scheme, host, port, enabled. Raw host is memory-only, never traced or snapshotted.
    proxies: tuple[tuple[str, str, int, bool], ...] = field(default=(), repr=False)
    dhcp: bool = True
    active: bool = True
    managed: bool = False

    def __post_init__(self) -> None:
        if not self.name or self.name.startswith("-") or any(ord(c) < 32 for c in self.name):
            raise ContractError("invalid_discovered_service")
        if not re.fullmatch(r"[a-z][a-z0-9]{0,14}", self.interface):
            raise ContractError("invalid_discovered_interface")
        for ip in self.dns:
            ipaddress.ip_address(ip)
        for scheme, host, port, _ in self.proxies:
            if scheme not in {"web", "secureweb"} or not 1 <= port <= 65535:
                raise ContractError("invalid_proxy_setting")
            if not re.fullmatch(r"[a-zA-Z0-9.:_-]{1,253}", host) or host.startswith("-"):
                raise ContractError("invalid_proxy_host")


@dataclass(frozen=True)
class PlanContext:
    scope: Scope
    current: tuple[ServiceSettings, ...]
    snapshot_id: str
    snapshot: tuple[ServiceSettings, ...]
    service_order: tuple[str, ...]
    # Only discovered candidates with prior evidence may enter this mapping.
    candidate_services: tuple[tuple[str, str], ...] = ()

    def service(self, service_id: str, *, previous: bool = False) -> ServiceSettings:
        rows = self.snapshot if previous else self.current
        match = next((s for s in rows if s.id == service_id), None)
        if match is None or match.managed:
            raise ContractError("service_missing_or_managed")
        return match


@dataclass(frozen=True)
class ConfirmCard:
    tool: str
    argv: tuple[tuple[str, ...], ...] = field(repr=False)
    why: str
    evidence_ids: tuple[str, ...]
    undo_argv: tuple[tuple[str, ...], ...] = field(repr=False)
    snapshot_id: str
    auto_revert_seconds: int = 20
    blocked_reason: str = "u1_plan_only"
    executable: bool = False

    def for_confirmation(self) -> dict[str, Json]:
        """Exact argv for a local confirmation UI only. Never log this raw object."""
        return {
            "tool": self.tool,
            "argv": [list(x) for x in self.argv],
            "why": self.why,
            "evidence_ids": list(self.evidence_ids),
            "undo_argv": [list(x) for x in self.undo_argv],
            "snapshot_id": self.snapshot_id,
            "auto_revert_seconds": self.auto_revert_seconds,
            "executable": self.executable,
            "blocked_reason": self.blocked_reason,
        }

    def for_trace(self, redactor: Redactor) -> dict[str, Json]:
        """Never serialize raw argv into model context, logs or persistent traces."""
        result = self.for_confirmation()
        for name, commands in (("argv", self.argv), ("undo_argv", self.undo_argv)):
            result[name] = [
                [redactor.handle("arg", item, remember=False) for item in command]
                for command in commands
            ]
        return result

    def for_journal(self, redactor: Redactor) -> dict[str, Json]:
        """Preserve fixed commands while masking service/proxy identity arguments."""
        result = self.for_confirmation()
        for key, commands in (("argv", self.argv), ("undo_argv", self.undo_argv)):
            rows: list[Json] = []
            for command in commands:
                args = list(command)
                if len(args) >= 3 and args[0] == NETWORKSETUP:
                    indices = range(2, len(args)) if args[1] == "-ordernetworkservices" else (2,)
                    for index in indices:
                        args[index] = redactor.handle("service", args[index], remember=False)
                    if args[1] in {"-setwebproxy", "-setsecurewebproxy"} and len(args) > 3:
                        args[3] = redactor.handle("host", args[3], remember=False)
                rows.append([redactor.scrub(arg) for arg in args])
            result[key] = rows
        return object_json(redactor.scrub_json(result))


def dns_command(service: ServiceSettings, servers: tuple[str, ...]) -> tuple[str, ...]:
    for server in servers:
        ipaddress.ip_address(server)
    return (NETWORKSETUP, "-setdnsservers", service.name, *(servers or ("Empty",)))


def proxy_commands(service: ServiceSettings, *, off: bool = False) -> tuple[tuple[str, ...], ...]:
    result: list[tuple[str, ...]] = []
    if {p[0] for p in service.proxies} != {"web", "secureweb"}:
        raise ContractError("complete_proxy_snapshot_required")
    for scheme, host, port, enabled in service.proxies:
        if not off:
            result.append((NETWORKSETUP, f"-set{scheme}proxy", service.name, host, str(port)))
        result.append(
            (
                NETWORKSETUP,
                f"-set{scheme}proxystate",
                service.name,
                "off" if off or not enabled else "on",
            )
        )
    return tuple(result)


def plan_change(action: Action, context: PlanContext) -> ConfirmCard:
    action = validate(action, context.scope)
    if action.tool not in CHANGES or action.text("snapshot_id") != context.snapshot_id:
        raise ContractError("change_requires_matching_snapshot")
    argv: tuple[tuple[str, ...], ...]
    undo: tuple[tuple[str, ...], ...]
    blocked = "u1_plan_only"
    if action.tool == Tool.FLUSH_DNS_CACHE:
        argv = (("/usr/bin/dscacheutil", "-flushcache"),)
        undo = ()
        blocked = "cache_eviction_has_no_inverse_u1_refused"
    elif action.tool in {Tool.SET_SERVICE_ORDER, Tool.REROUTE}:
        order = context.service_order
        if action.tool == Tool.REROUTE:
            service_id = dict(context.candidate_services).get(action.text("candidate_id"))
            if service_id is None:
                raise ContractError("reroute_requires_prior_evidence_and_configured_service")
            proposed = (service_id, *(x for x in order if x != service_id))
        else:
            proposed = action.texts("service_ids")
        if len(proposed) != len(order) or set(proposed) != set(order):
            raise ContractError("complete_service_order_required")
        argv = (
            (NETWORKSETUP, "-ordernetworkservices", *(context.service(x).name for x in proposed)),
        )
        undo = ((NETWORKSETUP, "-ordernetworkservices", *(context.service(x).name for x in order)),)
    else:
        service = context.service(action.text("service_id"))
        previous = context.service(service.id, previous=True)
        if action.tool == Tool.SET_DNS:
            policy = action.text("policy")
            servers = previous.dns if policy == "snapshot" else DNS_PRESETS.get(policy, ())
            argv, undo = (dns_command(service, servers),), (dns_command(service, service.dns),)
        elif action.tool == Tool.RESTORE_PROXY:
            argv = proxy_commands(previous, off=action.text("policy") == "off")
            undo = proxy_commands(service)
        elif action.tool == Tool.RENEW_LEASE:
            if not service.dhcp:
                raise ContractError("renew_requires_existing_dhcp")
            argv = (("/usr/sbin/ipconfig", "set", service.interface, "DHCP"),)
            undo = ()
            blocked = "lease_renewal_has_no_exact_inverse_u1_refused"
        elif action.tool == Tool.CYCLE_INTERFACE:
            if not service.active:
                raise ContractError("cycle_requires_initially_active_interface")
            argv = (
                ("/sbin/ifconfig", service.interface, "down"),
                ("/sbin/ifconfig", service.interface, "up"),
            )
            undo = (("/sbin/ifconfig", service.interface, "up"),)
        else:
            raise ContractError("unsupported_change")
    return ConfirmCard(
        action.tool.value,
        argv,
        action.text("why"),
        action.texts("evidence_ids"),
        undo,
        context.snapshot_id,
        blocked_reason=blocked,
    )


def execute_change(card: ConfirmCard) -> None:
    """No approval, flag, platform or privilege can enable a U1 network change."""
    raise ContractError("u1_changes_never_execute")
