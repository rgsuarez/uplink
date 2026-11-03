"""Read-only host collection after a posted handback. Never arm, inject, or run a VM."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from offline_agent.uplink.faults_v1 import digest
from offline_agent.uplink.faults_v1_inventory import READ_CODE, pin_ipv4, tailscale_addresses
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.remote_settings import SSH_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
SSH_PROOF = (
    "import json,os,platform; print(json.dumps({'node':platform.node().split('.')[0], "
    "'ssh_connection':os.environ.get('SSH_CONNECTION','')}))"
)


def released_handback(path: Path, marker: str, expected_node: str) -> str:
    if (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,253}", expected_node)
        or path.is_symlink()
        or not path.is_file()
        or path.suffix != ".md"
        or not path.resolve().is_relative_to(ROOT)
        or path.stat().st_size > 1_048_576
        or not marker.startswith(f"KAYLIN-OK {expected_node.upper()} FAULTS-V1-DRYRUN")
        or len(marker) < 20
    ):
        raise ValueError("exact_local_handback_marker_required")
    data = path.read_bytes()
    if not any(line.startswith(marker) for line in data.decode().splitlines()):
        raise ValueError("posted_handback_missing_no_host_contact")
    return hashlib.sha256(data).hexdigest()


def ssh(alias: str, code: str, timeout: int = 12) -> dict[str, object]:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,253}", alias):
        raise ValueError("ssh_alias_refused")
    r = subprocess.run(
        (*SSH_OPTIONS, "-4", alias, "python3 -B -"),
        input=code,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode or len(r.stdout.encode()) > 8 * 1024 * 1024:
        raise ValueError("readonly_ssh_failed")
    value: object = json.loads(r.stdout)
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("readonly_receipt_invalid")
    return value


def ipv4_proof(value: dict[str, object], expected_node: str) -> None:
    connection = value.get("ssh_connection")
    if value.get("node") != expected_node or not isinstance(connection, str):
        raise ValueError("ssh_target_identity_unproven")
    fields = connection.split()
    if len(fields) != 4 or any(ipaddress.ip_address(fields[i]).version != 4 for i in (0, 2)):
        raise ValueError("ssh_ipv4_unproven")


def save(path: Path, value: object) -> None:
    with path.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-read-only", action="store_true")
    parser.add_argument("--handback", type=Path)
    parser.add_argument("--marker")
    parser.add_argument("--protected-network", action="append", default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/uplink/faults-v1-dryrun")
    args = parser.parse_args()
    if not args.execute_read_only:
        print(
            json.dumps(
                {
                    "effects": False,
                    "mode": "plan",
                    "required": [
                        "posted handback",
                        "two verified IPv4 SSH paths",
                        "management network exclusions",
                    ],
                    "inject_available": False,
                }
            )
        )
        return
    if args.handback is None or args.marker is None or not args.protected_network:
        parser.error("gate file, exact relay marker and protected networks are required")
    config = load_config()
    release = released_handback(args.handback, args.marker, config.node)
    networks = tuple(str(ipaddress.ip_network(n, strict=False)) for n in args.protected_network)
    target = args.output.absolute()
    if (
        ".." in target.parts
        or not target.is_relative_to(ROOT / "artifacts")
        or target.exists()
        or any(p.is_symlink() for p in (target, *target.parents))
    ):
        raise ValueError("new_private_output_required")
    target.mkdir(parents=True, mode=0o700)
    save(target / "intent.json", {"mode": "read_only", "handback_sha256": release, "inject": False})
    try:
        for label, alias in (
            ("oob", config.oob_host),
            ("wifi", config.wifi_host),
        ):
            if released_handback(args.handback, args.marker, config.node) != release:
                raise ValueError("handback_changed")
            value = ssh(alias, SSH_PROOF)
            save(target / (label + "-ssh.private.json"), value)
            ipv4_proof(value, config.node)
        if released_handback(args.handback, args.marker, config.node) != release:
            raise ValueError("handback_changed")
        raw = ssh(config.oob_host, READ_CODE, timeout=120)
        save(target / "before-state.private.json", raw)
        ipv4_proof(raw, config.node)
        management = tailscale_addresses(raw.get("reads"))
        reads = raw["reads"]
        if not isinstance(reads, dict):
            raise ValueError("before_state_reads_required")
        specs = []
        for spec in pin_ipv4(raw, (uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]), networks):
            specs.append({"spec": spec.document(), "spec_sha256": digest(spec.document())})
        save(
            target / "pinned-specs.private.json",
            {
                "specs": specs,
                "status": "requires_scope_review_not_live_authority",
                "target_resolved_once": True,
                "ipv6_excluded": True,
            },
        )
        save(
            target / "summary.json",
            {
                "read_only": True,
                "status": "collected_requires_review",
                "ssh_ipv4_paths": 2,
                "tailscale_addresses": len(management),
                "failed_reads": [
                    k
                    for k, v in reads.items()
                    if not isinstance(v, dict) or v.get("exit_code") != 0
                ],
                "live_inject": False,
                "vm_contact": False,
            },
        )
        print("Read-only collection saved; scope review remains required.")
    except (ValueError, OSError, StopIteration, subprocess.SubprocessError) as exc:
        label = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc))
            else type(exc).__name__
        )
        save(target / "refusal.json", {"reason": label, "live_inject": False})
        raise SystemExit("Read-only collection refused: " + label) from None


if __name__ == "__main__":
    main()
