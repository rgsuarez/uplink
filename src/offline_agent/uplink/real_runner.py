"""Bounded lab-only recovery controller. The default run is a pure simulation."""

import argparse
import json
import math
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.native_protocol import EOT
from offline_agent.uplink.contracts import CHANGES, Action, Scope, Tool, parse_action
from offline_agent.uplink.lab_runner import Emission
from offline_agent.uplink.layer_policy import ModelRecord, journal_model_records
from offline_agent.uplink.model_identity import MODEL_NAME
from offline_agent.uplink.oracle import OracleResult, Verdict
from offline_agent.uplink.plans import ConfirmCard
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.probe_evidence import read_probe_evidence
from offline_agent.uplink.real_preflight import Checks, rehearse_boundary, run_preflight
from offline_agent.uplink.real_protocol import (
    PROBE_SECONDS,
    REAL_FAULTS,
    REAL_VERSION,
    RESTORES,
    RealSpec,
)
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.remote_settings import RemoteTargets
from offline_agent.uplink.snapshots import write_private_json
from offline_agent.uplink.trace import RunTrace, cleared, config_diff, decision, iso


class OOBLost(ContractError):
    """No further remote operation is permitted after transport loss."""


class WatchdogRestored(ContractError):
    """The watchdog restored baseline; the agent did not earn a recovery."""

    def __init__(self, reason: str, observed: dict[str, Json] | None = None) -> None:
        super().__init__(reason)
        self.observed = observed


class RevertFailed(ContractError):
    """A revert was attempted once; do not repeat it or start another trial."""


class RealBackend(Protocol):
    dry: bool
    targets: RemoteTargets
    last_probe_record: RawObservation | None
    last_wifi_ssh_record: RawObservation | None
    last_layer_probe: RawObservation | None
    authorization: dict[str, Json] | None
    last_model_records: list[ModelRecord]
    last_model_request_count: int | None

    def oob(self) -> None: ...
    def identity(self) -> dict[str, Json]: ...
    def command(self, op: str, payload: dict[str, object] | None = None) -> tuple[str, ...]: ...
    def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]: ...
    def probe(self) -> OracleResult: ...
    def wifi_ssh(self, interface: str) -> bool: ...
    def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission: ...


def armed(observed: dict[str, Json]) -> dict[str, Json]:
    if observed.get("timing_unproven") is True:
        raise RevertFailed("watchdog_clock_unproven_no_retry")
    if observed.get("watchdog") == "failed":
        raise RevertFailed("watchdog_restore_failed_no_retry")
    if observed.get("watchdog") == "reverted":
        if observed.get("baseline_equal") is True:
            raise WatchdogRestored("watchdog_ended_trial_no_recovery_credit", observed)
        raise RevertFailed("watchdog_restore_mismatch_no_retry")
    deadline = observed.get("deadline")
    if (
        observed.get("watchdog") != "armed"
        or observed.get("watchdog_ready") is not True
        or type(deadline) not in (int, float)
        or not 0 < float(str(deadline)) - time.time() <= 60
    ):
        raise ContractError("watchdog_not_armed_or_trial_expired")
    return {"state": "armed", "deadline_ts": iso(float(str(deadline))), "reason": "run_watchdog"}


def config_view(trace: RunTrace, observed: dict[str, Json]) -> dict[str, Json]:
    raw = object_json(observed.get("config"))
    if (
        set(raw) != {"dns", "proxy", "wifi_power"}
        or not isinstance(raw["dns"], str)
        or not isinstance(raw["proxy"], str)
        or type(raw["wifi_power"]) is not bool
    ):
        raise ContractError("invalid_configuration_evidence")
    return {
        "dns": trace.redactor.handle("config", raw["dns"]),
        "proxy": trace.redactor.handle("config", raw["proxy"]),
        "wifi_power": raw["wifi_power"],
    }


def observed_diff(
    trace: RunTrace, before: dict[str, Json], after: dict[str, Json]
) -> dict[str, Json]:
    old, new = config_view(trace, before), config_view(trace, after)
    return config_diff(
        trace.redactor.handle("snapshot", json.dumps(old, sort_keys=True), remember=False),
        old,
        new,
        after_id=trace.redactor.handle("snapshot", json.dumps(new, sort_keys=True), remember=False),
        observed=True,
    )


