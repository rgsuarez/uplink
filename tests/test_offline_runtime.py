"""Real child startup and denied capabilities for the local offline test harness."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def test_active_virtualenv_dependencies_survive_python_child() -> None:
    assert sys.prefix != sys.base_prefix
    assert Path(sys.executable) == Path(sys.executable).resolve()
    child = subprocess.run(
        [sys.executable, "-B", "-c",
         "import json,jsonschema,sys; from pathlib import Path; "
         "print(json.dumps({'dependency_in_venv': "
         "Path(jsonschema.__file__).is_relative_to(Path(sys.argv[1])), "
         "'same_runtime': Path(sys.executable).resolve()==Path(sys.argv[2])}))",
         sys.prefix, sys.executable],
        capture_output=True, text=True, check=True, timeout=10, env=os.environ,
    )
    assert not child.stderr
    assert json.loads(child.stdout) == {"dependency_in_venv": True, "same_runtime": True}


def test_network_guard_denies_before_resolution() -> None:
    with pytest.raises(PermissionError, match="cpu_qualification_network_forbidden"):
        socket.getaddrinfo("synthetic.invalid", 1)


def test_host_tool_guard_denies_even_a_local_version_request() -> None:
    with pytest.raises(PermissionError, match="cpu_qualification_host_tool_forbidden"):
        subprocess.run(["ssh", "-V"], capture_output=True, check=True, timeout=5)


def test_weight_guard_denies_large_synthetic_fixture_without_reading_it(tmp_path: Path) -> None:
    fixture = tmp_path / "synthetic.gguf"
    with fixture.open("wb") as handle:
        handle.truncate(1_048_577)
    with pytest.raises(PermissionError, match="cpu_qualification_real_weights_forbidden"):
        fixture.read_bytes()
