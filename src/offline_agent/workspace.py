"""Small editable source copy and an immutable, isolated test oracle."""

import contextlib
import hashlib
import json
import math
import os
import re
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from offline_agent.contracts import Json
from offline_agent.sandbox import select_plan


class WorkspaceError(ValueError):
    pass


def content_hash(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class TestResult:
    green: bool
    returncode: int
    timed_out: bool
    tests_run: int
    output: str
    workspace_hash: str
    elapsed_s: float
    stdout: str | None = None
    stderr: str | None = None

    def as_json(self) -> dict[str, Json]:
        result: dict[str, Json] = {
            "green": self.green,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "tests_run": self.tests_run,
            "output": self.output,
            "workspace_hash": self.workspace_hash,
            "elapsed_s": self.elapsed_s,
        }
        if self.stdout is not None:
            result["stdout"] = self.stdout
            result["stderr"] = self.stderr
        return result


class Workspace:
    def __init__(self, source: Path, oracle: Path, temp_root: Path) -> None:
        temp_root.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(prefix="task-", dir=temp_root)
        self.root = Path(self._temp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.oracle = self.root / "oracle.py"
        self.oracle.write_text(oracle.read_text())
        self.allowed: set[str] = set()
        files = sorted(source.rglob("*"))
        if len(files) > 200 or source.is_symlink():
            self._temp.cleanup()
            raise WorkspaceError("Source copy limit or symlink")
        try:
            for path in files:
                if path.is_symlink():
                    raise WorkspaceError("Symlinks are not allowed")
                if path.is_file():
                    if path.stat().st_size > 48_000:
                        raise WorkspaceError("Source file size limit")
                    name = path.relative_to(source).as_posix()
                    if any(part.startswith(".") for part in Path(name).parts):
                        raise WorkspaceError("Hidden source path")
                    target = self.work / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(path.read_text())
                    if name.endswith(".py"):
                        self.allowed.add(name)
        except (OSError, ValueError):
            self._temp.cleanup()
            raise
        self.latest_green: str | None = None

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._temp.cleanup()

    def path(self, name: str) -> Path:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise WorkspaceError("Path escapes workspace")
        if any(part.startswith(".") for part in relative.parts):
            raise WorkspaceError("Hidden paths are not exposed")
        target = self.work / relative
        for parent in (target, *target.parents):
            if parent == self.work:
                break
            if parent.is_symlink():
                raise WorkspaceError("Symlinks are not exposed")
        if not target.resolve().is_relative_to(self.work):
            raise WorkspaceError("Path escapes workspace")
        if not target.exists():
            raise WorkspaceError("Path does not exist")
        return target

    def snapshot(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in sorted(self.work.rglob("*")):
            if path.is_symlink():
                raise WorkspaceError("Unexpected workspace symlink")
            if path.is_file() and "__pycache__" not in path.parts:
                if path.stat().st_size > 48_000:
                    raise WorkspaceError("Workspace file size limit")
                result[path.relative_to(self.work).as_posix()] = path.read_text()
        return result

    def list_files(self, name: str) -> list[str]:
        directory = self.path(name)
        if not directory.is_dir():
            raise WorkspaceError("Expected directory")
        return [
            p.relative_to(self.work).as_posix()
            for p in sorted(directory.rglob("*"))
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts
        ][:100]

    def read_file(self, name: str) -> str:
        path = self.path(name)
        if not path.is_file() or path.stat().st_size > 48_000:
            raise WorkspaceError("Not a bounded text file")
        return path.read_text()

    def search(self, name: str, query: str) -> list[str]:
        if not query:
            raise WorkspaceError("Search query is empty")
        path = self.path(name)
        files = [name] if path.is_file() else self.list_files(name)
        matches: list[str] = []
        for file in files:
            for number, line in enumerate(self.read_file(file).splitlines(), 1):
                if query in line:
                    matches.append(f"{file}:{number}:{line[:300]}")
        return matches[:50]

    def patch(self, name: str, old: str, new: str) -> None:
        path = self.path(name)
        if name not in self.allowed:
            raise WorkspaceError("Only original source modules may be edited")
        original = self.read_file(name)
        if not old or old == new or original.count(old) != 1:
            raise WorkspaceError("Patch must replace exactly one nonempty occurrence")
        updated = original.replace(old, new, 1)
        if len(updated.encode()) > 48_000:
            raise WorkspaceError("Patched file size limit")
        path.write_text(updated)
        self.latest_green = None

    def can_declare_fixed(self) -> bool:
        return self.latest_green == content_hash(self.snapshot())

    def run_tests(self, timeout_s: float, *, separate_streams: bool = False) -> TestResult:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise WorkspaceError("Test timeout must be positive")
        before = content_hash(self.snapshot())
        self.latest_green = None
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        output_path = self.root / "test-output.txt"
        stderr_path = self.root / "test-stderr.txt"
        plan = select_plan(self.root, self.work, self.oracle, home, output_path)

        def limits() -> None:
            seconds = math.ceil(timeout_s) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
            resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

        start = time.monotonic()
        timed_out = False
        with contextlib.ExitStack() as stack:
            output = stack.enter_context(output_path.open("wb"))
            stderr = stack.enter_context(stderr_path.open("wb")) if separate_streams else None
            process = subprocess.Popen(
                plan.command,
                cwd=self.work,
                env=plan.environment,
                stdout=output,
                stderr=stderr if stderr is not None else subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=limits,
            )
            try:
                returncode = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
            finally:
                # A successful parent must not leave a child running after its test window.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        with output_path.open("rb") as handle:
            output_text = handle.read(12_000).decode(errors="replace")
        stdout_text = output_text if separate_streams else None
        stderr_text = None
        if separate_streams:
            with stderr_path.open("rb") as handle:
                stderr_text = handle.read(12_000).decode(errors="replace")
            output_text = (output_text + stderr_text)[:24_000]
        match = re.search(r"Ran (\d+) tests? in", output_text)
        count = int(match[1]) if match else 0
        after = content_hash(self.snapshot())
        green = returncode == 0 and not timed_out and count > 0 and before == after
        if green:
            self.latest_green = after
        return TestResult(
            green,
            returncode,
            timed_out,
            count,
            output_text,
            after,
            time.monotonic() - start,
            stdout_text,
            stderr_text,
        )
