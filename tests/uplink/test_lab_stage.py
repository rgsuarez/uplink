"""Worker integrity and placement pins, without SSH, root or a namespace mutation."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.lab_protocol import request
from offline_agent.uplink.lab_runner import LimaClient
from offline_agent.uplink.lab_stage import (
    EXEC_VERIFIED,
    STAGE_CODE,
    WORKERS,
    WorkerBundle,
    stage_worker,
    staging_failure,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("Traceback omitted\nValueError: unexpected_bundle_files\n", "unexpected_bundle_files"),
        ("ValueError: unrecognized_private_context\n", "unclassified_staging_failure"),
        ("", "unclassified_staging_failure"),
    ],
)
def test_stage_refusals_keep_known_reasons_without_raw_transport_output(
    stderr: str, expected: str
) -> None:
    assert staging_failure(stderr) == expected


def test_bundle_identity_changes_when_any_worker_changes(tmp_path: Path) -> None:
    source = tmp_path / "src/offline_agent/uplink"
    source.mkdir(parents=True)
    shell = tmp_path / "harness/linux/setup.sh"
    shell.parent.mkdir(parents=True)
    shell.write_text("echo fixture\n")
    for name in WORKERS:
        (source / name).write_text("# synthetic worker\n")
    before = WorkerBundle.read(source)
    (source / "lab_protocol.py").write_text("# changed protocol\n")
    after = WorkerBundle.read(source)
    assert before.directory != after.directory
    assert set(before.hashes) == {*WORKERS, "setup.sh"}


def test_worker_bundle_requires_every_regular_dependency(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="bounded_worker"):
        WorkerBundle.read(tmp_path)


def test_staging_refuses_test_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "lab")
    spy = Mock(side_effect=AssertionError("no transport"))
    monkeypatch.setattr("subprocess.run", spy)
    with pytest.raises(ContractError, match="explicit_lab"):
        stage_worker(ROOT / "src/offline_agent/uplink", authorize=True)
    spy.assert_not_called()


def test_lima_command_uses_verified_vm_path_not_host_mount() -> None:
    source = ROOT / "src/offline_agent/uplink"
    bundle = WorkerBundle.read(source)
    client = LimaClient("0123456789ab", source / "lab_worker.py", True, bundle)
    argv = client.command("observe")
    assert argv[:5] == ("limactl", "shell", "--workdir", "/", "uplink-harness")
    assert argv[5:7] == ("sudo", "-n")
    payload = json.loads(argv[-1])
    assert payload["directory"] == bundle.directory
    assert str(source) not in json.dumps(payload)
    assert payload["hashes"] == bundle.hashes


def test_staging_program_refuses_corrupted_existing_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir(mode=0o700)
    (directory / "fixture.py").write_text("corrupted")
    payload = {
        "operation": "prepare",
        "directory": str(directory),
        "hashes": {"fixture.py": "0" * 64},
    }
    monkeypatch.setattr("sys.argv", ["stage-code", json.dumps(payload)])
    with pytest.raises(ValueError, match="bundle_content_mismatch"):
        exec(compile(STAGE_CODE, "<trusted-stage-code>", "exec"), {})


def test_verified_launcher_refuses_changed_code_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / "lab_worker.py").write_text("raise AssertionError('must never execute')")
    payload = {
        "directory": str(directory),
        "hashes": {"lab_worker.py": "0" * 64},
        "execute": False,
        "request": request("observe", "0123456789ab"),
    }
    monkeypatch.setattr("sys.argv", ["verified-launcher", json.dumps(payload)])
    spy = Mock(side_effect=AssertionError("must never execute"))
    monkeypatch.setattr("os.execv", spy)
    with pytest.raises(ValueError, match="worker_bundle_changed"):
        exec(compile(EXEC_VERIFIED, "<trusted-launcher-code>", "exec"), {})
    spy.assert_not_called()
