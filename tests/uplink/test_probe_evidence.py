"""Requested programs and recovered networks never substitute for returned probe rows."""

import json
from pathlib import Path
from typing import cast

import pytest

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.contracts import Scope
from offline_agent.uplink.lab_runner import Emission
from offline_agent.uplink.oracle import OracleResult
from offline_agent.uplink.probe_evidence import (
    read_probe_evidence,
    require_positive_control,
    trace_probe_evidence,
)
from offline_agent.uplink.probe_overlay import overlay
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_runner import recover
from offline_agent.uplink.real_simulator import SimulatedLab
from offline_agent.uplink.records import RawObservation

from .probe_receipts import returned_measurement, returned_probe


def record(execution: dict[str, Json]) -> dict[str, Json]:
    return {
        "step": 1,
        "observation": {
            "source": "generated_probe_episode",
            "exit_code": 0,
            "stdout": json.dumps({"program": execution["requested"], "execution": execution}),
        },
    }


def test_positive_control_requires_one_actual_returned_read() -> None:
    value = returned_probe()
    assert require_positive_control(value) == value


@pytest.mark.parametrize(
    "change",
    [
        {"rows": []},
        {"status": "timeout"},
        {"uid": 0},
        {"uid": True},
        {"settings_unchanged": False},
        {"wifi_route_unchanged": False},
        {"after_sha256": "b" * 64},
        {"validated": {}},
    ],
)
def test_positive_control_rejects_incomplete_or_changed_evidence(change: dict[str, Json]) -> None:
    value = returned_probe()
    value.update(change)
    with pytest.raises(ContractError, match="probe_positive_control_"):
        require_positive_control(value)


@pytest.mark.parametrize("bad", (None, {}, [], "completed"))
def test_positive_control_refuses_missing_results(bad: object) -> None:
    with pytest.raises(ContractError, match="probe_positive_control_"):
        require_positive_control(bad)


def test_trace_counts_a_returned_negative_measurement_as_execution() -> None:
    value = returned_measurement()
    rows = cast(list[dict[str, Json]], value["rows"])
    rows[0]["ok"] = False
    evidence = trace_probe_evidence([record(value)], profile="lab_wifi")
    assert evidence["probe_enabled"] is True
    assert evidence["requested"] == evidence["attempted"] == evidence["returned"] == 1
    assert evidence["positive_results"] == 0
    assert evidence["non_read_state_returned"] == 1


@pytest.mark.parametrize("status", ("refused", "timeout"))
def test_six_refused_requests_cannot_earn_probe_enabled_credit(status: str) -> None:
    value = returned_probe()
    value.update(status=status, rows=[], reason="unprivileged_lab_probe_required")
    evidence = trace_probe_evidence([record(value) for _ in range(6)], profile="lab_wifi")
    assert evidence["requested"] == 6
    assert evidence["attempted"] == evidence["returned"] == 0
    assert evidence["probe_enabled"] is False and evidence["reason"] == "no_returned_probe_rows"


@pytest.mark.parametrize(
    "change",
    [
        {"uid": None},
        {"uid": True},
        {"profile": "linux_lab"},
        {"validated": {}},
        {"settings_unchanged": False},
        {"wifi_route_unchanged": False},
        {"after_sha256": "b" * 64},
    ],
)
def test_one_good_row_cannot_hide_invalid_execution_evidence(change: dict[str, Json]) -> None:
    bad = returned_measurement()
    bad.update(change)
    evidence = trace_probe_evidence(
        [record(returned_measurement()), record(bad)], profile="lab_wifi"
    )
    assert evidence["probe_enabled"] is False and evidence["accounting"] == "incomplete"


@pytest.mark.parametrize("field,value", (("index", True), ("index", 1), ("ok", 1)))
def test_rows_must_bind_to_typed_requested_primitive(field: str, value: Json) -> None:
    execution = returned_probe()
    cast(list[dict[str, Json]], execution["rows"])[0][field] = value
    evidence = trace_probe_evidence([record(execution)])
    assert evidence["probe_enabled"] is False and evidence["accounting"] == "incomplete"


def test_empty_or_malformed_trace_never_implies_execution(tmp_path: Path) -> None:
    assert trace_probe_evidence([])["probe_enabled"] is False
    broken = record(returned_probe())
    object_json(broken["observation"])["stdout"] = "not-json"
    evidence = trace_probe_evidence([record(returned_probe()), broken])
    assert evidence["probe_enabled"] is False and evidence["accounting"] == "incomplete"
    assert read_probe_evidence(tmp_path / "absent")["probe_enabled"] is False


def test_typed_refusal_reason_survives_accounting() -> None:
    execution = returned_probe()
    execution["uid"] = 0
    evidence = trace_probe_evidence([record(execution)])
    decisions = cast(list[dict[str, Json]], evidence["decisions"])
    assert decisions[0]["reason"] == "probe_execution_identity_unproven"
    assert evidence["probe_enabled"] is False


def test_preflight_and_online_oracle_do_not_count_as_trial_probes() -> None:
    before = record(returned_probe())
    object_json(before["observation"])["source"] = "probe_positive_control"
    oracle = record(returned_probe())
    object_json(oracle["observation"])["source"] = "wifi_probes"
    assert trace_probe_evidence([before, oracle])["probe_enabled"] is False


