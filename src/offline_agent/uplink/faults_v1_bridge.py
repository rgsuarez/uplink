"""Opt-in lab bridge for the separate IPv4 route/PF model controller.

Root sees only closed worker requests. The existing normal-user server handles
grammar-locked inference. This bridge never starts/stops a server or a VM.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import ipaddress
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink import faults_v1_worker as worker
from offline_agent.uplink.faults_v1 import FaultSpec, digest, parse_spec
from offline_agent.uplink.faults_v1_authority import specs, validate_window
from offline_agent.uplink.faults_v1_inventory import (
    READ_CODE,
    ipv4_targets,
    protected_networks,
    tailscale_addresses,
)
from offline_agent.uplink.faults_v1_model import generate
from offline_agent.uplink.lab_config import bench_root
from offline_agent.uplink.model_identity import GGUF_NAME, attest
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_serving import get_json, require_serving
from offline_agent.uplink.probe_trial_window import (
    birth,
    require_resources,
    resource_sample,
    runtime_digest,
    server_pids,
)
from offline_agent.uplink.real_bridge import require_host, wifi_probe
from offline_agent.uplink.real_transport import parse_oracle

ROOT = Path(__file__).resolve().parents[3]


def root_call(spec: FaultSpec, operation: str, payload: dict[str, Json]) -> dict[str, Json]:
    path = (
        Path(__file__).with_name("faults_v1_worker.py")
        if operation == "prepare"
        else Path("/var/db/uplink-faults-v1/runs") / spec.run_id / "faults_v1_worker.py"
    )
    result = subprocess.run(
        ("/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-B", str(path), "--execute"),
        input=json.dumps({"operation": operation, "spec": spec.document(), "payload": payload}),
        capture_output=True,
        text=True,
        timeout=14,
        check=False,
    )
    if result.returncode:
        import re

        label = result.stderr.strip().removeprefix("fault_package_refused:")
        raise ContractError(
            label if re.fullmatch(r"[a-z][a-z0-9_]{0,119}", label) else "newfault_root_refused"
        )
    return object_json(read_json(result.stdout, max_bytes=1_048_576))


def independent_oracle() -> dict[str, Json]:
    # An ephemeral salt avoids writing a new identity file for each bridge call.
    redactor = Redactor(os.urandom(32))
    return wifi_probe(redactor, lambda _: None).as_json()


def server(window: dict[str, Json]) -> dict[str, Json]:
    binding = {"arm": window["arm"], "adapter_path": window.get("adapter_path")}
    result = require_serving(binding, model_path=str(bench_root() / GGUF_NAME))
    props = object_json(get_json("props"))
    if object_json(props.get("default_generation_settings")).get("n_ctx") != 4096:
        raise ContractError("newfault_server_context_mismatch")
    pids = server_pids()
    if len(pids) != 1:
        raise ContractError("newfault_one_server_required")
    started = birth(pids[0])
    slots = get_json("slots")
    if (
        not started
        or not isinstance(slots, list)
        or len(slots) != 1
        or object_json(slots[0]).get("is_processing") is not False
    ):
        raise ContractError("newfault_idle_server_required")
    return result | {"pid": pids[0], "birth": started}


def before(spec: FaultSpec) -> dict[str, Json]:
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exec(compile(READ_CODE, "fixed-fault-survey", "exec"), {})
    raw = object_json(read_json(captured.getvalue(), max_bytes=8_388_608))
    networks = protected_networks(raw)
    management = tailscale_addresses(raw.get("reads"))
    if spec.target_ipv4 not in ipv4_targets(raw):
        raise ContractError("newfault_pinned_dns_answer_not_current")
    target = ipaddress.ip_address(spec.target_ipv4)
    if (
        any(
            target in ipaddress.ip_network(n)
            for n in networks
            if ipaddress.ip_network(n).version == 4
        )
        or spec.target_ipv4 in management
    ):
        raise ContractError("newfault_target_now_protected")
    # A new management/resolver/LAN dependency invalidates the reviewed inventory.
    if not set(networks) <= set(spec.protected_networks) or not set(management) <= set(
        spec.tailscale_addresses
    ):
        raise ContractError("newfault_inventory_changed_repin_required")
    native = subprocess.run(
        (str(Path(__file__).with_name("uplink-network")), "read"),
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    native_row = object_json(read_json(native.stdout)) if native.returncode == 0 else {}
    if (
        native_row.get("policy_safe") is not True
        or native_row.get("interface") != spec.wifi_interface
    ):
        raise ContractError("newfault_native_baseline_unproven")
    route = subprocess.run(
        ("/sbin/route", "-n", "get", "-host", "-ifscope", spec.wifi_interface, spec.target_ipv4),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    lines = {line.strip() for line in route.stdout.splitlines()}
    no_host_route = route.returncode == 0 and ("destination: " + spec.target_ipv4) not in lines
    no_host_route &= ("interface: " + spec.wifi_interface) in lines
    reads = object_json(raw["reads"])
    pf_info = str(object_json(reads["pf_info"]).get("stdout", ""))
    pf_states = object_json(reads["pf_states"])
    return {
        "checked_at": time.time(),
        "tailscale_inventory_verified": True,
        "protected_inventory_verified": True,
        "target_dns_binding_verified": True,
        "target_not_management_or_resolver": True,
        "target_probe_passed": worker.target_ok(spec),
        "protected_services_unchanged": True,
        "no_existing_target_host_route": no_host_route,
        "pf_enabled": worker.pf_status_token(pf_info) == "Status: Enabled",
        "no_target_pf_states": pf_states.get("exit_code") == 0
        and spec.target_ipv4 not in str(pf_states.get("stdout")),
        "survey_sha256": hashlib.sha256(captured.getvalue().encode()).hexdigest(),
    }


def dispatch(value: Json) -> dict[str, Json]:
    require_host()
    row = object_json(value)
    if set(row) != {"operation", "spec", "packet", "packet_sha256", "window", "payload"}:
        raise ContractError("newfault_bridge_request_refused")
    spec = parse_spec(row["spec"])
    packet, window, payload = (
        object_json(row["packet"]),
        object_json(row["window"]),
        object_json(row["payload"]),
    )
    if spec not in specs(packet) or digest(packet) != row["packet_sha256"]:
        raise ContractError("newfault_bridge_packet_mismatch")
    op = row["operation"]
    cleanup = op == "root" and payload.get("operation") in {
        "restore",
        "retire",
        "observe",
        "receipt",
    }
    validate_window(
        window,
        packet,
        str(row["packet_sha256"]),
        time.time(),
        cleanup=cleanup,
        admission=op in {"preflight", "choose"},
    )
    if (
        runtime_digest(ROOT) != packet["runtime_sha256"]
        or worker.package_digest(Path(__file__).parent) != packet["package_sha256"]
    ):
        raise ContractError("newfault_host_payload_mismatch")
    if op == "root" and set(payload) == {"operation", "payload"}:
        operation = payload["operation"]
        if operation not in {
            "prepare",
            "arm",
            "inject",
            "apply",
            "verify",
            "restore",
            "retire",
            "observe",
            "measure",
            "receipt",
        }:
            raise ContractError("newfault_root_operation_refused")
        if (
            operation == "prepare"
            and object_json(payload["payload"]).get("window")
            != object_json(window["worker_windows"])[spec.run_id]
        ):
            raise ContractError("newfault_nested_window_mismatch")
        return root_call(spec, str(operation), object_json(payload["payload"]))
    if op == "oracle" and not payload:
        return independent_oracle()
    if op == "resources" and not payload:
        return resource_sample() | {"serving": server(window)}
    if op == "preflight" and not payload:
        identity = attest(
            bench_root() / GGUF_NAME, bench_root() / "llama-b10353/llama-b10353/llama-server"
        )
        serving = server(window)
        facts = before(spec)
        oracle = independent_oracle()
        facts["online_six_passed"] = parse_oracle(oracle).fresh_online(time.time())
        return {
            "identity": identity,
            "serving": serving,
            "before_facts": facts,
            "oracle": oracle,
            "resources": resource_sample(),
            "arm": window["arm"],
            "runtime_sha256": packet["runtime_sha256"],
            "package_sha256": packet["package_sha256"],
        }
    if op == "choose" and set(payload) == {
        "measured",
        "oracle",
        "history",
        "timeout",
        "expected_server",
        "baseline_swap",
    }:
        serving = server(window)
        expected = object_json(payload["expected_server"])
        if any(
            serving.get(k) != expected.get(k)
            for k in ("pid", "birth", "arm", "base_gguf_sha256", "adapter_gguf_sha256")
        ):
            raise ContractError("newfault_serving_changed")
        measured = object_json(payload["measured"])
        parse_oracle(payload["oracle"])
        state = root_call(spec, "observe", {})
        if measured.get("snapshot_id") != state.get("snapshot_id"):
            raise ContractError("newfault_model_snapshot_mismatch")
        timeout = payload["timeout"]
        if type(timeout) not in (int, float) or not 0 < float(str(timeout)) <= 8:
            raise ContractError("newfault_model_timeout_refused")
        if state.get("lifecycle") == "injected" and float(
            str(state["remaining_seconds"])
        ) < 24 + float(str(timeout)):
            raise ContractError("newfault_root_deadline_model_budget")
        if state.get("lifecycle") not in {"injected", "restored"}:
            raise ContractError("newfault_model_lifecycle_refused")
        samples: list[Json] = []
        resource_stop: str | None = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                generate,
                ROOT,
                str(state["snapshot_id"]),
                measured,
                payload["oracle"],
                payload["history"],
                float(str(timeout)),
            )
            while True:
                try:
                    sample = resource_sample()
                    samples.append(sample)
                    require_resources(sample, payload["baseline_swap"])
                except (ValueError, OSError, subprocess.SubprocessError):
                    resource_stop = "newfault_resource_stop_during_model"
                try:
                    result = future.result(timeout=0.25)
                    break
                except FutureTimeout:
                    if future.done():
                        raise
                    continue
        if resource_stop:
            result["error"] = resource_stop
        result["resource_samples"] = samples
        result["serving"] = serving
        return result
    raise ContractError("newfault_bridge_operation_refused")


def main() -> None:
    try:
        data = sys.stdin.buffer.read(2_000_001)
        value = read_json(data.decode(), max_bytes=2_000_000)
        result: dict[str, Json] = {"ok": True, "result": dispatch(value)}
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        from offline_agent.uplink.faults_v1_controller import reason

        result = {"ok": False, "error": reason(exc)}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