def proposed_diff(
    trace: RunTrace, current: dict[str, Json], baseline: dict[str, Json], action: Action
) -> dict[str, Json]:
    old, target = config_view(trace, current), config_view(trace, baseline)
    new = dict(old)
    key = {Tool.SET_DNS: "dns", Tool.RESTORE_PROXY: "proxy", Tool.CYCLE_INTERFACE: "wifi_power"}[
        action.tool
    ]
    new[key] = target[key]
    return config_diff(
        trace.redactor.handle("snapshot", json.dumps(old, sort_keys=True), remember=False),
        old,
        new,
        after_id=trace.redactor.handle("snapshot", json.dumps(new, sort_keys=True), remember=False),
    )


def sync_state(trace: RunTrace, observed: dict[str, Json]) -> None:
    config_view(trace, observed)  # Register private digests before any raw receipt is serialized.
    fleet = observed.get("fleet_check")
    if isinstance(fleet, dict) and isinstance(fleet.get("sha256"), str):
        trace.redactor.handle("config", str(fleet["sha256"]))
    up = observed.get("wifi_up")
    if type(up) is not bool:
        raise ContractError("invalid_wifi_state")
    interfaces = observed.get("interfaces")
    wifi, active = observed.get("wifi_interface"), observed.get("active_interface")
    if (
        not isinstance(interfaces, list)
        or not isinstance(wifi, str)
        or (active is not None and not isinstance(active, str))
    ):
        raise ContractError("invalid_network_state")
    rows: list[Json] = []
    for item in interfaces:
        row = object_json(item)
        name, is_up = row.get("name"), row.get("up")
        if not isinstance(name, str) or type(is_up) is not bool:
            raise ContractError("invalid_interface_state")
        rows.append(
            {
                "id": trace.redactor.handle("interface", name, remember=False),
                "up": is_up,
                "state": "up" if is_up else "down",
            }
        )
    trace.state["interfaces"] = rows
    trace.state["active_route"] = {
        "interface_id": trace.redactor.handle("interface", active, remember=False)
        if active
        else None,
        "service_id": trace.scope.services[0] if active == wifi else None,
        "candidate_id": None,
    }


