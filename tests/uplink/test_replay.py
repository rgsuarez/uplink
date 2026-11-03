"""Read-only ANSI replay tests with synthetic clocks and no process/network access."""

import io
from pathlib import Path

import pytest

from offline_agent.uplink.contracts import Action, Tool
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.replay import replay
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import TraceFormatError

from .test_trace import Clock, simple_trace


@pytest.mark.parametrize("speed,expected", [(0, []), (1, [2, 4]), (2, [1, 2])])
def test_replay_preserves_order_and_scales_clock(
    tmp_path: Path, redactor: Redactor, speed: float, expected: list[float]
) -> None:
    path = simple_trace(tmp_path, redactor)
    clock = Clock()
    output = io.StringIO()
    replay(path, speed=speed, color=False, output=output, clock=clock.clock, sleep=clock.sleep)
    assert clock.sleeps == expected
    text = output.getvalue()
    assert "\x1b" not in text and text.index("START") < text.index("DIAGNOSE") < text.index(
        "REPORT"
    ) < text.index("FINISH")
    assert "$ /sbin/ifconfig -l" in text
    colored = io.StringIO()
    replay(path, speed=0, color=True, output=colored)
    assert "\x1b[36m" in colored.getvalue() and "\x1b[0m" in colored.getvalue()


@pytest.mark.parametrize(
    "content,reason",
    [("private-canary{", "line 1: invalid_json"), ("{}", "invalid_event"), ("", "missing_finish")],
)
def test_malformed_line_gives_clear_value_blind_error(
    tmp_path: Path, content: str, reason: str
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(content)
    with pytest.raises(TraceFormatError, match=reason) as caught:
        replay(path, speed=0, output=io.StringIO())
    assert "private-canary" not in str(caught.value)


def test_replay_neutralizes_untrusted_ansi_and_rejects_invalid_speed(
    tmp_path: Path, redactor: Redactor
) -> None:
    with RunTrace(redactor, directory=tmp_path) as trace:
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation((), "\x1b[31muntrusted\rrewrite"),
        )
    output = io.StringIO()
    replay(trace.path, speed=0, color=False, output=output)
    assert "\x1b" not in output.getvalue() and "\\x1b" in output.getvalue()
    for speed in (-1, float("nan"), float("inf")):
        with pytest.raises(TraceFormatError):
            replay(trace.path, speed=speed)


def test_cli_replay_never_invokes_diagnostics(
    tmp_path: Path,
    redactor: Redactor,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse
    from unittest.mock import Mock

    from offline_agent.uplink import cli

    path = simple_trace(tmp_path, redactor)
    forbidden = Mock(side_effect=AssertionError("replay must not diagnose"))
    monkeypatch.setattr(cli, "diagnose", forbidden)
    cli.run(argparse.Namespace(uplink_command="trace", path=path, speed=0, no_color=True), tmp_path)
    assert "DIAGNOSE" in capsys.readouterr().out
    forbidden.assert_not_called()


def test_replay_and_validator_import_only_standard_library() -> None:
    import ast
    import sys

    from offline_agent.uplink import replay as replay_module
    from offline_agent.uplink import trace_format

    for module in (replay_module, trace_format):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                assert (
                    node.module == "offline_agent.uplink.trace_format"
                    or (node.module or "").split(".")[0] in sys.stdlib_module_names
                )


def test_replay_labels_fixture_mode_and_model_absence(tmp_path: Path, redactor: Redactor) -> None:
    with RunTrace(redactor, directory=tmp_path, mode="test") as trace:
        trace.step("diagnose", Action(Tool.READ_VITALS, {}))
    output = io.StringIO()
    replay(trace.path, speed=0, color=False, output=output)
    assert "mode: test; model: none" in output.getvalue()
    assert "grammar:" in output.getvalue()
