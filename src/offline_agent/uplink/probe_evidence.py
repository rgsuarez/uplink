"""Evidence gates for real probes. Configuration is never execution evidence."""

import hashlib
import math
import re
from pathlib import Path
from typing import cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.probe_protocol import validate_program
from offline_agent.uplink.trace_format import number, read_trace

POSITIVE_CONTROL: dict[str, Json] = {
    "version": 1,
    "probes": [{"op": "read_state", "timeout_ms": 1000}],
}


def require_positive_control(value: object) -> dict[str, Json]:
    """Require one completed sandbox read, with exact settings and route readback."""
    if not isinstance(value, dict):
        raise ContractError("probe_positive_control_invalid")
    rows = value.get("rows")
    if (
        value.get("version") != 1
        or type(value.get("version")) is not int
        or value.get("profile") != "lab_wifi"
        or value.get("requested") != POSITIVE_CONTROL
        or value.get("validated") != POSITIVE_CONTROL
        or value.get("status") != "completed"
        or type(value.get("uid")) is not int
        or value["uid"] <= 0
        or not isinstance(rows, list)
        or len(rows) != 1
    ):
        raise ContractError("probe_positive_control_not_returned")
    row = rows[0]
    if (
        not isinstance(row, dict)
        or type(row.get("index")) is not int
        or row["index"] != 0
        or row.get("probe") != {"op": "read_state", "timeout_ms": 1000}
        or row.get("status") != "completed"
        or row.get("ok") is not True
        or type(row.get("elapsed_ms")) not in (int, float)
        or not math.isfinite(row["elapsed_ms"])
        or row["elapsed_ms"] < 0
    ):
        raise ContractError("probe_positive_control_not_returned")
    state = row.get("state")
    if (
        not isinstance(state, dict)
        or set(state)
        != {"dns_configured", "proxy_configured", "wifi_default_route", "lease_present"}
        or any(type(v) is not bool for v in state.values())
        or state["wifi_default_route"] is not True
        or state["lease_present"] is not True
    ):
        raise ContractError("probe_positive_control_state_unproven")
    before, after = value.get("before_sha256"), value.get("after_sha256")
    if (
        value.get("settings_unchanged") is not True
        or value.get("wifi_route_unchanged") is not True
        or not isinstance(before, str)
        or not re.fullmatch(r"[0-9a-f]{64}", before)
        or after != before
    ):
        raise ContractError("probe_positive_control_state_changed")
    return object_json(cast(Json, value))


def episode_counts(episode: dict[str, Json], *, profile: str | None = None) -> dict[str, Json]:
    """Count returned primitives, including negative measurements, never requests alone."""
    counts: dict[str, Json] = {
        "requested": 0,
        "attempted": 0,
        "returned": 0,
        "non_read_state_returned": 0,
        "positive_results": 0,
        "complete": False,
        "reason": None,
    }
    try:
        execution = object_json(episode.get("execution"))
        actual_profile = execution.get("profile")
        if profile is not None and actual_profile != profile:
            raise ContractError("probe_profile_mismatch")
        probes = validate_program(episode.get("program"), str(actual_profile), alternate=True)
        counts["requested"] = len(probes)
        if execution.get("requested") != episode.get("program"):
            raise ContractError("probe_requested_program_mismatch")
        rows = execution.get("rows")
        if not isinstance(rows, list) or len(rows) > len(probes):
            raise ContractError("probe_rows_invalid")
        if execution.get("status") not in {"completed", "refused", "timeout"}:
            raise ContractError("probe_execution_status_invalid")
        if rows and (
            type(execution.get("uid")) is not int
            or cast(int, execution["uid"]) <= 0
            or execution.get("validated") != episode.get("program")
        ):
            raise ContractError("probe_execution_identity_unproven")
        attempted = returned = non_read_state_returned = positive = 0
        for index, value in enumerate(rows):
            row = object_json(value)
            status = row.get("status")
            if (
                type(row.get("index")) is not int
                or row["index"] != index
                or row.get("probe") != probes[index].as_json()
                or status not in {"completed", "failed", "timeout", "unavailable", "refused"}
            ):
                raise ContractError("probe_row_binding_invalid")
            if status != "refused":
                if type(row.get("ok")) is not bool or not number(row.get("elapsed_ms")):
                    raise ContractError("probe_row_result_invalid")
                attempted += 1
            if status == "completed":
                returned += 1
                non_read_state_returned += probes[index].op != "read_state"
                positive += row["ok"] is True
        counts.update(
            attempted=attempted,
            returned=returned,
            non_read_state_returned=non_read_state_returned,
            positive_results=positive,
        )
        if returned and execution.get("status") != "completed":
            raise ContractError("probe_execution_not_completed")
        if execution.get("settings_unchanged") is not True:
            raise ContractError("probe_settings_unchanged_unproven")
        if actual_profile == "lab_wifi":
            before = execution.get("before_sha256")
            if (
                execution.get("wifi_route_unchanged") is not True
                or not isinstance(before, str)
                or not re.fullmatch(r"[0-9a-f]{64}", before)
                or execution.get("after_sha256") != before
            ):
                raise ContractError("probe_wifi_readback_unproven")
        counts["complete"] = True
    except (ValueError, TypeError, KeyError) as exc:
        # Keep the computed diagnosis, but never copy arbitrary child output into reports.
        reason = str(exc)
        counts["reason"] = (
            reason
            if re.fullmatch(r"probe_[a-z0-9_]{1,120}", reason)
            else "probe_execution_evidence_invalid"
        )
    return counts


