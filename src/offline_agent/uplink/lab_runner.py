"""Bounded host-side lab evaluation. The model stays outside the faulted client namespace."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import json
import platform
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, TypedDict, cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.native_protocol import EOT, server_owned_prompt
from offline_agent.providers import HttpTransport, Message, ProviderError
from offline_agent.uplink.contracts import (
    CHANGES,
    Action,
    Scope,
    Tool,
    parse_action,
)
from offline_agent.uplink.grammar import schema_gbnf
from offline_agent.uplink.lab_config import bench_root, configured_host
from offline_agent.uplink.lab_protocol import (
    ALTERNATE_GW,
    DNS_NAMES,
    FAULTS,
    LAB_VERSION,
    PRIMARY_GW,
    PROXY_URL,
    LabSpec,
    catalog,
    catalog_digest,
    request,
)
from offline_agent.uplink.lab_stage import WorkerBundle, stage_worker
from offline_agent.uplink.layer_policy import (
    ModelRecord,
    action_guide,
    diagnosis_schema,
    evidence_facts,
    journal_model_records,
    layer_action_schema,
    layer_tools,
    parse_diagnosis,
)
from offline_agent.uplink.model_identity import (
    GGUF_NAME,
    MODEL_NAME,
    attest,
    expected_model,
    validate_identity,
)
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.plans import ConfirmCard
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.repair_budget import (
    MAX_MUTATIONS,
    RESTORES,
    AttemptKey,
    key_document,
    mutation_key,
    snapshot_retry_allowed,
)
from offline_agent.uplink.snapshots import write_private_json
from offline_agent.uplink.trace import RunTrace, cleared, config_diff, decision, iso


class LabClient(Protocol):
    def command(
        self, operation: str, payload: dict[str, object] | None = None
    ) -> tuple[str, ...]: ...
    def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]: ...


@dataclass
class LimaClient:
    run_id: str
    worker: Path
    execute: bool
    bundle: WorkerBundle | None = None
    last_failure_details: dict[str, Json] | None = None

    def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
        request(operation, self.run_id, payload)
        if not self.execute or platform.system() != "Darwin" or not configured_host():
            raise ContractError("lab_effects_require_explicit_lab_execution")
        if not self.worker.is_absolute() or not self.worker.is_file():
            raise ContractError("lab_worker_path_required")
        if self.bundle is None:
            raise ContractError("verified_vm_worker_staging_required")
        command = self.command(operation, payload)
        try:
            result = subprocess.run(
                command,
                input="",
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContractError("lab_control_transport_failed_no_retry") from exc
        if result.returncode:
            try:
                refused = object_json(read_json(result.stdout, max_bytes=100000))
                reason = refused.get("reason")
                details = refused.get("failure_details")
                self.last_failure_details = (
                    object_json(details)
                    if details is not None
                    else {
                        "stdout": result.stdout[-8192:],
                        "stderr": result.stderr[-8192:],
                        "exit_code": result.returncode,
                    }
                )
            except (ContractError, ValueError):
                reason = None
                self.last_failure_details = {
                    "stderr": result.stderr[-8192:],
                    "exit_code": result.returncode,
                }
            safe = (
                reason
                if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", reason)
                else "unparseable_worker_receipt"
            )
            raise ContractError("lab_worker_refused_no_retry__" + safe)
        return object_json(read_json(result.stdout, max_bytes=100000))

    def command(self, operation: str, payload: dict[str, object] | None = None) -> tuple[str, ...]:
        value = request(operation, self.run_id, payload)
        bundle = self.bundle or WorkerBundle.read(self.worker.parent)
        return bundle.command(value, execute=True)


@dataclass(frozen=True)
class LabState:
    raw: dict[str, Json]
    dns: str
    proxy: str
    primary_up: bool
    has_address: bool
    gateway: str | None


def state(client: LabClient, spec: LabSpec) -> LabState:
    raw = client.call("observe")
    addresses, routes = raw.get("addresses"), raw.get("routes")
    if not isinstance(addresses, list) or not isinstance(routes, list):
        raise ContractError("lab_observation_shape")
    c0, _, _, _ = spec.interfaces
    interface = next((object_json(x) for x in addresses if object_json(x).get("ifname") == c0), {})
    flags = interface.get("flags", [])
    assigned = interface.get("addr_info", [])
    if not isinstance(flags, list) or not isinstance(assigned, list):
        raise ContractError("lab_interface_shape")
    gateway = next(
        (object_json(x).get("gateway") for x in routes if object_json(x).get("dst") == "default"),
        None,
    )
    resolver, proxy = raw.get("resolver"), raw.get("proxy")
    if not isinstance(resolver, str) or not isinstance(proxy, str):
        raise ContractError("lab_settings_shape")
    dns = next(
        (line.split()[1] for line in resolver.splitlines() if line.startswith("nameserver ")), ""
    )
    return LabState(
        raw, dns, proxy, "UP" in flags, bool(assigned), str(gateway) if gateway else None
    )


def configuration_facts(observed: LabState) -> dict[str, Json]:
    return {
        "dns_server": observed.dns,
        "proxy_url": observed.proxy,
        "primary_up": observed.primary_up,
        "lease_present": observed.has_address,
        "default_gateway": observed.gateway,
    }


def action_attempt_key(tool: str, observed: LabState, spec: LabSpec) -> AttemptKey:
    routes = observed.raw["routes"]
    assert isinstance(routes, list)
    route = next((object_json(r) for r in routes if object_json(r).get("dst") == "default"), {})
    device = route.get("dev")
    if device is not None and (
        not isinstance(device, str) or device not in {spec.interfaces[0], spec.interfaces[2]}
    ):
        raise ContractError("lab_route_target_unrecognized")
    path = json.dumps([spec.client, device, observed.gateway], separators=(",", ":"))
    return mutation_key(tool, path, configuration_facts(observed))


def policy_state(observed: LabState, known_good: dict[str, Json]) -> LabState:
    """Send normalized current and captured facts, not the full kernel address dump."""
    return replace(
        observed,
        raw={
            "current": configuration_facts(observed),
            "known_good_snapshot": known_good,
            "prior_alternate_online": observed.raw.get("prior_alternate_online") is True,
            "layer_probes": observed.raw.get("layer_probes"),
            "action_history": observed.raw.get("action_history", []),
            "verified_route_change_at": observed.raw.get("verified_route_change_at"),
            "retryable_restores": observed.raw.get("retryable_restores", []),
        },
    )


def probe(
    client: LabClient, redactor: Redactor, receipts: list[RawObservation] | None = None
) -> OracleResult:
    def call(operation: str, payload: dict[str, object]) -> dict[str, Json]:
        begun = time.monotonic()
        value = client.call(operation, payload)
        if receipts is not None:
            receipts.append(
                RawObservation(
                    client.command(operation, payload),
                    json.dumps(value),
                    exit_code=0,
                    duration_ms=(time.monotonic() - begun) * 1000,
                    source="lab_probe",
                )
            )
        return value

    started = time.time()
    evidence = []
    captive = False
    for index in range(2):
        value = call("resolve", {"target": index})
        ok = value.get("ok") is True
        latency = value.get("latency_ms")
        if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
            raise ContractError("invalid_probe_latency")
        evidence.append(
            Evidence(
                f"dns-{index + 1}",
                "dns",
                ok,
                "system_resolved" if ok else "system_resolution_failed",
                latency_ms=float(latency),
                target_id=redactor.handle("host", DNS_NAMES[index]),
            )
        )
    for index in range(4):
        value = call("fetch", {"target": index})
        tls = index < 2
        status = value.get("status")
        latency = value.get("latency_ms")
        proxy = value.get("proxy")
        if (
            type(status) is not int
            or type(latency) not in (int, float)
            or not isinstance(proxy, str)
        ):
            raise ContractError("invalid_fetch_evidence")
        ok = (
            value.get("ok") is True
            and status == 204
            and value.get("body_matches") is True
            and (not tls or value.get("tls_verified") is True)
        )
        captive |= not tls and status in (200, 301, 302, 303, 307, 308)
        path = redactor.handle("path", proxy or "direct", remember=False)
        evidence.append(
            Evidence(
                ("tls-" if tls else "portal-") + ("apple" if index % 2 == 0 else "google"),
                "tls" if tls else "portal",
                ok,
                "verified_lab_fixture" if ok else "probe_failed",
                path,
                float(cast(float, latency)),
                redactor.handle("host", DNS_NAMES[index % 2]),
                status,
                redactor.handle("proxy", proxy, remember=False) if proxy else "direct",
            )
        )
    verdict = (
        Verdict.CAPTIVE
        if captive
        else Verdict.ONLINE
        if all(x.ok for x in evidence)
        else Verdict.DEGRADED
        if any(x.ok for x in evidence)
        else Verdict.OFFLINE
    )
    return OracleResult(verdict, started, tuple(evidence), version=LAB_VERSION)


class LabWatchdogRestored(ContractError):
    pass


class LabRollbackFailed(ContractError):
    pass


def check_watchdog(raw: dict[str, Json]) -> None:
    timer = raw.get("timer")
    if not isinstance(timer, dict):
        raise ContractError("watchdog_receipt_required")
    if timer.get("reason") == "rollback_failed" or timer.get("state") in {
        "failed",
        "rollback_started",
    }:
        raise LabRollbackFailed("watchdog_rollback_failed_no_retry")
    if timer.get("reason") == "deadline_reverted":
        raise LabWatchdogRestored("watchdog_ended_trial_no_recovery_credit")
    if timer.get("state") == "armed":
        deadline = timer.get("deadline")
        if type(deadline) not in (int, float) or cast(float, deadline) <= time.time():
            raise ContractError("watchdog_deadline_expired_no_recovery_credit")


def probe_rows(receipts: list[RawObservation], redactor: Redactor) -> list[Json]:
    return [
        {
            "argv": [redactor.scrub(arg) for arg in raw.argv],
            "stdout": redactor.scrub(raw.stdout),
            "stderr": redactor.scrub(raw.stderr),
            "exit_code": raw.exit_code,
            "duration_ms": raw.duration_ms,
            "source": raw.source,
        }
        for raw in receipts
    ]


def probe_document(receipts: list[RawObservation], redactor: Redactor) -> RawObservation:
    return RawObservation(
        (),
        json.dumps(probe_rows(receipts, redactor)),
        exit_code=0,
        duration_ms=sum(r.duration_ms for r in receipts),
        source="lab_oracle",
    )


@dataclass(frozen=True)
class Emission:
    text: str
    timings: dict[str, Json]
    tokens: tuple[int, ...] | None = None
    raw_text: str | None = None


class ModelFields(TypedDict, total=False):
    model_text: str
    model_tokens: tuple[int, ...] | None
    timings: dict[str, Json]


class Policy(Protocol):
    name: str

    def choose(
        self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
    ) -> Emission: ...


class GlimmerPolicy:
    name = "uplink"

    def __init__(
        self,
        root: Path,
        *,
        execute: bool,
        probe_scripts: bool = False,
        probe_profile: str = "linux_lab",
        action_mode: str = "forced",
    ) -> None:
        if action_mode not in {"forced", "open"} or (action_mode == "open" and not probe_scripts):
            raise ContractError("invalid_probe_action_mode")
        self.root, self.execute = root, execute
        self.action_mode = action_mode
        self.records: list[ModelRecord] = []
        self.probe_scripts, self.probe_profile = probe_scripts, probe_profile
        self.probe_executor: Callable[[dict[str, Json]], dict[str, Json]] | None = None
        self.probe_result: dict[str, Json] = {}

    def generate(self, stage: str, prompt: list[Message], grammar: str) -> Emission:
        record = ModelRecord(stage, server_owned_prompt(self.root, prompt), grammar)
        self.records.append(record)
        started = time.monotonic()
        try:
            response = HttpTransport().post(
                _public_fixture("http://@PUBLIC_IPV4_056@:8080/completion"),
                {
                    "prompt": record.prompt,
                    "grammar": grammar,
                    "grammar_lazy": False,
                    "n_predict": (256 if stage == "diagnosis" else 192)
                    if self.probe_scripts
                    else 96
                    if stage == "diagnosis"
                    else 192,
                    "temperature": 0,
                    "seed": 0,
                    "stop": [EOT],
                    "stream": False,
                    "cache_prompt": True,
                    "id_slot": 0,
                },
                {},
                25 if self.probe_scripts else 15,
            )
            content = response.get("content")
            record.text = content if isinstance(content, str) else None
            record.timings = object_json(response.get("timings"))
            if (
                not isinstance(content, str)
                or response.get("truncated") is not False
                or response.get("stop") is not True
            ):
                raise ContractError("incomplete_uplink_completion")
            return Emission(content, record.timings)
        except (ContractError, ProviderError) as exc:
            record.failure = str(exc)
            raise
        finally:
            record.duration_ms = (time.monotonic() - started) * 1000

    def choose(
        self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
    ) -> Emission:
        self.records = []
        if not self.execute or platform.system() != "Darwin" or not configured_host():
            raise ContractError("model_inference_requires_lab_opt_in")
        if self.probe_scripts:
            from offline_agent.uplink.probe_policy import choose_with_probes

            return choose_with_probes(self, scope, observed, oracle, history=history)
        facts = evidence_facts(observed.raw, oracle)
        ids = tuple(facts)
        diagnosis = self.generate(
            "diagnosis",
            [
                Message(
                    "system",
                    "Diagnose the first failing network layer from observed evidence. "
                    'Return JSON only: {"layer":"link|ip|dns|proxy|captive|upstream",'
                    '"evidence":["id"]}. '
                    "Use 1-3 supplied evidence IDs. A working link is never a link fault. "
                    "A failed configured resolver with a working alternate means DNS. "
                    "Working direct HTTPS with failed proxied HTTPS means proxy. "
                    "Repair one layer then probe again: DNS before proxy for compound drift. "
                    "All oracle checks pass -> upstream, cite a pass; no repair. "
                    "Examples: link.up=true + dns-1.ok=false + snapshot-dns.changed=true -> dns; "
                    "dns-1.ok=true + tls-apple.ok=false + snapshot-proxy.changed=true -> proxy; "
                    "link.up=false + lease.present=false -> link.",
                ),
                Message(
                    "user",
                    json.dumps(
                        {"oracle": oracle.verdict.value, "evidence": facts}, separators=(",", ":")
                    ),
                ),
            ],
            schema_gbnf(diagnosis_schema(ids)),
        )
        try:
            diagnosed = parse_diagnosis(diagnosis.text.removesuffix(EOT), ids)
            tools = layer_tools(str(diagnosed["layer"]), facts, oracle)
        except ContractError as exc:
            self.records[-1].failure = str(exc)
            raise
        prompt = [
            Message(
                "system",
                "Restore verified connectivity with the smallest safe change. "
                'Return one JSON object: {"version":1,"tool":"name","args":{...}}. '
                "Only the listed tools for this layer are legal. All listed args required. "
                "Enums beginning with $ use exact values from the corresponding scope list. "
                "Use snapshot policy for observed DNS/proxy drift. Cite one failing oracle ID. "
                "cycle_interface costs 10 and is allowed only for an observed link failure; "
                "a DNS or proxy failure cannot justify cycling. "
                "Examples: DNS drift -> set_dns(policy=snapshot); proxy drift -> "
                "restore_proxy(policy=snapshot); all oracle checks pass -> declare_online. "
                + action_guide(scope, tools),
            ),
            Message(
                "user",
                json.dumps(
                    {
                        "diagnosis": diagnosed,
                        "oracle": oracle.verdict.value,
                        "evidence": facts,
                        "prior_alternate_online": observed.raw.get("prior_alternate_online")
                        is True,
                        "scope": {
                            "services": scope.services,
                            "candidates": scope.candidates,
                            "snapshots": scope.snapshots,
                            "evidence": scope.evidence,
                        },
                        "previous_actions": observed.raw.get("action_history", []),
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
        emission = self.generate("action", prompt, schema_gbnf(layer_action_schema(scope, tools)))
        try:
            parse_action(emission.text.removesuffix(EOT), scope, tools)
        except ContractError as exc:
            self.records[-1].failure = str(exc)
            raise
        return emission


class ScriptedPolicy:
    name = "baseline"

    def choose(
        self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
    ) -> Emission:
        tool = Tool.REPORT
        args: dict[str, Json] = {
            "component": "upstream",
            "physical_step": "check_cable",
            "evidence_ids": ["dns-1"],
        }
        common: dict[str, Json] = {
            "snapshot_id": scope.snapshots[0],
            "why": "snapshot_drift",
            "evidence_ids": ["dns-1"],
            "service_id": scope.services[0],
        }
        if oracle.verdict == Verdict.ONLINE:
            tool, args = Tool.DECLARE_ONLINE, {}
        elif oracle.verdict == Verdict.CAPTIVE:
            args.update(component="portal", physical_step="open_portal")
        elif observed.dns not in (PRIMARY_GW, ALTERNATE_GW):
            tool, args = Tool.SET_DNS, {**common, "policy": "snapshot"}
        elif observed.proxy not in (PROXY_URL, ""):
            tool, args = Tool.RESTORE_PROXY, {**common, "policy": "snapshot"}
        elif not observed.primary_up:
            tool, args = Tool.CYCLE_INTERFACE, common
        elif not observed.has_address:
            tool, args = Tool.RENEW_LEASE, common
        elif (
            observed.gateway == PRIMARY_GW
            and observed.raw.get("prior_alternate_online") is True
            and scope.candidates
        ):
            tool, args = (
                Tool.REROUTE,
                {
                    "snapshot_id": scope.snapshots[0],
                    "why": "alternate_route",
                    "evidence_ids": ["dns-1"],
                    "candidate_id": scope.candidates[0],
                },
            )
        return Emission(json.dumps(Action(tool, args).as_json()), {})


class ScriptedStrongPolicy(ScriptedPolicy):
    """The fixed script with the probe policy's measured primary-path precondition."""

    name = "scripted-strong"

    def choose(
        self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
    ) -> Emission:
        from offline_agent.uplink.probe_policy import primary_path_unavailable

        prior_actions = observed.raw.get("action_history", [])
        if not isinstance(prior_actions, list):
            raise ContractError("invalid_scripted_action_history")
        if (
            oracle.verdict != Verdict.ONLINE
            and scope.candidates
            and observed.raw.get("prior_alternate_online") is True
            and "reroute" not in prior_actions
            and primary_path_unavailable(evidence_facts(observed.raw, oracle), oracle)
        ):
            return Emission(
                json.dumps(
                    Action(
                        Tool.REROUTE,
                        {
                            "snapshot_id": scope.snapshots[0],
                            "why": "alternate_route",
                            "evidence_ids": ["dns-1"],
                            "candidate_id": scope.candidates[0],
                        },
                    ).as_json()
                ),
                {},
            )
        return super().choose(scope, observed, oracle, history)


