"""Scripted fixture qualification only. No model, trainer, SSH or provider imports.

Run one sealed cell inside the named Linux VM after the hold and slot are released.
Default execution is inert. A live receipt is infrastructure evidence, never model credit.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import cast

if __package__:
    from . import lab_worker as worker
    from .lab_protocol import LabSpec, request
    from .sealed_registry import CODES, VERSION, load
else:
    import lab_worker as worker  # type: ignore[no-redef,import-not-found]
    from lab_protocol import LabSpec, request  # type: ignore[no-redef,import-not-found]
    from sealed_registry import CODES, VERSION, load  # type: ignore[no-redef,import-not-found]


def check_stage(observed: dict[str, object], expected: dict[str, object]) -> None:
    if any(observed.get(key) != expected[key] for key in ("flags", "verdict", "app")):
        raise ValueError("sealed_observed_signature_mismatch")


def check_mechanism(code: str, observed: dict[str, object]) -> None:
    config = observed.get("configuration")
    if not isinstance(config, dict):
        raise ValueError("sealed_mechanism_evidence_missing")
    filtered = observed.get("filtered_packets")
    if code in {"X02", "X03", "F05"} and (type(filtered) is not int or filtered <= 0):
        raise ValueError("sealed_filter_not_observed")
    if code == "F05" and observed.get("alternate_dns_ok") is not False:
        raise ValueError("sealed_benign_dns_block_not_observed")
    if code == "X02" and observed.get("primary_tcp_443_ok") is not True:
        raise ValueError("sealed_pmtu_must_preserve_small_tcp")
    if code == "P04" and config.get("proxy") == config.get("http_proxy"):
        raise ValueError("sealed_https_only_drift_not_observed")
    if code == "P07" and (
        config.get("process_proxy_override") is None
        or config.get("proxy") == config.get("process_proxy_override")
    ):
        raise ValueError("sealed_external_override_not_observed")
    if code == "D09":
        disabled = config.get("ipv6_disabled")
        if (
            not isinstance(disabled, dict)
            or not disabled
            or any(v != "1" for v in disabled.values())
        ):
            raise ValueError("sealed_ipv6_disabled_unproven")
    if code == "G02" and not config.get("permanent_neighbors"):
        raise ValueError("sealed_permanent_neighbor_missing")
    if code == "V03":
        network = config.get("network")
        if not isinstance(network, dict) or _public_fixture("@PUBLIC_IPV6_112@") not in json.dumps(network):
            raise ValueError("sealed_rogue_route_missing")


def cleanup_proof(spec: LabSpec) -> dict[str, object]:
    state = worker.load(spec)
    names = {
        line.split()[0]
        for line in worker.run(("ip", "netns", "list")).stdout.splitlines()
        if line.split()
    }
    if names & {spec.client, spec.internet} or state.get("status") != "closed":
        raise ValueError("sealed_namespace_cleanup_unproven")
    rows = [json.loads(line) for line in (spec.state / "processes.jsonl").read_text().splitlines()]
    deadline = time.monotonic() + 2
    while True:
        active = 0
        for row in rows:
            stat = Path(f"/proc/{int(row['pid'])}/stat")
            try:
                fields = stat.read_text().split()
            except FileNotFoundError:
                continue
            if fields[21] != row["start_ticks"]:
                raise ValueError("sealed_cleanup_pid_reused")
            active += fields[2] != "Z"
        if not active:
            break
        if time.monotonic() >= deadline:
            raise ValueError("sealed_daemon_cleanup_unproven")
        time.sleep(0.05)
    if worker.host_fingerprint() != state["host_fingerprint"]:
        raise ValueError("sealed_host_fingerprint_changed")
    return {
        "closed": True,
        "owned_namespaces": 0,
        "owned_active_daemons": 0,
        "host_fingerprint_match": True,
    }


def qualify(chosen: dict[str, object], run_id: str, *, execute: bool = False) -> dict[str, object]:
    fixture = load(chosen)
    spec = LabSpec(run_id)
    if not execute:
        return {"version": VERSION, "selection": chosen, "effects": False, "model_calls": 0}
    worker.require_vm(True)
    # One run ID is one attempt. Setup refuses reuse; no cell is replaced on failure.
    receipt: dict[str, object] = {
        "version": VERSION,
        "selection": chosen,
        "run_id": run_id,
        "model_calls": 0,
        "qualified": False,
        "stages": [],
    }
    created = False
    try:
        worker.dispatch(request("up", run_id, chosen), execute=True)
        created = True
        observed = worker.dispatch(request("fixture_check", run_id), execute=True)
        check_stage(observed, {"flags": "TTTTTT", "verdict": "ONLINE", "app": True})
        receipt["baseline"] = observed
        b = cast(dict[str, object], fixture["baseline"])
        if b["alternate"] is True:
            worker.dispatch(request("arm", run_id, {"seconds": 20}), execute=True)
            worker.dispatch(
                request("apply", run_id, {"tool": "reroute", "policy": "alternate"}), execute=True
            )
            alternate = worker.dispatch(request("fixture_check", run_id), execute=True)
            check_stage(alternate, {"flags": "TTTTTT", "verdict": "ONLINE", "app": True})
            receipt["alternate"] = alternate
            worker.dispatch(request("undo", run_id), execute=True)
        worker.dispatch(
            request("arm", run_id, {"seconds": 55, "probe_scripts": True}), execute=True
        )
        worker.dispatch(request("inject", run_id, chosen), execute=True)
        if fixture["code"] == "I02":
            try:
                worker.dispatch(
                    request("apply", run_id, {"tool": "renew_lease", "policy": "snapshot"}),
                    execute=True,
                )
            except worker.LabCommandFailed as exc:
                argv = exc.details.get("argv")
                if not isinstance(argv, list) or "udhcpc" not in argv:
                    raise
                receipt["dhcp_no_offer"] = exc.details
            else:
                raise ValueError("sealed_dhcp_still_answers")
        stages = cast(list[dict[str, object]], fixture["stages"])
        evidence: list[dict[str, object]] = []
        receipt["stages"] = evidence
        for index, expected in enumerate(stages):
            if index:
                tool = str(expected["after"])
                worker.dispatch(
                    request(
                        "apply",
                        run_id,
                        {"tool": tool, "policy": "alternate" if tool == "reroute" else "snapshot"},
                    ),
                    execute=True,
                )
            observed = worker.dispatch(request("fixture_check", run_id), execute=True)
            evidence.append({"after": expected["after"], "observed": observed})
            check_stage(observed, expected)
            if index == 0:
                check_mechanism(str(fixture["code"]), observed)
            timer = worker.load(spec).get("timer")
            if (
                not isinstance(timer, dict)
                or timer.get("state") != "armed"
                or float(str(timer.get("deadline", 0))) <= time.time()
            ):
                raise ValueError("sealed_watchdog_restoration_is_not_success")
        receipt["signatures_match"] = True
        # Capture partial repair before cleanup. This is scripted qualification,
        # not a model handoff and not a scored 10/10 result.
        receipt["terminal_state"] = worker.observe(spec, worker.load(spec))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        receipt["failure"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    finally:
        if created:
            try:
                worker.dispatch(request("undo", run_id), execute=True)
                check = json.loads((spec.state / "sealed-revert.json").read_text())
                if not isinstance(check, dict) or check.get("match") is not True:
                    raise ValueError("sealed_revert_unproven")
                receipt["exact_revert"] = check
                restored = worker.dispatch(request("fixture_check", run_id), execute=True)
                check_stage(restored, {"flags": "TTTTTT", "verdict": "ONLINE", "app": True})
                receipt["restored_baseline"] = restored
                worker.dispatch(request("down", run_id), execute=True)
                receipt["cleanup"] = cleanup_proof(spec)
                receipt["qualified"] = (
                    receipt.get("signatures_match") is True and "failure" not in receipt
                )
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                receipt["cleanup_failure"] = (
                    str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                )
        if created:
            worker.atomic_json(spec.state / "sealed-qualification.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=CODES, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = qualify(
        {"fixture": args.fixture, "sha256": args.sha256}, args.run_id, execute=args.execute
    )
    print(json.dumps(result, sort_keys=True))
    if args.execute and result.get("qualified") is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