def recover(
    backend: RealBackend,
    spec: RealSpec,
    directory: Path,
    *,
    confirm: Callable[[ConfirmCard], bool],
    preflight_checks: Checks | None = None,
    root: Path | None = None,
) -> dict[str, Json]:
    redactor = Redactor.local(directory)
    redactor.handle("host", backend.targets.oob_host)
    redactor.handle("host", backend.targets.wifi_host)
    scope = Scope(
        services=(redactor.handle("service", "Wi-Fi"),),
        snapshots=(redactor.handle("snapshot", spec.run_id),),
        evidence=("dns-1", "dns-2", "tls-apple", "tls-google", "portal-apple", "portal-google"),
        profile="general" if backend.dry else "lab_dns_proxy_snapshot",
    )
    readiness = run_preflight(
        backend,
        spec,
        directory,
        checks=preflight_checks,
        root=root or Path(__file__).resolve().parents[3],
    )
    identity, authorization = readiness.identity, readiness.authorization
    if authorization is not None and isinstance(authorization.get("serial"), str):
        # The preflight validated this against private config. Missing config must
        # still produce its typed refusal, without a second load masking the result.
        redactor.handle("serial", str(authorization["serial"]))
    preflight, oob_ok = readiness.failure, readiness.oob_ok

    trace = RunTrace(
        redactor,
        directory=directory / "traces",
        mode="test" if backend.dry else "real_eval",
        model_identity=identity,
        model_name="synthetic-policy" if backend.dry else MODEL_NAME,
        scope=scope,
        authorization=authorization,
    )
    prepared = False
    applied = False
    failure: str | None = None
    recovered = False
    hard_stop = preflight is not None
    watchdog_jobs_retired: bool | None = None
    wifi_ssh_back = False
    injection_state = "not_requested"
    last: OracleResult | None = None
    changes: list[Json] = []
    model_attempts = 0
    model_accounting_incomplete = False
    pending_emission: Emission | None = None
    model_count_before_response = 0
    history: list[Json] = []
    last_action: Action | None = None
    before: dict[str, Json] = {}
    observed: dict[str, Json] = {}
    try:
        if preflight is not None:
            if isinstance(preflight, OOBLost):
                raise preflight
            raise ContractError(str(readiness.receipt["failure"])) from preflight
        backend.oob()  # Required before every trial, independently of individual RPC checks.
        before = backend.call("prepare", {"fault": spec.fault, "seconds": spec.seconds})
        prepared = True
        observed = before
        sync_state(trace, before)
        if not backend.dry:
            baseline_wifi_ssh = backend.wifi_ssh(str(before["wifi_interface"]))
            trace.step(
                "diagnose",
                Action(Tool.READ_ROUTES, {}),
                observation=backend.last_wifi_ssh_record,
                choice=decision("normal_wifi_ssh_baseline", accepted=baseline_wifi_ssh),
            )
            if not baseline_wifi_ssh:
                raise ContractError("normal_wifi_ssh_baseline_required")
        last = backend.probe()
        trace.step(
            "diagnose",
            Action(Tool.PROBE_ONLINE, {}),
            oracle=last,
            observation=backend.last_probe_record,
        )
        if (
            not last.fresh_online(time.time())
            or before.get("baseline_equal") is not True
            or before.get("fleet_diff_match") is not True
        ):
            raise ContractError("healthy_wifi_baseline_required")
        observed = backend.call("arm")
        timer = armed(observed)
        injection_state = "requested"
        observed = backend.call("inject")
        injection_state = "acknowledged"
        sync_state(trace, observed)
        last = backend.probe()
        trace.step(
            "diagnose",
            Action(Tool.READ_VITALS, {}),
            observation=RawObservation(
                backend.command("inject"),
                json.dumps(observed),
                exit_code=0,
                duration_ms=float(str(observed.get("control_latency_ms", 0))),
            ),
            change=observed_diff(trace, before, observed),
            oracle=last,
            choice=decision("authorized_harness_fault_injected", timer=timer),
        )
        trace.step(
            "verify",
            Action(Tool.PROBE_ONLINE, {}),
            oracle=last,
            observation=backend.last_probe_record,
            choice=decision("injected_fault_oracle", timer=timer),
        )
        if last.verdict == Verdict.ONLINE:
            raise ContractError("injection_did_not_break_oracle")
        if spec.fault in {"wrong_dns", "dns_and_proxy"} and (
            observed.get("dns_changed") is not True
            or any(e.ok for e in last.evidence if e.kind == "dns")
        ):
            raise ContractError("dns_fault_not_observed_possible_resolver_cache")
        if spec.fault in {"stale_proxy", "dns_and_proxy"} and (
            observed.get("proxy_changed") is not True
            or any(e.ok for e in last.evidence if e.kind == "tls")
        ):
            raise ContractError("proxy_fault_not_observed")
        if spec.fault == "wifi_power_off" and observed.get("wifi_up") is not False:
            raise ContractError("wifi_power_fault_not_observed")
        for _ in range(8):
            observed = backend.call("observe")
            sync_state(trace, observed)
            timer = armed(observed)
            observed["action_history"] = list(history)
            pending_emission = None
            returned = False
            try:
                emission = backend.choose(scope, observed, last)
                returned = True
            finally:
                if not backend.dry:
                    if backend.last_model_request_count is None:
                        model_accounting_incomplete = True
                    else:
                        model_attempts += backend.last_model_request_count
                if backend.last_layer_probe is not None:
                    trace.step(
                        "diagnose",
                        Action(Tool.READ_VITALS, {}),
                        observation=backend.last_layer_probe,
                        choice=decision("read_only_layer_evidence"),
                    )
                if backend.last_model_records:
                    journal_model_records(trace, backend.last_model_records, defer_action=returned)
            pending_emission = emission
            model_count_before_response = trace.model_calls
            try:
                action = parse_action(emission.text.removesuffix(EOT), scope)
            except ContractError:
                trace.step(
                    "report",
                    None,
                    model_text=emission.raw_text or emission.text,
                    timings=emission.timings,
                    choice=decision("invalid_model_action", accepted=False, move="Report"),
                )
                raise
            history.append(action.as_json())
            if action.tool not in CHANGES and action.tool not in {
                Tool.REPORT,
                Tool.GIVE_UP,
                Tool.DECLARE_ONLINE,
            }:
                observed = backend.call("observe")
                sync_state(trace, observed)
                timer = armed(observed)
                if action.tool == Tool.PROBE_ONLINE:
                    last = backend.probe()
                trace.step(
                    "diagnose",
                    action,
                    model_text=emission.raw_text or emission.text,
                    timings=emission.timings,
                    observation=backend.last_probe_record
                    if action.tool == Tool.PROBE_ONLINE
                    else RawObservation(
                        backend.command("observe"), json.dumps(observed), exit_code=0
                    ),
                    oracle=last if action.tool == Tool.PROBE_ONLINE else None,
                    choice=decision("diagnostic_completed", timer=timer),
                )
                continue
            if action.tool in {Tool.REPORT, Tool.GIVE_UP}:
                trace.step(
                    "report",
                    action,
                    model_text=emission.raw_text or emission.text,
                    timings=emission.timings,
                    choice=decision("model_reported", move="Report"),
                )
                raise ContractError("model_reported_without_recovery")
            if action.tool.value not in RESTORES or (
                action.tool in {Tool.SET_DNS, Tool.RESTORE_PROXY}
                and action.args.get("policy") != "snapshot"
            ):
                trace.step(
                    "report",
                    action,
                    model_text=emission.raw_text or emission.text,
                    timings=emission.timings,
                    choice=decision("snapshot_restore_only", accepted=False, move="Report"),
                )
                raise ContractError("model_did_not_choose_allowed_restore")
            last_action = action
            card = ConfirmCard(
                action.tool.value,
                (backend.command("apply", {"tool": action.tool.value}),),
                "Restore the captured Wi-Fi setting because the oracle failed "
                "and configuration drift was observed.",
                action.texts("evidence_ids"),
                (backend.command("restore"),),
                scope.snapshots[0],
                spec.seconds,
                blocked_reason="dry_run" if backend.dry else "confirmation_required",
                executable=not backend.dry,
            )
            change = proposed_diff(trace, observed, before, action)
            trace.step(
                "plan",
                action,
                card=card,
                change=change,
                model_text=emission.raw_text or emission.text,
                timings=emission.timings,
                choice=decision("restore_snapshot", move="Repair", timer=timer),
            )
            approved = confirm(card)
            # Confirming slowly cannot extend the watchdog or resurrect an expired trial.
            timer = armed(backend.call("observe"))
            trace.step(
                "confirm",
                action,
                card=card,
                change=change,
                choice=decision(
                    "snapshot_restore_policy",
                    accepted=approved,
                    confirm="approved" if approved else "rejected",
                    timer=timer,
                ),
            )
            if not approved:
                raise ContractError("confirmation_denied")
            trace.step(
                "apply",
                action,
                change=change,
                observation=RawObservation(
                    backend.command("apply", {"tool": action.tool.value}), source="mutation_intent"
                ),
                choice=decision("snapshot_restore_requested", timer=timer),
            )
            applied = True
            changes.append(action.tool.value)
            result = backend.call("apply", {"tool": action.tool.value})
            sync_state(trace, result)
            trace.step(
                "verify",
                Action(Tool.READ_VITALS, {}),
                change=observed_diff(trace, observed, result),
                observation=RawObservation(
                    backend.command("apply", {"tool": action.tool.value}),
                    json.dumps(result),
                    exit_code=0,
                    duration_ms=float(str(result.get("control_latency_ms", 0))),
                ),
                choice=decision("snapshot_component_restored", timer=armed(result)),
            )
            observed = result
            previous = last
            last = backend.probe()
            trace.step(
                "verify",
                Action(Tool.PROBE_ONLINE, {}),
                oracle=last,
                observation=backend.last_probe_record,
                choice=decision(
                    "oracle_measured", timer={**timer, "reason": "armed_at_probe_start"}
                ),
            )
            result = backend.call("observe")
            sync_state(trace, result)
            armed(result)  # A watchdog repair never earns an agent recovery.
            if last.fresh_online(time.time()) and result.get("baseline_equal") is True:
                result = backend.call("disarm", {"verified_at": last.checked_at})
                if (
                    result.get("watchdog") != "disarmed"
                    or result.get("baseline_equal") is not True
                    or result.get("fleet_diff_match") is not True
                ):
                    raise RevertFailed("disarm_not_verified_no_retry")
                observed = result
                prepared = False
                trace.step(
                    "verify",
                    Action(Tool.PROBE_ONLINE, {}),
                    oracle=last,
                    choice=decision(
                        "online_and_snapshot_restored", timer=cleared("verified_online")
                    ),
                )
                applied = False
                recovered = True
                break
            if sum(e.ok for e in last.evidence) <= sum(e.ok for e in previous.evidence):
                raise ContractError("oracle_did_not_improve")
            trace.step(
                "verify",
                Action(Tool.PROBE_ONLINE, {}),
                oracle=last,
                choice=decision("partial_improvement_watchdog_retained", timer=armed(result)),
            )
            applied = False
        if not recovered:
            raise ContractError("recovery_step_budget_exhausted")
    except WatchdogRestored as exc:
        failure = str(exc)
        if exc.observed is not None:
            observed = exc.observed
            sync_state(trace, observed)
        trace.step(
            "verify",
            Action(Tool.PROBE_ONLINE, {}),
            choice=decision(
                "watchdog_restored_no_agent_credit",
                reverted=True,
                timer=cleared("deadline_expired"),
            ),
        )
    except (OOBLost, RevertFailed) as exc:
        failure = str(exc)
        hard_stop = True
        if isinstance(exc, OOBLost):
            oob_ok = False
        # No retry or alternate transport. The independent launchd job owns recovery.
    except (ContractError, ValueError, OSError, subprocess.SubprocessError) as exc:
        failure = str(exc) if isinstance(exc, ContractError) else type(exc).__name__
        if prepared:
            try:
                backend.oob()
                result = backend.call("restore")
                if (
                    result.get("baseline_equal") is not True
                    or result.get("watchdog") != "reverted"
                    or result.get("fleet_diff_match") is not True
                ):
                    raise RevertFailed("restore_failed_no_retry")
                sync_state(trace, result)
                trace.step(
                    "revert" if applied else "verify",
                    last_action if applied else Action(Tool.PROBE_ONLINE, {}),
                    change=observed_diff(trace, observed, result),
                    choice=decision(
                        "restore_completed", reverted=True, timer=cleared("trial_aborted")
                    ),
                )
                observed = result
            except (OOBLost, RevertFailed, ContractError, ValueError, OSError) as restore_error:
                hard_stop = True
                if isinstance(restore_error, OOBLost):
                    oob_ok = False
                failure = (
                    (failure or "trial_failed")
                    + ";restore_failed_no_retry:"
                    + type(restore_error).__name__
                )
    finally:
        rollback_verified = (
            observed.get("baseline_equal") is True
            and observed.get("fleet_diff_match") is True
            and observed.get("watchdog") in {"disarmed", "reverted"}
        )
        if backend.dry:
            wifi_ssh_back = rollback_verified
        elif injection_state != "not_requested" and rollback_verified and not hard_stop:
            try:
                wifi_ssh_back = backend.wifi_ssh(str(observed["wifi_interface"]))
                trace.step(
                    "verify",
                    Action(Tool.READ_ROUTES, {}),
                    observation=backend.last_wifi_ssh_record,
                    choice=decision("normal_wifi_ssh_after_restoration", accepted=wifi_ssh_back),
                )
                if not wifi_ssh_back:
                    failure = "normal_wifi_ssh_not_restored_no_retry"
                    hard_stop = True
            except (OOBLost, ContractError, OSError) as exc:
                failure = str(exc) if isinstance(exc, ContractError) else type(exc).__name__
                hard_stop = True
                if isinstance(exc, OOBLost):
                    oob_ok = False
        if not backend.dry and rollback_verified and not hard_stop:
            try:
                retired = backend.call("retire")
                watchdog_jobs_retired = (
                    retired.get("job_retired") is True
                    and type(retired.get("registered_jobs")) is int
                    and retired.get("registered_jobs") == 0
                )
                trace.step(
                    "verify",
                    Action(Tool.READ_VITALS, {}),
                    observation=RawObservation(
                        backend.command("retire"), json.dumps(retired), exit_code=0
                    ),
                    choice=decision(
                        "finished_watchdog_jobs_retired", accepted=watchdog_jobs_retired
                    ),
                )
                if not watchdog_jobs_retired:
                    raise ContractError("watchdog_retirement_unconfirmed")
            except (OOBLost, ContractError, ValueError, OSError) as exc:
                failure = str(exc) if isinstance(exc, ContractError) else type(exc).__name__
                hard_stop = True
                watchdog_jobs_retired = False
                if isinstance(exc, OOBLost):
                    oob_ok = False
        duration = observed.get("baseline_restored_after_s")
        if recovered and (
            type(duration) not in (int, float)
            or not math.isfinite(cast(float, duration))
            or not 0 <= cast(float, duration) <= 60
        ):
            failure = "rollback_deadline_unproven_no_retry"
            hard_stop = True
        if hard_stop:
            recovered = False
        unrecorded = pending_emission if trace.model_calls == model_count_before_response else None
        trace.step(
            "report",
            Action(Tool.DECLARE_ONLINE, {})
            if recovered
            else Action(Tool.GIVE_UP, {"reason": "needs_human"}),
            model_text=(unrecorded.raw_text or unrecorded.text) if unrecorded else None,
            timings=unrecorded.timings if unrecorded else None,
            choice=decision(
                "verified_recovery" if recovered else failure or "stopped",
                accepted=recovered,
                move="no-op" if recovered else "Report",
            ),
        )
        trace.finish("ONLINE" if recovered else "ERROR")
    probe_evidence = read_probe_evidence(trace.path, profile="lab_wifi")
    probe_enabled = probe_evidence["probe_enabled"] is True
    trial_passed = recovered and (not spec.probe_scripts or probe_enabled)
    receipt: dict[str, Json] = {
        "version": 1,
        "kind": "uplink-real-eval",
        "profile": REAL_VERSION,
        "preflight": readiness.receipt,
        "evidence_kind": "MOCK" if backend.dry else "lab_wifi",
        "fault": spec.fault,
        "run_id": spec.run_id,
        "recovered": recovered,
        "failure": failure,
        "changes": changes,
        "trace": trace.path.name,
        "model": {
            **identity,
            "model_used": True
            if model_attempts
            else None
            if model_accounting_incomplete
            else False,
        },
        "model_calls_attempted": None if model_accounting_incomplete else model_attempts,
        "model_calls_confirmed": model_attempts,
        "model_call_accounting": "incomplete" if model_accounting_incomplete else "complete",
        "authorization": authorization,
        "injection_state": injection_state,
        "rollback_verified": rollback_verified,
        "fleet_diff_match": rollback_verified,
        "revert_seconds": observed.get("baseline_restored_after_s"),
        "probe_scripts": spec.probe_scripts,
        "probe_enabled": probe_enabled,
        "non_read_state_returned": probe_evidence["non_read_state_returned"],
        "probe_execution": probe_evidence,
        "probe_episodes": probe_evidence["episodes"],
        "trial_passed": trial_passed,
        "probe_failure": probe_evidence["reason"] if spec.probe_scripts else None,
        "oob_ok": oob_ok,
        "wifi_ssh_back": wifi_ssh_back,
        "hard_stop": hard_stop,
        "watchdog_jobs_retired": watchdog_jobs_retired,
        "final_state": redactor.scrub_json(observed),
        "completed_at": time.time(),
        "oracle": last.as_json() if last else None,
    }
    receipt = object_json(redactor.scrub_serials_json(receipt))
    write_private_json(directory / f"{spec.run_id}-receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=REAL_FAULTS, default="dns_and_proxy")
    parser.add_argument("--trials", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--output", type=Path, default=Path("artifacts/uplink/real"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-restores", action="store_true")
    parser.add_argument("--probe-scripts", action="store_true")
    parser.add_argument("--action-mode", choices=("forced", "open"), default="forced")
    parser.add_argument("--serving-arm", choices=("base",))
    parser.add_argument("--oob-host")
    parser.add_argument("--wifi-host")
    parser.add_argument(
        "--rehearse-boundary",
        action="store_true",
        help="Stop before the first SSH process; no lab contact or trial",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="With separate live approval, run only the unified preflight",
    )
    args = parser.parse_args()
    if args.action_mode == "open" and not args.probe_scripts:
        parser.error("open actions require --probe-scripts")
    if args.rehearse_boundary and (args.execute or args.preflight_only):
        parser.error("boundary rehearsal excludes execution and live preflight")
    if args.preflight_only and not args.execute:
        parser.error("live preflight requires --execute and explicit confirmations")
    targets = RemoteTargets.from_environment(args.oob_host, args.wifi_host)
    if args.execute and (not args.confirm_live or not args.confirm_restores):
        parser.error("live runs require explicit live and snapshot-restore confirmation")
    rows: list[Json] = []
    root = Path(__file__).resolve().parents[3]
    if args.rehearse_boundary:
        spec = RealSpec(
            uuid.uuid4().hex[:12],
            args.fault,
            PROBE_SECONDS if args.probe_scripts else 40,
            args.probe_scripts,
        )
        rehearse_boundary(spec, root, args.output, targets)
        return
    for _ in range(args.trials):
        spec = RealSpec(
            uuid.uuid4().hex[:12],
            args.fault,
            PROBE_SECONDS if args.probe_scripts else 40,
            args.probe_scripts,
        )
        backend: RealBackend
        if args.execute:
            from offline_agent.uplink.real_journal import wait_for_trial_slot
            from offline_agent.uplink.real_transport import LabBackend

            wait_for_trial_slot(root)
            backend = LabBackend(
                spec,
                execute=True,
                targets=targets,
                action_mode=args.action_mode,
                serving={"arm": args.serving_arm, "adapter_path": None}
                if args.serving_arm
                else None,
            )
        else:
            from offline_agent.uplink.real_simulator import SimulatedLab

            backend = SimulatedLab(spec)

        def show(card: ConfirmCard) -> bool:
            display_filter = Redactor.local(args.output)
            if args.execute:
                display_filter.register_lab_identity()
            display_filter.handle("host", targets.oob_host)
            display_filter.handle("host", targets.wifi_host)
            print(display_filter.display(json.dumps(card.for_confirmation(), indent=2)))
            return not args.execute or bool(args.confirm_restores)

        if args.preflight_only:
            readiness = run_preflight(backend, spec, args.output, root=root)
            if readiness.failure is not None:
                append_abort(root, spec, str(readiness.receipt["failure"]), args.output)
            print(json.dumps(readiness.receipt, indent=2))
            raise SystemExit(0 if readiness.failure is None else 1)
        row = recover(backend, spec, args.output, confirm=show, root=root)
        if args.execute:
            from offline_agent.uplink.real_journal import append_trial

            if row.get("hard_stop") is True:
                append_abort(root, spec, str(row["failure"]), args.output)
            elif args.probe_scripts and row.get("probe_enabled") is not True:
                append_abort(root, spec, str(row["probe_failure"]), args.output)
            print(
                append_trial(
                    root,
                    # Authorization remains raw only in memory for the existing
                    # journal gate. Persisted receipts contain its serial pseudonym.
                    {**row, "authorization": backend.authorization},
                    outcome="RECOVERED_WITHOUT_PROBES"
                    if row["recovered"] is True and row["trial_passed"] is not True
                    else None,
                ),
                flush=True,
            )
        rows.append(row)
        if not row["trial_passed"]:
            break
    summary: dict[str, Json] = {
        "evidence_kind": "lab_wifi" if args.execute else "MOCK",
        "requested_trials": args.trials,
        "completed_trials": len(rows),
        "three_consecutive_dns_proxy_recoveries": args.execute
        and args.fault == "dns_and_proxy"
        and len(rows) == 3
        and all(isinstance(r, dict) and r["recovered"] is True for r in rows),
        "three_consecutive_probe_enabled_recoveries": args.execute
        and args.probe_scripts
        and args.fault == "dns_and_proxy"
        and len(rows) == 3
        and all(
            isinstance(r, dict) and r["recovered"] is True and r["probe_enabled"] is True
            for r in rows
        ),
        "receipts": rows,
    }
    write_private_json(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    if not all(isinstance(r, dict) and r["trial_passed"] is True for r in rows):
        raise SystemExit(1)


def append_abort(root: Path, spec: RealSpec, failure: str, output: Path) -> None:
    # Preserve every earlier refusal, including failures before a root run exists.
    with (root / "ABORT-U1.md").open("a") as stream:
        stream.write(
            "\n\n## Stopped "
            + spec.run_id
            + "\n\nReason: "
            + failure
            + ". No retry.\nPreflight: "
            + str(output / f"{spec.run_id}-preflight.json")
            + "\nTrial receipt (only if prepared by the runner): "
            + str(output / f"{spec.run_id}-receipt.json")
            + "\n"
        )


if __name__ == "__main__":
    main()
