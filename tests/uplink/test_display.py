
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
import io
import json
from pathlib import Path

import pytest

from offline_agent.uplink.contracts import Action, Tool
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.replay import replay
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import TraceFormatError, read_trace, validate_record


@pytest.mark.parametrize(
    "kind,value",
    [
        ("network", "Fictional Wi-Fi Network"),
        ("network", "HiddenHotspot"),
        ("mac", _public_fixture("@PUBLIC_MAC_116@")),
        ("host", "router.synthetic.invalid"),
        ("user", "fictional-user"),
        ("identifier", "fictional-private-value"),
    ],
)
def test_display_redacts_identifier_classes_literally(
    redactor: Redactor, kind: str, value: str
) -> None:
    redactor.handle(kind, value)
    shown = redactor.display("tool --target '" + value + "' --timeout 3")
    assert value not in shown and "«redacted»" in shown and "--timeout 3" in shown


@pytest.mark.parametrize(
    "text",
    [
        "curl https://example.com/",
        _public_fixture("tool --dns @PUBLIC_IPV4_095@"),
        _public_fixture("tool --ip @PUBLIC_IPV6_098@"),
        "tool --password fictional-canary",
        "tool token=fictional-canary",
        "keychain: fictional-item",
    ],
)
def test_display_scrubs_public_addresses_hosts_and_secrets(redactor: Redactor, text: str) -> None:
    shown = redactor.display(text)
    assert (
        "«redacted»" in shown and "fictional-canary" not in shown and "fictional-item" not in shown
    )


def test_display_retains_fixed_command_real_service_and_private_address(redactor: Redactor) -> None:
    redactor.handle("service", "Wi-Fi")
    command = _public_fixture("/usr/sbin/networksetup -setdnsservers Wi-Fi @PUBLIC_IPV4_078@")
    assert redactor.display(command) == command
    assert redactor.display("/sbin/ifconfig en0 up") == "/sbin/ifconfig en0 up"


def test_every_record_has_display_and_replay_prints_it(tmp_path: Path, redactor: Redactor) -> None:
    redactor.handle("service", "Wi-Fi")
    with RunTrace(redactor, directory=tmp_path) as trace:
        trace.step(
            "diagnose",
            Action(Tool.READ_DNS, {}),
            observation=RawObservation(
                ("/usr/sbin/networksetup", "-getdnsservers", "Wi-Fi"), _public_fixture("@PUBLIC_IPV4_078@"), "", 0, 2
            ),
        )
    rows = list(read_trace(trace.path))
    assert all(r["display"] for r in rows)
    assert rows[1]["display"] == "/usr/sbin/networksetup -getdnsservers Wi-Fi"
    assert "Wi-Fi" not in json.dumps(rows[1]["observation"])
    assert redactor.scan_trace(trace.path.read_text())["passed"]
    output = io.StringIO()
    replay(trace.path, speed=0, color=False, output=output)
    assert all(str(r["display"]) in output.getvalue() for r in rows)
    invalid = dict(rows[0])
    del invalid["display"]
    with pytest.raises(TraceFormatError):
        validate_record(invalid)


def test_old_version_one_trace_remains_readable(tmp_path: Path, redactor: Redactor) -> None:
    with RunTrace(redactor, directory=tmp_path) as trace:
        trace.step("diagnose", Action(Tool.READ_VITALS, {}))
    rows = list(read_trace(trace.path))
    for row in rows:
        row["trace_version"] = 1
        del row["display"]
        if row["event"] == "start":
            row["model"] = {"name": None, "provider": None}
            row.pop("authorization", None)
    trace.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert len(list(read_trace(trace.path))) == 3
