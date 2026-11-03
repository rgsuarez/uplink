"""Correction 1: timed, byte-bound Kaylin confirmation is required before launch."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_friday as friday


def hashes() -> dict[str, str]:
    return {key: "a" * 64 for key in friday.HASH_KEYS} | {
        "original_ok": friday.ORIGINAL_OK_SHA256,
        "correction": friday.CORRECTION_SHA256,
    }


def receipt() -> dict[str, object]:
    return {
        "kind": "TOP10-KAYLIN-HASH-VERIFIED-v1",
        "verified": True,
        "approved_by": "Kaylin",
        "submitted_at": c.OWNER_RELEASE_EARLIEST + 1,
        "verified_at": c.KAYLIN_VERIFY_BY,
        "submission_source": "synthetic submission receipt",
        "verification_source": "synthetic Kaylin verification receipt",
        "hashes": hashes(),
    }


def test_verification_at_142500_and_earliest_owner_are_accepted() -> None:
    friday.validate_hash_verification(receipt(), hashes(), c.OWNER_RELEASE_EARLIEST, c.WINDOW_START)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "unverified",
        "wrong_reviewer",
        "late",
        "future",
        "pre_release",
        "verified_before_send",
        "missing_source",
        "changed_go",
        "changed_policy",
        "changed_packet",
        "wrong_correction",
        "extra_field",
        "nan_time",
        "boolean_time",
    ],
)
def test_unverified_late_or_different_hashes_never_admit(mutation: str) -> None:
    value = copy.deepcopy(receipt())
    now = c.WINDOW_START
    if mutation == "missing":
        value = {}
    elif mutation == "unverified":
        value["verified"] = False
    elif mutation == "wrong_reviewer":
        value["approved_by"] = "Amanda"
    elif mutation == "late":
        value["verified_at"] = c.KAYLIN_VERIFY_BY + 0.001
    elif mutation == "future":
        now = c.KAYLIN_VERIFY_BY - 1
    elif mutation == "pre_release":
        value["submitted_at"] = c.OWNER_RELEASE_EARLIEST - 1
    elif mutation == "verified_before_send":
        value["verified_at"] = c.OWNER_RELEASE_EARLIEST
    elif mutation == "missing_source":
        value["verification_source"] = ""
    elif mutation in {"changed_go", "changed_policy", "changed_packet"}:
        value["hashes"] = hashes() | {mutation.removeprefix("changed_"): "b" * 64}
    elif mutation == "wrong_correction":
        value["hashes"] = hashes() | {"correction": "b" * 64}
    elif mutation == "extra_field":
        value["ignored"] = True
    elif mutation == "nan_time":
        value["verified_at"] = float("nan")
    elif mutation == "boolean_time":
        value["verified_at"] = True
    with pytest.raises(c.Refused):
        friday.validate_hash_verification(value, hashes(), c.OWNER_RELEASE_EARLIEST, now)


@pytest.mark.parametrize(
    "release,now,allowed",
    [
        (c.OWNER_RELEASE_EARLIEST, c.RELAY_LOAD_CHECK_AT, True),
        (c.OWNER_RELEASE_LATEST, c.RELAY_LOAD_CHECK_AT, True),
        (c.OWNER_RELEASE_EARLIEST - 0.001, c.WINDOW_START, False),
        (c.OWNER_RELEASE_LATEST + 0.001, c.WINDOW_START, False),
        (c.OWNER_RELEASE_LATEST, c.OWNER_RELEASE_LATEST - 1, False),
    ],
)
def test_owner_release_is_exact_140500_through_141500(
    release: float, now: float, allowed: bool
) -> None:
    if allowed:
        assert c.validate_owner_release(release, now) == release
    else:
        with pytest.raises(c.Refused):
            c.validate_owner_release(release, now)


def test_missing_verification_refuses_before_artifact_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object) -> None:
        pytest.fail("missing confirmation reached artifact or host reads")

    monkeypatch.setattr(friday, "artifact_hashes", forbidden)
    with pytest.raises(c.Refused, match="verification_missing_no_run"):
        friday.require_hash_verification(tmp_path, "a" * 64, {}, "{}", "{}", c.WINDOW_START)


def test_actual_go_bytes_are_bound_and_changes_invalidate_review(tmp_path: Path) -> None:
    packet = tmp_path / "TONIGHT/FRI-2130/live-stage-packet"
    packet.mkdir(parents=True)
    owner = json.dumps({"released_at": c.OWNER_RELEASE_EARLIEST})
    approval = "{}"
    (tmp_path / "OWNER-HANDBACK-TOP10.json").write_text(owner)
    (tmp_path / "RICHARD-APPROVAL-TOP10-FRI.json").write_text(approval)
    (tmp_path / "harness/top10").mkdir(parents=True)
    c.save(tmp_path / "harness/top10/FROZEN.json", {})
    manifest_sha = c.file_hash(tmp_path / "harness/top10/FROZEN.json")
    window = {
        "owner_handback_sha256": c.file_hash(tmp_path / "OWNER-HANDBACK-TOP10.json"),
        "root_approval_sha256": c.file_hash(tmp_path / "RICHARD-APPROVAL-TOP10-FRI.json"),
    }
    c.save(tmp_path / "GO-BENCH-TOP10.json", window)
    for name in [
        "KAYLIN-OK-TOP10-FRI.md",
        "KAYLIN-OK-TOP10-FRI-CORRECTION.md",
        "ops/top10-stage/policy.py",
        "TONIGHT/FRI-2130/live-stage-packet.tar",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic")
    (packet / "policy.py").write_text("synthetic")
    c.save(packet / "packet.json", {})
    (packet / ("uplink-top10-" + manifest_sha[:12])).write_text("synthetic")
    before = friday.artifact_hashes(tmp_path, manifest_sha, window, owner, approval)
    assert before["go"] == c.file_hash(tmp_path / "GO-BENCH-TOP10.json")
    with (tmp_path / "GO-BENCH-TOP10.json").open("a") as stream:
        stream.write(" ")
    after = friday.artifact_hashes(tmp_path, manifest_sha, window, owner, approval)
    assert after["go"] != before["go"]
    assert c.read(tmp_path / "GO-BENCH-TOP10.json") == window
    (packet / "policy.py").write_text("changed")
    with pytest.raises(c.Refused, match="reviewed_policy_copy_changed"):
        friday.artifact_hashes(tmp_path, manifest_sha, window, owner, approval)
