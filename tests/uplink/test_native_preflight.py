"""Staging admission pins. Temporary files and fake compiler only; no native effects."""

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "native_preflight", ROOT / "scripts/check_macos_swift.py"
)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    native = tmp_path / "harness/macos"
    native.mkdir(parents=True)
    (native / "First.swift").write_text('print("fixture")\n')
    return tmp_path


@pytest.fixture
def compiler(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gate.os, "geteuid", lambda: 501)
    compiler = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(gate.subprocess, "run", compiler)
    return compiler


def receipt(source: Path) -> Path:
    result = gate.typecheck(source)
    path = source / "receipt.json"
    path.write_text(json.dumps(result))
    return path


def test_typechecks_every_swift_file_once_in_a_removed_temp_directory(
    source: Path,
    compiler: Mock,
) -> None:
    nested = source / "harness/macos/nested"
    nested.mkdir()
    (nested / "Second.swift").write_text('print("second")\n')
    (nested / "not-swift.txt").write_text("not a compiler input")
    originals = gate.swift_sources(source)

    def checked(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        directory = Path(str(kwargs["cwd"]))
        assert directory != source and directory.is_dir()
        assert argv[:4] == ["xcrun", "swiftc", "-typecheck", "-module-cache-path"]
        assert Path(argv[4]).parent == directory
        assert (directory / argv[5]).read_bytes() == originals[argv[5]]
        return subprocess.CompletedProcess(argv, 0, "", "")

    compiler.side_effect = checked
    result = gate.typecheck(source)
    assert result["passed"] is True and result["temporary_directory_removed"] is True
    assert compiler.call_count == 2
    assert set(result["results"]) == set(originals)
    for call in compiler.call_args_list:
        assert not Path(call.kwargs["cwd"]).exists()
    assert gate.swift_sources(source) == originals


@pytest.mark.parametrize("system,uid", [("Darwin", 0), ("Linux", 501)])
def test_refuses_root_or_non_macos_before_compiler(
    source: Path,
    compiler: Mock,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    uid: int,
) -> None:
    monkeypatch.setattr(gate.platform, "system", lambda: system)
    monkeypatch.setattr(gate.os, "geteuid", lambda: uid)
    with pytest.raises(ValueError, match="requires_nonroot_macos"):
        gate.typecheck(source)
    compiler.assert_not_called()


def test_missing_sources_cannot_be_an_empty_pass(tmp_path: Path, compiler: Mock) -> None:
    with pytest.raises(ValueError, match="swift_sources_required"):
        gate.typecheck(tmp_path)
    compiler.assert_not_called()


def test_source_symlink_cannot_hide_unchecked_input(source: Path, compiler: Mock) -> None:
    (source / "harness/macos/linked.swift").symlink_to(source / "harness/macos/First.swift")
    with pytest.raises(ValueError, match="symlink_refused"):
        gate.typecheck(source)
    compiler.assert_not_called()


def test_compiler_failure_is_recorded_without_retry(source: Path, compiler: Mock) -> None:
    (source / "harness/macos/Second.swift").write_text("unreached\n")
    compiler.return_value = subprocess.CompletedProcess([], 1, "", "synthetic type error")
    result = gate.typecheck(source)
    assert result["passed"] is False and result["temporary_directory_removed"] is True
    assert result["results"]["harness/macos/First.swift"] == {
        "exit_code": 1,
        "reason": "native_typecheck_failed",
        "diagnostic": "synthetic type error",
    }
    compiler.assert_called_once()


@pytest.mark.parametrize(
    "error,reason",
    [
        (FileNotFoundError(), "native_compiler_unavailable"),
        (subprocess.TimeoutExpired("swiftc", 120), "native_typecheck_timeout"),
    ],
)
def test_unavailable_compiler_cannot_produce_a_pass(
    source: Path,
    compiler: Mock,
    error: Exception,
    reason: str,
) -> None:
    compiler.side_effect = error
    result = gate.typecheck(source)
    assert result["passed"] is False and result["temporary_directory_removed"] is True
    assert result["results"]["harness/macos/First.swift"] == {
        "exit_code": None,
        "reason": reason,
        "diagnostic": "",
    }
    compiler.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"sources": {}},
        {"gate_sha256": "0" * 64},
        {"non_root": False},
        {"temporary_directory_removed": False},
        {"results": {}},
        {"compiler": ["xcrun", "swiftc", "-parse"]},
        {"results": {"harness/macos/First.swift": {"exit_code": 1, "reason": "passed"}}},
        {"results": {"harness/macos/First.swift": {"exit_code": False, "reason": "passed"}}},
    ],
)
def test_staging_refuses_failed_stale_incomplete_or_parse_only_receipt(
    source: Path,
    compiler: Mock,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    path = receipt(source)
    value = json.loads(path.read_text())
    value.update(change)
    path.write_text(json.dumps(value))
    payload = Mock(side_effect=AssertionError("must refuse before packaging"))
    monkeypatch.setattr(gate, "git_payload", payload)
    output = source / "stage.tar"
    with pytest.raises(ValueError):
        gate.archive(source, path, output, "HEAD")
    assert not output.exists()
    payload.assert_not_called()


def test_staging_refuses_missing_receipt_before_packaging(source: Path) -> None:
    output = source / "stage.tar"
    with pytest.raises(ValueError, match="receipt_required"):
        gate.archive(source, source / "absent.json", output, "HEAD")
    assert not output.exists()


def test_native_edit_invalidates_previous_success(source: Path, compiler: Mock) -> None:
    path = receipt(source)
    (source / "harness/macos/First.swift").write_text("changed source\n")
    with pytest.raises(ValueError, match="matching_native_typecheck_pass_required"):
        gate.verify(source, path)


def test_staging_refuses_a_different_git_revision(
    source: Path,
    compiler: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = receipt(source)
    monkeypatch.setattr(gate, "git_payload", lambda *_: {"harness/macos/First.swift": b"older"})
    output = source / "stage.tar"
    with pytest.raises(ValueError, match="revision_differs"):
        gate.archive(source, path, output, "HEAD")
    assert not output.exists()


@pytest.mark.parametrize("forbidden", ["lab.local.json", "uplink-network", ".env.local"])
def test_staging_refuses_private_config_or_compiled_helpers(
    source: Path,
    compiler: Mock,
    monkeypatch: pytest.MonkeyPatch,
    forbidden: str,
) -> None:
    path = receipt(source)
    files = {**gate.swift_sources(source), "src/offline_agent/uplink/" + forbidden: b"fixture"}
    monkeypatch.setattr(gate, "git_payload", lambda *_: files)
    with pytest.raises(ValueError, match="private_or_compiled"):
        gate.archive(source, path, source / "stage.tar", "HEAD")
    assert not (source / "stage.tar").exists()


def test_staging_payload_contains_exact_checked_sources_and_receipt(
    source: Path,
    compiler: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = receipt(source)
    files = {**gate.swift_sources(source), "src/offline_agent/uplink/real_worker.py": b"fixture"}
    monkeypatch.setattr(gate, "git_payload", lambda *_: dict(files))
    output = source / "stage.tar"
    gate.archive(source, path, output, "HEAD")
    with tarfile.open(fileobj=io.BytesIO(output.read_bytes())) as archive:
        assert set(archive.getnames()) == {*files, "source-manifest.json", "native-typecheck.json"}
        for name, data in files.items():
            member = archive.extractfile(name)
            assert member is not None and member.read() == data
        manifest = archive.extractfile("source-manifest.json")
        assert manifest is not None and json.load(manifest) == gate.source_hashes(files)
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        gate.archive(source, path, output, "HEAD")
