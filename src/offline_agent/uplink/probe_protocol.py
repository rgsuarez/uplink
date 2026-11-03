"""Closed read-only probe programs. Stdlib-only so the same validator runs in the VM.

Programs contain identifiers and bounded integers, never code, paths, URLs or argv.
The execution adapter, rather than the model, resolves every identifier.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import cast

VERSION = 1
MAX_PROBES = 8
MAX_EPISODE_MS = 30_000
MAX_TIMEOUT_MS = 5_000
PROFILES = ("linux_lab", "lab_wifi")
TIMEOUTS = (500, 1000, 2000, 4000, 5000)
OPS = ("read_state", "ping_gateway", "dns_query", "tcp_connect", "https_head", "captive_check")


class ProbeRefused(ValueError):
    pass


@dataclass(frozen=True)
class Probe:
    op: str
    values: dict[str, str | int]

    @property
    def timeout_ms(self) -> int:
        return cast(int, self.values["timeout_ms"])

    def as_json(self) -> dict[str, object]:
        return {"op": self.op, **self.values}


def options(profile: str, alternate: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if profile not in PROFILES or type(alternate) is not bool:
        raise ProbeRefused("unknown_probe_profile")
    paths = ("current", "alternate") if profile == "linux_lab" and alternate else ("current",)
    resolvers = (
        ("configured", "alternate")
        if profile == "linux_lab"
        else ("configured", "cloudflare", "google")
    )
    return paths, resolvers


def fields(profile: str, alternate: bool) -> dict[str, dict[str, tuple[str | int, ...]]]:
    paths, resolvers = options(profile, alternate)
    return {
        "read_state": {"timeout_ms": TIMEOUTS},
        "ping_gateway": {
            "path": paths,
            "count": (1, 3, 5, 10, 20),
            "interval_ms": (200, 500, 1000),
            "timeout_ms": TIMEOUTS,
        },
        "dns_query": {"name": ("one", "two"), "resolver": resolvers, "timeout_ms": TIMEOUTS},
        "tcp_connect": {
            "endpoint": ("one", "two", "proxy"),
            "path": ("current",),
            "timeout_ms": TIMEOUTS,
        },
        "https_head": {
            "endpoint": ("one", "two"),
            "path": ("current",),
            "timeout_ms": TIMEOUTS,
        },
        "captive_check": {"endpoint": ("one", "two"), "timeout_ms": TIMEOUTS},
    }


def validate_program(value: object, profile: str, *, alternate: bool = False) -> tuple[Probe, ...]:
    allowed = fields(profile, alternate)
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "probes"}
        or type(value["version"]) is not int
        or value["version"] != VERSION
        or not isinstance(value["probes"], list)
        or not 1 <= len(value["probes"]) <= MAX_PROBES
    ):
        raise ProbeRefused("invalid_probe_program")
    result = []
    for row in value["probes"]:
        if not isinstance(row, dict) or not isinstance(row.get("op"), str):
            raise ProbeRefused("invalid_probe")
        op = row["op"]
        if op not in allowed or set(row) != {"op", *allowed[op]}:
            raise ProbeRefused("probe_primitive_or_fields_refused")
        values: dict[str, str | int] = {}
        for key, choices in allowed[op].items():
            item = row[key]
            if not any(type(item) is type(option) and item == option for option in choices):
                raise ProbeRefused("probe_argument_refused")
            values[key] = cast(str | int, item)
        probe = Probe(op, values)
        if op == "ping_gateway" and (
            (cast(int, values["count"]) - 1) * cast(int, values["interval_ms"]) >= probe.timeout_ms
        ):
            raise ProbeRefused("ping_burst_exceeds_timeout")
        result.append(probe)
    if sum(p.timeout_ms for p in result) > MAX_EPISODE_MS:
        raise ProbeRefused("probe_program_budget_exceeded")
    return tuple(result)


def parse_program(text: str, profile: str, *, alternate: bool = False) -> tuple[Probe, ...]:
    if len(text.encode()) > 16_384:
        raise ProbeRefused("probe_program_size_limit")
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ProbeRefused("probe_program_json_refused") from exc
    return validate_program(value, profile, alternate=alternate)


def program_schema(profile: str, *, alternate: bool = False) -> dict[str, object]:
    variants = []
    for op, arguments in fields(profile, alternate).items():
        properties: dict[str, object] = {"op": {"const": op}}
        properties.update({key: {"enum": list(values)} for key, values in arguments.items()})
        variants.append(
            {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        )
    return {
        "type": "object",
        "properties": {
            "version": {"const": VERSION},
            "probes": {
                "type": "array",
                "items": {"oneOf": variants},
                "minItems": 1,
                "maxItems": MAX_PROBES,
            },
        },
        "required": ["version", "probes"],
        "additionalProperties": False,
    }


@dataclass
class ProbeBudget:
    attempted: int = 0
    reserved_ms: int = 0
    elapsed_ms: float = 0.0

    @classmethod
    def read(cls, value: object) -> ProbeBudget:
        if (
            not isinstance(value, dict)
            or set(value) != {"attempted", "reserved_ms", "elapsed_ms"}
            or type(value["attempted"]) is not int
            or not 0 <= value["attempted"] <= MAX_PROBES
            or type(value["reserved_ms"]) is not int
            or not 0 <= value["reserved_ms"] <= MAX_EPISODE_MS
            or type(value["elapsed_ms"]) not in (int, float)
            or not math.isfinite(value["elapsed_ms"])
            or value["elapsed_ms"] < 0
        ):
            raise ProbeRefused("invalid_probe_budget")
        return cls(value["attempted"], value["reserved_ms"], float(value["elapsed_ms"]))

    def admit(self, probes: tuple[Probe, ...]) -> None:
        if (
            self.attempted + len(probes) > MAX_PROBES
            or self.reserved_ms + sum(p.timeout_ms for p in probes) > MAX_EPISODE_MS
            or self.elapsed_ms >= MAX_EPISODE_MS
        ):
            raise ProbeRefused("probe_episode_budget_exceeded")
        self.attempted += len(probes)
        self.reserved_ms += sum(p.timeout_ms for p in probes)

    def as_json(self) -> dict[str, object]:
        return {
            "attempted": self.attempted,
            "reserved_ms": self.reserved_ms,
            "elapsed_ms": self.elapsed_ms,
        }
