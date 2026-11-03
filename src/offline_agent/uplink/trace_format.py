"""Version 1 trace contract and strict reader. Standard library only, also used by replay."""

import json
import math
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import cast

type TraceJson = None | bool | int | float | str | list[TraceJson] | dict[str, TraceJson]
TRACE_VERSION = 4
PHASES = ("diagnose", "plan", "confirm", "apply", "verify", "revert", "report")
VERDICTS = ("ONLINE", "CAPTIVE", "DEGRADED", "OFFLINE")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}")
COMMON = {"event", "trace_version", "run_id", "ts", "elapsed_ms", "net_state", "display"}
STEP_FIELDS = {
    "step",
    "phase",
    "action",
    "observation",
    "config_diff",
    "oracle",
    "model",
    "decision",
    "perf",
    "confirm_card",
}
PERF_FIELDS = {
    "prompt_n",
    "predicted_n",
    "cached_n",
    "prompt_per_second",
    "predicted_per_second",
    "prompt_ms",
    "predicted_ms",
}


class TraceFormatError(ValueError):
    pass


def mapping(value: object) -> dict[str, TraceJson]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise TraceFormatError("expected_object")
    return cast(dict[str, TraceJson], value)


def fields(value: object, expected: set[str]) -> dict[str, TraceJson]:
    obj = mapping(value)
    if set(obj) != expected:
        raise TraceFormatError("missing_or_unknown_fields")
    return obj


def number(value: object, *, integer: bool = False) -> bool:
    return (
        type(value) in ((int,) if integer else (int, float))
        and math.isfinite(cast(float, value))
        and cast(float, value) >= 0
    )


def timestamp(value: object) -> bool:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def authorization_record(value: object) -> None:
    """Validate a recorded approval's shape. Runtime authorization stays in the executor."""
    row = fields(
        value,
        {
            "serial",
            "allowed_faults",
            "fault_families",
            "scope",
            "approved_by",
            "approved_at",
            "fleet_clearance_at",
            "minimum_trial_interval_seconds",
            "clearance_sha256",
        },
    )
    if (
        any(
            not isinstance(row[k], str) or not row[k]
            for k in (
                "serial",
                "scope",
                "approved_by",
                "approved_at",
                "fleet_clearance_at",
                "clearance_sha256",
            )
        )
        or not strings(row["allowed_faults"])
        or not row["allowed_faults"]
        or not strings(row["fault_families"])
        or not row["fault_families"]
        or not number(row["minimum_trial_interval_seconds"], integer=True)
        or not re.fullmatch(r"[0-9a-f]{64}", str(row["clearance_sha256"]))
    ):
        raise TraceFormatError("invalid_run_authorization")
    try:
        datetime.fromisoformat(str(row["approved_at"]))
        datetime.fromisoformat(str(row["fleet_clearance_at"]))
    except ValueError as exc:
        raise TraceFormatError("invalid_run_authorization") from exc


def net_state(value: object) -> None:
    state = fields(value, {"interfaces", "active_route", "latest_oracle"})
    interfaces = state["interfaces"]
    if not isinstance(interfaces, list):
        raise TraceFormatError("invalid_interfaces")
    for raw in interfaces:
        row = fields(raw, {"id", "up", "state"})
        if (
            not isinstance(row["id"], str)
            or not re.fullmatch(r"interface-[0-9a-f]{24}", row["id"])
            or row["up"] is not None
            and type(row["up"]) is not bool
            or row["state"] not in ("up", "down", "unknown")
        ):
            raise TraceFormatError("invalid_interface_state")
    route = fields(state["active_route"], {"interface_id", "service_id", "candidate_id"})
    if any(
        v is not None and (not isinstance(v, str) or not re.fullmatch(r"[a-z]+-[0-9a-f]{24}", v))
        for v in route.values()
    ):
        raise TraceFormatError("invalid_route_handles")
    oracle = fields(state["latest_oracle"], {"verdict", "ts"})
    if (
        oracle["verdict"] not in (*VERDICTS, None)
        or (oracle["verdict"] is None) != (oracle["ts"] is None)
        or oracle["ts"] is not None
        and not timestamp(oracle["ts"])
    ):
        raise TraceFormatError("invalid_latest_oracle")


