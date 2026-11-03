import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from offline_agent.contracts import ContractError, Tool, action_text, parse_action, tool_schema
from offline_agent.grammar import gbnf


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (Tool.LIST_FILES, {"path": "."}),
        (Tool.READ_FILE, {"path": "solution.py"}),
        (Tool.SEARCH, {"path": ".", "query": "return"}),
        (Tool.APPLY_PATCH, {"path": "solution.py", "old": "a - b", "new": "a + b"}),
        (Tool.RUN_TESTS, {}),
        (Tool.DECLARE_DONE, {"status": "fixed", "reason": "Current tests passed"}),
    ],
)
def test_six_tools(tool: Tool, args: dict[str, str]) -> None:
    text = json.dumps({"tool": tool.value, "args": args})
    assert parse_action(text).tool == tool


@pytest.mark.parametrize(
    "text",
    [
        "",
        "```json\n{}\n```",
        "[]",
        "null",
        "NaN",
        '{"tool":"run_tests","tool":"run_tests","args":{}}',
        '{"tool":"run_tests","args":{},"thought":"text"}',
        '{"tool":"shell","args":{"command":"ls"}}',
        '{"tool":"run_tests","args":{"command":"true"}}',
        '{"tool":"read_file","args":{"path":5}}',
        '{"tool":"declare_done","args":{"status":"maybe","reason":""}}',
        '{"tool":"read_file","args":{"path":NaN}}',
    ],
)
def test_invalid_actions(text: str) -> None:
    with pytest.raises(ContractError):
        parse_action(text)


def test_published_contracts_match() -> None:
    root = Path(__file__).resolve().parents[1]
    assert json.loads((root / "schemas/tools.schema.json").read_text()) == tool_schema()
    assert (root / "schemas/tools.gbnf").read_text() == gbnf()
    Draft202012Validator.check_schema(tool_schema())
    assert parse_action(action_text(Tool.RUN_TESTS)).args == {}
