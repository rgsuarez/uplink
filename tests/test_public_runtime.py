"""The venv runtime is readable without granting the entire home directory."""

import sys
from pathlib import Path

import pytest

from offline_agent.sandbox import SandboxUnavailable, macos_plan


def test_venv_base_runtime_is_readable_and_network_stays_denied(tmp_path):
    plan = macos_plan(tmp_path, tmp_path / "work", tmp_path / "oracle.py",
                      tmp_path / "home", tmp_path / "output")
    policy = plan.command[2]
    assert str(Path(sys.base_prefix).resolve()) in policy
    assert "(deny default)" in policy
    assert "allow network" not in policy
    assert plan.environment.keys() == {"PATH", "HOME", "TMPDIR"}


@pytest.mark.parametrize("prefix", ["/", "", str(Path.home())])
def test_unsafe_or_empty_runtime_prefix_is_refused(tmp_path, monkeypatch, prefix):
    monkeypatch.setattr(sys, "base_prefix", prefix)
    with pytest.raises(SandboxUnavailable, match="unsafe_python_runtime_prefix"):
        macos_plan(tmp_path, tmp_path / "work", tmp_path / "oracle.py",
                   tmp_path / "home", tmp_path / "output")
