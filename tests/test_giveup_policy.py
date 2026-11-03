"""Small invariant pins for terminal-only v2; no inference or network."""

from dataclasses import replace

import pytest

from offline_agent.agent import RunResult
from offline_agent.giveup_policy import EXHAUSTED_REASONS, POLICY_ID, finalize
from offline_agent.workspace import TestResult as OracleEvidence
from offline_agent.workspace import content_hash

FILES = {"solution.py": "value = 0\n"}


def result(outcome: str = "budget_exhausted", reason: str = "step_budget") -> RunResult:
    return RunResult(
        "unlabeled", outcome, reason, 16, 100, 100, 0, 0, 16, 120, 118, "fixture", 0, FILES
    )


def red() -> OracleEvidence:
    return OracleEvidence(
        False, 1, False, 3, "Ran 3 tests\nFAILED (failures=1)", content_hash(FILES), 0.1
    )


@pytest.mark.parametrize("reason", sorted(EXHAUSTED_REASONS))
def test_exhaustion_attaches_exact_failing_evidence_without_extra_calls(reason: str) -> None:
    before = result(reason=reason)
    decision = finalize(before, red())
    assert decision.applied
    assert decision.result.outcome == "cannot_fix"
    assert decision.result.reason == POLICY_ID + ":" + reason
    assert replace(decision.result, outcome=before.outcome, reason=before.reason) == before
    assert decision.as_json()["verification"] == red().as_json()


@pytest.mark.parametrize("outcome", ["fixed", "budget_exhausted", "provider_error"])
def test_passing_final_tests_never_become_policy_giveup(outcome: str) -> None:
    before = result(outcome)
    decision = finalize(before, replace(red(), green=True, returncode=0, output="OK"))
    assert not decision.applied
    assert decision.result == before


@pytest.mark.parametrize(
    "verification",
    [
        replace(red(), tests_run=0),
        replace(red(), timed_out=True),
        replace(red(), returncode=0),
        replace(red(), returncode=-9),
        replace(red(), output="  "),
        replace(red(), workspace_hash="stale"),
    ],
)
def test_missing_timeout_crash_or_stale_evidence_never_fabricates_giveup(
    verification: OracleEvidence,
) -> None:
    before = result()
    decision = finalize(before, verification)
    assert not decision.applied
    assert decision.result == before


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("running", "step_budget"),
        ("provider_error", "http_or_response_failure"),
        ("fixed", "green"),
        ("cannot_fix", "model_reason"),
        ("budget_exhausted", "unknown"),
    ],
)
def test_no_early_or_infrastructure_conversion(outcome: str, reason: str) -> None:
    before = result(outcome, reason)
    assert finalize(before, red()).result == before
    assert not finalize(before, red()).applied


def test_red_then_green_at_last_available_step_has_no_premature_giveup() -> None:
    for step in range(1, 16):
        prefix = replace(result("running"), steps=step, provider_calls=step)
        assert not finalize(prefix, red()).applied
    last = replace(red(), green=True, returncode=0, output="Ran 3 tests\nOK")
    assert not finalize(result(), last).applied


def test_decision_cannot_see_task_labels_or_missing_fixture_names() -> None:
    before = result()
    generic = replace(red(), output="test_arithmetic ... FAIL\nRan 3 tests\nFAILED")
    assert finalize(before, generic).applied
