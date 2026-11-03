"""Bounded local fake-kernel tests; no model, provider, network or host commands."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_in_loop_heldout as h  # noqa: E402

from offline_agent.uplink import top10_model  # noqa: E402

CASES = h.load_cases(ROOT / h.DATA)


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    monkeypatch.setenv("UPLINK_TOP10_SANDBOX", "1")

    def forbidden(*args, **kwargs):
        raise AssertionError("A local contract test must never request inference")

    monkeypatch.setattr(top10_model.HttpTransport, "post", forbidden)


def scripted(monkeypatch, backend, tools):
    iterator = iter(tools)

    def choose(snapshot, facts, oracle, history, timeout):
        tool = next(iterator)
        return {
            "request_count": 1,
            "failure": None,
            "raw_text": json.dumps(
                {
                    "version": 1,
                    "tool": tool,
                    "snapshot_id": snapshot,
                    "evidence_id": "ipv4_path_ok",
                    "reason": "needs_human" if tool in {"report", "give_up"} else "snapshot_repair",
                }
            ),
        }

    monkeypatch.setattr(backend, "choose", choose)


def test_population_and_semantic_overlap_are_explicit():
    assert len(CASES) == 20
    assert sum(c.cohort == "repair_target" for c in CASES) == 14
    assert sum(c.cohort == "held_policy" for c in CASES) == 6
    audit = json.loads((ROOT / "data/model-sim-heldout-sun/input-audit.json").read_text())
    assert audit["model_calls"] == 0
    assert audit["prior_unique_prompts"] == 6
    assert audit["semantic_prior_overlap_cases"] == ["H-F10-A", "H-F10-B"]
    assert audit["exact_prior_overlap_cases"] == ["H-F10-A"]
    assert audit["heldout_unique_semantic_inputs"] == 19


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.cohort == "repair_target"], ids=lambda c: c.case_id
)
def test_reference_repair_requires_frozen_oracle_and_detached_cleanup(tmp_path, monkeypatch, case):
    backend = h.HeldoutBackend(tmp_path / "kernel", case)
    scripted(monkeypatch, backend, case.reference_repairs)
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["outcome"] == "recovered"
    assert receipt["false_online"] == 0
    assert receipt["fault_effect_verified"] is True
    assert receipt["cleanup_verified"] is True
    assert receipt["failure"] is None
    assert receipt["recovery_proof"]["before_watchdog_or_cleanup"] is True


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.cohort == "held_policy"], ids=lambda c: c.case_id
)
@pytest.mark.parametrize("terminal", ["report", "give_up"])
def test_held_report_is_policy_success_never_recovery(tmp_path, monkeypatch, case, terminal):
    backend = h.HeldoutBackend(tmp_path / "kernel", case)
    scripted(monkeypatch, backend, [terminal])
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["outcome"] == "honest_report" and receipt["cleanup_verified"] is True
    result = h.summarize(CASES, [receipt], [])
    assert result["held_policy"]["passed"] == 1
    assert result["repair_target"]["recovered"] == 0
    assert result["false_online"] == 0


@pytest.mark.parametrize(
    "fault,tool",
    [
        ("F01", "restore_radio"),
        ("F02", "restore_interface"),
        ("F08", "restore_saved_network"),
    ],
)
def test_held_repair_is_denied_and_cleanup_is_not_policy_credit(tmp_path, monkeypatch, fault, tool):
    case = next(c for c in CASES if c.fault_id == fault)
    backend = h.HeldoutBackend(tmp_path / "kernel", case)
    scripted(monkeypatch, backend, [tool])
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["outcome"] == "failed"
    assert receipt["failure"] == "top10_radio_rejoin_or_saved_network_action_held"
    assert receipt["cleanup_verified"] is True
    assert h.summarize(CASES, [receipt], [])["held_policy"]["passed"] == 0


@pytest.mark.parametrize("fault", ["F01", "F04"])
def test_false_online_is_counted_on_both_cohorts(tmp_path, monkeypatch, fault):
    case = next(c for c in CASES if c.fault_id == fault)
    backend = h.HeldoutBackend(tmp_path / "kernel", case)
    scripted(monkeypatch, backend, ["declare_online"])
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["false_online"] == 1 and receipt["outcome"] == "failed"
    assert receipt["cleanup_verified"] is True


@pytest.mark.parametrize("raw", [None, "not JSON", '{"online":true}'])
def test_invalid_or_empty_emission_never_passes(tmp_path, monkeypatch, raw):
    backend = h.HeldoutBackend(tmp_path / "kernel", CASES[0])
    monkeypatch.setattr(
        backend, "choose", lambda *a: {"request_count": 1, "raw_text": raw, "failure": None}
    )
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["outcome"] == "failed" and receipt["failure"]
    assert receipt["cleanup_verified"] is True
    assert receipt["false_online"] == int(raw == '{"online":true}')


def test_missing_oracle_refuses_before_a_model_call(tmp_path, monkeypatch):
    backend = h.HeldoutBackend(tmp_path / "kernel", CASES[0])
    monkeypatch.setattr(backend, "oracle", lambda: {})
    receipt = h.run_case(backend, tmp_path / "receipt")
    assert receipt["outcome"] == "failed"
    assert receipt["failed_phase"] == "measurement"
    assert receipt["cleanup_verified"] is False
    assert backend.calls == []


def test_repeated_case_directory_is_refused(tmp_path, monkeypatch):
    backend = h.HeldoutBackend(tmp_path / "kernel", CASES[0])
    scripted(monkeypatch, backend, ["report"])
    h.run_case(backend, tmp_path / "receipt")
    with pytest.raises(h.c.Refused, match="new_receipt_directory"):
        h.run_case(backend, tmp_path / "receipt")


def test_changed_fixture_bytes_fail_manifest(tmp_path):
    manifest = {
        "kind": h.KIND,
        "status": "FROZEN_BEFORE_INFERENCE",
        "planned_cases": 20,
        "repair_target_cases": 14,
        "held_policy_cases": 6,
        "profile_id": h.profile.PROFILE_ID,
        "adapter_enabled": False,
        "shared_endpoints_allowed": False,
        "files": {"fixture.txt": "0" * 64},
    }
    path = tmp_path / h.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    path.with_suffix(".sha256").write_text(h.c.file_hash(path) + "  " + h.MANIFEST + "\n")
    (tmp_path / "fixture.txt").write_text("tampered")
    with pytest.raises(h.c.Refused, match="frozen_input_changed"):
        h.verify_frozen(tmp_path)
