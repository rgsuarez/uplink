"""Trace acceptance, privacy and replay pins. All activity and clocks are synthetic."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
from pathlib import Path
from typing import cast

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import Action, Tool, parse_action
from offline_agent.uplink.diagnostics import NS, Inspection
from offline_agent.uplink.oracle import OnlineOracle, ProxyConfig
from offline_agent.uplink.plans import plan_change
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.trace import RunTrace, cleared, config_diff, decision, iso, model_fields
from offline_agent.uplink.trace_format import (
    COMMON,
    STEP_FIELDS,
    TraceFormatError,
    mapping,
    read_trace,
    validate_record,
)

from .test_contracts import CONTEXT, SCOPE, sample
from .test_session import HealthyTransport


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def wall(self) -> float:
        return 1_793_100_000 + self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def simple_trace(tmp_path: Path, redactor: Redactor) -> Path:
    clock = Clock()
    with RunTrace(redactor, directory=tmp_path, wall=clock.wall, clock=clock.clock) as trace:
        clock.now = 2
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation(("/sbin/ifconfig", "-l"), "en0 en1\n", "", 0, 1.25),
        )
        clock.now = 6
        trace.step(
            "report",
            Action(Tool.GIVE_UP, {"reason": "needs_human"}),
            choice=decision("human_needed", move="Report"),
        )
    return trace.path


def test_trace_is_flushed_per_line_and_has_start_finish_net_state(
    tmp_path: Path, redactor: Redactor, inspection: Inspection
) -> None:
    trace = RunTrace(redactor, directory=tmp_path)
    assert len(trace.path.read_text().splitlines()) == 1
    trace.update_state(inspection.vitals)
    trace.step("diagnose", Action(Tool.READ_VITALS, {}))
    assert len(trace.path.read_text().splitlines()) == 2
    trace.finish()
    rows = list(read_trace(trace.path))
    assert [r["event"] for r in rows] == ["start", "step", "finish"]
    assert rows[0]["schema_version"] == 1 and len(str(rows[0]["grammar_sha256"])) == 64
    assert mapping(rows[-1]["totals"])["model_calls"] == 0
    assert rows[1]["model"] is None and rows[1]["perf"] is None
    state = mapping(rows[1]["net_state"])
    interfaces = cast(list[dict[str, Json]], state["interfaces"])
    assert len(interfaces) == 7 and all(str(i["id"]).startswith("interface-") for i in interfaces)
    assert mapping(state["active_route"])["interface_id"]
    assert trace.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("field", sorted(COMMON | STEP_FIELDS))
def test_every_required_step_field_is_enforced(
    tmp_path: Path, redactor: Redactor, field: str
) -> None:
    row = dict(list(read_trace(simple_trace(tmp_path, redactor)))[1])
    del row[field]
    with pytest.raises(TraceFormatError):
        validate_record(row)


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "shell"),
        ("phase", "repair"),
        ("step", 0),
        ("step", True),
        ("ts", "2026-10-27T08:25:01-05:00"),
        ("ts", "2026-10-27T08:25:01.123Z"),
        ("ts", "2026-99-27T08:25:01.123-05:00"),
        ("elapsed_ms", -1),
    ],
)
def test_phase_timestamp_and_counter_refusals(
    tmp_path: Path, redactor: Redactor, field: str, value: Json
) -> None:
    row = dict(list(read_trace(simple_trace(tmp_path, redactor)))[1])
    row[field] = value
    with pytest.raises(TraceFormatError):
        validate_record(row)


def test_raw_observations_and_model_secrets_are_scrubbed_before_flush(
    tmp_path: Path, redactor: Redactor
) -> None:
    private = "synthetic-private-canary"
    redactor.handle("user", "synthetic-person")
    raw = "Preferred networks on en0:\n\tFictional Home Network\n"
    with RunTrace(
        redactor, directory=tmp_path, model_name="synthetic-model", provider="llama_cpp"
    ) as trace:
        trace.diagnostic(
            RawObservation(
                (NS, "-listpreferredwirelessnetworks", "en0"), raw, exit_code=0, source="available"
            )
        )
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation(
                ("/sbin/ifconfig", "en0"),
                _public_fixture("ether @PUBLIC_MAC_114@\nhost_name: laptop.synthetic.invalid\n"),
                "password: "
                + private
                + "\nkeychain: "
                + private
                + "\nPRIVATE_HOME/file\n",
                1,
                2.1,
            ),
            model_text="token=" + private + "\nSSID: Fictional Home Network",
            model_tokens=(10, 20),
            timings={
                "prompt_n": 10,
                "predicted_n": 2,
                "cache_n": 4,
                "prompt_per_second": 50,
                "predicted_per_second": 20,
                "prompt_ms": 200,
                "predicted_ms": 100,
            },
        )
    text = trace.path.read_text()
    for forbidden in (
        private,
        "Fictional Home Network",
        _public_fixture("@PUBLIC_MAC_114@"),
        "laptop.synthetic.invalid",
        "synthetic-person",
    ):
        assert forbidden not in text
    rows = list(read_trace(trace.path))
    model = mapping(rows[2]["model"])
    assert model["redacted"] and model["tokens"] is None
    assert mapping(rows[2]["perf"])["cached_n"] == 4
    assert redactor.scan(text)["passed"]


def test_model_timings_are_provider_values_not_inferred(redactor: Redactor) -> None:
    text = '{"version":1,"tool":"read_vitals","args":{}}'
    model, perf = model_fields(
        text,
        (1, 2),
        {"prompt_n": 9, "predicted_n": 2, "predicted_per_second": 19.75, "prompt_ms": 100},
        redactor,
    )
    assert model["text"] == text and model["tokens"] == [1, 2] and not model["redacted"]
    assert perf["predicted_per_second"] == 19.75 and perf["cached_n"] is None
    with pytest.raises(ContractError):
        model_fields(text, None, {"predicted_per_second": float("nan")}, redactor)
    with pytest.raises(ContractError):
        model_fields(text, None, {"prompt_n": True}, redactor)


def test_oracle_trace_has_every_subresult_latency_and_proxy_path(
    tmp_path: Path, redactor: Redactor
) -> None:
    oracle = OnlineOracle(
        HealthyTransport(), ProxyConfig({"https": "http://proxy.synthetic.invalid:8"}, {}), redactor
    )
    result = oracle.probe()
    with RunTrace(redactor, directory=tmp_path) as trace:
        trace.step("verify", Action(Tool.PROBE_ONLINE, {}), oracle=result)
    row = list(read_trace(trace.path))[1]
    result_row = mapping(row["oracle"])
    for key in ("dns", "https", "captive"):
        parts = cast(list[dict[str, Json]], result_row[key])
        assert len(parts) == 2 and all(cast(float, p["latency_ms"]) >= 0 for p in parts)
        assert all(str(p["target_id"]).startswith("host-") for p in parts)
    assert all(
        str(p["proxy_used"]).startswith("proxy-")
        for p in cast(list[dict[str, Json]], result_row["https"])
    )
    assert mapping(mapping(row["net_state"])["latest_oracle"])["verdict"] == "ONLINE"


def test_confirm_is_explicit_and_u1_timer_cannot_arm(tmp_path: Path, redactor: Redactor) -> None:
    action = parse_action(sample(Tool.SET_DNS), SCOPE)
    card = plan_change(action, CONTEXT)
    redactor.handle("service", "Synthetic Wi-Fi")
    change = config_diff("snapshot-a", {"dns": [_public_fixture("@PUBLIC_IPV4_075@")]}, {"dns": [_public_fixture("@PUBLIC_IPV4_003@")]})
    with RunTrace(redactor, directory=tmp_path, mode="plan", scope=SCOPE) as trace:
        with pytest.raises(ContractError, match="matching_plan"):
            trace.step(
                "confirm",
                action,
                change=change,
                card=card,
                choice=decision("blocked", confirm="not_requested_u1", timer=cleared("u1")),
            )
        trace.step("plan", action, change=change, card=card)
        trace.step(
            "confirm",
            action,
            change=change,
            card=card,
            choice=decision(
                "blocked", confirm="not_requested_u1", timer=cleared("u1_execution_disabled")
            ),
        )
        with pytest.raises(ContractError, match="apply_requires"):
            trace.step("apply", action, change=change)
    rows = list(read_trace(trace.path))
    assert [r["phase"] for r in rows[1:-1]] == ["plan", "confirm"]
    confirmation = rows[2]
    assert confirmation["confirm_card"] and confirmation["config_diff"]
    assert mapping(mapping(confirmation["decision"])["timer"])["state"] == "cleared"
    assert "Synthetic Wi-Fi" in str(confirmation["display"])
    assert "Synthetic Wi-Fi" not in json.dumps(confirmation["confirm_card"])


def test_simulated_timer_arm_and_auto_revert_are_ordered_not_executed(
    tmp_path: Path, redactor: Redactor
) -> None:
    action = parse_action(sample(Tool.SET_DNS), SCOPE)
    card = plan_change(action, CONTEXT)
    change = config_diff("snapshot-a", {"dns": [_public_fixture("@PUBLIC_IPV4_075@")]}, {"dns": [_public_fixture("@PUBLIC_IPV4_003@")]})
    timer: dict[str, Json] = {
        "state": "armed",
        "deadline_ts": iso(1_793_100_020),
        "reason": "fixture_only",
    }
    with RunTrace(redactor, directory=tmp_path, mode="test", scope=SCOPE) as trace:
        trace.step("plan", action, change=change, card=card)
        trace.step(
            "confirm",
            action,
            change=change,
            card=card,
            choice=decision("approved_fixture", confirm="approved", timer=timer),
        )
        trace.step("apply", action, change=change, choice=decision("simulated", timer=timer))
        trace.step(
            "revert",
            action,
            change=change,
            choice=decision(
                "deadline_fired_fixture", reverted=True, timer=cleared("reverted_fixture")
            ),
        )
    rows = list(read_trace(trace.path))
    assert [r["phase"] for r in rows[1:-1]] == ["plan", "confirm", "apply", "revert"]
    assert mapping(rows[-2]["decision"])["auto_revert_triggered"]


def test_invalid_step_does_not_advance_journal_state(tmp_path: Path, redactor: Redactor) -> None:
    action = parse_action(sample(Tool.SET_DNS), SCOPE)
    card = plan_change(action, CONTEXT)
    change = config_diff("snapshot-a", {}, {})
    with RunTrace(redactor, directory=tmp_path, scope=SCOPE) as trace:
        with pytest.raises(TraceFormatError, match="config_diff"):
            trace.step("plan", action, card=card)
        with pytest.raises(ContractError, match="matching_plan"):
            trace.step(
                "confirm",
                action,
                change=change,
                card=card,
                choice=decision("blocked", confirm="not_requested_u1", timer=cleared("u1")),
            )
        trace.step("diagnose", Action(Tool.READ_VITALS, {}))
    assert mapping(list(read_trace(trace.path))[-1]["totals"])["steps"] == 1


def test_same_salted_identifier_is_used_in_raw_and_typed_evidence(redactor: Redactor) -> None:
    expected = redactor.handle("network", "Fictional Named Network")
    assert redactor.scrub("SSID: Fictional Named Network") == "SSID: " + expected


def test_replay_rejects_apply_without_confirmation(tmp_path: Path, redactor: Redactor) -> None:
    action = parse_action(sample(Tool.SET_DNS), SCOPE)
    card = plan_change(action, CONTEXT)
    with RunTrace(redactor, directory=tmp_path, mode="test", scope=SCOPE) as trace:
        trace.step("plan", action, change=config_diff("snapshot-a", {}, {}), card=card)
    rows = list(read_trace(trace.path))
    rows[1]["phase"] = "apply"
    trace.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(TraceFormatError, match="apply_without_confirm"):
        list(read_trace(trace.path))


def test_missing_provider_counts_are_unknown_not_invented_zero(
    tmp_path: Path, redactor: Redactor
) -> None:
    with RunTrace(
        redactor, directory=tmp_path, model_name="synthetic-model", provider="llama_cpp"
    ) as trace:
        trace.step("diagnose", Action(Tool.READ_VITALS, {}), model_text="{}", timings={})
    rows = list(read_trace(trace.path))
    assert mapping(rows[-1]["totals"])["model_calls"] == 1
    assert mapping(rows[-1]["totals"])["prompt_n"] is None
    assert mapping(rows[-1]["totals"])["predicted_n"] is None
    with (
        RunTrace(redactor, directory=tmp_path) as no_model,
        pytest.raises(ContractError, match="model_identity_required"),
    ):
        no_model.step("diagnose", Action(Tool.READ_VITALS, {}), model_text="{}")


def test_session_rejection_keeps_actual_model_emission_and_timings(
    tmp_path: Path, redactor: Redactor, inspection: Inspection
) -> None:
    from offline_agent.uplink.session import Session

    raw = '{"version":1,"tool":"shell","args":{}}'
    oracle = OnlineOracle(HealthyTransport(), ProxyConfig({}, {}), redactor)
    with (
        Session(
            inspection.vitals,
            oracle,
            redactor,
            trace_directory=tmp_path,
            model_name="synthetic-model",
            provider="llama_cpp",
        ) as session,
        pytest.raises(ContractError),
    ):
        session.dispatch(
            raw,
            model_text=raw,
            model_tokens=(8, 9),
            timings={"prompt_n": 10, "predicted_n": 2, "predicted_per_second": 17},
        )
    rows = list(read_trace(session.writer.path))
    assert mapping(rows[0]["model"])["active_model_name"] == "synthetic-model"
    assert rows[1]["action"] is None and mapping(rows[1]["decision"])["outcome"] == "rejected"
    assert mapping(rows[1]["model"])["text"] == raw
    assert mapping(rows[1]["perf"])["predicted_n"] == 2


def test_armed_fixture_cannot_hide_watchdog_with_new_plan(
    tmp_path: Path, redactor: Redactor
) -> None:
    action = parse_action(sample(Tool.SET_DNS), SCOPE)
    card = plan_change(action, CONTEXT)
    diff = config_diff("snapshot-a", {}, {})
    timer: dict[str, Json] = {
        "state": "armed",
        "deadline_ts": iso(1_793_100_020),
        "reason": "fixture",
    }
    with RunTrace(redactor, directory=tmp_path, mode="test", scope=SCOPE) as trace:
        trace.step("plan", action, change=diff, card=card)
        trace.step(
            "confirm",
            action,
            change=diff,
            card=card,
            choice=decision("fixture", confirm="approved", timer=timer),
        )
        trace.step("apply", action, change=diff, choice=decision("fixture", timer=timer))
        with pytest.raises(ContractError, match="unresolved_watchdog"):
            trace.step("plan", action, change=diff, card=card)
    assert list(read_trace(trace.path))[-1]["verdict"] == "ERROR"


def test_dhcp_client_identifier_is_masked_as_a_whole(redactor: Redactor) -> None:
    raw = _public_fixture("client_identifier (uint8_mult): @PUBLIC_MAC_113@:01\nClient ID: fictional-account\n")
    redactor.learn_command(("/usr/sbin/ipconfig", "getpacket", "en0"), raw)
    scrubbed = redactor.scrub(raw)
    assert _public_fixture("@PUBLIC_MAC_113@:01") not in scrubbed
    assert "fictional-account" not in scrubbed
    assert "client_identifier" in scrubbed and "identifier-" in scrubbed


def test_unpadded_mac_is_redacted_and_canonicalizes_to_same_handle(redactor: Redactor) -> None:
    short, padded = _public_fixture("@PUBLIC_MAC_116@"), _public_fixture("@PUBLIC_MAC_115@")
    assert not redactor.scan(short)["passed"]
    assert redactor.handle("mac", short) == redactor.handle("mac", padded)
    assert short not in redactor.scrub("gateway " + short)
    assert redactor.scan(redactor.scrub(short))["passed"]
    assert redactor.scan(_public_fixture("2001:db8:@PUBLIC_MAC_116@"))["passed"]
    assert redactor.scrub(_public_fixture("2001:db8:@PUBLIC_MAC_116@")) == _public_fixture("2001:db8:@PUBLIC_MAC_116@")


def test_eval_trace_is_the_same_flushed_file_in_canonical_replay_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from offline_agent.uplink import trace as journal

    canonical = tmp_path / "uplink/traces"
    monkeypatch.setattr(journal, "DEFAULT_DIRECTORY", canonical)
    with journal.RunTrace(
        Redactor(b"i" * 32), directory=tmp_path / "uplink/evals/trial/traces", mode="test"
    ) as run:
        indexed = canonical / run.path.name
        assert indexed.is_file() and indexed.samefile(run.path)
        assert json.loads(indexed.read_text().splitlines()[0])["event"] == "start"
    assert json.loads(indexed.read_text().splitlines()[-1])["event"] == "finish"
