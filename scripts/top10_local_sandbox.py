"""Run TOP10 qualification with network and host settings executables denied."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def profile(output: Path, native: Path | None = None) -> str:
    def quote(p: Path) -> str:
        return json.dumps(str(p))

    executables = {Path(sys.executable).resolve(), ROOT / ".venv/bin/python", Path(sys.executable)}
    if native is not None:
        executables.add(native.resolve())
    return (
        "\n".join(
            [
                "(version 1)",
                "(deny default)",
                "(allow file-read*)",
                "(allow sysctl-read)",
                "(allow mach-lookup)",
                "(allow process-fork)",
                "(allow process-info*)",
                "(allow signal (target self))",
                "(allow process-exec "
                + " ".join("(literal " + quote(p) + ")" for p in executables)
                + ")",
                "(allow file-write* (subpath " + quote(output) + ') (literal "/dev/null"))',
                "(deny network*)",
            ]
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--native-self-test", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    output = args.output.absolute()
    if (
        not output.is_relative_to(ROOT / "artifacts/uplink")
        or output.exists()
        or any(p.is_symlink() for p in (output, *output.parents))
    ):
        parser.error("new private output under artifacts/uplink is required")
    output.mkdir(parents=True, mode=0o700)
    (output / "tmp").mkdir(mode=0o700)
    policy = profile(output, args.native_self_test)
    (output / "sandbox.sb").write_text(policy)
    if args.native_self_test:
        if args.command or not args.native_self_test.resolve().is_relative_to(
            ROOT / "artifacts/uplink"
        ):
            parser.error("only the local native self-test is allowed")
        command = [str(args.native_self_test.resolve()), "self-test"]
    else:
        remainder = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not remainder:
            parser.error("a Python module or script command is required")
        command = [sys.executable, "-B", *remainder]
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TMPDIR": str(output / "tmp"),
        "UPLINK_TOP10_SANDBOX": "1",
        "UPLINK_TOP10_SANDBOX_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", policy, *command], cwd=ROOT, env=environment, check=False
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
