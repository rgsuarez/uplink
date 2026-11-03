"""Private, atomic last-known-good evidence snapshots. No raw identifiers or argv."""

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.diagnostics import Vitals, rank_candidates
from offline_agent.uplink.oracle import OracleResult, oracle_fingerprint


def obj(properties: dict[str, Json]) -> dict[str, Json]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def string(pattern: str) -> dict[str, Json]:
    return {"type": "string", "pattern": "^(?:" + pattern + ")$", "maxLength": 120}


def sequence(item: Json) -> dict[str, Json]:
    return {"type": "array", "items": item, "maxItems": 256}


def nullable(value: Json) -> dict[str, Json]:
    return {"anyOf": [value, {"type": "null"}]}


HANDLE = string(r"[a-z]+-[0-9a-f]{24}")
INTERFACE = string(r"[a-z][a-z0-9]{0,14}")
IP = string(r"[0-9a-fA-F:.]+")
BOOL: dict[str, Json] = {"type": "boolean"}
PROXY = obj(
    {
        "kind": {"enum": ["http", "https", "socks", "pac", "wpad"]},
        "enabled": nullable(BOOL),
        "host_id": nullable(HANDLE),
        "port": nullable({"type": "integer", "minimum": 1, "maximum": 65535}),
    }
)
SERVICE = obj(
    {
        "dns": sequence(IP),
        "dns_mode": {"enum": ["manual", "dhcp", "unavailable"]},
        "proxies": sequence(PROXY),
        "interface": nullable(INTERFACE),
    }
)
INTERFACE_STATE = obj({"addresses": sequence(IP), "up": nullable(BOOL), "leased": BOOL})
STATE_SCHEMA = obj(
    {
        "services": {
            "type": "object",
            "patternProperties": {"^service-[0-9a-f]{24}$": SERVICE},
            "additionalProperties": False,
            "maxProperties": 128,
        },
        "interfaces": {
            "type": "object",
            "patternProperties": {"^[a-z][a-z0-9]{0,14}$": INTERFACE_STATE},
            "additionalProperties": False,
            "maxProperties": 128,
        },
        "service_order": sequence(HANDLE),
        "default_interface": nullable(INTERFACE),
        "default_gateway": nullable(IP),
    }
)
SNAPSHOT_SCHEMA = obj(
    {
        "version": {"const": 1},
        "id": string(r"(?:snapshot|checkpoint)-[0-9a-f]{32}"),
        "created_at": {"type": "number", "minimum": 0},
        "oracle_fingerprint": {"const": oracle_fingerprint()},
        "state": STATE_SCHEMA,
        "online": BOOL,
    }
)


def snapshot_state(vitals: Vitals) -> dict[str, Json]:
    value = vitals.as_json()
    services: dict[str, Json] = {}
    interfaces: dict[str, Json] = {}
    raw_services, raw_interfaces = value["services"], value["interfaces"]
    assert isinstance(raw_services, list) and isinstance(raw_interfaces, list)
    for raw in raw_services:
        row = object_json(raw)
        services[str(row["id"])] = {k: row[k] for k in ("dns", "dns_mode", "proxies", "interface")}
    for raw in raw_interfaces:
        row = object_json(raw)
        interfaces[str(row["id"])] = {k: row[k] for k in ("addresses", "up", "leased")}
    return {
        "services": services,
        "interfaces": interfaces,
        "service_order": value["service_order"],
        "default_interface": vitals.default_interface,
        "default_gateway": vitals.default_gateway,
    }


def write_private_json(path: Path, value: dict[str, Json]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("artifact_symlink_refused")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".uplink-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Snapshot:
    id: str
    created_at: float
    state: dict[str, Json]
    online: bool = True

    def as_json(self) -> dict[str, Json]:
        return {
            "version": 1,
            "id": self.id,
            "created_at": self.created_at,
            "state": self.state,
            "oracle_fingerprint": oracle_fingerprint(),
            "online": self.online,
        }


class SnapshotStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, vitals: Vitals, oracle: OracleResult) -> Snapshot:
        if not oracle.fresh_online(time.time()):
            raise ContractError("snapshot_requires_fresh_online_evidence")
        return self._write(vitals, online=True)

    def checkpoint(self, vitals: Vitals) -> Snapshot:
        """Capture pre-change state even offline; never promote it to last-known-good."""
        return self._write(vitals, online=False)

    def _write(self, vitals: Vitals, *, online: bool) -> Snapshot:
        prefix = "snapshot-" if online else "checkpoint-"
        snapshot = Snapshot(prefix + uuid.uuid4().hex, time.time(), snapshot_state(vitals), online)
        if not Draft202012Validator(SNAPSHOT_SCHEMA).is_valid(snapshot.as_json()):
            raise ContractError("snapshot_schema_refused")
        write_private_json(self.directory / f"{snapshot.id}.json", snapshot.as_json())
        return snapshot

    def read(self, path: Path) -> Snapshot:
        if path.is_symlink() or path.parent.resolve() != self.directory.resolve():
            raise ContractError("snapshot_path_refused")
        try:
            value = object_json(read_json(path.read_text(), max_bytes=262144))
        except UnicodeError as exc:
            raise ContractError("snapshot_encoding_refused") from exc
        if not Draft202012Validator(SNAPSHOT_SCHEMA).is_valid(value):
            raise ContractError("snapshot_schema_refused")
        created = value["created_at"]
        assert isinstance(created, (int, float))
        online = value["online"]
        assert isinstance(online, bool)
        if str(value["id"]).startswith("snapshot-") != online:
            raise ContractError("snapshot_kind_mismatch")
        return Snapshot(str(value["id"]), float(created), object_json(value["state"]), online)

    def latest(self) -> Snapshot | None:
        rows = [self.read(p) for p in self.directory.glob("snapshot-*.json")]
        return max(rows, key=lambda s: s.created_at) if rows else None


def diff_snapshot(snapshot: Snapshot, current: Vitals) -> tuple[dict[str, Json], ...]:
    changes: list[dict[str, Json]] = []

    def compare(path: str, before: Json, after: Json) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                compare(f"{path}.{key}" if path else key, before.get(key), after.get(key))
        elif before != after:
            changes.append({"field": path, "before": before, "after": after})

    compare("", snapshot.state, snapshot_state(current))
    return tuple(changes)


def with_prior_online(vitals: Vitals, snapshot: Snapshot | None) -> Vitals:
    if snapshot is None or not snapshot.online:
        return vitals
    default = snapshot.state["default_interface"]
    prior_services = object_json(snapshot.state["services"])
    candidates = []
    for c in vitals.candidates:
        # Online evidence covers the active default route, never every saved Wi-Fi SSID.
        if c.network_id is None and c.interface == default and c.service_id in prior_services:
            c = replace(
                c,
                evidence=tuple(sorted({*c.evidence, "verified_online_snapshot"})),
                score=100,
                eligible=True,
            )
        candidates.append(c)
    return replace(vitals, candidates=rank_candidates(tuple(candidates)))
