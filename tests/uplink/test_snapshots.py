
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.diagnostics import Inspection
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.snapshots import SnapshotStore, diff_snapshot, with_prior_online


def test_private_salt_is_stable_scoped_and_never_in_trace(tmp_path: Path) -> None:
    redactor = Redactor.local(tmp_path)
    assert Redactor.local(tmp_path).handle("network", "Fictional") == redactor.handle(
        "network", "Fictional"
    )
    assert Redactor(b"y" * 32).handle("network", "Fictional") != redactor.handle(
        "network", "Fictional"
    )
    assert redactor.handle("host", "Fictional") != redactor.handle("network", "Fictional")
    assert (tmp_path / "salt.key").stat().st_mode & 0o777 == 0o600
    assert "salt=" not in repr(redactor)
    assert not redactor.scan("Fictional")["passed"]
    assert not redactor.scan(_public_fixture("@PUBLIC_MAC_114@"))["passed"]


def test_snapshot_roundtrip_diff_and_prior_online_ranking(
    tmp_path: Path, inspection: Inspection, online_result: OracleResult
) -> None:
    store = SnapshotStore(tmp_path)
    assert store.latest() is None
    saved = store.save(inspection.vitals, online_result)
    assert store.latest() == saved
    path = tmp_path / f"{saved.id}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert diff_snapshot(saved, inspection.vitals) == ()
    changed_service = replace(inspection.vitals.services[0], dns=(_public_fixture("@PUBLIC_IPV4_089@"),))
    current = replace(
        inspection.vitals, services=(changed_service, *inspection.vitals.services[1:])
    )
    diff = diff_snapshot(saved, current)
    assert (
        len(diff) == 1
        and diff[0]["before"] == [_public_fixture("@PUBLIC_IPV4_075@")]
        and diff[0]["after"] == [_public_fixture("@PUBLIC_IPV4_089@")]
    )
    ranked = with_prior_online(current, saved).candidates
    assert ranked[0].score == 100
    assert all(c.score < 100 for c in ranked if c.kind == "saved_wifi")


@pytest.mark.parametrize("verdict", [Verdict.CAPTIVE, Verdict.DEGRADED, Verdict.OFFLINE])
def test_failed_or_stale_oracle_never_replaces_last_known_good(
    tmp_path: Path, inspection: Inspection, verdict: Verdict
) -> None:
    store = SnapshotStore(tmp_path)
    for oracle in (
        OracleResult(verdict, time.time(), ()),
        OracleResult(Verdict.ONLINE, time.time() - 31, ()),
    ):
        with pytest.raises(ContractError):
            store.save(inspection.vitals, oracle)
    assert not list(tmp_path.iterdir())


def test_tampered_snapshot_fails_closed_without_echo(
    tmp_path: Path, inspection: Inspection, online_result: OracleResult
) -> None:
    store = SnapshotStore(tmp_path)
    saved = store.save(inspection.vitals, online_result)
    path = tmp_path / f"{saved.id}.json"
    value = saved.as_json()
    value["secret"] = "Fictional raw identifier"
    path.write_text(json.dumps(value))
    with pytest.raises(ContractError, match="schema_refused"):
        store.read(path)


def test_offline_checkpoint_never_becomes_last_known_good(
    tmp_path: Path, inspection: Inspection
) -> None:
    store = SnapshotStore(tmp_path)
    checkpoint = store.checkpoint(inspection.vitals)
    assert not checkpoint.online and checkpoint.id.startswith("checkpoint-")
    assert store.read(tmp_path / f"{checkpoint.id}.json") == checkpoint
    assert store.latest() is None
    assert with_prior_online(inspection.vitals, checkpoint) == inspection.vitals


def test_online_label_without_independent_evidence_is_not_a_snapshot(
    tmp_path: Path, inspection: Inspection
) -> None:
    with pytest.raises(ContractError):
        SnapshotStore(tmp_path).save(
            inspection.vitals, OracleResult(Verdict.ONLINE, time.time(), ())
        )
