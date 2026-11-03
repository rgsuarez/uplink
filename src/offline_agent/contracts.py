"""The entire model-facing action language. No free-form shell tool exists."""

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from jsonschema import Draft202012Validator

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]


class ContractError(ValueError):
    pass


class Tool(StrEnum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    SEARCH = "search"
    APPLY_PATCH = "apply_patch"
    RUN_TESTS = "run_tests"
    DECLARE_DONE = "declare_done"


def _string(max_length: int = 16000) -> dict[str, Json]:
    return {"type": "string", "maxLength": max_length}


ARGUMENTS: dict[Tool, dict[str, Json]] = {
    Tool.LIST_FILES: {"path": _string(240)},
    Tool.READ_FILE: {"path": _string(240)},
    Tool.SEARCH: {"path": _string(240), "query": _string(200)},
    Tool.APPLY_PATCH: {"path": _string(240), "old": _string(), "new": _string()},
    Tool.RUN_TESTS: {},
    Tool.DECLARE_DONE: {
        "status": {"enum": ["fixed", "cannot_fix"]},
        "reason": _string(1000),
    },
}


def tool_schema() -> dict[str, Json]:
    variants: list[Json] = []
    for tool, properties in ARGUMENTS.items():
        variants.append(
            {
                "type": "object",
                "properties": {
                    "tool": {"const": tool.value},
                    "args": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                },
                "required": ["tool", "args"],
                "additionalProperties": False,
            }
        )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "oneOf": variants}


def _normalize(value: object, depth: int = 0) -> Json:
    if depth > 20:
        raise ContractError("JSON nesting limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_normalize(item, depth + 1) for item in cast(list[object], value)]
    if isinstance(value, dict):
        pairs = cast(dict[object, object], value)
        if not all(isinstance(key, str) for key in pairs):
            raise ContractError("JSON object keys must be strings")
        return {str(key): _normalize(item, depth + 1) for key, item in pairs.items()}
    raise ContractError("Invalid JSON value")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("Duplicate JSON key")
        result[key] = value
    return result


def read_json(text: str, *, max_bytes: int = 100_000) -> Json:
    if len(text.encode()) > max_bytes:
        raise ContractError("JSON size limit")
    try:
        raw: object = json.loads(text, object_pairs_hook=_unique_pairs)
        return _normalize(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("Malformed JSON") from exc


def object_json(value: Json) -> dict[str, Json]:
    if not isinstance(value, dict):
        raise ContractError("Expected JSON object")
    return value


@dataclass(frozen=True)
class Action:
    tool: Tool
    args: dict[str, Json]

    def text(self, key: str) -> str:
        value = self.args[key]
        if not isinstance(value, str):
            raise ContractError("Expected string argument")
        return value

    def as_json(self) -> dict[str, Json]:
        return {"tool": self.tool.value, "args": self.args}


def parse_action(text: str) -> Action:
    obj = object_json(read_json(text))
    if not Draft202012Validator(tool_schema()).is_valid(obj):
        raise ContractError("Action does not match tool schema")
    name = obj["tool"]
    if not isinstance(name, str):
        raise ContractError("Tool must be a string")
    return Action(Tool(name), object_json(obj["args"]))


def action_text(tool: Tool, **args: Json) -> str:
    text = json.dumps({"tool": tool.value, "args": args}, ensure_ascii=True)
    parse_action(text)
    return text
