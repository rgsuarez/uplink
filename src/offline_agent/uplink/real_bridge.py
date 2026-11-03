"""Unprivileged lab bridge. The model runs outside the fault and never receives root RPCs."""

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.providers import ProviderError
from offline_agent.uplink.contracts import Scope, parse_action
from offline_agent.uplink.lab_config import bench_root, configured_host
from offline_agent.uplink.lab_runner import GlimmerPolicy, LabState
from offline_agent.uplink.model_identity import GGUF_NAME, attest
from offline_agent.uplink.oracle import (
    OnlineOracle,
    OracleResult,
    ProxyConfig,
    SystemTransport,
    Verdict,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.real_protocol import validate_request
from offline_agent.uplink.records import RawObservation

ROOT = Path(__file__).resolve().parents[3]


def require_host() -> None:
    if (
        platform.system() != "Darwin"
        or not configured_host()
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        raise ContractError("live_bridge_requires_lab_outside_tests")


def proxies(value: Json, iface: str, env: dict[str, str]) -> ProxyConfig:
    """Resolve the explicitly bound interface's effective CFNetwork proxy dictionary."""
    values = object_json(value)
    if "__SCOPED__" in values:
        scoped = object_json(values["__SCOPED__"])
        if iface not in scoped:
            return ProxyConfig({}, env, known=False)
        values = object_json(scoped[iface])
    if any(k.startswith("__") for k in values):
        return ProxyConfig({}, env, known=False)
    flags = (
        "HTTPEnable",
        "HTTPSEnable",
        "SOCKSEnable",
        "ProxyAutoConfigEnable",
        "ProxyAutoDiscoveryEnable",
        "ExcludeSimpleHostnames",
    )
    if any(type(values.get(k, 0)) is not int or values.get(k, 0) not in (0, 1) for k in flags):
        return ProxyConfig({}, env, known=False)
    system: dict[str, str] = {}
    for scheme, prefix in (("http", "HTTP"), ("https", "HTTPS")):
        if values.get(prefix + "Enable") == 1:
            host, port = values.get(prefix + "Proxy"), values.get(prefix + "Port")
            if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
                return ProxyConfig({}, env, known=False)
            if ":" in host and not host.startswith("["):
                host = "[" + host + "]"
            system[scheme] = f"http://{host}:{port}"
    exceptions = values.get("ExceptionsList", [])
    if not isinstance(exceptions, list) or not all(isinstance(x, str) for x in exceptions):
        return ProxyConfig({}, env, known=False)
    return ProxyConfig(
        system,
        env,
        tuple(cast(list[str], exceptions)),
        pac=values.get("ProxyAutoConfigEnable") == 1,
        autodiscovery=values.get("ProxyAutoDiscoveryEnable") == 1,
        known=values.get("SOCKSEnable", 0) == 0,
        exclude_simple=values.get("ExcludeSimpleHostnames") == 1,
    )


def wifi_probe(redactor: Redactor, on_probe: Callable[[RawObservation], None]) -> OracleResult:
    require_host()
    result = subprocess.run(
        (str(Path(__file__).with_name("uplink-network")), "read"),
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if result.returncode:
        raise ContractError("wifi_probe_configuration_unavailable")
    configuration = object_json(read_json(result.stdout))
    iface = configuration.get("interface")
    if not isinstance(iface, str) or iface == "en7":
        raise ContractError("wifi_probe_interface_refused")

    def route_matches() -> bool:
        route = subprocess.run(
            ("/sbin/route", "-n", "get", "default"),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return route.returncode == 0 and any(
            line.strip() == "interface: " + iface for line in route.stdout.splitlines()
        )

    before = route_matches()
    oracle = OnlineOracle(
        SystemTransport(on_probe, interface=iface),
        proxies(configuration.get("effective_proxy"), iface, dict(os.environ)),
        redactor,
    )
    verdict = oracle.probe(timeout=0.8)
    if not before or not route_matches():
        # The probes stay bound to Wi-Fi; OOB or another route cannot certify this trial.
        return OracleResult(
            Verdict.OFFLINE,
            verdict.checked_at,
            tuple(
                type(e)(
                    e.id,
                    e.kind,
                    False,
                    "wifi_route_not_exclusive",
                    e.path_id,
                    e.latency_ms,
                    e.target_id,
                    e.status,
                    e.proxy_used,
                )
                for e in verdict.evidence
            ),
        )
    return verdict


def probe_receipt(
    result: OracleResult, observations: list[RawObservation], redactor: Redactor
) -> dict[str, Json]:
    rows: list[Json] = []
    for raw in observations:
        rows.append(
            {
                "argv": [redactor.scrub(arg) for arg in raw.argv],
                "display": redactor.display(shlex.join(raw.argv)),
                "stdout": redactor.handle("body", raw.stdout, remember=False)
                if raw.source == "http_probe" and raw.stdout
                else redactor.scrub(raw.stdout),
                "stderr": redactor.scrub(raw.stderr),
                "exit_code": raw.exit_code,
                "duration_ms": raw.duration_ms,
                "source": raw.source,
            }
        )
    document: dict[str, Json] = {"oracle": result.as_json(), "observations": rows}
    if not redactor.scan_trace(json.dumps(document))["passed"]:
        raise ContractError("probe_receipt_privacy_refused")
    return document


def root_call(value: dict[str, Json]) -> dict[str, Json]:
    command = validate_request(value)
    run_id, op = str(command["run_id"]), command["operation"]
    worker = (
        Path(__file__).with_name("real_worker.py")
        if op in {"prepare", "retire"}
        else Path("/var/db/uplink-real/runs") / run_id / "real_worker.py"
    )
    result = subprocess.run(
        ("/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-B", str(worker), "--execute"),
        input=json.dumps(command),
        capture_output=True,
        text=True,
        timeout=18,
        check=False,
    )
    try:
        reply = object_json(read_json(result.stdout))
    except ContractError as exc:
        raise ContractError("root_helper_reply_unavailable") from exc
    if result.returncode or reply.get("ok") is not True:
        reason = reply.get("error")
        raise ContractError(str(reason) if isinstance(reason, str) else "root_helper_refused")
    return object_json(reply.get("result"))


def handle(value: dict[str, Json]) -> dict[str, Json]:
    require_host()
    op = value.get("bridge")
    if op == "root" and set(value) == {"bridge", "request"}:
        return root_call(object_json(value["request"]))
    if op == "attest" and set(value) in ({"bridge"}, {"bridge", "serving"}):
        bench = bench_root()
        identity = attest(bench / GGUF_NAME, bench / "llama-b10353/llama-b10353/llama-server")
        if "serving" in value:
            from offline_agent.uplink.probe_serving import require_serving

            return {
                "identity": identity,
                "serving": require_serving(value["serving"], model_path=str(bench / GGUF_NAME)),
            }
        return identity
    if op == "probe" and set(value) == {"bridge"}:
        redactor = Redactor.local(ROOT / "artifacts/uplink/bridge")
        redactor.register_lab_identity()
        observations: list[RawObservation] = []
        result = wifi_probe(redactor, observations.append)
        return probe_receipt(result, observations, redactor)
    is_probe = value.get("probe_scripts") is True
    if op == "choose" and set(value) == {"bridge", "scope", "observed", "oracle"} | (
        {"probe_scripts", "run_id"} if is_probe else set()
    ) | ({"action_mode"} if "action_mode" in value else set()) | (
        {"serving"} if "serving" in value else set()
    ):
        from offline_agent.uplink.real_transport import parse_oracle

        raw = object_json(value["scope"])
        if (
            set(raw) != {"services", "snapshots", "evidence", "profile"}
            or raw["profile"] != "lab_dns_proxy_snapshot"
        ):
            raise ContractError("invalid_model_scope")
        for key in ("services", "snapshots", "evidence"):
            items = raw[key]
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise ContractError("invalid_model_scope")
        scope = Scope(
            services=tuple(cast(list[str], raw["services"])),
            snapshots=tuple(cast(list[str], raw["snapshots"])),
            evidence=tuple(cast(list[str], raw["evidence"])),
            profile="lab_dns_proxy_snapshot",
        )
        observed = object_json(value["observed"])
        # Do not provide the injected fault label or raw private settings to the model.
        facts = {
            k: observed[k]
            for k in (
                "wifi_up",
                "wifi_default_route",
                "dns_changed",
                "proxy_changed",
                "baseline_equal",
            )
        }
        if any(type(v) is not bool for v in facts.values()):
            raise ContractError("invalid_model_observation")
        history = observed.get("action_history", [])
        if not isinstance(history, list) or len(history) > 8:
            raise ContractError("invalid_model_action_history")
        parsed_history = [parse_action(json.dumps(item), scope) for item in history]
        facts["action_history"] = [item.as_json() for item in parsed_history]
        facts["instruction"] = (
            "Only restore snapshot DNS or web/secure proxy settings. No other mutations."
        )
        from offline_agent.uplink.real_diagnostics import layer_probe

        action_mode = value.get("action_mode", "forced")
        if (
            not isinstance(action_mode, str)
            or action_mode not in {"forced", "open"}
            or action_mode == "open"
            and not is_probe
        ):
            raise ContractError("invalid_probe_action_mode")
        if action_mode == "open":
            facts["action_history"] = [item.tool.value for item in parsed_history]
        serving = None
        if "serving" in value:
            from offline_agent.uplink.probe_serving import require_serving

            serving = require_serving(value["serving"], model_path=str(bench_root() / GGUF_NAME))
        policy = GlimmerPolicy(
            ROOT,
            execute=True,
            probe_scripts=is_probe,
            probe_profile="lab_wifi",
            action_mode=str(action_mode),
        )
        raw_text: str | None = None
        facts["layer_probes"] = {}
        try:
            measured = parse_oracle(value["oracle"])
            if is_probe:
                from offline_agent.uplink.real_probe_scripts import execute

                deadline = observed.get("deadline")
                if type(deadline) not in (int, float):
                    raise ContractError("probe_deadline_required")
                policy.probe_executor = lambda program: execute(
                    program,
                    str(value["run_id"]),
                    float(cast(float, deadline)),
                    ROOT,
                    Redactor.local(ROOT / "artifacts/uplink/bridge"),
                )
            else:
                facts["layer_probes"] = layer_probe(
                    measured, Redactor.local(ROOT / "artifacts/uplink/bridge")
                )
            state = LabState(facts, "", "", facts["wifi_up"] is True, True, None)
            from offline_agent.providers import Message

            prior = (
                [Message("assistant", json.dumps(item.as_json())) for item in parsed_history]
                if action_mode == "open"
                else []
            )
            emission = policy.choose(scope, state, measured, prior)
            text, timings, error = emission.text, emission.timings, None
            raw_text = emission.raw_text
        except (ContractError, ProviderError) as exc:
            text, timings, error = None, {}, str(exc)
        if is_probe and serving is not None:
            policy.probe_result["serving"] = serving
        return {
            **({"raw_text": raw_text} if is_probe else {}),
            "text": text,
            "timings": timings,
            "error": error,
            "records": [record.as_json() for record in policy.records],
            "layer_probes": policy.probe_result if is_probe else facts["layer_probes"],
        }
    raise ContractError("unregistered_bridge_operation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-request")
    args = parser.parse_args()
    started = time.monotonic()
    redactor: Redactor | None = None
    try:
        value: dict[str, Json] = (
            {"bridge": "root", "request": read_json(args.root_request, max_bytes=16384)}
            if args.root_request
            else object_json(read_json(sys.stdin.read(131073), max_bytes=131072))
        )
        require_host()
        redactor = Redactor.local(ROOT / "artifacts/uplink/bridge")
        redactor.register_lab_identity()
        result = handle(value)
        result = object_json(redactor.scrub_serials_json(result))
        print(
            json.dumps(
                {"ok": True, "result": result, "duration_ms": (time.monotonic() - started) * 1000}
            )
        )
    except (
        ContractError,
        ProviderError,
        ValueError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        reason = str(exc) if isinstance(exc, (ContractError, ProviderError)) else type(exc).__name__
        if redactor is not None:
            reason = redactor.scrub_serials(reason)
        print(json.dumps({"ok": False, "error": reason}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
