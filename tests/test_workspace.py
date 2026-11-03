
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
from pathlib import Path

import pytest

from offline_agent.workspace import Workspace, WorkspaceError


def test_red_patch_green_and_copy_isolation(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    before = (source / "solution.py").read_text()
    with Workspace(source, oracle, temp) as work:
        assert not work.can_declare_fixed()
        red = work.run_tests(3)
        assert not red.green, red.output
        assert red.tests_run == 2
        work.patch("solution.py", "a - b", "a + b")
        green = work.run_tests(3)
        assert green.green, green.output
        assert work.can_declare_fixed()
        work.patch("solution.py", "a + b", "a * b")
        assert not work.can_declare_fixed()
        assert not work.run_tests(3).green
    assert (source / "solution.py").read_text() == before
    assert not any(temp.iterdir())


@pytest.mark.parametrize("path", ["../oracle.py", "/etc/passwd", ".git/config", "missing.py"])
def test_paths_blocked(task_files: tuple[Path, Path, Path], path: str) -> None:
    with Workspace(*task_files) as work, pytest.raises(WorkspaceError):
        work.read_file(path)


def test_symlink_source_refused(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    (source / "link.py").symlink_to(oracle)
    with pytest.raises(WorkspaceError, match="Symlinks"):
        Workspace(source, oracle, temp)


@pytest.mark.parametrize(
    ("old", "new"), [("", "x"), ("missing", "x"), ("a", "x"), ("a - b", "a - b")]
)
def test_ambiguous_or_empty_patch_refused(
    task_files: tuple[Path, Path, Path], old: str, new: str
) -> None:
    with Workspace(*task_files) as work, pytest.raises(WorkspaceError):
        work.patch("solution.py", old, new)


def test_read_list_and_literal_search(task_files: tuple[Path, Path, Path]) -> None:
    with Workspace(*task_files) as work:
        assert work.list_files(".") == ["solution.py"]
        assert work.search(".", "a - b") == ["solution.py:2:    return a - b"]
        with pytest.raises(WorkspaceError):
            work.search(".", "")


def test_timeout_and_no_tests_cannot_be_green(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    oracle.write_text("while True: pass\n")
    with Workspace(source, oracle, temp) as work:
        result = work.run_tests(0.15)
        assert result.timed_out
        assert not result.green
    oracle.write_text("pass\n")
    with Workspace(source, oracle, temp) as work:
        assert not work.run_tests(3).green


def test_no_secret_environment_in_child(
    task_files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, oracle, temp = task_files
    monkeypatch.setenv("F1_TEST_SENTINEL", "should-not-be-in-child")
    (source / "solution.py").write_text(
        "import os\ndef add(a,b):\n"
        "    assert 'F1_TEST_SENTINEL' not in os.environ\n    return a+b\n"
    )
    with Workspace(source, oracle, temp) as work:
        result = work.run_tests(3)
        assert result.green, result.output


def test_os_sandbox_blocks_network_and_oracle_write(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    (source / "solution.py").write_text(
        _public_fixture("import socket\nfrom pathlib import Path\n"
        "def add(a,b):\n"
        "    try:\n        socket.create_connection(('@PUBLIC_IPV4_056@', 9), timeout=.1)\n"
        "    except OSError:\n        pass\n    else:\n        raise AssertionError('network')\n"
        "    try:\n        (Path.cwd().parent/'oracle.py').write_text('pass')\n"
        "    except OSError:\n        pass\n"
        "    else:\n        raise AssertionError('oracle write')\n"
        "    return a+b\n")
    )
    with Workspace(source, oracle, temp) as work:
        result = work.run_tests(3)
        assert result.green, result.output


def test_opt_in_diagnostics_preserve_child_streams(task_files: tuple[Path, Path, Path]) -> None:
    source, oracle, temp = task_files
    original = (source / "solution.py").read_text()
    (source / "solution.py").write_text("print('child-stdout-marker', flush=True)\n" + original)
    with Workspace(source, oracle, temp) as work:
        result = work.run_tests(3, separate_streams=True)
        assert not result.green and result.returncode == 1 and result.tests_run == 2
        assert result.stdout == "child-stdout-marker\n"
        assert result.stderr is not None and "Ran 2 tests" in result.stderr
        assert result.as_json()["stdout"] == result.stdout
        assert result.as_json()["stderr"] == result.stderr
        normal = work.run_tests(3)
        assert normal.stdout is None and normal.stderr is None
        assert "stdout" not in normal.as_json() and "stderr" not in normal.as_json()
