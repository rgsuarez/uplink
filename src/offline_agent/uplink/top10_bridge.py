"""Normal-user BASE bridge. Root receives only the closed snapshot RPC protocol."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from offline_agent.contracts import object_json, read_json
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.lab_config import bench_root, load_config
from offline_agent.uplink.model_identity import GGUF_NAME, attest
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_serving import get_json, require_serving
from offline_agent.uplink.probe_trial_window import (
    birth,
    require_resources,
    resource_sample,
    server_pids,
)
from offline_agent.uplink.real_bridge import require_host, wifi_probe
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_kernel import route_fields
from offline_agent.uplink.top10_model import generate
from offline_agent.uplink.top10_oob import validate_proof

ROOT = Path(__file__).resolve().parents[3]
SUITE_OPS = {
    "reserve_suite",
    "begin_preflight",
    "admit_suite",
    "finish_slot",
    "stop_suite",
    "suite_status",
}
CLEANUP = {"restore", "close", "retire", "status", "observe"}


def owner_handback(
    raw: object, window: dict[str, object], now: float, *, starting: bool = False
) -> None:
    if not isinstance(raw, str) or len(raw.encode()) > 16_384:
        raise c.Refused("top10_owner_handback_required")
    if hashlib.sha256(raw.encode()).hexdigest() != window["owner_handback_sha256"]:
        raise c.Refused("top10_owner_handback_binding_changed")
    row = c.parse_json(raw)
    if set(row) != {
        "kind",
        "released",
        "approved_by",
        "manifest_sha256",
        "arm",
        "pid",
        "birth",
        "released_at",
    }:
        raise c.Refused("top10_owner_handback_fields_refused")
    if (
        row["kind"] != "TOP10-OWNER-HANDBACK"
        or row["released"] is not True
        or row["approved_by"] != "Kaylin"
        or row["arm"] != "BASE"
        or row["pid"] != c.SERVER_PID
        or row["birth"] != window["server_birth"]
        or row["manifest_sha256"] != window["manifest_sha256"]
    ):
        raise c.Refused("top10_current_owner_release_required")
    released = c.validate_owner_release(row["released_at"], now)
    if starting:
        c.validate_launch(now, released)


def management(window: dict[str, object]) -> None:
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if (
        len(connection) != 4
        or connection[0] != window["oob_client_ipv4"]
        or connection[2] != c.OOB_SERVER_IPV4
        or window.get("oob_interface") != c.OOB_INTERFACE
        or window.get("oob_server_ipv4") != c.OOB_SERVER_IPV4
    ):
        raise c.Refused("top10_current_ipv4_oob_session_required")
    try:
        ipaddress.IPv4Address(connection[0])
        ipaddress.IPv4Address(connection[2])
    except ValueError as exc:
        raise c.Refused("top10_ipv4_management_required") from exc
    result = subprocess.run(
        ("/sbin/route", "-n", "get", "-host", connection[0]),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    if result.returncode or route_fields(result.stdout).get("interface") != window["oob_interface"]:
        raise c.Refused("top10_independent_oob_route_unproven")
    result = subprocess.run(
        ("/usr/sbin/ipconfig", "getifaddr", str(window["oob_interface"])),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    if result.returncode or result.stdout.strip() != c.OOB_SERVER_IPV4:
        raise c.Refused("top10_oob_interface_address_changed")


def serving(window: dict[str, object]) -> dict[str, object]:
    if server_pids() != [c.SERVER_PID] or birth(c.SERVER_PID) != window["server_birth"]:
        raise c.Refused("top10_pid_19904_identity_changed")
    result = require_serving(
        {"arm": "base", "adapter_path": None}, model_path=str(bench_root() / GGUF_NAME)
    )
    props = object_json(get_json("props"))
    slots = get_json("slots")
    if (
        object_json(props.get("default_generation_settings")).get("n_ctx") != 4096
        or not isinstance(slots, list)
        or len(slots) != 1
        or object_json(slots[0]).get("is_processing") is not False
    ):
        raise c.Refused("top10_one_idle_4096_context_slot_required")
    return dict(result) | {
        "base_verified": True,
        "pid": c.SERVER_PID,
        "birth": window["server_birth"],
    }


def root_call(
    operation: str, slot: dict[str, object], payload: dict[str, object]
) -> dict[str, object]:
    if operation not in SUITE_OPS | {"prepare", "arm", "inject", "verify", "apply"} | CLEANUP:
        raise c.Refused("top10_root_operation_refused")
    run_id = c.text(slot.get("run_id"), r"[0-9a-f]{12}")
    path = (
        Path(__file__).with_name("top10_worker.py")
        if operation in SUITE_OPS | {"prepare"}
        else Path("/private/var/db/uplink-top10/runs") / run_id / "top10_worker.py"
    )
    result = subprocess.run(
        ("/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-B", str(path), "--execute"),
        input=json.dumps(
            {"version": 1, "operation": operation, "run_id": run_id, "payload": payload}
        ),
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    if result.returncode:
        reason = result.stderr.strip().removeprefix("top10_worker_refused:")
        raise c.Refused(
            reason if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,119}", reason) else "top10_root_refused"
        )
    return c.obj(read_json(result.stdout, max_bytes=2_000_000))


def independent_oracle() -> dict[str, object]:
    return dict(wifi_probe(Redactor(os.urandom(32)), lambda _: None).as_json())


def file_identity() -> dict[str, object]:
    # A full GGUF hash is measured once in preflight. Every later gate pins the
    # same file inode, size and modification time plus live server identity.
    result: dict[str, object] = {}
    for name, path in {
        "model": bench_root() / GGUF_NAME,
        "server": bench_root() / "llama-b10353/llama-b10353/llama-server",
    }.items():
        if path.is_symlink() or not path.is_file():
            raise c.Refused("top10_model_file_identity_refused")
        stat = path.stat()
        result[name] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    return result


def dispatch(value: object) -> dict[str, object]:
    row = c.obj(value)
    if set(row) != {
        "operation",
        "slot",
        "manifest_sha256",
        "window",
        "owner_handback",
        "payload",
        "root_approval",
        "oob_proof",
    }:
        raise c.Refused("top10_bridge_request_refused")
    manifest_path = ROOT / "harness/top10/FROZEN.json"
    manifest = c.verify_manifest(ROOT, manifest_path)
    manifest_sha = c.file_hash(manifest_path)
    if manifest_sha != row["manifest_sha256"]:
        raise c.Refused("top10_bridge_manifest_changed")
    op = c.text(row["operation"], r"[a-z_]{1,32}")
    payload = c.obj(row["payload"])
    cleanup = (
        op in {"oracle", "resources", "stop_suite", "finish_slot"}
        or op == "root"
        and payload.get("operation") in CLEANUP
    )
    window = c.validate_window(row["window"], manifest, manifest_sha, time.time(), cleanup=cleanup)
    starting = op in {"reserve_suite", "preflight"} or (
        op == "root"
        and payload.get("operation") in {"prepare", "arm", "inject"}
        and row["slot"] == c.selected_slots(manifest)[0]
    )
    owner_handback(row["owner_handback"], window, time.time(), starting=starting)
    c.validate_root_approval(row["root_approval"], window)
    require_host()
    if os.geteuid() == 0 or tuple(int(x) for x in platform.mac_ver()[0].split(".")[:1]) < (15,):
        raise c.Refused("top10_normal_user_macos15_required")
    if c.file_hash(Path(__file__).with_name("lab.local.json")) != window["private_config_sha256"]:
        raise c.Refused("top10_private_config_binding_changed")
    load_config()
    slot = c.obj(row["slot"])
    if not isinstance(manifest["slots"], list) or slot not in manifest["slots"]:
        raise c.Refused("top10_slot_not_registered")
    c.require_bench_fault(slot.get("fault_id"))
    management(window)
    if not cleanup:
        validate_proof(
            row["oob_proof"],
            window,
            "preflight" if op in {"reserve_suite", "preflight"} else str(slot["run_id"]),
            time.time(),
        )
    suite_payload: dict[str, object] = {
        "source_root": str(ROOT),
        "manifest_sha256": manifest_sha,
        "window": window,
        "root_approval": row["root_approval"],
        "oob_proof": row["oob_proof"],
    }
    if op in {"reserve_suite", "finish_slot", "stop_suite"}:
        return root_call(op, slot, suite_payload | {"detail": payload})
    # Reservation always precedes these host probes. It survives refused preflight.
    if op == "preflight" and not payload:
        root_call("begin_preflight", slot, suite_payload | {"detail": {}})
    if op == "preflight" and not payload:
        started = time.monotonic()
        identity_before = file_identity()
        identity = attest(
            bench_root() / GGUF_NAME, bench_root() / "llama-b10353/llama-b10353/llama-server"
        )
        if identity_before != file_identity():
            raise c.Refused("top10_model_changed_during_attestation")
        server = serving(window)
        resources = resource_sample()
        require_resources(resources, resources["swap_mib"])
        oracle = independent_oracle()
        from .top10_score import online

        if (
            not online(oracle, time.time())
            or time.monotonic() - started > 600
            or time.time() > c.WINDOW_START + 600
        ):
            raise c.Refused("top10_preflight_online_or_budget_refused")
        proof = server | {
            "identity": identity,
            "resources": resources,
            "oracle": oracle,
            "file_identity": identity_before,
            "base_verified": True,
            "oob": row["oob_proof"],
        }
        owner_handback(row["owner_handback"], window, time.time(), starting=True)
        root_call("admit_suite", slot, suite_payload | {"detail": proof})
        return proof
    if op == "root" and set(payload) == {"operation", "payload"}:
        operation = c.text(payload["operation"], r"[a-z_]{1,32}")
        detail = c.obj(payload["payload"])
        if operation in SUITE_OPS:
            raise c.Refused("top10_nested_suite_operation_refused")
        if operation == "prepare":
            if detail:
                raise c.Refused("top10_empty_payload_required")
            detail = suite_payload | {"fault_id": slot["fault_id"]}
        return root_call(operation, slot, detail)
    if op == "oracle" and not payload:
        return independent_oracle()
    if op == "resources" and set(payload) == {"preflight"}:
        saved = root_call("suite_status", slot, suite_payload | {"detail": {}})
        proof = c.obj(saved.get("preflight"))
        if proof != payload["preflight"]:
            raise c.Refused("top10_preflight_proof_changed")
        management(window)
        if proof.get("file_identity") != file_identity():
            raise c.Refused("top10_attested_files_changed")
        sample = resource_sample()
        require_resources(
            sample,
            object_json(read_json(json.dumps(proof["resources"], allow_nan=False)))["swap_mib"],
        )
        return serving(window) | {"resources": sample}
    if op == "choose" and set(payload) == {
        "snapshot_id",
        "facts",
        "oracle",
        "history",
        "timeout",
        "preflight",
    }:
        saved = root_call("suite_status", slot, suite_payload | {"detail": {}})
        proof = c.obj(saved.get("preflight"))
        if proof != payload["preflight"]:
            raise c.Refused("top10_preflight_proof_changed")
        serving(window)
        if proof.get("file_identity") != file_identity():
            raise c.Refused("top10_attested_files_changed")
        sample = resource_sample()
        require_resources(
            sample,
            object_json(read_json(json.dumps(proof["resources"], allow_nan=False)))["swap_mib"],
        )
        status = root_call("status", slot, {})
        snapshot = c.text(payload["snapshot_id"], r"[0-9a-f]{64}")
        timeout = c.number(payload["timeout"], 0.1, c.MODEL_TIMEOUT)
        history = payload["history"]
        if not isinstance(history, list) or any(not isinstance(a, str) for a in history):
            raise c.Refused("top10_model_history_refused")
        if (
            status.get("snapshot_id") != snapshot
            or status.get("lifecycle") not in {"fault_verified", "deciding"}
            or status.get("watchdog_restore") is not False
            or status.get("controller_cleanup") is not False
            or c.number(status.get("remaining_seconds"), 0, 45) < timeout + 18
        ):
            raise c.Refused("top10_model_budget_or_state_refused")
        result: dict[str, object] = dict(
            generate(
                ROOT,
                snapshot,
                payload["facts"],
                payload["oracle"],
                [str(a) for a in history],
                timeout,
            )
        )
        try:
            after = resource_sample()
            require_resources(
                after,
                object_json(read_json(json.dumps(proof["resources"], allow_nan=False)))["swap_mib"],
            )
            serving(window)
            management(window)
            result["resource_after"] = after
        except Exception as exc:
            result["failure"] = label(exc)
        return result
    raise c.Refused("top10_bridge_operation_refused")


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        value = read_json(raw.decode(), max_bytes=2_000_000)
        result: dict[str, object] = {"ok": True, "result": dispatch(value)}
    except Exception as exc:
        result = {"ok": False, "reason": label(exc)}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
