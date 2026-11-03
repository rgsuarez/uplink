"""One attempt per new fault in the existing VM; default prints a no-effect plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.uplink.faults_v1_linux import FAULTS, TOOLS
from offline_agent.uplink.lab_protocol import LAB_VERSION
from offline_agent.uplink.lab_runner import LabClient, LimaClient, probe
from offline_agent.uplink.lab_stage import WorkerBundle, stage_worker
from offline_agent.uplink.privacy import Redactor

ROOT = Path(__file__).resolve().parents[1]


def require_lab_online(value: dict[str, Json], now: float) -> None:
    """Validate the existing Linux oracle contract without changing its version."""
    checked = value.get("checked_at")
    rows = value.get("evidence")
    expected = {
        "dns-1": "dns",
        "dns-2": "dns",
        "tls-apple": "tls",
        "tls-google": "tls",
        "portal-apple": "portal",
        "portal-google": "portal",
    }
    if (
        value.get("version") != LAB_VERSION
        or value.get("verdict") != "ONLINE"
        or type(checked) not in (int, float)
        or not math.isfinite(now)
        or not 0 <= now - float(str(checked)) <= 30
        or not isinstance(rows, list)
        or len(rows) != 6
    ):
        raise ValueError("package_online_oracle_failed")
    evidence = [object_json(row) for row in rows]
    if {row.get("id") for row in evidence} != set(expected) or any(
        row.get("ok") is not True
        or row.get("kind") != expected[str(row["id"])]
        or row.get("kind") != "dns"
        and (not row.get("path_id") or row.get("status") != 204)
        for row in evidence
    ):
        raise ValueError("package_online_oracle_failed")


def run_case(
    client: LabClient, fault: str, verify_online: Callable[[], dict[str, Json]]
) -> dict[str, object]:
    if fault not in FAULTS:
        raise ValueError("unknown_fault_package")
    prepared = False
    armed = False
    applied = False
    result: dict[str, object] = {"fault": fault, "model_calls": 0, "passed": False}
    try:
        client.call("up", {"fault_package": fault})
        prepared = True
        before_online = verify_online()
        result["before_online"] = before_online
        require_lab_online(before_online, time.time())
        before = client.call("fault_package_check")
        if (
            before.get("snapshot_match") is not True
            or object_json(before["target"]).get("ok") is not True
        ):
            raise ValueError("package_baseline_unproven")
        client.call("arm", {"seconds": 55, "probe_scripts": True})
        armed = True
        client.call("inject", {"fault": fault})
        broken = client.call("fault_package_check")
        result["fault_measurement"] = broken
        if (
            object_json(broken["target"]).get("ok") is not False
            or broken.get("snapshot_match") is not False
        ):
            raise ValueError("package_fault_not_effective")
        tool = TOOLS[FAULTS.index(fault)]
        client.call("apply", {"tool": tool, "policy": "snapshot"})
        applied = True
        after_online = verify_online()
        result["after_online"] = after_online
        require_lab_online(after_online, time.time())
        after = client.call("fault_package_check")
        result["after"] = after
        if (
            after.get("snapshot_match") is not True
            or object_json(after["target"]).get("ok") is not True
        ):
            raise ValueError("package_repair_not_verified")
        client.call("disarm")
        armed = False
        result.update(passed=True, repair=tool, repair_mode="scripted_typed_snapshot_action")
    except (ValueError, OSError) as exc:
        result["failure_class"] = type(exc).__name__
        details = getattr(client, "last_failure_details", None)
        if isinstance(details, dict):
            result["failure_details"] = details
        result["reason"] = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("package_")
            else "case_failed_see_worker_audit"
        )
    finally:
        if prepared:
            if armed:
                try:
                    result["rollback"] = client.call("undo")
                except (ValueError, OSError):
                    result.update(passed=False, rollback_failed=True)
            try:
                result["cleanup"] = client.call("down")
            except (ValueError, OSError):
                result.update(passed=False, cleanup_failed=True)
        result["typed_action_attempted"] = applied
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--window", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = ROOT / "src/offline_agent/uplink"
    bundle = WorkerBundle.read(source)
    if not args.execute:
        print(
            json.dumps(
                {
                    "effects": False,
                    "faults": FAULTS,
                    "worker_hashes": bundle.hashes,
                    "model_calls": 0,
                    "requires": "new KAYLIN-OK-FAULTS-V1-VM window",
                },
                indent=2,
            )
        )
        return
    if args.window is None or args.output is None:
        parser.error("exact window and new output required")
    window = json.loads(args.window.read_bytes())
    now = time.time()
    times = [datetime.fromisoformat(window[k]) for k in ("starts_at", "expires_at")]
    if (
        window.get("kind") != "KAYLIN-OK-FAULTS-V1-VM"
        or window.get("authorized") is not True
        or window.get("exclusive") is not True
        or window.get("worker_hashes") != bundle.hashes
        or window.get("r2_handback_verified") is not True
        or any(t.tzinfo is None for t in times)
        or not times[0].timestamp() <= now <= times[1].timestamp() - 300
    ):
        raise SystemExit("exact released VM window required")
    target = args.output.absolute()
    if (
        ".." in target.parts
        or not target.is_relative_to(ROOT / "artifacts")
        or target.exists()
        or any(p.is_symlink() for p in (target, *target.parents))
    ):
        raise SystemExit("new private artifact output required")
    target.mkdir(mode=0o700, parents=True)
    stage_worker(source, authorize=True)
    redactor = Redactor.local(target)
    for fault in FAULTS:
        if time.time() > times[1].timestamp() - 120:
            raise SystemExit("window has insufficient remaining time")
        run_id = uuid.uuid4().hex[:12]
        client = LimaClient(run_id, source / "lab_worker.py", True, bundle)

        def online(active: LabClient = client) -> dict[str, Json]:
            result = probe(active, redactor)
            return result.as_json()

        result = run_case(client, fault, online)
        result.update(
            run_id=run_id,
            worker_hashes=bundle.hashes,
            window_sha256=hashlib.sha256(args.window.read_bytes()).hexdigest(),
        )
        path = target / (fault + ".json")
        path.write_text(json.dumps(result, indent=2) + "\n")
        path.chmod(0o600)
        print(json.dumps({"fault": fault, "passed": result["passed"]}))
        if result["passed"] is not True:
            raise SystemExit("fault package stopped; no retry")


if __name__ == "__main__":
    main()
