"""Platform-selected OS containment plans. Unsupported or missing backends fail closed."""

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class SandboxUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class SandboxPlan:
    backend: str
    command: tuple[str, ...]
    environment: dict[str, str]
    runtime_verified: bool


def macos_plan(root: Path, work: Path, oracle: Path, home: Path, output: Path) -> SandboxPlan:
    binary = Path("/usr/bin/sandbox-exec")
    if not binary.is_file():
        raise SandboxUnavailable("macOS sandbox-exec missing")
    read_paths = [
        "/System",
        "/usr",
        "/opt/homebrew",
        "/Library/Apple",
        str(root),
        str(Path(sys.executable).parent.parent.resolve()),
    ]
    # A venv can link to an interpreter whose standard library lives elsewhere.
    # Admit only that exact runtime prefix, never the whole account home or root.
    runtime_prefix = Path(sys.base_prefix)
    if (
        not runtime_prefix.is_absolute()
        or runtime_prefix.resolve() in {Path("/"), Path.home().resolve()}
    ):
        raise SandboxUnavailable("unsafe_python_runtime_prefix")
    read_paths.append(str(runtime_prefix.resolve()))
    reads = " ".join(f"(subpath {json.dumps(p)})" for p in read_paths)
    writes = " ".join(f"(subpath {json.dumps(str(p))})" for p in (work, home))
    profile = (
        "(version 1)(deny default)(allow process-exec)(allow process-fork)(allow sysctl-read)"
        "(allow mach-lookup)(allow file-read-metadata)"
        f'(allow file-read* {reads} (literal "/") (literal "/dev/null") (literal "/dev/urandom"))'
        f'(allow file-write* {writes} (literal {json.dumps(str(output))}) (literal "/dev/null"))'
    )
    return SandboxPlan(
        "sandbox-exec",
        (str(binary), "-p", profile, sys.executable, "-I", "-S", "-B", str(oracle)),
        {"PATH": os.defpath, "HOME": str(home), "TMPDIR": str(home)},
        True,
    )


def linux_plan(
    root: Path,
    work: Path,
    oracle: Path,
    *,
    executable: str | None = None,
    prefix: str | None = None,
    base_prefix: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[Path], bool] = Path.exists,
) -> SandboxPlan:
    binary = which("bwrap")
    if binary is None:
        raise SandboxUnavailable("bubblewrap_missing_no_unsandboxed_fallback")
    python = executable or sys.executable
    command = [
        binary,
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]
    # Deliberately do not mount the host root, home, credentials, model volumes, or project.
    runtime = [
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path(prefix or sys.prefix),
        Path(base_prefix or sys.base_prefix),
    ]
    mounted: list[Path] = []
    for path in sorted(set(runtime), key=lambda p: len(p.parts)):
        if path == Path("/") or path == root or path == Path.home():
            raise SandboxUnavailable("unsafe_runtime_mount")
        if exists(path) and not any(path.is_relative_to(parent) for parent in mounted):
            command += ["--ro-bind", str(path), str(path)]
            mounted.append(path)
    if not any(Path(python).is_relative_to(path) for path in mounted):
        raise SandboxUnavailable("python_runtime_not_mounted")
    if not root.is_absolute() or not work.is_relative_to(root) or not oracle.is_relative_to(root):
        raise SandboxUnavailable("invalid_sandbox_workspace")
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        "/home/agent",
        "--dir",
        "/oracle",
        "--ro-bind",
        str(oracle),
        "/oracle/oracle.py",
        "--bind",
        str(work),
        "/work",
        "--chdir",
        "/work",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/home/agent",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--",
        python,
        "-I",
        "-S",
        "-B",
        "/oracle/oracle.py",
    ]
    return SandboxPlan(
        "bubblewrap",
        tuple(command),
        {"PATH": "/usr/bin:/bin", "HOME": str(root), "TMPDIR": str(root)},
        False,  # A real container acceptance receipt is still required.
    )


def select_plan(
    root: Path, work: Path, oracle: Path, home: Path, output: Path, platform: str | None = None
) -> SandboxPlan:
    host = platform or sys.platform
    if host == "darwin":
        return macos_plan(root, work, oracle, home, output)
    if host == "linux":
        return linux_plan(root, work, oracle)
    raise SandboxUnavailable("unsupported_platform_no_unsandboxed_fallback")
