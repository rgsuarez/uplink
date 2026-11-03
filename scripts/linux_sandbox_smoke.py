"""Real-container acceptance check with durable, bounded child failure evidence."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import sys
import tempfile
from pathlib import Path

from offline_agent.contracts import Json
from offline_agent.workspace import TestResult, Workspace


def record_result(output: Path, phase: str, result: TestResult, accepted: bool) -> None:
    receipt: dict[str, Json] = {
        "platform": "linux",
        "backend": "bubblewrap",
        "phase": phase,
        "accepted": accepted,
        "model_weights_loaded": False,
        "child": result.as_json(),
    }
    text = json.dumps(receipt, indent=2)
    output.write_text(text + "\n")
    if not accepted:
        raise SystemExit(f"Linux {phase} proof failed; receipt={output}\n{text}")


def main() -> None:
    if sys.platform != "linux":
        raise SystemExit("Linux container required; this path remains unverified on this host")
    root = Path(__file__).resolve().parents[1]
    temp_root = root / "artifacts/tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    output = root / "artifacts/linux-sandbox-acceptance.json"
    with tempfile.TemporaryDirectory(prefix="linux-proof-", dir=temp_root) as name:
        source = Path(name) / "source"
        source.mkdir()
        (source / "solution.py").write_text("def add(a,b):\n    return a - b\n")
        oracle = Path(name) / "oracle.py"
        oracle.write_text(
            _public_fixture("import os,socket,unittest,sys\nfrom pathlib import Path\n"
            "sys.path.insert(0,str(Path.cwd()))\nimport solution\n"
            "class Proof(unittest.TestCase):\n"
            "    def test_repair(self): self.assertEqual(solution.add(2,3),5)\n"
            "    def test_network(self):\n"
            "        with self.assertRaises(OSError):\n"
            "            socket.create_connection(('@PUBLIC_IPV4_003@',443),timeout=.2)\n"
            "    def test_oracle_readonly(self):\n"
            "        with self.assertRaises(OSError):\n"
            "            Path(__file__).write_text('tamper')\n"
            "    def test_home_hidden(self):\n"
            "        self.assertFalse(Path('/root/.ssh').exists())\n"
            "        self.assertNotIn('MODEL_API_KEY',os.environ)\n"
            "unittest.main(verbosity=2)\n")
        )
        with Workspace(source, oracle, temp_root) as work:
            red = work.run_tests(5, separate_streams=True)
            record_result(output, "red-path", red, red.tests_run == 4 and not red.green)
            work.patch("solution.py", "a - b", "a + b")
            green = work.run_tests(5, separate_streams=True)
            record_result(
                output, "containment/green-path", green, green.green and work.can_declare_fixed()
            )
            work.patch("solution.py", "a + b", "a * b")
            if work.can_declare_fixed():
                raise SystemExit("Linux stale-success proof failed")
        oracle.write_text("while True: pass\n")
        with Workspace(source, oracle, temp_root) as work:
            timeout = work.run_tests(0.25, separate_streams=True)
            record_result(output, "timeout", timeout, timeout.timed_out and not timeout.green)
    receipt = {
        "platform": "linux",
        "backend": "bubblewrap",
        "real_container_verified": True,
        "red_green_stale_success": True,
        "network_denied": True,
        "oracle_readonly": True,
        "credentials_hidden": True,
        "timeout": True,
        "model_weights_loaded": False,
    }
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