def validate_record(value: object) -> dict[str, TraceJson]:
    row = mapping(value)
    event = row.get("event")
    extra = (
        {"schema_version", "model", "grammar_sha256", "mode"}
        if event == "start"
        else {"verdict", "totals"}
        if event == "finish"
        else STEP_FIELDS
        if event == "step"
        else None
    )
    if extra is None:
        raise TraceFormatError("invalid_event")
    if event == "start" and row.get("trace_version") == 4:
        extra = extra | {"authorization"}
    fields(row, (COMMON - {"display"} if row.get("trace_version") == 1 else COMMON) | extra)
    if row.get("trace_version") != 1 and (
        not isinstance(row.get("display"), str) or not row["display"]
    ):
        raise TraceFormatError("display_required")
    if (
        type(row["trace_version"]) is not int
        or row["trace_version"] not in (1, 2, 3, TRACE_VERSION)
        or not isinstance(row["run_id"], str)
        or not re.fullmatch(r"uplink-[0-9a-f]{32}", row["run_id"])
        or not timestamp(row["ts"])
        or not number(row["elapsed_ms"])
    ):
        raise TraceFormatError("invalid_record_header")
    net_state(row["net_state"])
    if event == "start":
        if (
            row["schema_version"] != 1
            or type(row["schema_version"]) is not int
            or row["mode"] not in ("diagnose", "plan", "dry_run", "test", "linux_eval", "real_eval")
            or not isinstance(row["grammar_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["grammar_sha256"])
        ):
            raise TraceFormatError("invalid_start")
        identity_keys = (
            {"provider", "name"}
            if row["trace_version"] in (1, 2)
            else {
                "name",
                "gguf_filename",
                "gguf_sha256",
                "source_repo",
                "llama_cpp_build",
                "host_chip",
                "provider",
                "active_model_name",
                "verification",
                "verified_at",
                "model_used",
            }
        )
        identity = fields(row["model"], identity_keys)
        if row["trace_version"] in (3, 4):
            if (
                identity["name"] != "Muse Glimmer 30B"
                or identity["gguf_filename"] != "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
                or identity["source_repo"] != "meta-models/Muse-Glimmer-30B-GGUF"
                or not isinstance(identity["gguf_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", identity["gguf_sha256"])
                or not isinstance(identity["llama_cpp_build"], str)
                or not isinstance(identity["host_chip"], str)
                or type(identity["model_used"]) is not bool
                or identity["verification"]
                not in ("expected", "file_verified", "server_verified", "fixture")
            ):
                raise TraceFormatError("invalid_model_provenance")
        elif any(v is not None and not isinstance(v, str) for v in identity.values()):
            raise TraceFormatError("invalid_model_identity")
        if row["trace_version"] == 4 and row["authorization"] is not None:
            authorization_record(row["authorization"])
    elif event == "finish":
        if row["verdict"] not in (
            *VERDICTS,
            "UNKNOWN",
            "ERROR",
            "PLAN_ONLY",
            "REPORTED",
            "GAVE_UP",
        ):
            raise TraceFormatError("invalid_finish_verdict")
        totals = fields(
            row["totals"], {"steps", "model_calls", "prompt_n", "predicted_n", "elapsed_ms"}
        )
        if (
            not number(totals["steps"], integer=True)
            or not number(totals["model_calls"], integer=True)
            or not number(totals["elapsed_ms"])
            or any(
                totals[k] is not None and not number(totals[k], integer=True)
                for k in ("prompt_n", "predicted_n")
            )
        ):
            raise TraceFormatError("invalid_totals")
    else:
        validate_step(row)
    return row


def validate_step(row: dict[str, TraceJson]) -> None:
    if not number(row["step"], integer=True) or row["step"] == 0 or row["phase"] not in PHASES:
        raise TraceFormatError("invalid_step_or_phase")
    action = row["action"]
    if action is not None:
        action = fields(action, {"version", "tool", "args"})
        if (
            action["version"] != 1
            or type(action["version"]) is not int
            or not isinstance(action["tool"], str)
            or not re.fullmatch(r"[a-z_]+", action["tool"])
        ):
            raise TraceFormatError("invalid_action")
        mapping(action["args"])
    observation = fields(
        row["observation"], {"argv", "stdout", "stderr", "exit_code", "duration_ms", "source"}
    )
    if (
        not strings(observation["argv"])
        or not isinstance(observation["stdout"], str)
        or not isinstance(observation["stderr"], str)
        or not isinstance(observation["source"], str)
        or observation["exit_code"] is not None
        and type(observation["exit_code"]) is not int
        or not number(observation["duration_ms"])
    ):
        raise TraceFormatError("invalid_observation")
    decision = fields(
        row["decision"],
        {"outcome", "reason", "confirm_outcome", "timer", "auto_revert_triggered", "next_move"},
    )
    if (
        decision["outcome"] not in ("accepted", "rejected")
        or not isinstance(decision["reason"], str)
        or decision["confirm_outcome"]
        not in (None, "approved", "rejected", "cancelled", "not_requested_u1")
        or type(decision["auto_revert_triggered"]) is not bool
        or decision["next_move"] not in (None, "Repair", "Reroute", "Report", "no-op")
    ):
        raise TraceFormatError("invalid_decision")
    if action is None and decision["outcome"] != "rejected":
        raise TraceFormatError("accepted_action_required")
    if decision["timer"] is not None:
        timer = fields(decision["timer"], {"state", "deadline_ts", "reason"})
        if (
            timer["state"] not in ("armed", "cleared")
            or not isinstance(timer["reason"], str)
            or timer["state"] == "armed"
            and not timestamp(timer["deadline_ts"])
            or timer["state"] == "cleared"
            and timer["deadline_ts"] is not None
        ):
            raise TraceFormatError("invalid_timer")
    if row["config_diff"] is not None:
        diff = fields(
            row["config_diff"],
            {
                "before_snapshot_id",
                "after_snapshot_id",
                "kind",
                "before",
                "after",
                "structured",
                "unified",
            },
        )
        if (
            not isinstance(diff["before_snapshot_id"], str)
            or diff["kind"] not in ("proposed", "observed")
            or not isinstance(diff["structured"], list)
            or not isinstance(diff["unified"], str)
        ):
            raise TraceFormatError("invalid_config_diff")
    if row["phase"] in ("plan", "confirm", "apply", "revert") and row["config_diff"] is None:
        raise TraceFormatError("change_step_requires_config_diff")
    if row["phase"] == "confirm":
        card = fields(
            row["confirm_card"],
            {
                "tool",
                "argv",
                "why",
                "evidence_ids",
                "undo_argv",
                "snapshot_id",
                "auto_revert_seconds",
                "executable",
                "blocked_reason",
            },
        )
        for key in ("argv", "undo_argv"):
            commands = card[key]
            if not isinstance(commands, list) or not all(strings(x) for x in commands):
                raise TraceFormatError("invalid_card_argv")
        if (
            not number(card["auto_revert_seconds"])
            or decision["confirm_outcome"] is None
            or decision["timer"] is None
        ):
            raise TraceFormatError("explicit_confirmation_required")
    if row["oracle"] is not None:
        oracle = fields(row["oracle"], {"verdict", "ts", "dns", "https", "captive", "latency_ms"})
        if (
            oracle["verdict"] not in VERDICTS
            or not timestamp(oracle["ts"])
            or not number(oracle["latency_ms"])
        ):
            raise TraceFormatError("invalid_oracle")
        for key in ("dns", "https", "captive"):
            values = oracle[key]
            if not isinstance(values, list) or len(values) != 2:
                raise TraceFormatError("incomplete_oracle_subresults")
            for value in values:
                sub = mapping(value)
                if (
                    type(sub.get("ok")) is not bool
                    or not number(sub.get("latency_ms"))
                    or not isinstance(sub.get("target_id"), str)
                ):
                    raise TraceFormatError("invalid_oracle_subresult")
                if key != "dns" and not {"status", "proxy_used", "path_id"} <= sub.keys():
                    raise TraceFormatError("missing_proxy_evidence")
    if row["model"] is None:
        if row["perf"] is not None:
            raise TraceFormatError("performance_without_model_call")
    else:
        model = fields(row["model"], {"text", "tokens", "redacted", "provider_timings"})
        if not isinstance(model["text"], str) or type(model["redacted"]) is not bool:
            raise TraceFormatError("invalid_model_output")
        tokens = model["tokens"]
        if tokens is not None and (
            not isinstance(tokens, list) or not all(number(x, integer=True) for x in tokens)
        ):
            raise TraceFormatError("invalid_model_tokens")
        perf = fields(row["perf"], PERF_FIELDS)
        if not all(
            v is None or number(v, integer=k in {"prompt_n", "predicted_n", "cached_n"})
            for k, v in perf.items()
        ):
            raise TraceFormatError("invalid_provider_timings")


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TraceFormatError("duplicate_key")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise TraceFormatError("nonfinite_json_refused")


def read_trace(path: Path) -> Iterator[dict[str, TraceJson]]:
    run_id: TraceJson = None
    previous_elapsed = -1.0
    steps = 0
    finished = False
    planned: TraceJson = None
    approved = applied = False
    mode: TraceJson = None
    total_bytes = 0
    try:
        with path.open() as stream:
            for line_number, line in enumerate(stream, 1):
                total_bytes += len(line.encode())
                try:
                    if len(line) > 1_048_576 or total_bytes > 32_000_000 or finished:
                        raise TraceFormatError("trace_limit_or_record_after_finish")
                    row = validate_record(
                        json.loads(
                            line, object_pairs_hook=unique_pairs, parse_constant=invalid_constant
                        )
                    )
                    if line_number == 1:
                        if row["event"] != "start":
                            raise TraceFormatError("start_required")
                        run_id = row["run_id"]
                        mode = row["mode"]
                    elif row["event"] == "start":
                        raise TraceFormatError("duplicate_start")
                    elapsed = cast(float, row["elapsed_ms"])
                    if row["run_id"] != run_id or elapsed < previous_elapsed:
                        raise TraceFormatError("run_or_time_order_mismatch")
                    previous_elapsed = elapsed
                    if row["event"] == "step":
                        steps += 1
                        if row["step"] != steps:
                            raise TraceFormatError("step_order_mismatch")
                        choice = mapping(row["decision"])
                        timer = choice["timer"]
                        phase = row["phase"]
                        if phase == "plan":
                            if applied:
                                raise TraceFormatError("unresolved_watchdog_before_plan")
                            planned, approved, applied = row["action"], False, False
                        elif phase == "confirm":
                            if planned is None or row["action"] != planned:
                                raise TraceFormatError("confirm_without_matching_plan")
                            approved = choice["confirm_outcome"] == "approved"
                            if mode not in {"test", "linux_eval", "real_eval"} and (
                                approved or mapping(timer)["state"] == "armed"
                            ):
                                raise TraceFormatError("u1_timer_or_approval_refused")
                        elif phase == "apply":
                            if (
                                mode not in {"test", "linux_eval", "real_eval"}
                                or not approved
                                or row["action"] != planned
                                or timer is None
                                or mapping(timer)["state"] != "armed"
                            ):
                                raise TraceFormatError("apply_without_confirm_and_timer")
                            applied = True
                        elif phase in ("verify", "revert"):
                            if phase == "revert" and not applied:
                                raise TraceFormatError("revert_without_apply")
                            if applied:
                                if timer is None:
                                    raise TraceFormatError("watchdog_state_required")
                                if mapping(timer)["state"] == "cleared" or (
                                    phase == "verify"
                                    and choice["outcome"] == "accepted"
                                    and choice["reason"] == "partial_improvement_watchdog_retained"
                                ):
                                    applied = approved = False
                    if row["event"] == "finish":
                        finished = True
                        if mapping(row["totals"])["steps"] != steps:
                            raise TraceFormatError("totals_mismatch")
                        if applied and row["verdict"] != "ERROR":
                            raise TraceFormatError("finish_with_unresolved_watchdog")
                except (TraceFormatError, ValueError, RecursionError, TypeError) as exc:
                    reason = str(exc) if isinstance(exc, TraceFormatError) else "invalid_json"
                    raise TraceFormatError(f"line {line_number}: {reason}") from None
                yield row
    except (OSError, UnicodeError):
        raise TraceFormatError("trace_unreadable") from None
    if not finished:
        raise TraceFormatError("missing_finish_record")