def trace_probe_evidence(
    records: list[dict[str, Json]], *, profile: str | None = None
) -> dict[str, Json]:
    """Use only generated-probe observations in the validated recovery trace.

    Preflight probes, read_state and the independent ONLINE oracle do not earn credit.
    Missing or malformed decision evidence cannot be rescued by another valid row.
    """
    decisions: list[Json] = []
    episodes: list[Json] = []
    complete = True
    requested = attempted = returned = non_read_state_returned = positive = 0
    for record in records:
        observation = record.get("observation")
        if not isinstance(observation, dict) or observation.get("source") != (
            "generated_probe_episode"
        ):
            continue
        try:
            raw = observation.get("stdout")
            if (
                type(observation.get("exit_code")) is not int
                or observation["exit_code"] != 0
                or not isinstance(raw, str)
            ):
                raise ContractError("probe_observation_unavailable")
            episode = object_json(read_json(raw, max_bytes=131072))
            counts = episode_counts(episode, profile=profile)
            episodes.append(episode)
        except (ValueError, TypeError):
            counts = {
                "requested": 0,
                "attempted": 0,
                "returned": 0,
                "non_read_state_returned": 0,
                "positive_results": 0,
                "complete": False,
                "reason": "probe_observation_unparseable",
            }
        decisions.append({"trace_step": record.get("step"), **counts})
        complete &= counts["complete"] is True
        requested += cast(int, counts["requested"])
        attempted += cast(int, counts["attempted"])
        returned += cast(int, counts["returned"])
        non_read_state_returned += cast(int, counts["non_read_state_returned"])
        positive += cast(int, counts["positive_results"])
    enabled = complete and non_read_state_returned > 0
    return {
        "version": 2,
        "source": "recovery_trace_generated_probe_episode",
        "probe_enabled": enabled,
        "accounting": "complete" if complete else "incomplete",
        "requested": requested,
        "attempted": attempted,
        "returned": returned,
        "non_read_state_returned": non_read_state_returned,
        "positive_results": positive,
        "reason": None
        if enabled
        else "probe_execution_evidence_invalid"
        if not complete
        else "no_returned_non_read_state_probe_rows"
        if returned
        else "no_returned_probe_rows",
        "decisions": decisions,
        "episodes": episodes,
    }


def read_probe_evidence(path: Path, *, profile: str | None = None) -> dict[str, Json]:
    try:
        records = list(read_trace(path))
        evidence = trace_probe_evidence(records, profile=profile)
        evidence["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return evidence
    except (OSError, ValueError):
        evidence = trace_probe_evidence([], profile=profile)
        evidence.update(accounting="incomplete", reason="probe_trace_unreadable", trace_sha256=None)
        return evidence
