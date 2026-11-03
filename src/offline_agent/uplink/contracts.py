"""Closed, versioned action contract. No model-authored text can become argv."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json, read_json

VERSION = 1


class Tool(StrEnum):
    READ_VITALS = "read_vitals"
    PROBE_ONLINE = "probe_online"
    READ_DNS = "read_dns"
    READ_PROXY = "read_proxy"
    READ_ROUTES = "read_routes"
    LIST_ROUTE_CANDIDATES = "list_route_candidates"
    DIFF_SNAPSHOT = "diff_snapshot"
    SET_DNS = "set_dns"
    RESTORE_PROXY = "restore_proxy"
    RENEW_LEASE = "renew_lease"
    FLUSH_DNS_CACHE = "flush_dns_cache"
    CYCLE_INTERFACE = "cycle_interface"
    REROUTE = "reroute"
    SET_SERVICE_ORDER = "set_service_order"
    REPORT = "report"
    DECLARE_ONLINE = "declare_online"
    GIVE_UP = "give_up"


CHANGES = frozenset(
    {
        Tool.SET_DNS,
        Tool.RESTORE_PROXY,
        Tool.RENEW_LEASE,
        Tool.FLUSH_DNS_CACHE,
        Tool.CYCLE_INTERFACE,
        Tool.REROUTE,
        Tool.SET_SERVICE_ORDER,
    }
)
REASONS = ("snapshot_drift", "dns_failed", "proxy_failed", "lease_missing", "alternate_route")
COMPONENTS = ("dns", "proxy", "lease", "interface", "upstream", "portal", "site", "unknown")
PHYSICAL_STEPS = ("check_cable", "enable_saved_hotspot", "connect_phone_usb", "open_portal", "wait")
DNS_PRESETS = {"cloudflare": (_public_fixture("@PUBLIC_IPV4_003@"), _public_fixture("@PUBLIC_IPV4_002@")), "google": (_public_fixture("@PUBLIC_IPV4_095@"), _public_fixture("@PUBLIC_IPV4_094@"))}


@dataclass(frozen=True)
class Scope:
    """Discovery-owned handles, never arbitrary model-supplied service names."""

    services: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    snapshots: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    profile: str = "general"

    def __post_init__(self) -> None:
        if self.profile not in {"general", "lab_dns_proxy_snapshot"}:
            raise ContractError("unknown_action_profile")


EMPTY_SCOPE = Scope()


def enum(values: tuple[str, ...]) -> dict[str, Json]:
    # An empty enum would be an invalid schema; false is an unsatisfiable schema.
    return {"enum": list(values)} if values else {"not": {}}


def array(values: tuple[str, ...], maximum: int = 8) -> dict[str, Json]:
    return {
        "type": "array",
        "items": enum(values),
        "minItems": 1,
        "maxItems": maximum,
        "uniqueItems": True,
    }


def arguments(scope: Scope) -> dict[Tool, dict[str, Json]]:
    result: dict[Tool, dict[str, Json]] = {tool: {} for tool in Tool}
    result[Tool.DIFF_SNAPSHOT] = {"snapshot_id": enum(scope.snapshots)}
    for tool in CHANGES:
        result[tool] = {
            "snapshot_id": enum(scope.snapshots),
            "why": enum(REASONS),
            "evidence_ids": array(scope.evidence),
        }
        if tool not in {Tool.FLUSH_DNS_CACHE, Tool.REROUTE, Tool.SET_SERVICE_ORDER}:
            result[tool]["service_id"] = enum(scope.services)
    result[Tool.SET_DNS]["policy"] = enum(("dhcp", "snapshot", *DNS_PRESETS))
    result[Tool.RESTORE_PROXY]["policy"] = enum(("off", "snapshot"))
    result[Tool.REROUTE]["candidate_id"] = enum(scope.candidates)
    result[Tool.SET_SERVICE_ORDER]["service_ids"] = array(scope.services, 32)
    result[Tool.REPORT] = {
        "component": enum(COMPONENTS),
        "physical_step": enum(PHYSICAL_STEPS),
        "evidence_ids": array(scope.evidence),
    }
    result[Tool.GIVE_UP] = {"reason": enum(("insufficient_evidence", "needs_human", "budget"))}
    if scope.profile == "lab_dns_proxy_snapshot":
        result = {
            tool: fields
            for tool, fields in result.items()
            if tool not in CHANGES or tool in {Tool.SET_DNS, Tool.RESTORE_PROXY}
        }
        result[Tool.SET_DNS]["policy"] = enum(("snapshot",))
        result[Tool.RESTORE_PROXY]["policy"] = enum(("snapshot",))
    return result


def action_schema(scope: Scope, tools: tuple[Tool, ...] | None = None) -> dict[str, Json]:
    variants: list[Json] = []
    fields_by_tool = arguments(scope)
    for tool in tools if tools is not None else fields_by_tool:
        if tool not in fields_by_tool:
            continue
        fields = fields_by_tool[tool]
        variants.append(
            {
                "type": "object",
                "properties": {
                    "version": {"const": VERSION},
                    "tool": {"const": tool.value},
                    "args": {
                        "type": "object",
                        "properties": fields,
                        "required": list(fields),
                        "additionalProperties": False,
                    },
                },
                "required": ["version", "tool", "args"],
                "additionalProperties": False,
            }
        )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "oneOf": variants}


@dataclass(frozen=True)
class Action:
    tool: Tool
    args: dict[str, Json]

    def text(self, key: str) -> str:
        value = self.args[key]
        if not isinstance(value, str):
            raise ContractError("expected_string")
        return value

    def texts(self, key: str) -> tuple[str, ...]:
        value = self.args[key]
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ContractError("expected_strings")
        return tuple(cast(list[str], value))

    def as_json(self) -> dict[str, Json]:
        return {"version": VERSION, "tool": self.tool.value, "args": self.args}


def parse_action(
    text: str, scope: Scope = EMPTY_SCOPE, tools: tuple[Tool, ...] | None = None
) -> Action:
    value = object_json(read_json(text, max_bytes=8192))
    if not Draft202012Validator(action_schema(scope, tools)).is_valid(value):
        # Never put raw invalid model output or validation errors into traces.
        raise ContractError("uplink_action_refused")
    return Action(Tool(str(value["tool"])), object_json(value["args"]))


def validate(action: Action, scope: Scope) -> Action:
    return parse_action(json.dumps(action.as_json()), scope)