def operation(action: Action) -> tuple[str, str]:
    if action.tool == Tool.SET_DNS:
        if action.text("policy") not in ("snapshot", "dhcp"):
            raise ContractError("lab_dns_allowlist_fixture_only")
        return "set_dns", action.text("policy")
    if action.tool == Tool.RESTORE_PROXY:
        return "restore_proxy", action.text("policy")
    if action.tool in (Tool.RENEW_LEASE, Tool.CYCLE_INTERFACE):
        return action.tool.value, "snapshot"
    if action.tool == Tool.REROUTE:
        return "reroute", "alternate"
    raise ContractError("lab_mutation_not_supported")


def trace_state(trace: RunTrace, observed: LabState, spec: LabSpec) -> None:
    interfaces = []
    for raw in cast(list[Json], observed.raw["addresses"]):
        row = object_json(raw)
        name = str(row["ifname"])
        up = "UP" in cast(list[Json], row.get("flags", []))
        interfaces.append(
            {
                "id": trace.redactor.handle("interface", name, remember=False),
                "up": up,
                "state": "up" if up else "down",
            }
        )
    primary = observed.gateway == PRIMARY_GW
    iface = spec.interfaces[0 if primary else 2]
    trace.state["interfaces"] = cast(list[Json], interfaces)
    trace.state["active_route"] = {
        "interface_id": trace.redactor.handle("interface", iface, remember=False),
        "service_id": trace.scope.services[0 if primary else 1],
        "candidate_id": trace.scope.candidates[0] if not primary else None,
    }


