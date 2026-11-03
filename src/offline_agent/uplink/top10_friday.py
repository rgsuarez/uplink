"""Require Kaylin's exact artifact verification by Friday's 14:25 deadline."""

from __future__ import annotations

import hashlib
from pathlib import Path

from offline_agent.uplink import top10_contract as c

ORIGINAL_OK_SHA256 = "fed4a4f064cc0ce437ac90a50803fbda2a4010e1f7053fe82c2175ad680dfc90"
CORRECTION_SHA256 = "77280a383f02a9e5edb7fb0eb35e2e269ff4d9ded08eb58c55f8e4adbeda3c10"
VERIFICATION_FILE = "KAYLIN-HASH-VERIFIED-TOP10-FRI.json"
HASH_KEYS = {
    "original_ok",
    "correction",
    "manifest",
    "owner",
    "go",
    "approval",
    "policy",
    "packet",
    "packet_descriptor",
    "sudoers",
}


def validate_hash_verification(
    value: object, hashes: dict[str, str], released_at: object, now: float
) -> None:
    released = c.validate_owner_release(released_at, now)
    row = c.obj(value)
    if (
        set(row)
        != {
            "kind",
            "verified",
            "approved_by",
            "submitted_at",
            "verified_at",
            "submission_source",
            "verification_source",
            "hashes",
        }
        or row["kind"] != "TOP10-KAYLIN-HASH-VERIFIED-v1"
        or row["verified"] is not True
        or row["approved_by"] != "Kaylin"
        or set(hashes) != HASH_KEYS
        or hashes["original_ok"] != ORIGINAL_OK_SHA256
        or hashes["correction"] != CORRECTION_SHA256
        or row["hashes"] != hashes
    ):
        raise c.Refused("top10_exact_kaylin_hash_verification_required")
    for digest in hashes.values():
        c.text(digest, r"[0-9a-f]{64}")
    submitted = c.number(row["submitted_at"], released, min(now, c.KAYLIN_VERIFY_BY))
    c.number(row["verified_at"], submitted, min(now, c.KAYLIN_VERIFY_BY))
    c.text(row["submission_source"], r"[^\r\n]{1,512}")
    c.text(row["verification_source"], r"[^\r\n]{1,512}")


def artifact_hashes(
    root: Path, manifest_sha: str, window: dict[str, object], owner: str, approval: str
) -> dict[str, str]:
    packet = root / "TONIGHT/FRI-2130/live-stage-packet"
    go = root / "GO-BENCH-TOP10.json"
    owner_sha = hashlib.sha256(owner.encode()).hexdigest()
    approval_sha = hashlib.sha256(approval.encode()).hexdigest()
    if (
        c.read(go, private=True) != window
        or owner_sha != window["owner_handback_sha256"]
        or approval_sha != window["root_approval_sha256"]
        or c.file_hash(root / "OWNER-HANDBACK-TOP10.json") != owner_sha
        or c.file_hash(root / "RICHARD-APPROVAL-TOP10-FRI.json") != approval_sha
        or c.file_hash(root / "harness/top10/FROZEN.json") != manifest_sha
    ):
        raise c.Refused("top10_reviewed_authority_bytes_changed")
    hashes = {
        "original_ok": c.file_hash(root / "KAYLIN-OK-TOP10-FRI.md"),
        "correction": c.file_hash(root / "KAYLIN-OK-TOP10-FRI-CORRECTION.md"),
        "manifest": manifest_sha,
        "owner": owner_sha,
        "go": c.file_hash(go),
        "approval": approval_sha,
        "policy": c.file_hash(root / "ops/top10-stage/policy.py"),
        "packet": c.file_hash(packet.parent / "live-stage-packet.tar"),
        "packet_descriptor": c.file_hash(packet / "packet.json"),
        "sudoers": c.file_hash(packet / ("uplink-top10-" + manifest_sha[:12])),
    }
    if c.file_hash(packet / "policy.py") != hashes["policy"]:
        raise c.Refused("top10_reviewed_policy_copy_changed")
    return hashes


def require_hash_verification(
    root: Path, manifest_sha: str, window: dict[str, object], owner: str, approval: str, now: float
) -> None:
    """Read externally supplied confirmation; never fabricate a successful review."""
    try:
        receipt = c.read(root / VERIFICATION_FILE, private=True)
    except FileNotFoundError as exc:
        raise c.Refused("top10_kaylin_hash_verification_missing_no_run") from exc
    hashes = artifact_hashes(root, manifest_sha, window, owner, approval)
    validate_hash_verification(receipt, hashes, c.parse_json(owner).get("released_at"), now)
