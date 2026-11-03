"""Corpus v2 freeze and independent, per-case mutation verification."""

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from offline_agent.contracts import Json, object_json, read_json
from offline_agent.corpus import CorpusError, Task, _task, local_file, sha256, verify_hashlist
from offline_agent.workspace import TestResult, Workspace

MANIFESTS = {"train": "data/v2/train.json", "heldout": "eval/v2/heldout.json"}
FREEZES = {"train": "data/v2/train.sha256", "heldout": "eval/v2/heldout.sha256"}


def load_v2(root: Path, split: str) -> tuple[Task, ...]:
    if split not in MANIFESTS:
        raise CorpusError("Unknown corpus split")
    receipt = object_json(read_json(local_file(root, "data/v2/sources.json").read_text()))
    sources = receipt.get("sources")
    if receipt.get("version") != 2 or not isinstance(sources, list):
        raise CorpusError("Invalid v2 source receipts")
    groups: dict[str, tuple[Task, ...]] = {}
    for key, manifest in MANIFESTS.items():
        frozen = verify_hashlist(root, FREEZES[key])
        doc = object_json(read_json(local_file(root, manifest).read_text(), max_bytes=4_000_000))
        rows = doc.get("tasks")
        if doc.get("version") != 2 or not isinstance(rows, list):
            raise CorpusError("Invalid v2 task manifest")
        tasks = tuple(_task(value) for value in rows)
        required = {manifest, "data/v2/sources.json", "data/synthetic/LICENSE"}
        identities: dict[str, tuple[str, str, str]] = {}
        for value in sources:
            source = object_json(value)
            if source.get("split") != key:
                continue
            path, repo, rev, license_id = (
                source.get(n) for n in ("source", "repository", "revision", "license")
            )
            if not all(isinstance(v, str) for v in (path, repo, rev, license_id)):
                raise CorpusError("Invalid v2 source identity")
            assert isinstance(path, str) and isinstance(repo, str)
            assert isinstance(rev, str) and isinstance(license_id, str)
            identities[path] = (repo, rev, license_id)
            for name, asset_value in object_json(source.get("assets")).items():
                asset = object_json(asset_value)
                if asset.get("sha256") != sha256(local_file(root, name)):
                    raise CorpusError("V2 source receipt mismatch")
                required.add(name)
        for task in tasks:
            if task.split != key or task.computed_hash(root) != task.task_hash:
                raise CorpusError("V2 task identity/hash mismatch")
            if identities.get(task.source) != (task.repository, task.revision, task.license):
                raise CorpusError("V2 task source identity mismatch")
            if task.broken(root).count(task.planted) != 1 and not task.impossible:
                raise CorpusError("V2 mutation not uniquely reversible")
            required.update((task.source, task.oracle))
        if not tasks or required != frozen:
            raise CorpusError("V2 freeze coverage mismatch")
        groups[key] = tasks
    train, heldout = groups["train"], groups["heldout"]
    if {t.repository for t in train} & {t.repository for t in heldout}:
        raise CorpusError("V2 source repository/family leakage")
    all_tasks = train + heldout
    if len({t.id for t in all_tasks}) != len(all_tasks) or len(
        {t.task_hash for t in all_tasks}
    ) != len(all_tasks):
        raise CorpusError("Duplicate v2 task")
    for key, tasks in groups.items():
        expected_impossible = 20 if key == "train" else 10
        if sum(t.impossible for t in tasks) != expected_impossible:
            raise CorpusError("V2 impossible split count mismatch")
    if sum(not t.impossible for t in all_tasks) < 150 or len(heldout) < 40:
        raise CorpusError("V2 scale floor not met")
    return groups[split]


@dataclass(frozen=True)
class CaseResults:
    passed: frozenset[str]
    failed: frozenset[str]


