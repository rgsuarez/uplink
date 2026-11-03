"""Plan-only staging command. Explicit execution requires current KAYLIN-OK authority."""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
from pathlib import Path

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_cli import ROOT, live
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_oob import target_ssh
from offline_agent.uplink.top10_stage_receiver import checked_members


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--go", type=Path)
    parser.add_argument("--owner-handback", type=Path)
    parser.add_argument("--root-approval", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        if not args.execute:
            print(
                json.dumps(
                    {
                        "mode": "PLAN_ONLY",
                        "target_contacts": 0,
                        "model_calls": 0,
                        "requires": "KAYLIN-OK GO, owner release, Richard approval, frozen package",
                    }
                )
            )
            return
        if args.package is None or args.receipt is None or args.receipt.exists():
            raise c.Refused("top10_package_and_new_receipt_required")
        session = live(args.go, args.owner_handback, args.root_approval)
        archive = c.read_bytes(args.package, maximum=32 * 1024 * 1024)
        checked_members(archive, session.manifest_sha)
        config = c.read_bytes(
            ROOT / "src/offline_agent/uplink/lab.local.json", maximum=8192, private=True
        )
        args.receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        session.consume(staging=True)
        try:
            session.qualify("stage")
        except Exception as exc:
            c.save(
                args.receipt,
                {
                    "staged": False,
                    "reason": label(exc),
                    "attempt_consumed": True,
                    "oob": session.oob_evidence,
                },
                exclusive=True,
            )
            raise
        body = {
            "archive": base64.b64encode(archive).decode(),
            "config": base64.b64encode(config).decode(),
            "manifest_sha256": session.manifest_sha,
            "window": session.window,
            "owner": session.owner,
            "root_approval": session.root_approval,
            "oob_proof": session.oob_proof,
        }
        source = c.read_bytes(ROOT / "src/offline_agent/uplink/top10_stage_receiver.py").decode()
        command = "/usr/bin/python3 -I -B -c " + shlex.quote(source)
        try:
            result = subprocess.run(
                (*target_ssh(session.targets.oob_host), command),
                input=json.dumps(body),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            receipt = c.parse_json(result.stdout)
        except Exception as exc:
            c.save(
                args.receipt,
                {
                    "staged": False,
                    "reason": label(exc),
                    "attempt_consumed": True,
                    "target_stage_status": "unknown_no_automatic_retry",
                    "oob": session.oob_evidence,
                },
                exclusive=True,
            )
            raise
        c.save(args.receipt, receipt | {"oob": session.oob_evidence}, exclusive=True)
        if (
            result.returncode
            or receipt.get("staged") is not True
            or receipt.get("manifest_sha256") != session.manifest_sha
        ):
            raise c.Refused("top10_staging_not_verified_no_retry")
        print(json.dumps(receipt))
    except Exception as exc:
        print(json.dumps({"refused": label(exc)}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
