"""Hash-frozen task definitions, disjoint source identities, and planted-bug checks."""

import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Never

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.workspace import TestResult, Workspace


class CorpusError(ValueError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_file(root: Path, name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise CorpusError("Invalid corpus path")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise CorpusError("Corpus path escapes root")
    if not target.is_file():
        raise CorpusError("Corpus file missing")
    return target


def verify_hashlist(root: Path, name: str) -> set[str]:
    paths: set[str] = set()
    for line in local_file(root, name).read_text().splitlines():
        if not re.fullmatch(r"[0-9a-f]{64}  [^\r\n]+", line):
            raise CorpusError("Malformed freeze manifest")
        digest, path = line.split("  ", 1)
        if path in paths:
            raise CorpusError("Duplicate frozen path")
        paths.add(path)
        if sha256(local_file(root, path)) != digest:
            raise CorpusError("Frozen file hash mismatch: " + path)
    if not paths:
        raise CorpusError("Empty freeze manifest")
    return paths


@dataclass(frozen=True)
class Task:
    id: str
    split: str
    repository: str
    revision: str
    license: str
    source: str
    oracle: str
    mission: str
    original: str
    planted: str
    impossible: bool
    task_hash: str

    def reference(self, root: Path) -> str:
        return local_file(root, self.source).read_text()

    def broken(self, root: Path) -> str:
        source = self.reference(root)
        if self.impossible:
            return source
        if not self.original or source.count(self.original) != 1 or self.original == self.planted:
            raise CorpusError("Planted mutation does not apply uniquely")
        return source.replace(self.original, self.planted, 1)

    def computed_hash(self, root: Path) -> str:
        data = asdict(self)
        data.pop("task_hash")
        data["source_sha256"] = sha256(local_file(root, self.source))
        data["oracle_sha256"] = sha256(local_file(root, self.oracle))
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def metadata(self) -> dict[str, Json]:
        return {
            "task_hash": self.task_hash,
            "split": self.split,
            "repository": self.repository,
            "revision": self.revision,
            "impossible": self.impossible,
        }


def _task(value: Json) -> Task:
    row = object_json(value)
    names = (
        "id",
        "split",
        "repository",
        "revision",
        "license",
        "source",
        "oracle",
        "mission",
        "original",
        "planted",
        "task_hash",
    )
    if set(row) != set(names) | {"impossible"}:
        raise CorpusError("Unexpected task fields")
    text: dict[str, str] = {}
    for name in names:
        val = row[name]
        if not isinstance(val, str):
            raise CorpusError("Invalid task string")
        text[name] = val
    impossible = row["impossible"]
    if not isinstance(impossible, bool):
        raise CorpusError("Invalid impossibility label")
    if text["split"] not in {"train", "heldout"} or text["license"] not in {
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "CC0-1.0",
    }:
        raise CorpusError("Unapproved split or license")
    if not re.fullmatch(r"[a-z0-9-]+", text["id"]):
        raise CorpusError("Invalid task id")
    return Task(**text, impossible=impossible)


def load_tasks(root: Path, split: str, version: int = 1) -> tuple[Task, ...]:
    if version == 2:
        from offline_agent.corpus_v2 import load_v2

        return load_v2(root, split)
    if version != 1:
        raise CorpusError("Unknown corpus version")
    if split not in {"train", "heldout"}:
        raise CorpusError("Unknown split")
    manifests = {"train": "data/train.json", "heldout": "eval/heldout.json"}
    freezes = {"train": "data/train.sha256", "heldout": "eval/heldout.sha256"}
    groups: dict[str, tuple[Task, ...]] = {}
    try:
        receipts = object_json(read_json(local_file(root, "data/sources.json").read_text()))
        sources = receipts.get("sources")
        if not isinstance(sources, list):
            raise CorpusError("Missing upstream receipts")
        for key in manifests:
            frozen = verify_hashlist(root, freezes[key])
            doc = object_json(read_json(local_file(root, manifests[key]).read_text()))
            if doc.get("version") != 1 or not isinstance(doc.get("tasks"), list):
                raise CorpusError("Unknown corpus version")
            values = doc["tasks"]
            assert isinstance(values, list)
            tasks = tuple(_task(value) for value in values)
            if not tasks:
                raise CorpusError("Empty task split")
            required = {manifests[key], "data/sources.json", "data/synthetic/LICENSE"}
            for task in tasks:
                if task.split != key or task.computed_hash(root) != task.task_hash:
                    raise CorpusError("Task identity or content hash mismatch")
                task.broken(root)
                required.update((task.source, task.oracle))
            for source_value in sources:
                source_row = object_json(source_value)
                if source_row.get("split") != key:
                    continue
                assets = object_json(source_row["assets"])
                for name, receipt_value in assets.items():
                    receipt = object_json(receipt_value)
                    if receipt.get("sha256") != sha256(local_file(root, name)):
                        raise CorpusError("Upstream receipt mismatch")
                    required.add(name)
            if required != frozen:
                raise CorpusError("Freeze coverage mismatch")
            groups[key] = tasks
    except (ContractError, KeyError, OSError) as exc:
        raise CorpusError("Invalid corpus manifest") from exc
    train, heldout = groups["train"], groups["heldout"]
    if {t.repository for t in train} & {t.repository for t in heldout}:
        raise CorpusError("Source repository leakage")
    all_tasks = train + heldout
    if len({t.id for t in all_tasks}) != len(all_tasks):
        raise CorpusError("Duplicate task id")
    if len({t.task_hash for t in all_tasks}) != len(all_tasks):
        raise CorpusError("Duplicate task content")
    return groups[split]


def _verification_refused(
    root: Path,
    task: Task,
    reason: str,
    clean: TestResult,
    broken: TestResult,
    repair: TestResult | None = None,
) -> Never:
    failure = root / "artifacts/corpus-failure.json"
    failure.parent.mkdir(parents=True, exist_ok=True)
    failure.write_text(
        json.dumps(
            {
                "task_id": task.id,
                "task_hash": task.task_hash,
                "reason": reason,
                "reference": clean.as_json(),
                "planted": broken.as_json(),
                "repair": repair.as_json() if repair is not None else None,
            },
            indent=2,
        )
        + "\n"
    )
    raise CorpusError(f"{reason}: {task.id}; evidence: artifacts/corpus-failure.json")


def verify_planted_tasks(root: Path, temp_root: Path, version: int = 1) -> list[dict[str, Json]]:
    if version == 2:
        from offline_agent.corpus_v2 import verify_v2

        return verify_v2(root, temp_root)
    if version != 1:
        raise CorpusError("Unknown corpus version")
    evidence: list[dict[str, Json]] = []
    temp_root.mkdir(parents=True, exist_ok=True)
    for task in load_tasks(root, "train") + load_tasks(root, "heldout"):
        with tempfile.TemporaryDirectory(dir=temp_root, prefix="verify-") as name:
            source = Path(name) / "source"
            source.mkdir()
            (source / "solution.py").write_text(task.reference(root))
            with Workspace(source, local_file(root, task.oracle), temp_root) as work:
                clean = work.run_tests(3)
            (source / "solution.py").write_text(task.broken(root))
            with Workspace(source, local_file(root, task.oracle), temp_root) as work:
                broken = work.run_tests(3)
                failures = sum(
                    int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", broken.output)
                )
                if task.impossible:
                    if clean.green or broken.green or failures != 1 or broken.tests_run < 3:
                        _verification_refused(
                            root, task, "Impossible fixture not verified", clean, broken
                        )
                    repaired = False
                else:
                    if not clean.green or broken.green or not 0 < failures < broken.tests_run:
                        _verification_refused(root, task, "Planted bug not verified", clean, broken)
                    work.patch("solution.py", task.planted, task.original)
                    repair = work.run_tests(3)
                    if not repair.green:
                        _verification_refused(
                            root, task, "Reference repair failed", clean, broken, repair
                        )
                    repaired = repair.green
            evidence.append(
                {
                    "task_id": task.id,
                    "task_hash": task.task_hash,
                    "split": task.split,
                    "impossible": task.impossible,
                    "reference_green": clean.green,
                    "planted_green": broken.green,
                    "reference_repair_green": repaired,
                    "tests": broken.tests_run,
                    "known_failures": failures,
                    "preserved_passing_tests": broken.tests_run - failures,
                }
            )
    return evidence
