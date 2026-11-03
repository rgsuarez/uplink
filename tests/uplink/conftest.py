"""Synthetic macOS fixtures only. Fixture readers never spawn operating-system tools."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
from pathlib import Path

import pytest

from offline_agent.contracts import object_json
from offline_agent.uplink.diagnostics import (
    IF,
    IP,
    NS,
    SC,
    Inspection,
    MacDiagnostics,
    ReadResult,
)
from offline_agent.uplink.oracle import OracleResult
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.real_preflight import Checks


class FixtureReader:
    def __init__(self) -> None:
        row = object_json(json.loads((Path(__file__).parent / "fixtures/macos26.json").read_text()))
        self.values = {k: str(v) for k, v in row.items()}
        self.calls: list[tuple[str, ...]] = []

    def read(self, argv: tuple[str, ...]) -> ReadResult:
        self.calls.append(argv)
        exact = {
            (NS, "-listallhardwareports"): "hardware",
            (NS, "-listallnetworkservices"): "services",
            (NS, "-listnetworkserviceorder"): "order",
            (IF, "-l"): "interfaces",
            (SC, "--dns"): "dns",
            (SC, "--proxy"): "system_proxy",
            (SC, "--nwi"): "interfaces",
            ("/sbin/route", "-n", "get", "default"): "default",
            ("/usr/sbin/netstat", "-rn"): "routes",
        }
        if argv in exact:
            return ReadResult(self.values[exact[argv]])
        if argv[0] == NS:
            if argv[1] == "-getinfo":
                return ReadResult(self.values["info"])
            if argv[1] == "-getdnsservers":
                return ReadResult(_public_fixture("@PUBLIC_IPV4_075@\n"))
            if argv[1] == "-getsearchdomains":
                return ReadResult("synthetic.invalid\n")
            if argv[1] in {"-getwebproxy", "-getsecurewebproxy", "-getsocksfirewallproxy"}:
                return ReadResult(self.values["proxy"])
            if argv[1] == "-getautoproxyurl":
                return ReadResult("URL: http://pac.synthetic.invalid/config\nEnabled: No\n")
            if argv[1] == "-getproxyautodiscovery":
                return ReadResult("Auto Proxy Discovery: Off\n")
            if argv[1] == "-listpreferredwirelessnetworks":
                return ReadResult(self.values["saved"])
        if argv[0] == IP:
            if argv[1] == "getsummary":
                return ReadResult(status="command_failed")
            if argv[2] != "en0":
                return ReadResult(status="command_failed")
            return ReadResult(_public_fixture("@PUBLIC_IPV4_067@") if argv[1] == "getifaddr" else self.values["packet"])
        if argv[0] == IF:
            return ReadResult(self.values["ifconfig"] if argv[1] == "en0" else "status: inactive")
        raise AssertionError("unexpected diagnostic command")


@pytest.fixture
def reader() -> FixtureReader:
    return FixtureReader()


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(b"x" * 32)


@pytest.fixture
def inspection(reader: FixtureReader, redactor: Redactor) -> Inspection:
    return MacDiagnostics(reader, redactor).inspect(env={})


@pytest.fixture(autouse=True)
def no_live_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every Uplink test is hermetic; per-test fake process runners may override this."""

    def refused(*args: object, **kwargs: object) -> None:
        raise AssertionError("Uplink tests may not spawn processes or open sockets")

    from offline_agent.uplink import lab_config

    config = tmp_path_factory.mktemp("lab-config") / "lab.local.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "node": "lab",
                "oob_host": "lab-oob",
                "wifi_host": "lab-wifi",
                "bench_relative": "lab-bench",
                "source_relative": "lab-bench/uplink-u1",
                "python_relative": "lab-bench/offline-agent/.venv/bin/python",
                "approved_serial": "LABTEST0001",
                "approved_by": "test operator",
                "approved_at": "2026-10-27T13:30:00-05:00",
                "fleet_clearance_at": "2026-10-27T13:31:00-05:00",
                "clearance_file": "FLEET-CLEARANCE.local.md",
            }
        )
    )
    config.chmod(0o600)
    monkeypatch.setattr(lab_config, "CONFIG_PATH", config)
    monkeypatch.setattr("offline_agent.uplink.trace.DEFAULT_DIRECTORY", tmp_path / "traces")
    monkeypatch.setattr(
        "offline_agent.uplink.model_identity.host_chip", lambda: "Synthetic test chip"
    )
    monkeypatch.setattr("subprocess.run", refused)
    monkeypatch.setattr("subprocess.Popen", refused)
    monkeypatch.setattr("os.system", refused)
    monkeypatch.setattr("socket.socket", refused)


@pytest.fixture
def online_result() -> "OracleResult":
    import time

    from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict

    return OracleResult(
        Verdict.ONLINE,
        time.time(),
        (
            Evidence("dns-1", "dns", True, "system_resolved"),
            Evidence("dns-2", "dns", True, "system_resolved"),
            Evidence("tls-apple", "tls", True, "verified", "path-synthetic"),
            Evidence("tls-google", "tls", True, "verified", "path-synthetic"),
            Evidence("portal-apple", "portal", True, "verified", "path-synthetic"),
            Evidence("portal-google", "portal", True, "verified", "path-synthetic"),
        ),
    )


@pytest.fixture
def fake_preflight_checks() -> "Checks":
    """Recovery tests inject passed build gates; the gate contract has its own fakes."""
    from offline_agent.contracts import Json

    class PassedChecks:
        def check(self, step: str) -> dict[str, Json]:
            return {"fixture": True, "step": step}

    return PassedChecks()