@pytest.mark.parametrize("kind", ("measurement", "read_state", "refused", "timeout"))
def test_recovery_receipt_separates_restoration_from_trace_probe_credit(
    tmp_path: Path, kind: str
) -> None:
    execution = returned_probe() if kind == "read_state" else returned_measurement()
    if kind in {"refused", "timeout"}:
        execution.update(status=kind, rows=[], reason="probe_not_returned")

    class ProbedLab(SimulatedLab):
        def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
            emission = super().choose(scope, observed, oracle)
            observation = object_json(record(execution)["observation"])
            self.last_layer_probe = RawObservation(
                (), str(observation["stdout"]), exit_code=0, source="generated_probe_episode"
            )
            return emission

    spec = RealSpec("0123456789ab", "dns_and_proxy", 55, True)
    row = recover(ProbedLab(spec), spec, tmp_path, confirm=lambda _: True)
    assert row["recovered"] is True
    qualifies = kind == "measurement"
    assert row["probe_enabled"] is qualifies and row["trial_passed"] is qualifies
    assert row["non_read_state_returned"] == (2 if qualifies else 0)
    evidence = read_probe_evidence(tmp_path / "traces" / str(row["trace"]), profile="lab_wifi")
    assert row["probe_execution"] == evidence
    assert evidence["requested"] == 2
    assert evidence["returned"] == (2 if kind in {"measurement", "read_state"} else 0)
    assert evidence["non_read_state_returned"] == row["non_read_state_returned"]
    if kind == "read_state":
        assert row["probe_failure"] == "no_returned_non_read_state_probe_rows"


def test_overlay_ignores_optimistic_receipt_flags_and_episodes() -> None:
    failed = returned_probe()
    failed.update(status="refused", rows=[])
    report = overlay(
        {"probe_scripts": True, "probe_enabled": True, "probe_episodes": [returned_probe()]},
        [record(failed), {"oracle": {"verdict": "ONLINE", "evidence": []}}],
    )
    assert report["probe_enabled"] is False
    assert object_json(report["probe_execution"])["returned"] == 0


def test_read_state_only_reports_returned_rows_but_refuses_probe_credit() -> None:
    evidence = trace_probe_evidence([record(returned_probe())], profile="lab_wifi")
    assert evidence["version"] == 2
    assert evidence["accounting"] == "complete" and evidence["returned"] == 1
    assert evidence["non_read_state_returned"] == 0 and evidence["probe_enabled"] is False
    assert evidence["reason"] == "no_returned_non_read_state_probe_rows"
    decisions = cast(list[dict[str, Json]], evidence["decisions"])
    assert decisions[0]["returned"] == 1 and decisions[0]["non_read_state_returned"] == 0
    report = overlay(
        {"probe_enabled": True, "non_read_state_returned": 99},
        [record(returned_probe()), {"oracle": {"verdict": "ONLINE", "evidence": []}}],
    )
    assert report["probe_enabled"] is False and report["non_read_state_returned"] == 0


@pytest.mark.parametrize(
    "probe",
    [
        {
            "op": "ping_gateway",
            "path": "current",
            "count": 1,
            "interval_ms": 200,
            "timeout_ms": 1000,
        },
        {"op": "dns_query", "name": "one", "resolver": "configured", "timeout_ms": 1000},
        {"op": "tcp_connect", "endpoint": "one", "path": "current", "timeout_ms": 1000},
        {"op": "https_head", "endpoint": "one", "path": "current", "timeout_ms": 1000},
        {"op": "captive_check", "endpoint": "one", "timeout_ms": 1000},
    ],
)
def test_completed_network_primitives_qualify_and_state_reads_do_not(
    probe: dict[str, Json],
) -> None:
    evidence = trace_probe_evidence([record(returned_probe()), record(returned_measurement(probe))])
    assert evidence["returned"] == 2 and evidence["non_read_state_returned"] == 1
    assert evidence["probe_enabled"] is True


@pytest.mark.parametrize("status", ("refused", "timeout", "failed", "unavailable"))
def test_read_state_cannot_cover_a_nonreturned_network_probe(status: str) -> None:
    execution = returned_measurement()
    cast(list[dict[str, Json]], execution["rows"])[0].update(status=status, ok=False)
    evidence = trace_probe_evidence([record(returned_probe()), record(execution)])
    assert evidence["returned"] == 1 and evidence["non_read_state_returned"] == 0
    assert evidence["probe_enabled"] is False


def test_read_state_only_batch_stops_after_first_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from offline_agent.uplink import real_runner, real_simulator

    class StateOnlyLab(SimulatedLab):
        def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
            emission = super().choose(scope, observed, oracle)
            self.last_layer_probe = RawObservation(
                (),
                str(object_json(record(returned_probe())["observation"])["stdout"]),
                exit_code=0,
                source="generated_probe_episode",
            )
            return emission

    monkeypatch.setattr(real_simulator, "SimulatedLab", StateOnlyLab)
    monkeypatch.setattr(
        "sys.argv",
        ["uplink-real", "--probe-scripts", "--trials", "3", "--output", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as stopped:
        real_runner.main()
    assert stopped.value.code == 1
    capsys.readouterr()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["requested_trials"] == 3 and summary["completed_trials"] == 1
    assert summary["three_consecutive_probe_enabled_recoveries"] is False
    receipt = summary["receipts"][0]
    assert receipt["recovered"] is True and receipt["trial_passed"] is False
    assert receipt["non_read_state_returned"] == 0
    assert receipt["probe_failure"] == "no_returned_non_read_state_probe_rows"
