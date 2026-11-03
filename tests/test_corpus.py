import json
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from offline_agent.corpus import (
    CorpusError,
    Task,
    load_tasks,
    sha256,
    verify_hashlist,
    verify_planted_tasks,
)
from offline_agent.workspace import TestResult as SandboxTestResult
from offline_agent.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def copied_corpus(tmp_path: Path) -> Path:
    for name in ("data", "eval"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def test_frozen_splits_disjoint_and_impossible_labeled() -> None:
    train = load_tasks(ROOT, "train")
    heldout = load_tasks(ROOT, "heldout")
    assert len(train) == 12
    assert len(heldout) == 11
    assert not {t.repository for t in train} & {t.repository for t in heldout}
    assert sum(t.impossible for t in train + heldout) == 4
    assert all(len(t.task_hash) == 64 for t in train + heldout)


def test_every_planted_bug_has_red_green_and_regression_evidence(tmp_path: Path) -> None:
    evidence = verify_planted_tasks(ROOT, tmp_path)
    assert len(evidence) == 23
    two_failures = {"train-train-numeric-clamping", "heldout-stringcase-uppercase"}
    for row in evidence:
        failures = 2 if row["task_id"] in two_failures else 1
        assert row["known_failures"] == failures
        assert row["preserved_passing_tests"] == 3 - failures
        assert row["reference_repair_green"] is (not row["impossible"])


@pytest.mark.parametrize(
    "path",
    [
        "eval/heldout.json",
        "data/train.json",
        "data/sources.json",
        "data/upstream/stringcase/module.py",
        "data/upstream/inflection/LICENSE",
        "data/synthetic/eval_text.py",
        "data/oracles/heldout-stringcase-trim.py",
    ],
)
def test_hash_mismatch_refuses_before_evaluation(tmp_path: Path, path: str) -> None:
    root = copied_corpus(tmp_path)
    target = root / path
    target.write_text(target.read_text() + "\n")
    with pytest.raises(CorpusError, match="hash mismatch"):
        load_tasks(root, "heldout")


def test_empty_or_malformed_freeze_refuses(tmp_path: Path) -> None:
    root = copied_corpus(tmp_path)
    target = root / "eval/heldout.sha256"
    target.write_text("")
    with pytest.raises(CorpusError, match="Empty"):
        load_tasks(root, "heldout")
    target.write_text("unparsed verdict\n")
    with pytest.raises(CorpusError, match="Malformed"):
        verify_hashlist(root, "eval/heldout.sha256")


def test_duplicate_and_escaping_freeze_entries_refused(tmp_path: Path) -> None:
    root = copied_corpus(tmp_path)
    target = root / "eval/heldout.sha256"
    lines = target.read_text().splitlines()
    target.write_text("\n".join(lines + [lines[0]]) + "\n")
    with pytest.raises(CorpusError, match="Duplicate"):
        verify_hashlist(root, "eval/heldout.sha256")
    target.write_text("0" * 64 + "  ../outside.py\n")
    with pytest.raises(CorpusError, match="path"):
        verify_hashlist(root, "eval/heldout.sha256")


def test_repository_leakage_detected_even_with_updated_content_hash(tmp_path: Path) -> None:
    root = copied_corpus(tmp_path)
    tasks = list(load_tasks(root, "heldout"))
    changed = replace(tasks[0], repository=load_tasks(root, "train")[0].repository)
    tasks[0] = replace(changed, task_hash=changed.computed_hash(root))
    manifest = root / "eval/heldout.json"
    manifest.write_text(json.dumps({"version": 1, "tasks": [asdict(t) for t in tasks]}))
    hashes = root / "eval/heldout.sha256"
    lines = hashes.read_text().splitlines()
    hashes.write_text(
        "\n".join(
            f"{sha256(manifest)}  eval/heldout.json"
            if line.endswith("  eval/heldout.json")
            else line
            for line in lines
        )
        + "\n"
    )
    with pytest.raises(CorpusError, match="leakage"):
        load_tasks(root, "heldout")


def test_frozen_manifest_cannot_be_overwritten() -> None:
    before = (ROOT / "eval/heldout.sha256").read_bytes()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/prepare_corpus.py"), "--freeze"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "already frozen" in result.stderr
    assert (ROOT / "eval/heldout.sha256").read_bytes() == before


def test_invalid_split_refuses() -> None:
    with pytest.raises(CorpusError, match="split"):
        load_tasks(ROOT, "unknown")


def test_task_hash_binds_mission_and_oracle() -> None:
    task: Task = load_tasks(ROOT, "train")[0]
    assert replace(task, mission="different").computed_hash(ROOT) != task.task_hash


def test_verification_failure_persists_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copied_corpus(tmp_path)

    def failed_sandbox(self: Workspace, timeout_s: float) -> SandboxTestResult:
        return SandboxTestResult(False, 71, False, 0, "sandbox_apply_refused", "unchanged", 0.01)

    monkeypatch.setattr(Workspace, "run_tests", failed_sandbox)
    with pytest.raises(CorpusError, match="evidence"):
        verify_planted_tasks(root, root / "artifacts/tmp")
    evidence = json.loads((root / "artifacts/corpus-failure.json").read_text())
    assert evidence["task_id"] == "train-inflection-dasherize"
    assert evidence["reason"] == "Planted bug not verified"
    assert evidence["reference"]["returncode"] == 71
    assert evidence["reference"]["output"] == "sandbox_apply_refused"
