import subprocess
import sys
from pathlib import Path

import pytest

from offline_agent.sandbox import SandboxUnavailable, linux_plan, select_plan

ROOT = Path(__file__).resolve().parents[1]


def test_linux_plan_has_namespaces_minimal_mounts_and_readonly_oracle(tmp_path: Path) -> None:
    work = tmp_path / "work"
    oracle = tmp_path / "oracle.py"
    plan = linux_plan(
        tmp_path,
        work,
        oracle,
        executable="/usr/bin/python3",
        prefix="/opt/venv",
        base_prefix="/usr",
        which=lambda _: "/usr/bin/bwrap",
        exists=lambda _: True,
    )
    args = list(plan.command)
    assert plan.backend == "bubblewrap" and plan.runtime_verified is False
    assert all(
        flag in args
        for flag in (
            "--unshare-all",
            "--unshare-user",
            "--disable-userns",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "--clearenv",
        )
    )
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--share-net" not in args and "--bind-data" not in args
    bindings = [(args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg == "--ro-bind"]
    assert (str(oracle), "/oracle/oracle.py") in bindings
    assert not any(source == "/" or source == str(ROOT) for source, _ in bindings)
    assert args[args.index("--bind") + 1 : args.index("--bind") + 3] == [str(work), "/work"]
    assert args[-6:] == ["--", "/usr/bin/python3", "-I", "-S", "-B", "/oracle/oracle.py"]
    assert set(plan.environment) == {"PATH", "HOME", "TMPDIR"}


def test_missing_bubblewrap_refuses_without_fallback(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable, match="no_unsandboxed_fallback"):
        linux_plan(tmp_path, tmp_path / "work", tmp_path / "oracle.py", which=lambda _: None)


def test_missing_interpreter_mount_refuses(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable, match="runtime_not_mounted"):
        linux_plan(
            tmp_path,
            tmp_path / "work",
            tmp_path / "oracle.py",
            executable="/outside/python",
            prefix="/usr",
            base_prefix="/usr",
            which=lambda _: "/usr/bin/bwrap",
            exists=lambda _: True,
        )


def test_runtime_mount_cannot_expose_host_root(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable, match="unsafe_runtime_mount"):
        linux_plan(
            tmp_path,
            tmp_path / "work",
            tmp_path / "oracle.py",
            executable="/usr/bin/python3",
            prefix="/",
            base_prefix="/usr",
            which=lambda _: "/usr/bin/bwrap",
            exists=lambda _: True,
        )


def test_unknown_platform_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable, match="unsupported_platform"):
        select_plan(
            tmp_path,
            tmp_path / "work",
            tmp_path / "oracle.py",
            tmp_path / "home",
            tmp_path / "output",
            platform="unsupported",
        )


def test_linux_runtime_proof_is_not_claimed_on_mac() -> None:
    if sys.platform == "linux":
        pytest.skip("This assertion is specific to the unverified host contract")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/linux_sandbox_smoke.py")],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "remains unverified" in result.stderr
    assert not (ROOT / "artifacts/linux-sandbox-acceptance.json").exists()
