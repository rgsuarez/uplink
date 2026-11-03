"""Normal-user bridge for one approved reduced run; authority precedes all host probes."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from offline_agent.contracts import object_json, read_json
from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.bench_profile import generate
from offline_agent.uplink.lab_config import bench_root
from offline_agent.uplink.model_identity import GGUF_NAME, attest
from offline_agent.uplink.probe_serving import get_json, require_serving
from offline_agent.uplink.probe_trial_window import (
    birth,
    require_resources,
    resource_sample,
    server_pids,
)
from offline_agent.uplink.real_bridge import require_host
from offline_agent.uplink.top10_bridge import file_identity, independent_oracle, management
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_oob import validate_proof
from offline_agent.uplink.top10_score import online

ROOT = Path(__file__).resolve().parents[3]
CLEANUP = {"oracle", "resources", "finish", "stop"}
ROOT_CLEANUP = {"restore", "observe", "close", "retire", "status"}


def root_call(op: str, run_id: str, payload: dict[str, object]) -> dict[str, object]:
    if (
        op
        not in {
            "reserve",
            "begin_preflight",
            "admit",
            "attempt_status",
            "reserve_decision",
            "decision_result",
            "finish",
            "stop",
            "prepare",
            "arm",
            "inject",
            "apply",
        }
        | ROOT_CLEANUP
    ):
        raise c.Refused("bench_root_operation_refused")
    path = Path(__file__).with_name("bench_worker.py")
    if op in {"arm", "inject", "apply"} | ROOT_CLEANUP:
        path = b.BASE / "runs" / c.text(run_id, r"[0-9a-f]{12}") / "bench_worker.py"
    result = subprocess.run(
        ("/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-B", str(path), "--execute"),
        input=json.dumps(
            {"version": 1, "operation": op, "run_id": run_id, "payload": payload}, allow_nan=False
        ),
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    if result.returncode:
        reason = result.stderr.strip().removeprefix("bench_worker_refused:")
        raise c.Refused(
            reason if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,119}", reason) else "bench_root_refused"
        )
    return c.obj(read_json(result.stdout, max_bytes=2_000_000))


def serving(window: dict[str, object]) -> dict[str, object]:
    pid = int(str(window["server_pid"]))
    if server_pids() != [pid] or birth(pid) != window["server_birth"]:
        raise c.Refused("bench_approved_server_identity_changed")
    result = require_serving(
        {"arm": "base", "adapter_path": None}, model_path=str(bench_root() / GGUF_NAME)
    )
    props, slots = object_json(get_json("props")), get_json("slots")
    if (
        object_json(props.get("default_generation_settings")).get("n_ctx")
        != window["server_context"]
        or not isinstance(slots, list)
        or len(slots) != 1
        or object_json(slots[0]).get("is_processing") is not False
    ):
        raise c.Refused("bench_one_idle_approved_context_slot_required")
    return dict(result) | {
        "base_verified": True,
        "pid": pid,
        "birth": window["server_birth"],
        "context": window["server_context"],
    }


def proof_and_resources(
    run_id: str, window: dict[str, object], wanted: object
) -> dict[str, object]:
    saved = root_call("attempt_status", run_id, {})
    proof = c.obj(saved.get("preflight"))
    if proof != wanted or proof.get("file_identity") != file_identity():
        raise c.Refused("bench_attested_files_or_preflight_changed")
    sample = resource_sample()
    require_resources(sample, object_json(read_json(json.dumps(proof["resources"])))["swap_mib"])
    return serving(window) | {"resources": sample}


def dispatch(value: object) -> dict[str, object]:
    row = c.obj(value)
    if set(row) != {
        "operation",
        "payload",
        "manifest_sha256",
        "package_sha256",
        "window",
        "owner",
        "richard",
        "oob",
    }:
        raise c.Refused("bench_bridge_request_refused")
    op, payload = c.text(row["operation"], r"[a-z_]{1,32}"), c.obj(row["payload"])
    cleanup = op in CLEANUP or op == "root" and payload.get("operation") in ROOT_CLEANUP
    manifest = b.verify_manifest(ROOT)
    manifest_sha = c.file_hash(ROOT / b.MANIFEST_PATH)
    run_id = str(manifest["run_id"])
    package_sha = c.file_hash(ROOT.parent / ("bench-reduced-" + run_id + ".tar"))
    if row["manifest_sha256"] != manifest_sha or row["package_sha256"] != package_sha:
        raise c.Refused("bench_package_binding_changed")
    window = b.authority(
        row["window"],
        manifest,
        manifest_sha,
        package_sha,
        time.time(),
        cleanup=cleanup,
        starting=op == "reserve",
    )
    b.approvals(window, row["owner"], row["richard"], time.time(), cleanup=cleanup)
    if (
        platform.system() != "Darwin"
        or os.geteuid() == 0
        or "UPLINK_TOP10_SANDBOX" in os.environ
        or tuple(int(x) for x in platform.mac_ver()[0].split(".")[:1]) < (15,)
    ):
        raise c.Refused("bench_live_normal_user_macos15_required")
    if (
        c.file_hash(ROOT / "src/offline_agent/uplink/lab.local.json")
        != window["private_config_sha256"]
    ):
        raise c.Refused("bench_private_config_binding_changed")
    require_host()
    management(window)
    if not cleanup:
        validate_proof(
            row["oob"],
            window,
            "preflight" if op in {"reserve", "preflight"} else run_id,
            time.time(),
        )
    if op == "reserve" and not payload:
        return root_call(
            "reserve",
            run_id,
            {
                k: row[k]
                for k in ("manifest_sha256", "package_sha256", "window", "owner", "richard", "oob")
            }
            | {"source_root": str(ROOT)},
        )
    if op == "preflight" and not payload:
        root_call("begin_preflight", run_id, {})
        started = time.monotonic()
        before = file_identity()
        if (bench_root() / GGUF_NAME).stat().st_size != b.MODEL_BYTES:
            raise c.Refused("bench_model_size_changed")
        identity = attest(
            bench_root() / GGUF_NAME, bench_root() / "llama-b10353/llama-b10353/llama-server"
        )
        if before != file_identity():
            raise c.Refused("bench_files_changed_during_attestation")
        server, sample, oracle = serving(window), resource_sample(), independent_oracle()
        require_resources(sample, sample["swap_mib"])
        if (
            not online(oracle, time.time())
            or time.monotonic() - started > 600
            or time.time() > b.timestamp(window["start"]) + 600
        ):
            raise c.Refused("bench_baseline_or_preflight_budget_refused")
        proof = server | {
            "identity": identity,
            "resources": sample,
            "oracle": oracle,
            "file_identity": before,
        }
        root_call("admit", run_id, proof)
        return proof
    if op in {"finish", "stop"}:
        return root_call(op, run_id, payload)
    if op == "oracle" and not payload:
        return independent_oracle()
    if op == "resources" and set(payload) == {"preflight"}:
        return proof_and_resources(run_id, window, payload["preflight"])
    if op == "root" and set(payload) == {"operation", "payload"}:
        root_op = c.text(payload["operation"], r"[a-z_]{1,32}")
        detail = c.obj(payload["payload"])
        if root_op not in {"prepare", "arm", "inject", "apply"} | ROOT_CLEANUP:
            raise c.Refused("bench_nested_ledger_operation_refused")
        if root_op == "prepare":
            if detail:
                raise c.Refused("bench_empty_payload_required")
            detail = {"oob": row["oob"]}
        return root_call(root_op, run_id, detail)
    if op == "choose" and set(payload) == {
        "snapshot_id",
        "facts",
        "oracle",
        "history",
        "timeout",
        "preflight",
    }:
        proof_and_resources(run_id, window, payload["preflight"])
        status = root_call("status", run_id, {})
        snapshot = c.text(payload["snapshot_id"], r"[0-9a-f]{64}")
        timeout = c.number(payload["timeout"], 0.1, 8)
        history = payload["history"]
        if not isinstance(history, list) or any(not isinstance(v, str) for v in history):
            raise c.Refused("bench_history_refused")
        if (
            status.get("snapshot_id") != snapshot
            or status.get("lifecycle") not in {"fault_verified", "deciding"}
            or status.get("watchdog_restore") is not False
            or status.get("controller_cleanup") is not False
            or c.number(status.get("remaining_seconds"), 0, 45) < timeout + 18
        ):
            raise c.Refused("bench_decision_state_or_budget_refused")
        root_call("reserve_decision", run_id, {"history": history})
        result: dict[str, object] = dict(
            generate(
                ROOT,
                snapshot,
                payload["facts"],
                payload["oracle"],
                history,
                timeout,
                context=int(str(window["server_context"])),
            )
        )
        try:
            result["resources_after"] = proof_and_resources(run_id, window, payload["preflight"])
            management(window)
        except Exception as exc:
            result["failure"] = label(exc)
        try:
            root_call("decision_result", run_id, result)
        except Exception as exc:
            result["failure"] = label(exc)
        return result
    raise c.Refused("bench_bridge_operation_refused")


def main() -> None:
    try:
        data = sys.stdin.buffer.read(2_000_001)
        result: dict[str, object] = {
            "ok": True,
            "result": dispatch(read_json(data.decode(), max_bytes=2_000_000)),
        }
    except Exception as exc:
        result = {"ok": False, "reason": label(exc)}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
