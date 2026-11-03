"""File-only presentation tests. Synthetic contract fixtures are never model results."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.model_identity import GGUF_NAME, GGUF_SHA256
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict


@pytest.fixture
def renderer() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts/replay_model_sim.py"
    spec = importlib.util.spec_from_file_location("model_sim_replay", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value: object) -> str:
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
    ).hexdigest()


def oracle(good: bool) -> dict[str, object]:
    return dict(
        OracleResult(
            Verdict.ONLINE if good else Verdict.OFFLINE,
            1700000000.0,
            tuple(
                Evidence(
                    k,
                    "dns" if k.startswith("dns") else "tls" if k.startswith("tls") else "portal",
                    good,
                    "PRIVATE_REASON_NOT_FOR_VIDEO",
                    "path-synthetic",
                )
                for k in c.CHECK_IDS
            ),
        ).as_json()
    )


def fixture(report: bool = False) -> dict[str, object]:
    facts = {k: k not in {"ipv4_linklocal", "portal_redirect_seen"} for k in c.FACTS}
    before = facts | {"dns_matches_snapshot": False, "persistent_baseline_equal": False}
    receipts = []
    records = {}
    for index, (fault, trial) in enumerate((f, t) for f in c.BENCH_FAULT_IDS for t in (1, 2, 3)):
        run_id = f"{index:012x}"
        action = {
            "version": 1,
            "tool": "report" if report else "restore_dns",
            "snapshot_id": "a" * 64,
            "evidence_id": "dns_matches_snapshot",
            "reason": "needs_human" if report else "snapshot_repair",
        }
        trial_records = {
            "observe-1.json": {"facts": before, "snapshot_id": "a" * 64},
            "oracle-1.json": oracle(False),
            "model-1.json": {
                "request_count": 1,
                "failure": None,
                "raw_text": json.dumps(action),
                "action": action,
                "admitted_tools": list(c.TOOLS),
                "duration_ms": 5000,
            },
        }
        if not report:
            trial_records.update(
                {
                    "verify-observation-1.json": {
                        "facts": facts,
                        "checked_at": 1700000000.0,
                        "measurement_state_unchanged": True,
                        "lifecycle": "deciding",
                        "watchdog_restore": False,
                        "controller_cleanup": False,
                        "completed_model_actions": ["restore_dns"],
                        "last_reason": None,
                    },
                    "verify-oracle-1.json": oracle(True),
                }
            )
        records[run_id] = trial_records
        receipts.append(
            {
                "fault_id": fault,
                "trial": trial,
                "run_id": run_id,
                "outcome": "honest_report" if report else "recovered",
                "decision_requests": 1,
                "false_online": 0,
                "cleanup_verified": True,
                "artifact_sha256": {name: digest(value) for name, value in trial_records.items()},
            }
        )
    return {
        "evidence_kind": "MODEL_IN_LOOP_SIM",
        "adapter_enabled": False,
        "profile_id": "snapshot-restore-descriptions-v1",
        "model": {"filename": GGUF_NAME, "sha256": GGUF_SHA256, "bytes": 16756683904},
        "receipts": receipts,
        "trial_records": records,
        "private": "PRIVATE_DEVICE_NOT_FOR_VIDEO",
        "summary": {
            "counts": {
                "recovered": 0 if report else 21,
                "honest_report": 21 if report else 0,
                "failed": 0,
                "blocked": 0,
            },
            "false_online": 0,
            "model_calls": 21,
        },
    }


def bind(renderer: Any, root: Path, value: dict[str, object]) -> Path:
    path = root / "record.json"
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    renderer.RUNS = {
        "adopted": (
            path.name,
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "snapshot-restore-descriptions-v1",
        )
    }
    return path


def test_valid_recovery_is_historical_and_private_fields_never_render(renderer, tmp_path) -> None:
    bind(renderer, tmp_path, fixture())
    result = renderer.trajectory("adopted", "F04", 1, tmp_path)
    assert "MODEL_IN_LOOP_SIM" in result and "not held-out and not live Wi-Fi" in result
    assert "`restore_dns`" in result and "RECOVERED in the recorded simulation" in result
    assert "zero new model calls or host probes" in result
    assert "PRIVATE_" not in result and "1700000000" not in result


def test_report_keeps_cleanup_separate_from_recovery(renderer, tmp_path) -> None:
    bind(renderer, tmp_path, fixture(report=True))
    result = renderer.trajectory("adopted", "F06", 1, tmp_path)
    assert "REPORT: no model-selected repair" in result
    assert "Recorded outcome: `honest_report`" in result and "Verifier: RECOVERED" not in result


def test_hash_drift_and_symlinks_refuse(renderer, tmp_path) -> None:
    path = bind(renderer, tmp_path, fixture())
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(c.Refused, match="source_hash"):
        renderer.trajectory("adopted", "F04", 1, tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    renderer.RUNS["adopted"] = (
        link.name,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        "snapshot-restore-descriptions-v1",
    )
    with pytest.raises(c.Refused, match="symlink"):
        renderer.load_run("adopted", tmp_path)


@pytest.mark.parametrize("change", ["empty", "adapter", "wrong_model", "duplicate", "summary"])
def test_bad_source_or_population_refuses(renderer, tmp_path, change) -> None:
    value = fixture()
    if change == "empty":
        value = {}
    elif change == "adapter":
        value["adapter_enabled"] = True
    elif change == "wrong_model":
        value["model"]["sha256"] = "b" * 64
    elif change == "duplicate":
        value["receipts"][1] = value["receipts"][0]
    else:
        value["summary"]["counts"]["recovered"] = 22
    bind(renderer, tmp_path, value)
    with pytest.raises(c.Refused):
        renderer.load_run("adopted", tmp_path)


@pytest.mark.parametrize(
    "change", ["artifact_hash", "malformed_action", "false_online", "cleanup_race"]
)
def test_incomplete_or_contradictory_trajectory_refuses(renderer, tmp_path, change) -> None:
    value = fixture()
    receipt = next(r for r in value["receipts"] if r["fault_id"] == "F04" and r["trial"] == 1)
    records = value["trial_records"][receipt["run_id"]]
    if change == "artifact_hash":
        receipt["artifact_sha256"]["model-1.json"] = "b" * 64
    else:
        if change == "malformed_action":
            records["model-1.json"]["raw_text"] = "not-json"
        elif change == "false_online":
            records["verify-oracle-1.json"]["evidence"][0]["ok"] = False
        else:
            records["verify-observation-1.json"]["controller_cleanup"] = True
        receipt["artifact_sha256"] = {name: digest(v) for name, v in records.items()}
    bind(renderer, tmp_path, value)
    with pytest.raises(ValueError):
        renderer.trajectory("adopted", "F04", 1, tmp_path)
