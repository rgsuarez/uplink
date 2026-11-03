"""Prepared fake-only dry-run pins; no network, real serial or real history mutation."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def scanner() -> Any:
    script = Path(__file__).resolve().parents[1] / "scripts/serial_history_scrub.py"
    spec = importlib.util.spec_from_file_location("serial_scrub", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.name", "Synthetic Fixture")
    git(tmp_path, "config", "user.email", "rgsuarez@gmail.com")
    (tmp_path / "fixture.txt").write_text("device=SYNTHETIC123\n")
    git(tmp_path, "add", "--", "fixture.txt")
    git(tmp_path, "commit", "--quiet", "-m", "synthetic fixture")
    tip = git(tmp_path, "rev-parse", "HEAD")
    for name in ("first", "second"):
        git(tmp_path, "branch", name, tip)
    return tmp_path, {"refs/heads/first": tip, "refs/heads/second": tip}


def test_dry_run_reports_both_histories_without_mutating_refs_or_objects(
    scanner: Any,
    history: tuple[Path, dict[str, str]],
) -> None:
    root, pins = history
    before_refs = git(root, "show-ref")
    before_objects = git(root, "count-objects", "-v")
    result = scanner.inspect(root, pins, b"SYNTHETIC123")
    assert result["serial_bearing_objects"] == 1
    assert all(row["serial_bearing_objects"] == 1 for row in result["per_branch"].values())
    assert result["dry_run_complete"] is True and result["publication_ready"] is False
    assert result["source_git_objects_written"] == result["source_refs_written"] == 0
    assert "SYNTHETIC123" not in str(result)
    assert git(root, "show-ref") == before_refs
    assert git(root, "count-objects", "-v") == before_objects


def test_stale_tip_refuses_without_a_projection(
    scanner: Any,
    history: tuple[Path, dict[str, str]],
) -> None:
    root, pins = history
    pins["refs/heads/first"] = "0" * 40
    with pytest.raises(scanner.Refused, match="branch_pin_mismatch"):
        scanner.inspect(root, pins, b"SYNTHETIC123")


@pytest.mark.parametrize("pins", [{}, {"refs/heads/one": "0" * 40}])
def test_empty_or_partial_scope_is_not_a_success(scanner: Any, tmp_path: Path, pins: Any) -> None:
    with pytest.raises(scanner.Refused, match="exactly_two_explicit_branches_required"):
        scanner.inspect(tmp_path, pins, b"SYNTHETIC123")


@pytest.mark.parametrize("serial", [b"", b"short", b"contains-space "])
def test_invalid_serial_never_becomes_a_global_replacement(scanner: Any, serial: bytes) -> None:
    with pytest.raises(scanner.Refused, match="configured_serial_required"):
        scanner.scrub_projection(b"unchanged", serial)


def test_embedded_serial_projection_is_value_blind(scanner: Any) -> None:
    count, digest = scanner.scrub_projection(b"prefix-SYNTHETIC123-suffix", b"SYNTHETIC123")
    assert count == 1 and len(digest) == 64


def test_parent_traversal_cannot_escape_private_report_directory(
    scanner: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scanner, "load_config", lambda _: SimpleNamespace(approved_serial="TEST000001")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "serial_history_scrub.py",
            "--dry-run",
            "--repo",
            str(tmp_path),
            "--private-config",
            "unused",
            "--branch",
            "refs/heads/a=" + "a" * 40,
            "--branch",
            "refs/heads/b=" + "b" * 40,
            "--output",
            str(tmp_path / "artifacts/../escaped.json"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        scanner.main()
    assert error.value.code == 1
    assert not (tmp_path / "escaped.json").exists()
