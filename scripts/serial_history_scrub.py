"""Dry-run serial replacement inventory. No rewrite, remote call or publication mode."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from offline_agent.uplink.lab_config import load_config

REPLACEMENT = b"REDACTED-NO-LIVE-AUTHORITY"
MAX_BYTES = 128 * 1024 * 1024
MAX_OBJECTS = 20_000


class Refused(ValueError):
    """Closed refusal labels only; Git diagnostics can contain private values."""


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", *args],
            cwd=root,
            input=data,
            capture_output=True,
            timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("git_read_unavailable") from exc
    if result.returncode:
        raise Refused("git_read_refused")
    return result.stdout


def refs(root: Path) -> dict[str, str]:
    rows = git(root, "for-each-ref", "--format=%(refname) %(objectname)").decode().splitlines()
    return dict(row.rsplit(" ", 1) for row in rows)


def reachable(root: Path, commit: str) -> dict[str, str]:
    return {
        row.partition(" ")[0]: row.partition(" ")[2]
        for row in git(root, "rev-list", "--objects", commit).decode().splitlines()
    }


def scrub_projection(content: bytes, serial: bytes) -> tuple[int, str]:
    if not re.fullmatch(rb"[A-Za-z0-9]{8,32}", serial) or serial in REPLACEMENT:
        raise Refused("configured_serial_required")
    count = content.count(serial)
    proposed = content.replace(serial, REPLACEMENT)
    if serial in proposed:
        raise Refused("replacement_control_failed")
    return count, hashlib.sha256(proposed).hexdigest()


def inspect(root: Path, requested: dict[str, str], serial: bytes) -> dict[str, object]:
    """Read immutable tips and project literal replacements without writing Git objects."""
    scrub_projection(b"control:" + serial, serial)
    if len(requested) != 2:
        raise Refused("exactly_two_explicit_branches_required")
    before = refs(root)
    for ref, expected in requested.items():
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]+", ref):
            raise Refused("explicit_head_ref_required")
        if not re.fullmatch(r"[0-9a-f]{40}", expected) or before.get(ref) != expected:
            raise Refused("branch_pin_mismatch")
    histories = {ref: reachable(root, oid) for ref, oid in requested.items()}
    names = {oid: path for history in histories.values() for oid, path in history.items()}
    if not names or len(names) > MAX_OBJECTS:
        raise Refused("object_count_outside_cap")
    info = (
        git(
            root,
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            data=("\n".join(sorted(names)) + "\n").encode(),
        )
        .decode()
        .splitlines()
    )
    objects = []
    for row in info:
        fields = row.split()
        if len(fields) != 3 or fields[1] not in {"blob", "tree", "commit", "tag"}:
            raise Refused("object_metadata_unavailable")
        objects.append((fields[0], fields[1], int(fields[2])))
    if sum(size for _, _, size in objects) > MAX_BYTES:
        raise Refused("history_scan_exceeds_128MiB")
    findings: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    signed_commits = 0
    try:
        process = subprocess.Popen(
            ["git", "--no-replace-objects", "cat-file", "--batch"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            for oid, kind, size in objects:
                process.stdin.write((oid + "\n").encode())
                process.stdin.flush()
                if process.stdout.readline().decode().split() != [oid, kind, str(size)]:
                    raise Refused("object_header_mismatch")
                content = process.stdout.read(size)
                if len(content) != size or process.stdout.read(1) != b"\n":
                    raise Refused("object_body_incomplete")
                signed_commits += kind == "commit" and b"\ngpgsig " in content
                count, projected_digest = scrub_projection(content, serial)
                counts[kind] += 1
                if count:
                    findings.append(
                        {
                            "oid": oid,
                            "kind": kind,
                            "bytes": size,
                            "occurrences": count,
                            "path_hint": names[oid].replace(serial.decode(), "[redacted]"),
                            "in_branches": [ref for ref, h in histories.items() if oid in h],
                            "literal_replacement_sha256": projected_digest,
                            "projected_literal_serial_count": 0,
                            "not_a_rewritten_git_oid": True,
                        }
                    )
        finally:
            process.stdin.close()
            process.stdout.close()
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise Refused("object_reader_timeout") from None
        if code:
            raise Refused("object_reader_failed")
    except OSError as exc:
        raise Refused("object_reader_unavailable") from exc
    hit_oids = {str(r["oid"]) for r in findings}
    other_refs = []
    for ref, oid in before.items():
        if ref in requested:
            continue
        retained = hit_oids.intersection(reachable(root, oid))
        if retained:
            other_refs.append(
                {
                    "ref": ref.replace(serial.decode(), "[redacted]"),
                    "tip": oid,
                    "retained_serial_objects": len(retained),
                }
            )
    after = refs(root)
    selected_unchanged = all(after.get(ref) == oid for ref, oid in requested.items())
    return {
        "version": 1,
        "mode": "dry_run_only",
        "recorded_at": datetime.now(UTC).isoformat(),
        "requested_branches": requested,
        "selected_tips_unchanged": selected_unchanged,
        "all_ref_snapshot_unchanged": before == after,
        "scanned_objects": len(objects),
        "object_types": dict(counts),
        "scanned_bytes": sum(size for _, _, size in objects),
        "skipped_objects": 0,
        "signed_commits_requiring_new_signatures_if_rewritten": signed_commits,
        "serial_bearing_objects": len(findings),
        "per_branch": {
            ref: {"serial_bearing_objects": sum(str(r["oid"]) in history for r in findings)}
            for ref, history in histories.items()
        },
        "replacement_marker": REPLACEMENT.decode(),
        "findings": findings,
        "other_refs_retain_old_objects": other_refs,
        "refs_containing_configured_serial": sum(serial.decode() in ref for ref in before),
        "dry_run_complete": selected_unchanged,
        "publication_ready": False,
        "source_git_objects_written": 0,
        "source_refs_written": 0,
        "history_rewritten": False,
        "remote_calls": 0,
        "push": False,
        "limits": [
            "Only the configured exact serial is projected, not all secret formats.",
            "Other refs, tags, reflogs, backups, private artifacts and remote copies "
            "require separate disposition.",
            "Projected content hashes are not candidate tree/commit IDs; "
            "no sanitized history exists yet.",
            "Branch motion invalidates the current pin. Rebind before any future scrub.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--private-config", type=Path, required=True)
    parser.add_argument("--branch", action="append", required=True, metavar="FULL_REF=TIP_SHA")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        pairs = [value.split("=", 1) for value in args.branch]
        if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
            raise Refused("unique_ref_tip_pairs_required")
        config = load_config(args.private_config)
        root = args.repo.resolve()
        target = args.output.absolute()
        artifacts = root / "artifacts"
        if ".." in target.parts or not target.is_relative_to(artifacts) or target.exists():
            raise Refused("new_private_artifact_output_required")
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise Refused("output_symlink_refused")
        result = inspect(root, dict(pairs), config.approved_serial.encode())
        result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        encoded = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
        if config.approved_serial.encode() in encoded:
            raise Refused("output_redaction_failed")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "mode",
                        "scanned_objects",
                        "serial_bearing_objects",
                        "per_branch",
                        "selected_tips_unchanged",
                        "dry_run_complete",
                        "publication_ready",
                        "source_git_objects_written",
                        "source_refs_written",
                        "history_rewritten",
                    )
                }
            )
        )
        raise SystemExit(0 if result["dry_run_complete"] else 2)
    except (Refused, ValueError, OSError) as exc:
        label = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        parser.exit(1, "Refused: " + label + "\n")


if __name__ == "__main__":
    main()
