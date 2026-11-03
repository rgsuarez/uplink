"""CPU-only qualification: no network, host tools, GPU tools or real model weights."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

COUNTS = {"network_denied": 0, "host_tool_denied": 0, "model_weight_denied": 0}
EVENTS: list[dict[str, str]] = []
CURRENT_TEST = "collection"
HOST_TOOLS = {
    "ssh", "scp", "rsync", "curl", "wget", "modal", "llama-server", "llama-cli",
    "nvidia-smi", "networksetup", "ipconfig", "ifconfig", "route", "pfctl", "launchctl",
    "sudo", "swift", "swiftc", "cmake", "ninja",
}
PRIVATE_PACKAGE_TESTS = {
    "test_package_roundtrip_has_only_frozen_members",
    "test_staging_bootstrap_uses_approved_root_and_oob_guards",
    "test_changed_source_and_missing_required_member_refuse",
    "test_source_change_invalidates_freeze",
}
NETWORK_TESTS = {
    "tests/test_f6_fix.py::test_unrelated_listener_is_refused_before_any_server_or_model_open",
    "tests/test_f9.py::test_fake_llama_server_records_payload_and_native_timings",
}


def audit(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo"}:
        COUNTS["network_denied"] += 1
        EVENTS.append({"kind": "network_denied", "test": CURRENT_TEST})
        raise PermissionError("cpu_qualification_network_forbidden")
    if event == "subprocess.Popen" and len(args) > 1:
        command = args[1]
        if isinstance(command, (list, tuple)) and command:
            executable = Path(str(command[0])).name
            if executable in HOST_TOOLS:
                COUNTS["host_tool_denied"] += 1
                raise PermissionError("cpu_qualification_host_tool_forbidden")
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.suffix in {".gguf", ".safetensors", ".pt", ".pth"}:
            # Tests may create tiny synthetic artifact files, never real weights.
            try:
                large = path.stat().st_size > 1_048_576
            except OSError:
                large = False
            if large:
                COUNTS["model_weight_denied"] += 1
                raise PermissionError("cpu_qualification_real_weights_forbidden")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "private_host_package: needs the original private host bundle")
    sys.addaudithook(audit)


def pytest_runtest_setup(item: pytest.Item) -> None:
    global CURRENT_TEST
    CURRENT_TEST = item.nodeid.split("[", 1)[0]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        node = item.nodeid.split("[", 1)[0]
        reason = None
        if item.get_closest_marker("private_host_package"):
            reason = "requires unchanged private host package bytes, omitted from the public export"
        elif node in NETWORK_TESTS:
            reason = "requires a real loopback listener; network tests excluded from CPU-only run"
        elif node.startswith("tests/bench/test_package.py::"):
            reason = "requires the original private host package and non-distributed native binaries"
        elif (node.startswith("tests/top10/test_authority.py::")
              and node.rsplit("::", 1)[-1] in PRIVATE_PACKAGE_TESTS):
            reason = "requires non-distributed native host binaries and private package templates"
        elif node.startswith("tests/test_uplink_receipts.py::"):
            reason = "requires original private host receipts; public redactions retain provenance only"
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_sessionfinish() -> None:
    report = os.environ.get("PUBLIC_CPU_GUARD_REPORT")
    if report:
        Path(report).write_text(json.dumps(COUNTS | {"events": EVENTS}, indent=2) + "\n")
