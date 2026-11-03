"""Configured serials stay out of future artifacts; raw authority stays in memory."""

import io
import json
import time
from pathlib import Path
from typing import cast

import pytest

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink import lab_config
from offline_agent.uplink.contracts import Action, Tool
from offline_agent.uplink.model_identity import expected_model
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.real_fleet import approval, validate_authorization
from offline_agent.uplink.real_journal import append_trial
from offline_agent.uplink.real_preflight import Checks
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_runner import recover
from offline_agent.uplink.real_simulator import SimulatedLab
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import read_trace


def test_serial_redaction_covers_keys_embedded_json_handles_and_display() -> None:
    redactor = Redactor(b"s" * 32)
    redactor.register_lab_identity()
    serial = lab_config.load_config().approved_serial
    value: dict[str, Json] = {
        "authorization": {"serial": serial},
        "stderr": "device=" + serial.lower(),
        "nested": [{"host-" + serial + "-suffix": "prefix" + serial + "suffix"}],
        "serialized": json.dumps({"nested": serial}),
    }
    hidden = redactor.scrub_serials_json(value)
    assert "authorization" in object_json(hidden)
    assert redactor.scrub_serials_json(hidden) == hidden
    assert redactor.scan(json.dumps(hidden))["passed"] is True
    assert redactor.scan(redactor.scrub(json.dumps(value)))["passed"] is True
    assert redactor.scan(redactor.display("host-" + serial + "-suffix"))["passed"] is True
    assert redactor.scan(json.dumps(value))["passed"] is False


def test_serial_scan_detects_embedded_and_case_variant_leaks() -> None:
    redactor = Redactor(b"s" * 32)
    redactor.register_lab_identity()
    serial = lab_config.load_config().approved_serial
    for text in (serial, serial.lower(), "host-" + serial + "-suffix"):
        result = redactor.scan(text)
        assert result["passed"] is False and result["identifier_kinds"] == ["serial"]
        assert serial not in json.dumps(result)


@pytest.mark.parametrize("bad_config", ("missing", "malformed"))
def test_no_private_identity_is_never_silent_redaction_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_config: str
) -> None:
    path = tmp_path / "identity.json"
    if bad_config == "malformed":
        path.write_text("{}")
        path.chmod(0o600)
    monkeypatch.setattr(lab_config, "CONFIG_PATH", path)
    with pytest.raises(ValueError):
        Redactor(b"s" * 32).register_lab_identity()


def test_trace_start_and_raw_observations_redact_serial_before_flush(tmp_path: Path) -> None:
    redactor = Redactor.local(tmp_path)
    redactor.register_lab_identity()
    auth = cast(dict[str, Json], {**approval(), "clearance_sha256": "a" * 64})
    serial = lab_config.load_config().approved_serial
    with RunTrace(
        redactor, directory=tmp_path / "traces", mode="test", authorization=auth
    ) as trace:
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation(
                ("fake", "host-" + serial + "-suffix"),
                json.dumps({serial: "prefix" + serial + "suffix"}),
                serial.lower(),
                exit_code=0,
            ),
        )
    records = list(read_trace(trace.path))
    assert object_json(records[0]["authorization"])["serial"] != serial
    assert redactor.scan_trace(trace.path.read_text())["passed"] is True
    assert validate_authorization(auth)["serial"] == serial


def test_receipt_summary_and_handback_keep_raw_authority_only_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_preflight_checks: Checks
) -> None:
    spec = RealSpec("0123456789ab", "wrong_dns")
    backend = SimulatedLab(spec)
    backend.dry = False
    backend.authorization = cast(dict[str, Json], {**approval(), "clearance_sha256": "a" * 64})
    identity = expected_model(chip="Apple M4 Max")
    identity.update(verification="server_verified", verified_at=time.time())
    monkeypatch.setattr(backend, "identity", lambda: identity)
    row = recover(
        backend, spec, tmp_path, confirm=lambda _: True, preflight_checks=fake_preflight_checks
    )
    serial = lab_config.load_config().approved_serial
    assert object_json(row["authorization"])["serial"] != serial
    assert backend.authorization["serial"] == serial
    # The journal validates the original authority, without rewriting the saved receipt.
    line = append_trial(tmp_path, {**row, "authorization": backend.authorization})
    summary = json.dumps({"receipts": [row], "handback": line})
    redactor = Redactor.local(tmp_path)
    redactor.register_lab_identity()
    assert redactor.scan(summary)["passed"] is True
    outputs = [*tmp_path.rglob("*.json"), *tmp_path.rglob("*.jsonl"), *tmp_path.rglob("*.md")]
    assert outputs
    assert all(redactor.scan(path.read_text())["passed"] is True for path in outputs)


@pytest.mark.parametrize("failed", (False, True))
def test_bridge_filters_serial_from_success_and_error_replies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: bool,
) -> None:
    from offline_agent.uplink import real_bridge

    serial = lab_config.load_config().approved_serial

    def handle(_: dict[str, Json]) -> dict[str, Json]:
        if failed:
            raise ContractError("device_" + serial + "_unavailable")
        return {"serial": serial, "embedded": "host-" + serial + "-suffix"}

    monkeypatch.setattr(real_bridge, "ROOT", tmp_path)
    monkeypatch.setattr(real_bridge, "require_host", lambda: None)
    monkeypatch.setattr(real_bridge, "handle", handle)
    monkeypatch.setattr("sys.argv", ["real_bridge"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    if failed:
        with pytest.raises(SystemExit) as stopped:
            real_bridge.main()
        assert stopped.value.code == 2
    else:
        real_bridge.main()
    output = capsys.readouterr().out
    assert json.loads(output)["ok"] is (not failed)
    assert serial not in output
    redactor = Redactor(b"s" * 32)
    redactor.register_lab_identity()
    assert redactor.scan(output)["passed"] is True
