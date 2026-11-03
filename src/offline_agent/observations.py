"""Stable model-facing observations; raw diagnostics remain untouched in JSONL traces."""

import json
import re

from offline_agent.contracts import Action, Json, Tool, object_json


def observation_message(action: Action, observation: Json) -> str:
    normalized = observation
    if (
        action.tool == Tool.RUN_TESTS
        and isinstance(observation, dict)
        and "tests_run" in observation
    ):
        result = dict(object_json(observation))
        result.pop("elapsed_s", None)
        output = result.get("output")
        if isinstance(output, str):
            output = re.sub(
                r'File "[^"\n]*/(oracle|solution)\.py"', r'File "/sandbox/\1.py"', output
            )
            result["output"] = re.sub(
                r"(Ran \d+ tests? in )\d+(?:\.\d+)?s", r"\1<duration>s", output
            )
        normalized = result
    return "Tool observation: " + json.dumps(normalized)[:12_000]
