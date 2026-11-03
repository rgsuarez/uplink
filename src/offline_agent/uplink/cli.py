"""One read-only diagnostic entry point. Outputs only schema-shaped redacted evidence."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import socket
from pathlib import Path

from offline_agent.contracts import Json
from offline_agent.uplink.contracts import Action, Tool
from offline_agent.uplink.diagnostics import MacDiagnostics, ReadOnlyRunner
from offline_agent.uplink.oracle import OnlineOracle, SystemTransport, Verdict
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.snapshots import (
    SnapshotStore,
    diff_snapshot,
    with_prior_online,
    write_private_json,
)
from offline_agent.uplink.trace import RunTrace, decision


def diagnose(root: Path) -> dict[str, Json]:
    if platform.system() != "Darwin":
        raise ValueError("macos_diagnostics_require_macos")
    directory = root / "artifacts/uplink"
    redactor = Redactor.local(directory)
    redactor.handle("host", socket.gethostname())
    redactor.handle("user", getpass.getuser())
    with RunTrace(redactor, directory=directory / "traces", mode="diagnose") as trace:
        report = _diagnose(directory, redactor, trace)
    scan = redactor.scan_trace(trace.path.read_text())
    write_private_json(directory / "trace-redaction-scan.json", scan)
    if not scan["passed"]:
        raise ValueError("trace_redaction_scan_failed")
    report["trace_redaction_scan"] = scan
    write_private_json(directory / "diagnose.json", report)
    return report


def _diagnose(directory: Path, redactor: Redactor, trace: RunTrace) -> dict[str, Json]:
    inspection = MacDiagnostics(ReadOnlyRunner(), redactor, on_read=trace.diagnostic).inspect(
        env=os.environ, lease_directory=Path("/var/db/dhcpclient/leases")
    )
    trace.update_state(inspection.vitals)
    oracle = OnlineOracle(
        SystemTransport(
            on_probe=lambda raw: trace.step(
                "verify", Action(Tool.PROBE_ONLINE, {}), observation=raw
            )
        ),
        inspection.proxies,
        redactor,
    )
    result = oracle.probe()
    trace.step(
        "verify",
        Action(Tool.PROBE_ONLINE, {}),
        oracle=result,
        choice=decision(
            "independent_oracle_completed",
            move="no-op" if result.verdict == Verdict.ONLINE else "Report",
        ),
    )
    store = SnapshotStore(directory / "snapshots")
    snapshot = store.latest()
    vitals = with_prior_online(inspection.vitals, snapshot)
    differences = diff_snapshot(snapshot, vitals) if snapshot else ()
    report: dict[str, Json] = {
        "version": 1,
        "mode": "read_only",
        "vitals": vitals.as_json(),
        "oracle": result.as_json(),
        "changes_executed": 0,
        "previous_snapshot_id": snapshot.id if snapshot else None,
        "snapshot_diff": list(differences),
        "saved_snapshot_id": None,
        "trace_id": trace.run_id,
    }
    # Scan before *any* snapshot/report persistence, with identifiers still in memory.
    scan = redactor.scan(json.dumps(report))
    if not scan["passed"]:
        # Persist the reason class and counters, never the rejected evidence or bytes.
        write_private_json(directory / "redaction-scan.json", scan)
        trace.step(
            "report",
            Action(Tool.GIVE_UP, {"reason": "insufficient_evidence"}),
            choice=decision("redaction_scan_refused_report", accepted=False, move="Report"),
        )
        raise ValueError("redaction_scan_failed_no_report_written")
    if result.verdict == Verdict.ONLINE:
        snapshot = store.save(vitals, oracle.declare_online())
        report["saved_snapshot_id"] = snapshot.id
    report["redaction_scan"] = dict(scan)
    write_private_json(directory / "diagnose.json", report)
    write_private_json(directory / "redaction-scan.json", dict(scan))
    trace.step("report", Action(Tool.READ_VITALS, {}), choice=decision("redacted_report_saved"))
    return report


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "uplink", help="Read-only network diagnostics; no model or changes"
    )
    actions = parser.add_subparsers(dest="uplink_command", required=True)
    action = actions.add_parser("diagnose", help="Probe all interfaces and effective proxy path")
    action.add_argument("--json", action="store_true", help="Emit the redacted evidence object")
    trace = actions.add_parser("trace", help="Read-only terminal trace replay")
    commands = trace.add_subparsers(dest="trace_command", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("path", type=Path)
    replay.add_argument("--speed", type=float, default=1)
    replay.add_argument("--no-color", action="store_true")


def run(args: argparse.Namespace, root: Path) -> None:
    if getattr(args, "uplink_command", None) == "trace":
        from offline_agent.uplink.replay import replay

        replay(args.path, speed=args.speed, color=not args.no_color)
        return
    report = diagnose(root)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Read-only evidence saved to artifacts/uplink/diagnose.json")
        print(json.dumps(report["oracle"]))
