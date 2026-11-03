import os
import shlex
import site
import sys
from pathlib import Path

import pytest

pytest_plugins = ("public_cpu",)
_RUNTIME_BEFORE = pytest.StashKey[tuple[str, str | None]]()


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Keep the active venv's dependencies and expose its actual runtime to child fences."""
    if not config.pluginmanager.hasplugin("public_cpu"):
        raise pytest.UsageError("offline CPU guard is required")
    executable = Path(sys.executable).resolve(strict=True)
    config.stash[_RUNTIME_BEFORE] = (sys.executable, os.environ.get("PYTHONPATH"))
    # The frozen sandbox grants the interpreter's own prefix, not the venv symlink's
    # parent. Resolve that existing binary; do not change any sandbox permission.
    sys.executable = str(executable)
    paths = [*(str(path) for path in config.getini("pythonpath")), *site.getsitepackages()]
    paths.extend(filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep)))
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))


def pytest_unconfigure(config: pytest.Config) -> None:
    before = config.stash.get(_RUNTIME_BEFORE, None)
    if before is None:
        return
    sys.executable, pythonpath = before
    if pythonpath is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = pythonpath


@pytest.fixture
def offline_python(tmp_path: Path) -> Path:
    """Keep installed dependencies while using a sandbox-readable real interpreter."""
    launcher = tmp_path / "offline-python"
    packages = os.pathsep.join(site.getsitepackages())
    launcher.write_text(
        "#!/bin/sh\nexport PYTHONPATH="
        + shlex.quote(packages)
        + ':"${PYTHONPATH:-}"\nexec '
        + shlex.quote(str(Path(sys.executable).resolve()))
        + ' "$@"\n'
    )
    launcher.chmod(0o700)
    return launcher


@pytest.fixture
def task_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "solution.py").write_text("def add(a, b):\n    return a - b\n")
    oracle = tmp_path / "oracle.py"
    oracle.write_text(
        "import sys, unittest\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path.cwd()))\nimport solution\n"
        "class Checks(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(solution.add(2, 3), 5)\n"
        "    def test_identity(self):\n        self.assertEqual(solution.add(3, 0), 3)\n"
        "unittest.main()\n"
    )
    temp = tmp_path / "sandbox"
    return source, oracle, temp
