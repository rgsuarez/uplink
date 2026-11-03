"""Evaluator-only fixture contracts. Never import these expectations into model prompts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import cast

VERSION = "uplink-sealed-linux-v1"
CODES = ("P04", "D09", "X02", "G02", "I04", "I02", "X03", "X04", "C04", "P07", "F05", "V03")
FIELDS = {"version", "code", "catalog_id", "role", "baseline", "stages", "terminal"}
BASELINES = {"http_proxy", "https_proxy", "alternate", "ipv6"}
TOOLS = ("set_dns", "restore_proxy", "reroute")


def selection(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"fixture", "sha256"}:
        raise ValueError("sealed_selection_required")
    code, digest = value["fixture"], value["sha256"]
    if not isinstance(code, str) or code not in CODES:
        raise ValueError("sealed_fixture_not_allowlisted")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("sealed_fixture_hash_required")
    return {"fixture": code, "sha256": digest}


def fixture_path(code: str) -> Path:
    if code not in CODES:
        raise ValueError("sealed_fixture_not_allowlisted")
    staged = Path(__file__).with_name(code + ".json")
    if staged.exists() or staged.is_symlink():
        return staged
    parents = Path(__file__).resolve().parents
    if len(parents) < 4:
        raise ValueError("sealed_fixture_file_missing")
    return parents[3] / "harness/linux/sealed" / (code + ".json")


def parse(raw: object, code: str) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ValueError("sealed_fixture_schema")
    if raw["version"] != VERSION or raw["code"] != code or code not in CODES:
        raise ValueError("sealed_fixture_identity")
    if not isinstance(raw["catalog_id"], str) or not re.fullmatch(
        r"[a-z0-9_]{1,80}", raw["catalog_id"]
    ):
        raise ValueError("sealed_catalog_identity")
    if raw["role"] not in ("fault", "control"):
        raise ValueError("sealed_fixture_role")
    baseline = raw["baseline"]
    if not isinstance(baseline, dict) or set(baseline) != BASELINES:
        raise ValueError("sealed_baseline_schema")
    if (
        baseline["http_proxy"] not in ("configured", "direct")
        or baseline["https_proxy"] not in ("configured", "direct")
        or type(baseline["alternate"]) is not bool
        or baseline["ipv6"] not in ("disabled", "static_primary")
    ):
        raise ValueError("sealed_baseline_values")
    stages = raw["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= 3:
        raise ValueError("sealed_stages_required")
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict) or set(stage) != {"after", "flags", "verdict", "app"}:
            raise ValueError("sealed_stage_schema")
        if (index == 0 and stage["after"] != "inject") or (
            index > 0 and stage["after"] not in TOOLS
        ):
            raise ValueError("sealed_stage_action")
        if not isinstance(stage["flags"], str) or not re.fullmatch(r"[TF]{6}", stage["flags"]):
            raise ValueError("sealed_stage_checks")
        if (
            stage["verdict"] not in ("ONLINE", "OFFLINE", "DEGRADED", "CAPTIVE")
            or type(stage["app"]) is not bool
        ):
            raise ValueError("sealed_stage_outcome")
    terminal = raw["terminal"]
    if not isinstance(terminal, dict) or set(terminal) != {
        "tool",
        "layer",
        "component",
        "step",
        "reason",
        "citation",
    }:
        raise ValueError("sealed_terminal_schema")
    if terminal["tool"] not in ("recovered", "declare_online", "report", "give_up"):
        raise ValueError("sealed_terminal_tool")
    if not all(isinstance(value, str) for value in terminal.values()):
        raise ValueError("sealed_terminal_values")
    return cast(dict[str, object], raw)


def load(value: object) -> dict[str, object]:
    chosen = selection(value)
    path = fixture_path(chosen["fixture"])
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 16384:
        raise ValueError("sealed_fixture_file_refused")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != chosen["sha256"]:
        raise ValueError("sealed_fixture_hash_mismatch")
    return parse(json.loads(data), chosen["fixture"])
