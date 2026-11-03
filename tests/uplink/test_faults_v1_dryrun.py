"""Read-only collector admission and private inventory parsing; no host contact."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest


@pytest.fixture
def collector() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts/faults_v1_dryrun.py"
    spec = importlib.util.spec_from_file_location("fault_dryrun", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_contact_cannot_precede_posted_handback(
    collector: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    marker = "KAYLIN-OK LAB FAULTS-V1-DRYRUN: released synthetic window"
    path = tmp_path / "handback.md"
    with pytest.raises(ValueError):
        collector.released_handback(path, marker, "lab")
    path.write_text("Estimated end time is not a handback.")
    with pytest.raises(ValueError, match="missing_no_host_contact"):
        collector.released_handback(path, marker, "lab")
    path.write_text(marker)
    assert len(collector.released_handback(path, marker, "lab")) == 64
    for node in ("other-lab", "", "bad host"):
        with pytest.raises(ValueError, match="exact_local_handback_marker_required"):
            collector.released_handback(path, marker, node)
    with pytest.raises(ValueError):
        collector.released_handback(path, "GO", "lab")
    path.write_text("Quoted old text: " + marker)
    with pytest.raises(ValueError):
        collector.released_handback(path, marker, "lab")


def test_ipv4_proof_checks_both_endpoints_and_target_identity(collector: Any) -> None:
    collector.ipv4_proof({"node": "lab", "ssh_connection": _public_fixture("@PUBLIC_IPV4_066@ 1234 @PUBLIC_IPV4_055@ 22")}, "lab")
    for connection in (_public_fixture("@PUBLIC_IPV6_103@ 1234 @PUBLIC_IPV4_055@ 22"), _public_fixture("@PUBLIC_IPV4_066@ 1234 @PUBLIC_IPV6_104@ 22"), ""):
        with pytest.raises(ValueError):
            collector.ipv4_proof({"node": "lab", "ssh_connection": connection}, "lab")
    with pytest.raises(ValueError):
        collector.ipv4_proof(
            {"node": "foreign", "ssh_connection": _public_fixture("@PUBLIC_IPV4_066@ 1234 @PUBLIC_IPV4_055@ 22")}, "lab"
        )


def test_tailscale_inventory_covers_address_endpoints_from_both_reads(collector: Any) -> None:
    values = {
        "tailscale_netcheck": {"exit_code": 0, "stdout": _public_fixture('{"DERP":"@PUBLIC_IPV4_085@:3478"}')},
        "tailscale_status": {
            "exit_code": 0,
            "stdout": _public_fixture('{"TailscaleIPs":["@PUBLIC_IPV4_055@","@PUBLIC_IPV6_111@"],"Endpoint":"[@PUBLIC_IPV6_105@]:443"}'),
        },
    }
    assert set(collector.tailscale_addresses(values)) == {
        _public_fixture("@PUBLIC_IPV4_085@"),
        _public_fixture("@PUBLIC_IPV4_055@"),
        _public_fixture("@PUBLIC_IPV6_111@"),
        _public_fixture("@PUBLIC_IPV6_105@"),
    }
    for value in (
        {},
        {**values, "tailscale_netcheck": {"exit_code": 1, "stdout": "failure"}},
        {k: {"exit_code": 0, "stdout": "{}"} for k in values},
    ):
        with pytest.raises(ValueError):
            collector.tailscale_addresses(value)


def test_remote_collection_uses_getters_only(
    collector: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "synthetic output", ""))
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/tailscale")
    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a: [(2, 1, 6, "", (_public_fixture("@PUBLIC_IPV4_094@"), 443))])
    monkeypatch.setenv("SSH_CONNECTION", _public_fixture("@PUBLIC_IPV4_066@ 1234 @PUBLIC_IPV4_055@ 22"))
    exec(collector.READ_CODE, {})
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["kind"] == "faults-v1-read-only"
    commands = [c.args[0] for c in run.call_args_list]
    assert len(commands) >= 25
    for command in commands:
        assert not any(
            arg.startswith("-set") or arg in ("-e", "-d", "-F", "bootstrap", "inject", "limactl")
            for arg in command
        )
        if "/sbin/pfctl" in command:
            assert "-f" not in command
    assert ["/usr/local/bin/tailscale", "netcheck", "--format=json"] in commands
    assert ["/usr/local/bin/tailscale", "status", "--json"] in commands
