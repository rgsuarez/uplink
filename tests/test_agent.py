import json
from pathlib import Path

import pytest

from offline_agent.agent import Budget, run_agent
from offline_agent.contracts import Tool, action_text
from offline_agent.providers import MockProvider


def test_fixed_without_green_tests_is_rejected(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    result = run_agent(
        task_id="false-fixed",
        mission="Repair addition",
        source=source,
        oracle=oracle,
        provider=MockProvider(
            (
                action_text(Tool.DECLARE_DONE, status="fixed", reason="Trust me"),
                action_text(Tool.DECLARE_DONE, status="cannot_fix", reason="No verified repair"),
            )
        ),
        trace=temp.parent / "trace.jsonl",
        temp_root=temp,
    )
    assert result.outcome == "cannot_fix"
    assert result.false_claims == 1
    rows = [json.loads(line) for line in (temp.parent / "trace.jsonl").read_text().splitlines()]
    assert rows[1]["error"] == "fixed_requires_current_green_tests"
    assert rows[-1]["event"] == "finish"


def test_successful_repair_trace(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    result = run_agent(
        task_id="repair",
        mission="Repair addition",
        source=source,
        oracle=oracle,
        provider=MockProvider(
            (
                action_text(Tool.LIST_FILES, path="."),
                action_text(Tool.READ_FILE, path="solution.py"),
                action_text(Tool.RUN_TESTS),
                action_text(Tool.APPLY_PATCH, path="solution.py", old="a - b", new="a + b"),
                action_text(Tool.RUN_TESTS),
                action_text(Tool.DECLARE_DONE, status="fixed", reason="Two oracle tests passed"),
            )
        ),
        trace=temp.parent / "trace.jsonl",
        temp_root=temp,
    )
    assert result.outcome == "fixed"
    assert result.invalid_actions == result.false_claims == 0
    assert result.steps == result.provider_calls == 6
    assert result.input_tokens + result.output_tokens <= Budget().max_tokens
    assert "a + b" in result.final_files["solution.py"]


def test_patch_invalidates_green_in_agent(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    (source / "solution.py").write_text("def add(a,b):\n    return a + b\n")
    result = run_agent(
        task_id="stale",
        mission="Repair",
        source=source,
        oracle=oracle,
        provider=MockProvider(
            (
                action_text(Tool.RUN_TESTS),
                action_text(Tool.APPLY_PATCH, path="solution.py", old="a + b", new="a - b"),
                action_text(Tool.DECLARE_DONE, status="fixed", reason="Old tests were green"),
            )
        ),
        trace=temp.parent / "trace.jsonl",
        temp_root=temp,
    )
    assert result.outcome == "cannot_fix"
    assert result.false_claims == 1


def test_step_budget(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    result = run_agent(
        task_id="steps",
        mission="Repair",
        source=source,
        oracle=oracle,
        provider=MockProvider(("invalid",) * 5),
        budget=Budget(max_steps=2),
        trace=temp.parent / "trace.jsonl",
        temp_root=temp,
    )
    assert result.outcome == "budget_exhausted"
    assert result.reason == "step_budget"
    assert result.invalid_actions == result.steps == 2


def test_token_budget_refuses_before_provider(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    result = run_agent(
        task_id="tokens",
        mission="Repair",
        source=source,
        oracle=oracle,
        provider=MockProvider(),
        budget=Budget(max_tokens=1),
        trace=temp.parent / "trace.jsonl",
        temp_root=temp,
    )
    assert result.reason == "token_budget"
    assert result.provider_calls == 0


@pytest.mark.parametrize("seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_time_budget_refused(seconds: float) -> None:
    with pytest.raises(ValueError):
        Budget(max_seconds=seconds)


@pytest.mark.parametrize("steps", [0, -1, True])
def test_invalid_step_budget_refused(steps: int) -> None:
    with pytest.raises(ValueError):
        Budget(max_steps=steps)