def evaluate(
    root: Path,
    client: LabClient,
    spec: LabSpec,
    fault: str,
    policy: Policy,
    directory: Path,
    *,
    dry: bool = False,
    steps: int = 8,
    model_identity: dict[str, Json] | None = None,
) -> dict[str, Json]:
    if fault not in FAULTS:
        raise ContractError("unknown_lab_fault")
    redactor = Redactor.local(directory)
    scope = Scope(
        tuple(redactor.handle("service", x, remember=False) for x in ("primary", "alternate")),
        (redactor.handle("candidate", "alternate", remember=False),),
        ("checkpoint-" + uuid.uuid4().hex,),
        ("dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google"),
    )
    model_name = (
        "MOCK policy fixture"
        if dry and policy.name == "uplink"
        else "Muse Glimmer 30B"
        if policy.name == "uplink"
        else None
    )
    identity = model_identity or expected_model(
        provider="mock" if dry else "llama_cpp", active_name=model_name
    )
    validate_identity(identity, live=not dry)
    attempted: list[str] = []
    attempt_keys: list[AttemptKey] = []
    verified_route_change_at: int | None = None
    restore_retries: list[Json] = []
    model_attempts = 0
    model_started = 0.0
    pending_model: ModelFields = {}
    model_count_before_response = 0
    created = False
    failure: str | None = None
    revert_failed = False
    watchdog_restored = False
    failure_details: dict[str, Json] | None = None
    generated_probes = isinstance(policy, GlimmerPolicy) and policy.probe_scripts
    probe_episodes: list[Json] = []
    probe_records: list[RawObservation] = []

    def measured_probe() -> OracleResult:
        probe_records.clear()
        return probe(client, redactor, probe_records)

    history: list[Message] = []
    outcome = "not_run"
    app_resumed = False
    last: OracleResult | None = None
    with RunTrace(
        redactor,
        directory=directory / "traces",
        mode="test" if dry else "linux_eval",
        scope=scope,
        model_name=model_name,
        model_identity=identity,
        provider="mock" if dry else "llama_cpp" if model_name else None,
    ) as trace:
        try:
            client.call("up")
            created = True
            initial = state(client, spec)
            known_good = configuration_facts(initial)
            trace_state(trace, initial, spec)
            healthy = measured_probe()
            trace.step(
                "diagnose",
                Action(Tool.PROBE_ONLINE, {}),
                oracle=healthy,
                observation=probe_document(probe_records, redactor),
                choice=decision("healthy_fixture_qualified"),
            )
            if healthy.verdict != Verdict.ONLINE:
                raise ContractError("healthy_lab_oracle_required")
            # Prove the alternate before advertising it as a previously successful route.
            client.call("arm", {"seconds": 20})
            client.call("apply", {"tool": "reroute", "policy": "alternate"})
            trace_state(trace, state(client, spec), spec)
            alternate = measured_probe()
            trace.step(
                "diagnose",
                Action(Tool.PROBE_ONLINE, {}),
                oracle=alternate,
                observation=probe_document(probe_records, redactor),
                choice=decision("alternate_fixture_qualified"),
            )
            client.call("undo")
            if alternate.verdict != Verdict.ONLINE:
                raise ContractError("alternate_oracle_required")
            client.call(
                "arm",
                {"seconds": 55, "probe_scripts": True} if generated_probes else {"seconds": 20},
            )
            client.call("inject", {"fault": fault})
            observed = state(client, spec)
            observed.raw["prior_alternate_online"] = True
            last = measured_probe()
            trace_state(trace, observed, spec)
            trace.step(
                "verify",
                Action(Tool.PROBE_ONLINE, {}),
                oracle=last,
                observation=probe_document(probe_records, redactor),
                choice=decision(
                    "lab_injection_observed",
                    move="no-op" if fault in {"healthy", "misleading_status"} else None,
                ),
            )
            for _ in range(steps):
                observed = state(client, spec)
                observed.raw["prior_alternate_online"] = True
                check_watchdog(observed.raw)
                model_started = time.monotonic()
                if not dry and policy.name == "uplink" and not isinstance(policy, GlimmerPolicy):
                    model_attempts += 1
                    model_started = time.monotonic()
                pending_model = {}
                if isinstance(policy, GlimmerPolicy):
                    probe_started = time.monotonic()
                    if generated_probes:
                        policy.probe_executor = lambda program: client.call(
                            "probe_script", {"program": program}
                        )
                    else:
                        observed.raw["layer_probes"] = client.call("layer_probe")
                    if not generated_probes:
                        trace.step(
                            "diagnose",
                            Action(Tool.READ_VITALS, {}),
                            observation=RawObservation(
                                client.command("layer_probe"),
                                json.dumps(observed.raw["layer_probes"]),
                                exit_code=0,
                                duration_ms=(time.monotonic() - probe_started) * 1000,
                                source="layer_probes",
                            ),
                            choice=decision("read_only_layer_evidence"),
                        )
                    observed.raw["action_history"] = list(attempted)
                    observed.raw["verified_route_change_at"] = verified_route_change_at
                    observed.raw["retryable_restores"] = [
                        tool
                        for tool in sorted(RESTORES)
                        if snapshot_retry_allowed(
                            tool,
                            tuple(attempted),
                            verified_route_change_at,
                            attempt_keys=tuple(attempt_keys),
                            current_key=action_attempt_key(tool, observed, spec),
                        )
                    ]
                    check_watchdog(state(client, spec).raw)
                    returned = False
                    try:
                        emission = policy.choose(
                            scope, policy_state(observed, known_good), last, history
                        )
                        returned = True
                    finally:
                        # Count both requests, including an interrupted second stage.
                        model_attempts += len(policy.records)
                        journal_model_records(trace, policy.records, defer_action=returned)
                        if generated_probes and policy.probe_result:
                            scrubbed = object_json(redactor.scrub_json(policy.probe_result))
                            probe_episodes.append(scrubbed)
                            trace.step(
                                "diagnose",
                                Action(Tool.READ_VITALS, {}),
                                observation=RawObservation(
                                    (),
                                    json.dumps(scrubbed),
                                    duration_ms=(time.monotonic() - model_started) * 1000,
                                    source="generated_probe_episode",
                                ),
                                choice=decision("generated_probe_evidence"),
                            )
                else:
                    observed.raw["action_history"] = list(attempted)
                    emission = policy.choose(
                        scope, policy_state(observed, known_good), last, history
                    )
                model_args: ModelFields = (
                    {
                        "model_text": emission.raw_text or emission.text,
                        "model_tokens": emission.tokens,
                        "timings": emission.timings,
                    }
                    if policy.name == "uplink"
                    else {}
                )
                pending_model = model_args
                model_count_before_response = trace.model_calls
                try:
                    action = parse_action(emission.text.removesuffix(EOT), scope)
                except ContractError:
                    trace.step(
                        "report",
                        None,
                        choice=decision("model_action_rejected", accepted=False),
                        **model_args,
                    )
                    raise
                observed = state(client, spec)
                check_watchdog(observed.raw)
                if action.tool in CHANGES and last.verdict == Verdict.ONLINE:
                    trace.step(
                        "report",
                        action,
                        oracle=last,
                        choice=decision(
                            "healthy_network_mutation_refused", accepted=False, move="no-op"
                        ),
                        **model_args,
                    )
                    outcome = "healthy_change_refused"
                    break
                if action.tool == Tool.DECLARE_ONLINE:
                    fresh = measured_probe()
                    begun = time.monotonic()
                    app = client.call("fetch", {"target": 4})
                    probe_records.append(
                        RawObservation(
                            client.command("fetch", {"target": 4}),
                            json.dumps(app),
                            exit_code=0,
                            duration_ms=(time.monotonic() - begun) * 1000,
                            source="lab_app",
                        )
                    )
                    check_watchdog(state(client, spec).raw)
                    app_resumed = app.get("ok") is True
                    accepted = fresh.verdict == Verdict.ONLINE and app_resumed
                    trace.step(
                        "verify",
                        action,
                        oracle=fresh,
                        observation=probe_document(probe_records, redactor),
                        choice=decision(
                            "lab_app_resumed" if accepted else "app_or_oracle_failed",
                            accepted=accepted,
                            move="no-op",
                        ),
                        **model_args,
                    )
                    outcome = "recovered" if accepted else "site_report_required"
                    last = fresh
                    break
                if action.tool in {Tool.REPORT, Tool.GIVE_UP}:
                    trace.step(
                        "report",
                        action,
                        choice=decision("bounded_handoff", move="Report"),
                        **model_args,
                    )
                    outcome = "reported"
                    break
                if action.tool in {
                    Tool.READ_VITALS,
                    Tool.READ_DNS,
                    Tool.READ_PROXY,
                    Tool.READ_ROUTES,
                    Tool.LIST_ROUTE_CANDIDATES,
                    Tool.PROBE_ONLINE,
                    Tool.DIFF_SNAPSHOT,
                }:
                    observed = state(client, spec)
                    observed.raw["prior_alternate_online"] = True
                    last = measured_probe()
                    trace_state(trace, observed, spec)
                    trace.step(
                        "diagnose",
                        action,
                        observation=RawObservation(
                            (),
                            json.dumps(
                                redactor.scrub_json(
                                    {
                                        "state": observed.raw,
                                        "probes": probe_rows(probe_records, redactor),
                                    }
                                )
                            ),
                            source="lab_state",
                        ),
                        oracle=last,
                        **model_args,
                    )
                    history.extend(
                        (
                            Message("assistant", emission.text),
                            Message("user", json.dumps(observed.raw)),
                        )
                    )
                    continue
                if "service_id" in action.args and action.text("service_id") != scope.services[0]:
                    raise ContractError("lab_action_service_mismatch")
                tool, policy_name = operation(action)
                if len(attempted) >= MAX_MUTATIONS:
                    raise ContractError("mutation_budget_exhausted")
                current_key = action_attempt_key(tool, observed, spec)
                retry = False
                if tool in attempted:
                    setting = "dns_server" if tool == "set_dns" else "proxy_url"
                    retry = (
                        generated_probes
                        and policy_name == "snapshot"
                        and snapshot_retry_allowed(
                            tool,
                            tuple(attempted),
                            verified_route_change_at,
                            attempt_keys=tuple(attempt_keys),
                            current_key=current_key,
                        )
                        and configuration_facts(observed)[setting] != known_good[setting]
                    )
                    if not retry:
                        raise ContractError("repeat_mutation_refused")
                    restore_retries.append(
                        {
                            "tool": tool,
                            "verified_route_change_at": verified_route_change_at,
                            "previous_key": key_document(attempt_keys[attempted.index(tool)]),
                            "current_key": key_document(current_key),
                        }
                    )
                attempted.append(tool)
                attempt_keys.append(current_key)
                previous_gateway = observed.gateway
                before = observed.raw
                command = client.command("apply", {"tool": tool, "policy": policy_name})
                card = ConfirmCard(
                    tool,
                    (command,),
                    action.text("why"),
                    action.texts("evidence_ids"),
                    (client.command("undo"),),
                    scope.snapshots[0],
                    blocked_reason="isolated_lab_only",
                    executable=not dry,
                )
                change = config_diff(
                    scope.snapshots[0], before, {"proposed_tool": tool, "policy": policy_name}
                )
                trace.step(
                    "plan",
                    action,
                    change=change,
                    card=card,
                    choice=decision(
                        "snapshot_restore_retry_after_verified_route" if retry else "typed_lab_plan"
                    ),
                    **model_args,
                )
                # Probe episodes keep their one initial deadline through every layer.
                # The classic profile retains its existing per-operation timer.
                if generated_probes:
                    check_watchdog(state(client, spec).raw)
                    armed = object_json(state(client, spec).raw["timer"])
                else:
                    client.call("disarm")
                    armed = client.call("arm", {"seconds": 20})
                deadline = armed.get("deadline")
                if not isinstance(deadline, (int, float)):
                    raise ContractError("watchdog_receipt_required")
                timer: dict[str, Json] = {
                    "state": "armed",
                    "deadline_ts": iso(deadline),
                    "reason": "lab_watchdog_started",
                }
                trace.step(
                    "confirm",
                    action,
                    change=change,
                    card=card,
                    choice=decision("benchmark_policy_approval", confirm="approved", timer=timer),
                )
                client.call("apply", {"tool": tool, "policy": policy_name})
                observed = state(client, spec)
                trace_state(trace, observed, spec)
                actual = config_diff(scope.snapshots[0], before, observed.raw, observed=True)
                trace.step(
                    "apply",
                    action,
                    change=actual,
                    choice=decision("isolated_lab_applied", timer=timer),
                )
                following = measured_probe()
                trace.step(
                    "verify",
                    Action(Tool.PROBE_ONLINE, {}),
                    oracle=following,
                    observation=probe_document(probe_records, redactor),
                    choice=decision(
                        "oracle_measured", timer={**timer, "reason": "armed_at_probe_start"}
                    ),
                )
                check_watchdog(state(client, spec).raw)
                improved = sum(e.ok for e in following.evidence) > sum(e.ok for e in last.evidence)
                if (
                    generated_probes
                    and tool == "reroute"
                    and improved
                    and previous_gateway != observed.gateway == ALTERNATE_GW
                ):
                    verified_route_change_at = len(attempted) - 1
                if improved and (not generated_probes or following.verdict == Verdict.ONLINE):
                    client.call("disarm")
                    trace.step(
                        "verify",
                        Action(Tool.PROBE_ONLINE, {}),
                        oracle=following,
                        choice=decision("oracle_improved", timer=cleared("oracle_improved")),
                    )
                elif improved and generated_probes:
                    trace.step(
                        "verify",
                        Action(Tool.PROBE_ONLINE, {}),
                        oracle=following,
                        choice=decision("partial_improvement_watchdog_retained", timer=timer),
                    )
                elif not improved:
                    try:
                        client.call("undo")
                    except (ContractError, ValueError, OSError, subprocess.SubprocessError):
                        revert_failed = True
                        raise
                    trace.step(
                        "revert",
                        action,
                        change=config_diff(
                            scope.snapshots[0], observed.raw, state(client, spec).raw, observed=True
                        ),
                        choice=decision(
                            "no_oracle_improvement", reverted=True, timer=cleared("restored")
                        ),
                    )
                    outcome = "reverted"
                    if generated_probes:
                        trace.step(
                            "report",
                            Action(Tool.GIVE_UP, {"reason": "needs_human"}),
                            choice=decision(
                                "reversible_attempt_failed_restored_handoff", move="Report"
                            ),
                        )
                        outcome = "reported_after_revert"
                    last = following
                    break
                if generated_probes and following.verdict == Verdict.ONLINE:
                    # Verify the repair's application result immediately. A second pair
                    # of model requests adds no evidence to an already measured success.
                    app_started = time.monotonic()
                    app = client.call("fetch", {"target": 4})
                    check_watchdog(state(client, spec).raw)
                    app_resumed = app.get("ok") is True
                    trace.step(
                        "verify",
                        Action(Tool.DECLARE_ONLINE, {}),
                        oracle=following,
                        observation=RawObservation(
                            client.command("fetch", {"target": 4}),
                            json.dumps(app),
                            exit_code=0,
                            duration_ms=(time.monotonic() - app_started) * 1000,
                            source="lab_app",
                        ),
                        choice=decision(
                            "oracle_and_application_verified_after_repair",
                            accepted=app_resumed,
                            move="no-op",
                        ),
                    )
                    outcome = "recovered" if app_resumed else "site_report_required"
                    last = following
                    break
                last = following
                observed.raw["prior_alternate_online"] = True
            else:
                outcome = "budget_exhausted"
        except (
            ContractError,
            ProviderError,
            ValueError,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            if isinstance(exc, LabRollbackFailed):
                revert_failed = True
            if isinstance(exc, LabWatchdogRestored):
                watchdog_restored = True
                trace.step(
                    "verify",
                    Action(Tool.PROBE_ONLINE, {}),
                    choice=decision(
                        "watchdog_restored_no_agent_credit",
                        reverted=True,
                        timer=cleared("deadline_expired"),
                    ),
                )
            outcome = "refused_" + type(exc).__name__
            failure = (
                str(exc) if isinstance(exc, (ContractError, ProviderError)) else type(exc).__name__
            )
            details = getattr(client, "last_failure_details", None)
            if isinstance(details, dict):
                failure_details = object_json(redactor.scrub_json(details))
            trace.step(
                "report",
                Action(Tool.GIVE_UP, {"reason": "needs_human"}),
                observation=RawObservation(
                    (),
                    json.dumps(failure_details),
                    stderr=str(failure_details.get("stderr", "")),
                    source="lab_command_failure",
                )
                if failure_details is not None
                else RawObservation(
                    ("llama.cpp", "completion"),
                    stderr=failure,
                    duration_ms=max(0.0, (time.monotonic() - model_started) * 1000),
                    source="model_request_failed_no_response",
                )
                if isinstance(exc, ProviderError) and model_attempts
                else None,
                choice=decision(failure, accepted=False, move="Report"),
                **(pending_model if trace.model_calls == model_count_before_response else {}),
            )
        finally:
            if created and not revert_failed:
                try:
                    if not watchdog_restored:
                        client.call("undo")
                    client.call("down")
                except (ContractError, ValueError, OSError, subprocess.SubprocessError):
                    failure = "cleanup_failed_no_retry"
                    outcome = "cleanup_failed"
            if failure:
                trace.finish("ERROR")
    if outcome == "recovered" and fault in {"healthy", "misleading_status"} and not attempted:
        outcome = "healthy_noop"
    receipt: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-lab-eval",
        "profile": LAB_VERSION,
        "fault": fault,
        "arm": policy.name,
        "action_mode": policy.action_mode if isinstance(policy, GlimmerPolicy) else None,
        "comparison_arm": (
            "glimmer-" + policy.action_mode
            if isinstance(policy, GlimmerPolicy) and policy.probe_scripts
            else "scripted"
            if policy.name == "baseline"
            else policy.name
        ),
        "model": {**identity, "model_used": model_attempts > 0},
        "model_calls_attempted": model_attempts,
        "evidence_kind": "MOCK" if dry else "linux_namespace",
        "outcome": outcome,
        "failure": failure,
        "failure_details": failure_details,
        "probe_scripts": generated_probes,
        "probe_episodes": probe_episodes,
        "oracle": last.verdict.value if last else None,
        "app_resumed": app_resumed,
        "changes": list(attempted),
        "restore_retries": restore_retries,
        "attempt_keys": [key_document(key) for key in attempt_keys],
        "trace": trace.path.name,
        "catalog_sha256": catalog_digest(root),
    }
    write_private_json(directory / f"{fault}-{policy.name}-{spec.run_id}.json", receipt)
    if failure:
        raise ContractError("lab_evaluation_stopped_see_receipt")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=("all", *FAULTS), default="all")
    parser.add_argument(
        "--arm", choices=("both", "uplink", "baseline", "scripted-strong"), default="both"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-lab", action="store_true")
    parser.add_argument("--probe-scripts", action="store_true")
    parser.add_argument("--action-mode", choices=("forced", "open"), default="forced")
    parser.add_argument("--model", type=Path, default=bench_root() / GGUF_NAME)
    parser.add_argument(
        "--binary",
        type=Path,
        default=bench_root() / "llama-b10353/llama-b10353/llama-server",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    catalog(root)
    if args.execute and not args.approved_lab:
        parser.error("explicit isolated-lab approval required")
    if args.action_mode == "open" and not args.probe_scripts:
        parser.error("open action mode requires --probe-scripts")
    identity = attest(args.model, args.binary) if args.execute else None
    bundle = (
        stage_worker(root / "src/offline_agent/uplink", authorize=True) if args.execute else None
    )
    from offline_agent.uplink.lab_simulator import SimulatedClient, SimulatedPolicy

    output = (
        root / "artifacts/uplink/evals" / (("live-" if args.execute else "dry-") + uuid.uuid4().hex)
    )
    receipts: list[Json] = []
    for fault in FAULTS if args.fault == "all" else (args.fault,):
        for arm in ("uplink", "baseline") if args.arm == "both" else (args.arm,):
            spec = LabSpec(uuid.uuid4().hex[:12])
            policy: Policy = (
                (
                    GlimmerPolicy(
                        root,
                        execute=True,
                        probe_scripts=args.probe_scripts,
                        action_mode=args.action_mode,
                    )
                    if args.execute
                    else SimulatedPolicy()
                )
                if arm == "uplink"
                else ScriptedStrongPolicy()
                if arm == "scripted-strong"
                else ScriptedPolicy()
            )
            client: LabClient = (
                LimaClient(
                    spec.run_id, root / "src/offline_agent/uplink/lab_worker.py", True, bundle
                )
                if args.execute
                else SimulatedClient(spec)
            )
            run_identity = dict(identity) if identity else None
            if run_identity is not None:
                run_identity["active_model_name"] = MODEL_NAME if arm == "uplink" else None
                run_identity["provider"] = "llama_cpp" if arm == "uplink" else None
            receipts.append(
                evaluate(
                    root,
                    client,
                    spec,
                    fault,
                    policy,
                    output,
                    dry=not args.execute,
                    model_identity=run_identity,
                )
            )
    write_private_json(
        output / "summary.json",
        {
            "version": 1,
            "mode": "live" if args.execute else "dry_run",
            "receipts": receipts,
            "model": identity or expected_model(),
            "inference_calls": None if args.execute else 0,
        },
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "cases": len(receipts),
                "mode": "live" if args.execute else "dry_run",
            }
        )
    )


if __name__ == "__main__":
    main()
