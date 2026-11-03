
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
import argparse
import json
from pathlib import Path

import pytest

from offline_agent.uplink import cli
from offline_agent.uplink.diagnostics import Inspection
from offline_agent.uplink.privacy import Redactor

from .test_session import HealthyTransport


def test_cli_import_and_help_do_not_load_a_model_or_touch_network(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["uplink", "diagnose", "--json"])
    assert args.command == "uplink" and args.uplink_command == "diagnose" and args.json


def test_diagnose_cli_writes_only_redacted_reports(
    tmp_path: Path,
    inspection: Inspection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "synthetic-host.invalid")
    monkeypatch.setattr("getpass.getuser", lambda: "synthetic-person")
    monkeypatch.setattr(
        "offline_agent.uplink.cli.MacDiagnostics.inspect", lambda self, **kwargs: inspection
    )
    monkeypatch.setattr(cli, "SystemTransport", lambda **kwargs: HealthyTransport())
    cli.run(argparse.Namespace(json=True), tmp_path)
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["mode"] == "read_only" and report["changes_executed"] == 0
    assert report["oracle"]["verdict"] == "ONLINE" and report["redaction_scan"]["passed"]
    assert report["saved_snapshot_id"].startswith("snapshot-")
    for value in (
        "Fictional Research Network",
        _public_fixture("@PUBLIC_MAC_114@"),
        "synthetic-host.invalid",
        "synthetic-person",
    ):
        assert value not in text
    assert (tmp_path / "artifacts/uplink/diagnose.json").exists()
    assert (tmp_path / "artifacts/uplink/redaction-scan.json").exists()


def test_diagnose_refuses_wrong_platform_before_any_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    with pytest.raises(ValueError, match="require_macos"):
        cli.diagnose(tmp_path)
    assert not list(tmp_path.iterdir())


def test_full_synthetic_diagnose_emits_and_validates_each_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.trace_format import mapping, read_trace

    from .conftest import FixtureReader

    reader = FixtureReader()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "synthetic-host.invalid")
    monkeypatch.setattr("getpass.getuser", lambda: "synthetic-person")
    monkeypatch.setattr(cli, "ReadOnlyRunner", lambda: reader)
    monkeypatch.setattr(cli, "SystemTransport", lambda **kwargs: HealthyTransport())
    monkeypatch.setattr(
        "offline_agent.uplink.diagnostics.prior_leases", lambda *args: (set(), "unavailable")
    )
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    report = cli.diagnose(tmp_path)
    path = tmp_path / "artifacts/uplink/traces" / f"{report['trace_id']}.jsonl"
    rows = list(read_trace(path))
    steps = [r for r in rows if r["event"] == "step"]
    assert len(steps) == len(reader.calls) + 2
    assert rows[-1]["verdict"] == "ONLINE"
    assert all("net_state" in row for row in rows)
    assert mapping(steps[-2]["oracle"])["verdict"] == "ONLINE"
    text = path.read_text()
    for private in (
        "Fictional Research Network",
        "Fictional iPhone Hotspot",
        "synthetic-person",
        _public_fixture("@PUBLIC_MAC_114@"),
        "proxy.synthetic.invalid",
    ):
        assert private not in text


def test_failed_report_privacy_scan_still_finishes_trace_without_raw_bytes(
    tmp_path: Path,
    inspection: Inspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.trace_format import read_trace

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("socket.gethostname", lambda: "synthetic-host.invalid")
    monkeypatch.setattr("getpass.getuser", lambda: "synthetic-person")
    monkeypatch.setattr(
        "offline_agent.uplink.cli.MacDiagnostics.inspect", lambda self, **kwargs: inspection
    )
    monkeypatch.setattr(cli, "SystemTransport", lambda **kwargs: HealthyTransport())
    real_scan = Redactor.scan
    monkeypatch.setattr(
        "offline_agent.uplink.privacy.Redactor.scan",
        lambda self, text: (
            {
                "passed": False,
                "known_identifier_hits": 1,
                "mac_pattern_hits": 0,
                "identifier_kinds": ["network"],
            }
            if '"mode": "read_only"' in text
            else real_scan(self, text)
        ),
    )
    with pytest.raises(ValueError, match="redaction_scan_failed"):
        cli.diagnose(tmp_path)
    folder = tmp_path / "artifacts/uplink"
    assert not (folder / "diagnose.json").exists()
    assert (folder / "redaction-scan.json").exists()
    trace = next((folder / "traces").glob("*.jsonl"))
    rows = list(read_trace(trace))
    assert rows[-1]["verdict"] == "ERROR"
    assert "Fictional" not in trace.read_text()
