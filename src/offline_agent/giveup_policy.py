"""Opt-in v2: finalize an exhausted v1 run using the independent final oracle.

This policy does not interrupt inference, infer task labels, or declare success.
The v1 agent, prompts, budgets and trace bytes remain unchanged. A separate policy
receipt binds the original trace, final source and independent test evidence.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from offline_agent.agent import RunResult
from offline_agent.contracts import Json
from offline_agent.corpus import Task, local_file
from offline_agent.workspace import TestResult, Workspace, content_hash

POLICY_ID = "giveup-v2-budget-terminal"
EXHAUSTED_REASONS = frozenset(
    {"step_budget", "wall_budget", "token_budget", "provider_budget_overrun"}
)


@dataclass(frozen=True)
class PolicyDecision:
    result: RunResult
    applied: bool
    explanation: str
    verification: TestResult

    def as_json(self) -> dict[str, Json]:
        return {
            "version": 2,
            "policy_id": POLICY_ID,
            "applied": self.applied,
            "explanation": self.explanation,
            "result": self.result.as_json(),
            "verification": self.verification.as_json(),
        }


def finalize(result: RunResult, verification: TestResult) -> PolicyDecision:
    """No early threshold: consume 100% of the original available action budget.

    Only a completed, parsed, failing final test run on these exact files admits
    a policy give-up. Provider errors and missing/degraded evidence stay failures.
    Cannot-fix means not repaired within this budget, not mathematically unsolvable.
    """
    if verification.workspace_hash != content_hash(result.final_files):
        reason = "final_source_evidence_mismatch"
    elif verification.green:
        reason = "final_tests_green_preserve_v1"
    elif (
        verification.tests_run < 1
        or verification.timed_out
        or verification.returncode <= 0
        or not verification.output.strip()
    ):
        reason = "missing_or_degraded_failing_evidence"
    elif result.outcome != "budget_exhausted" or result.reason not in EXHAUSTED_REASONS:
        reason = "preserve_nonbudget_v1_terminal"
    else:
        return PolicyDecision(
            replace(result, outcome="cannot_fix", reason=POLICY_ID + ":" + result.reason),
            True,
            "Available v1 budget ended; independent final tests still fail. "
            "No claim that this task is inherently impossible.",
            verification,
        )
    return PolicyDecision(result, False, reason, verification)


def verify_source(root: Path, task: Task, files: dict[str, str]) -> TestResult:
    """The same immutable final-source verification as v1, retaining its output."""
    import tempfile

    if set(files) != {"solution.py"}:
        raise ValueError("final_source_contract_mismatch")
    temp_root = root / "artifacts/tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f8-final-", dir=temp_root) as name:
        source = Path(name) / "source"
        source.mkdir()
        (source / "solution.py").write_text(files["solution.py"])
        with Workspace(source, local_file(root, task.oracle), temp_root) as work:
            return work.run_tests(3)
