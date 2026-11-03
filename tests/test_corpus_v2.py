import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from offline_agent.contracts import object_json, read_json
from offline_agent.corpus import CorpusError, load_tasks, sha256
from offline_agent.corpus_specs import specs
from offline_agent.corpus_v2 import check_mutation
from offline_agent.mutations import generate_mutations

ROOT = Path(__file__).resolve().parents[1]


def test_scale_and_split_invariants() -> None:
    train, heldout = (load_tasks(ROOT, split, 2) for split in ("train", "heldout"))
    assert len(train) == 140 and len(heldout) == 50
    assert sum(not t.impossible for t in train + heldout) == 160
    assert sum(t.impossible for t in train) == 20
    assert sum(t.impossible for t in heldout) == 10
    assert not {t.repository for t in train} & {t.repository for t in heldout}
    assert all(t.license in {"MIT", "BSD-3-Clause", "CC0-1.0"} for t in train + heldout)


def test_f1_frozen_lists_remain_historical() -> None:
    assert (
        sha256(ROOT / "data/train.sha256")
        == "b701d29f7d9b466d73aeca3179af9694f3d7353ca30154d31b88f2eae19edd22"
    )
    assert (
        sha256(ROOT / "eval/heldout.sha256")
        == "bff54733a55855687c2cf5610d6250e026386193cf6c3898a6acbf7e5f56f282"
    )
    assert len(load_tasks(ROOT, "train")) == 12


def test_generator_is_deterministic_and_mutants_are_distinct() -> None:
    for spec in specs():
        source = (ROOT / spec.source).read_text()
        first = list(generate_mutations(source, frozenset(spec.cases)))
        assert first == list(generate_mutations(source, frozenset(spec.cases)))
        assert first
        for mutation in first:
            assert source.count(mutation.original) == 1
            broken = source.replace(mutation.original, mutation.planted, 1)
            assert broken.count(mutation.planted) == 1
            ast.parse(broken)
    recipes = object_json(read_json((ROOT / "data/v2/recipes.json").read_text()))["mutations"]
    assert isinstance(recipes, list)
    assert len(recipes) == 160
    assert len({str(object_json(row)["mutated_source_sha256"]) for row in recipes}) == 160


def test_mutation_spans_handle_non_ascii_bytes_and_empty_sources() -> None:
    source = '# accent: café\ndef add(a,b):\n    """naïve"""\n    return a + b\n'
    assert list(generate_mutations("", frozenset())) == []
    candidates = list(generate_mutations(source, frozenset({"add"})))
    assert candidates
    for mutation in candidates:
        ast.parse(source.replace(mutation.original, mutation.planted, 1))


@pytest.mark.parametrize(
    "family",
    ["inflection", "boltons", "numeric-v2", "sequence-v2", "stringcase", "text-v2", "records-v2"],
)
def test_real_reference_plant_repair_and_regression_evidence(tmp_path: Path, family: str) -> None:
    tasks = load_tasks(ROOT, "train", 2) + load_tasks(ROOT, "heldout", 2)
    task = next(t for t in tasks if f"-{family}-" in t.id)
    evidence = check_mutation(ROOT, task, tmp_path)
    assert evidence["reference_green"] is True
    assert evidence["planted_green"] is False
    assert evidence["repair_green"] is True
    assert evidence["fail_to_pass"] and evidence["pass_to_pass"]


def test_impossible_retains_passing_source_and_missing_immutable_dependency(tmp_path: Path) -> None:
    task = next(t for t in load_tasks(ROOT, "heldout", 2) if t.impossible)
    evidence = check_mutation(ROOT, task, tmp_path)
    assert evidence["reference_green"] is False
    assert evidence["planted_green"] is False
    assert evidence["repair_green"] is None
    assert evidence["fail_to_pass"] == ["test_required_fixture"]
    assert evidence["pass_to_pass"]


@pytest.mark.parametrize(
    "path",
    [
        "eval/v2/heldout.json",
        "data/v2/train.json",
        "data/v2/sources.json",
        "data/v2/synthetic/text.py",
        "data/v2/upstream/boltons/LICENSE",
    ],
)
def test_v2_tamper_refuses(tmp_path: Path, path: str) -> None:
    for name in ("data", "eval"):
        shutil.copytree(ROOT / name, tmp_path / name)
    target = tmp_path / path
    target.write_text(target.read_text() + "\n")
    with pytest.raises(CorpusError, match="hash mismatch"):
        load_tasks(tmp_path, "heldout", 2)


def test_v2_freeze_is_not_overwritten() -> None:
    before = (ROOT / "eval/v2/heldout.sha256").read_bytes()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_corpus_v2.py"), "--freeze"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "already frozen" in result.stderr
    assert (ROOT / "eval/v2/heldout.sha256").read_bytes() == before


def test_unknown_version_and_split_refuse() -> None:
    with pytest.raises(CorpusError, match="version"):
        load_tasks(ROOT, "train", 3)
    with pytest.raises(CorpusError, match="split"):
        load_tasks(ROOT, "unknown", 2)
