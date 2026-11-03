"""GBNF and JSON Schema describe the same six-action protocol."""

import json

from offline_agent.contracts import ARGUMENTS, Tool


def gbnf() -> str:
    def literal(value: str) -> str:
        return json.dumps(value)

    names = [tool.value.replace("_", "-") for tool in Tool]
    lines = ["root ::= " + " | ".join(names)]
    for tool in Tool:
        name = tool.value.replace("_", "-")
        parts = [literal("{"), "ws", literal('"tool"'), "ws", literal(":"), "ws"]
        parts += [literal(json.dumps(tool.value)), "ws", literal(","), "ws"]
        parts += [literal('"args"'), "ws", literal(":"), "ws", literal("{"), "ws"]
        for index, key in enumerate(ARGUMENTS[tool]):
            if index:
                parts += [literal(","), "ws"]
            parts += [literal(json.dumps(key)), "ws", literal(":"), "ws"]
            parts += ["status" if key == "status" else "string", "ws"]
        parts += [literal("}"), "ws", literal("}"), "ws"]
        lines.append(name + " ::= " + " ".join(parts))
    lines += [
        'status ::= "\\"fixed\\"" | "\\"cannot_fix\\""',
        r'string ::= "\"" ([^"\\\x00-\x1F] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4}))* "\""',
        r"ws ::= [ \t\n\r]*",
    ]
    return "\n".join(lines) + "\n"