def case_results(result: TestResult) -> CaseResults:
    passed = frozenset(
        re.findall(r"^(test_[a-zA-Z0-9_]+) \([^\n]+\) \.\.\. ok$", result.output, re.M)
    )
    failed = frozenset(
        re.findall(r"^(test_[a-zA-Z0-9_]+) \([^\n]+\) \.\.\. (?:FAIL|ERROR)$", result.output, re.M)
    )
    if not passed or len(passed | failed) != result.tests_run or passed & failed:
        raise CorpusError("Unparsed or incomplete per-case oracle verdict")
    return CaseResults(passed, failed)


def check_mutation(
    root: Path, task: Task, temp_root: Path, *, oracle: Path | None = None
) -> dict[str, Json]:
    """All stages run independently; a passing summary alone is insufficient evidence."""
    stages: dict[str, TestResult] = {}
    cases: dict[str, CaseResults] = {}
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="v2-verify-", dir=temp_root) as name:
            source = Path(name) / "source"
            source.mkdir()
            for label, text in (
                ("reference", task.reference(root)),
                ("planted", task.broken(root)),
            ):
                (source / "solution.py").write_text(text)
                with Workspace(source, oracle or local_file(root, task.oracle), temp_root) as work:
                    stages[label] = work.run_tests(3)
                    cases[label] = case_results(stages[label])
                    if label == "planted" and not task.impossible:
                        work.patch("solution.py", task.planted, task.original)
                        stages["repair"] = work.run_tests(3)
                        cases["repair"] = case_results(stages["repair"])
        clean, broken = stages["reference"], stages["planted"]
        if task.impossible:
            if (
                clean.green
                or broken.green
                or len(cases["planted"].failed) != 1
                or cases["reference"] != cases["planted"]
                or "test_required_fixture" not in cases["planted"].failed
            ):
                raise CorpusError("Impossible dependency contract not verified")
        elif (
            not clean.green
            or broken.green
            or not stages["repair"].green
            or not cases["planted"].failed
            or cases["reference"].passed != cases["repair"].passed
            or cases["reference"].passed != cases["planted"].passed | cases["planted"].failed
        ):
            raise CorpusError("Reference/plant/repair invariant failed")
        return {
            "task_id": task.id,
            "task_hash": task.task_hash,
            "split": task.split,
            "impossible": task.impossible,
            "reference_green": clean.green,
            "planted_green": broken.green,
            "repair_green": stages["repair"].green if "repair" in stages else None,
            "tests_run": broken.tests_run,
            "fail_to_pass": list(sorted(cases["planted"].failed)),
            "pass_to_pass": list(sorted(cases["planted"].passed)),
            "oracle_sha256": sha256(oracle or local_file(root, task.oracle)),
        }
    except (CorpusError, ValueError, OSError) as exc:
        output = root / "artifacts/v2-verification-failure.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "reason": str(exc),
                    "stages": {name: result.as_json() for name, result in stages.items()},
                },
                indent=2,
            )
            + "\n"
        )
        raise CorpusError("V2 verification refused: " + task.id) from exc


def verify_v2(root: Path, temp_root: Path) -> list[dict[str, Json]]:
    tasks = load_v2(root, "train") + load_v2(root, "heldout")
    output = root / "artifacts/corpus-v2-verification.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Json]] = []
    with output.open("w") as handle:
        for task in tasks:
            row = check_mutation(root, task, temp_root)
            results.append(row)
            handle.write(json.dumps(row) + "\n")
            handle.flush()
    report: dict[str, Json] = {
        "version": 2,
        "tasks": len(tasks),
        "solvable": sum(not t.impossible for t in tasks),
        "impossible": sum(t.impossible for t in tasks),
        "train_tasks": sum(t.split == "train" for t in tasks),
        "heldout_tasks": sum(t.split == "heldout" for t in tasks),
        "evidence_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "train_freeze_sha256": sha256(root / FREEZES["train"]),
        "heldout_freeze_sha256": sha256(root / FREEZES["heldout"]),
        "all_verified": True,
    }
    (root / "docs/CORPUS-V2.json").write_text(json.dumps(report, indent=2) + "\n")
    return results
