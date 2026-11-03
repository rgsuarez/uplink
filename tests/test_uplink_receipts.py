"""Offline evidence verification must reject missing, altered or incomplete proof."""

import hashlib
import json
import runpy
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "evidence/uplink/forced-20261027"


def test_published_receipts_recompute_the_historical_claim() -> None:
    verify = runpy.run_path(str(ROOT / "scripts/uplink_receipts.py"))["verify"]
    result = verify(BUNDLE)
    assert result["verified"] and result["complete"] and result["cells"] == 24
    forced, scripted = result["rollup"]
    assert (forced["repairs"], forced["reroutes"], forced["post_probe_labels_correct"]) == (3, 5, 5)
    assert (scripted["repairs"], scripted["reroutes"]) == (3, 2)
    assert forced["healthy_noops"] == scripted["healthy_noops"] == 2


@pytest.mark.parametrize("mutation", ["altered", "missing", "extra", "empty"])
def test_corrupt_or_incomplete_bundle_never_passes(tmp_path: Path, mutation: str) -> None:
    verify = runpy.run_path(str(ROOT / "scripts/uplink_receipts.py"))["verify"]
    target = tmp_path / "bundle"
    shutil.copytree(BUNDLE, target)
    manifest = json.loads((target / "manifest.json").read_text())
    first = target / manifest["cases"][0]["case"]
    if mutation == "altered":
        first.write_bytes(first.read_bytes() + b"\n")
    elif mutation == "missing":
        first.unlink()
    elif mutation == "extra":
        (target / "unregistered.json").write_text("{}")
    else:
        (target / "manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        verify(target)


def test_partial_attempt_cannot_be_relabeled_complete(tmp_path: Path) -> None:
    verify = runpy.run_path(str(ROOT / "scripts/uplink_receipts.py"))["verify"]
    target = tmp_path / "partial"
    shutil.copytree(ROOT / "evidence/uplink/open-history-stop-20261027", target)
    assert verify(target)["complete"] is False
    path = target / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["complete"] = True
    path.write_text(json.dumps(manifest))
    (target / "manifest.sha256").write_text(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  manifest.json\n"
    )
    with pytest.raises(ValueError, match="completion_or_schedule_mismatch"):
        verify(target)


@pytest.mark.parametrize("contradiction", ["oracle", "cleanup"])
def test_rehashed_contradictory_proof_is_still_refused(tmp_path: Path, contradiction: str) -> None:
    verify = runpy.run_path(str(ROOT / "scripts/uplink_receipts.py"))["verify"]
    target = tmp_path / "bundle"
    shutil.copytree(BUNDLE, target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if contradiction == "oracle":
        name = manifest["cases"][0]["trace"]
        path = target / name
        events = [json.loads(line) for line in path.read_text().splitlines()]
        accepted = next(
            event
            for event in events
            if event.get("oracle")
            and event["oracle"]["verdict"] == "ONLINE"
            and (event.get("action") or {}).get("tool") == "declare_online"
        )
        accepted["oracle"]["dns"][0]["ok"] = False
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
        reason = "online_without_six_successful_checks"
    else:
        name = "final-cleanup.json"
        path = target / name
        cleanup = json.loads(path.read_text())
        cleanup["active_owned_daemons"] = 1
        path.write_text(json.dumps(cleanup))
        reason = "final_cleanup_not_verified"
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    (target / "manifest.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json\n"
    )
    with pytest.raises(ValueError, match=reason):
        verify(target)
