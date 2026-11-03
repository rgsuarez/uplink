"""GBNF compiled from the action schema, using the coding grammar's literal framing.

Canonical key order is intentional. Runtime validation additionally enforces array
uniqueness and discovery membership. No generic JSON/string/argv rule exists.
"""

import json

from offline_agent.contracts import Json, object_json
from offline_agent.uplink.contracts import EMPTY_SCOPE, Scope, Tool, action_schema


def gbnf(scope: Scope = EMPTY_SCOPE, tools: tuple[Tool, ...] | None = None) -> str:
    return schema_gbnf(action_schema(scope, tools))


def schema_gbnf(schema: dict[str, Json]) -> str:
    rules: list[str] = []
    counter = 0

    def compile_rule(schema: dict[str, Json]) -> str | None:
        nonlocal counter
        counter += 1
        name = f"v{counter}"
        expression: str
        if "not" in schema:
            return None
        if "const" in schema:
            expression = json.dumps(json.dumps(schema["const"]))
        elif "enum" in schema:
            values = schema["enum"]
            assert isinstance(values, list)
            expression = " | ".join(json.dumps(json.dumps(v)) for v in values)
        elif "oneOf" in schema:
            variants = schema["oneOf"]
            assert isinstance(variants, list)
            children = [compile_rule(object_json(v)) for v in variants]
            expression = " | ".join(c for c in children if c is not None)
        elif schema.get("type") == "object":
            properties = object_json(schema["properties"])
            parts = [json.dumps("{"), "ws"]
            for index, (key, child) in enumerate(properties.items()):
                compiled = compile_rule(object_json(child))
                if compiled is None:
                    return None
                if index:
                    parts += [json.dumps(","), "ws"]
                parts += [json.dumps(json.dumps(key)), "ws", json.dumps(":"), "ws", compiled, "ws"]
            expression = " ".join([*parts, json.dumps("}")])
        elif schema.get("type") == "array" and "prefixItems" in schema:
            items = schema["prefixItems"]
            assert isinstance(items, list) and items
            assert schema["minItems"] == schema["maxItems"] == len(items)
            parts = [json.dumps("["), "ws"]
            for index, item in enumerate(items):
                child = compile_rule(object_json(item))
                if child is None:
                    return None
                if index:
                    parts += [json.dumps(","), "ws"]
                parts += [child, "ws"]
            expression = " ".join([*parts, json.dumps("]")])
        elif schema.get("type") == "array":
            child = compile_rule(object_json(schema["items"]))
            if child is None:
                return None
            maximum = schema["maxItems"]
            assert isinstance(maximum, int)
            expression = (
                f'"[" ws {child} ws "]"'
                if maximum == 1
                else f'"[" ws {child} (ws "," ws {child}){{0,{maximum - 1}}} ws "]"'
            )
        else:
            raise ValueError("unsupported_grammar_schema")
        rules.append(f"{name} ::= {expression}")
        return name

    root = compile_rule(schema)
    assert root is not None
    return f"root ::= {root} ws\n" + "\n".join(rules) + "\nws ::= [ \\t\\n\\r]*\n"
