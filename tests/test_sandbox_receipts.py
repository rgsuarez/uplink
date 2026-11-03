"""A containment refusal retains the child's actual, bounded diagnostic evidence."""

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from offline_agent.workspace import TestResult as Result

record_result = cast(
    Callable[[Path, str, Result, bool], None],
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/linux_sandbox_smoke.py"))[
        "record_result"
    ],
)


@pytest.mark.parametrize(
    ("returncode", "timed_out", "tests", "accepted"),
    [(1, False, 4, True), (1, False, 0, False), (-9, True, 0, False)],
)
def test_receipt_survives_acceptance_launch_refusal_and_timeout(
    tmp_path: Path, returncode: int, timed_out: bool, tests: int, accepted: bool
) -> None:
    result = Result(
        False, returncode, timed_out, tests, "out\nerr\n", "a" * 64, 0.1, "out\n", "err\n"
    )
    target = tmp_path / "receipt.json"
    if accepted:
        record_result(target, "red-path", result, accepted)
    else:
        with pytest.raises(SystemExit) as error:
            record_result(target, "red-path", result, accepted)
        text = str(error.value)
        assert "red-path proof failed" in text and "err\\n" in text
    receipt = json.loads(target.read_text())
    assert receipt["accepted"] is accepted
    assert receipt["child"]["returncode"] == returncode
    assert receipt["child"]["timed_out"] is timed_out
    assert receipt["child"]["tests_run"] == tests
    assert receipt["child"]["stdout"] == "out\n"
    assert receipt["child"]["stderr"] == "err\n"
