"""Render local recovery evidence for video. No model, network, or recovery effects.

V5 actions/probes come from the hash-bound trace, not optimistic receipt copies.
FAULTS-V1 accepts a manifest of saved worker requests/responses and root state.
This is a descriptive view, never a new acceptance oracle or live authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import stat
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.faults_v1 import parse_action, parse_spec
from offline_agent.uplink.probe_trial_score import score_trial
from offline_agent.uplink.trace_format import read_trace

FAULT_NAMES = {
    "dns_and_proxy": "DNS and proxy settings",
    "test_host_blackhole": "Wi-Fi route to the pinned test destination",
    "wifi_ipv6_off": "Wi-Fi IPv6 disabled",
    "owned_pf_outbound": "Outbound HTTPS to the pinned destination blocked",
}
CHECKS = {
    "dns-1": ("dns", "DNS / Apple"),
    "dns-2": ("dns", "DNS / Google"),
    "tls-apple": ("tls", "HTTPS / Apple (effective proxy policy)"),
    "tls-google": ("tls", "HTTPS / Google (effective proxy policy)"),
    "portal-apple": ("portal", "Captive check / Apple"),
    "portal-google": ("portal", "Captive check / Google"),
}
TOOLS = {"set_dns", "restore_proxy", "report", "declare_online", "reroute"}
LAYERS = {"dns", "proxy", "link", "ip", "captive", "upstream", "healthy", "unknown"}
OPS = {"dns_query", "https_head", "captive_check", "read_state"}
UNKNOWN = "UNKNOWN (not recorded)"


def load(path: Path, expected: str | None = None) -> tuple[dict[str, Json], str]:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ContractError("demo_symlink_refused")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4_000_000:
        raise ContractError("demo_bounded_regular_receipt_required")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and (not re.fullmatch(r"[0-9a-f]{64}", expected) or digest != expected):
        raise ContractError("demo_receipt_hash_mismatch")
    return object_json(read_json(data.decode(), max_bytes=4_000_000)), digest


def number(value: Json) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ContractError("demo_nonnegative_finite_number_required")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ContractError("demo_nonnegative_finite_number_required") from exc
    if not math.isfinite(result):
        raise ContractError("demo_nonnegative_finite_number_required")
    return result


def status(value: Json) -> str:
    if value is None:
        return UNKNOWN
    if type(value) is not bool:
        raise ContractError("demo_boolean_required")
    return "PASS" if value else "FAIL"


def enum(value: Json, allowed: set[str]) -> str:
    if value is None:
        return UNKNOWN
    if not isinstance(value, str) or value not in allowed:
        raise ContractError("demo_unrecognized_label")
    return value


def citations(value: object) -> str:
    if not isinstance(value, list) or any(
        not isinstance(x, str) or x not in {*CHECKS, "fault-v1-target", "fault-v1-drift"}
        for x in value
    ):
        raise ContractError("demo_unrecognized_citation")
    return ", ".join(str(x) for x in value) or "none recorded"


class Transcript:
    def __init__(self, receipt: dict[str, Json], digest: str) -> None:
        self.receipt = receipt
        self.sources = {"R": digest}
        self.rows: list[tuple[str, str, str]] = []
        self.notes: list[str] = []

    def add(self, label: str, value: str, source: str) -> None:
        self.rows.append((label, value, source))

    def identity(self, run_id: Json, fault: Json) -> None:
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{12}", run_id):
            raise ContractError("demo_run_id_required")
        enum(fault, set(FAULT_NAMES))
        self.run_id = run_id
        self.fault = str(fault)

    def oracle(self, value: Json, source: str) -> None:
        oracle = object_json(value or {})
        if oracle and oracle.get("version") != "uplink-online-v1":
            raise ContractError("demo_mac_oracle_required")
        verdict = enum(oracle.get("verdict"), {"ONLINE", "OFFLINE", "DEGRADED", "CAPTIVE"})
        raw = oracle.get("evidence", [])
        if not isinstance(raw, list):
            raise ContractError("demo_oracle_evidence_required")
        checks: dict[str, tuple[int, dict[str, Json]]] = {}
        for index, item in enumerate(raw):
            row = object_json(item)
            key = enum(row.get("id"), set(CHECKS))
            if key not in CHECKS or key in checks or row.get("kind") != CHECKS[key][0]:
                raise ContractError("demo_duplicate_or_wrong_oracle_check")
            checks[key] = index, row
        complete = len(checks) == len(CHECKS) and all(
            r.get("ok") is True and (r["kind"] == "dns" or bool(r.get("path_id")))
            for _, r in checks.values()
        )
        self.add("Final oracle", verdict + " (recorded)", source + "/verdict")
        if verdict == "ONLINE" and not complete:
            self.notes.append(
                "ONLINE claim is UNPROVEN: required check evidence is missing or failed."
            )
        for key, (_, label) in CHECKS.items():
            if key not in checks:
                self.add(label, UNKNOWN, source + "/evidence")
                continue
            index, row = checks[key]
            result = status(row.get("ok"))
            code = row.get("status")
            if code is not None:
                if type(code) is not int or not 100 <= code <= 599:
                    raise ContractError("demo_http_status_required")
                result += f"; HTTP {code}"
            if row["kind"] != "dns":
                route = row.get("proxy_used")
                # Never copy proxy URLs, credentials, paths or arbitrary reason strings.
                result += "; route " + ("direct" if route == "direct" else "not displayed")
            self.add(label, result, f"{source}/evidence/{index}")

    def render(self) -> str:
        lines = [
            "# Recovery receipt",
            "",
            f"**{FAULT_NAMES[self.fault]}** | Run `{self.run_id}`",
            "",
            "Recorded evidence only. This transcript does not run or authorize a trial.",
            "",
            "| Phase | Recorded result |",
            "| --- | --- |",
            *(f"| {label} | {value} |" for label, value, _ in self.rows),
            "",
            *[note + "\n" for note in self.notes],
            "<details>",
            "<summary>Receipt provenance and exact source hashes</summary>",
            "",
            "Sources below identify exact file bytes. Paths are JSON pointers; trace pointers",
            "use zero-based record indexes and `stdout-json` for the embedded episode.",
            "Durations are rounded to milliseconds for display; the source retains full precision.",
            "",
            "| Source | SHA-256 |",
            "| --- | --- |",
            *(f"| {label} | `{sha}` |" for label, sha in self.sources.items()),
            "",
            "| Phase | Source fields |",
            "| --- | --- |",
            *(f"| {label} | `{source}` |" for label, _, source in self.rows),
            "",
            "</details>",
            "",
        ]
        return "\n".join(lines)


def v5(path: Path, view: Transcript) -> None:
    receipt = view.receipt
    if receipt.get("fault") != "dns_and_proxy" or receipt.get("probe_scripts") is not True:
        raise ContractError("demo_probe_trial_required")
    view.identity(receipt.get("run_id"), receipt.get("fault"))
    view.add("Fault / run", FAULT_NAMES[view.fault] + " / " + view.run_id, "R:/fault,run_id")
    # The existing scorer verifies strict trace shape, hash and action emission/choices.
    scored = score_trial(path, "base")
    trace_path = path.parent / "traces" / str(receipt["trace"])
    if any(p.is_symlink() for p in trace_path.parents):
        raise ContractError("demo_symlink_refused")
    records = list(read_trace(trace_path))
    if hashlib.sha256(trace_path.read_bytes()).hexdigest() != scored["trace_sha256"]:
        raise ContractError("demo_trace_changed")
    view.sources["T"] = str(scored["trace_sha256"])
    view.add(
        "Evidence class",
        enum(receipt.get("evidence_kind"), {"lab_wifi", "MOCK"}),
        "R:/evidence_kind",
    )
    for index, record in enumerate(records):
        observation = record.get("observation")
        if (
            not isinstance(observation, dict)
            or observation.get("source") != "generated_probe_episode"
        ):
            continue
        episode = object_json(read_json(str(observation["stdout"]), max_bytes=131072))
        source = f"T:/{index}/observation/stdout-json"
        execution = object_json(episode.get("execution", {}))
        rows = execution.get("rows", [])
        if not isinstance(rows, list):
            raise ContractError("demo_probe_rows_required")
        for position, item in enumerate(rows):
            probe = object_json(item)
            op = enum(object_json(probe.get("probe", {})).get("op"), OPS)
            state = enum(probe.get("status"), {"completed", "failed", "refused", "timeout"})
            result = "positive" if probe.get("ok") is True else "negative"
            if type(probe.get("ok")) is not bool:
                result = "unknown"
            if state != "completed":
                result = "unavailable"
            detail = f"{op}: {state}; measurement {result}"
            if state != "completed":
                detail += "; no returned-measurement credit"
            view.add("Evidence", detail, f"{source}/execution/rows/{position}")
        action = object_json(episode.get("action", {}))
        after = object_json(episode.get("hypothesis_after", {}))
        mode = enum(episode.get("action_mode"), {"forced", "open"})
        tools = episode.get("admitted_tools")
        if not isinstance(tools, list):
            raise ContractError("demo_recorded_choices_required")
        choice = (
            "FORCED"
            if mode == "forced"
            else ("OPEN choice" if len(tools) > 1 else "SINGLE CHOICE (no open-choice credit)")
        )
        view.add(
            "Chosen action",
            f"{enum(action.get('tool'), TOOLS)}; {choice}; "
            f"layer {enum(after.get('layer'), LAYERS)}; cites {citations(after.get('evidence'))}",
            source + "/action,hypothesis_after,action_mode,admitted_tools",
        )
    view.oracle(receipt.get("oracle"), "R:/oracle")
    seconds = number(receipt.get("revert_seconds"))
    final = object_json(receipt.get("final_state", {}))
    timed = seconds is not None and final.get("timing_unproven") is False
    view.add(
        "Revert time",
        f"{seconds:.3f} s (injection to exact restoration)" if timed else UNKNOWN,
        "R:/revert_seconds; R:/final_state/timing_unproven",
    )
    for label, key in (
        ("Exact snapshot diff", "fleet_diff_match"),
        ("Rollback verified", "rollback_verified"),
        ("OOB reachable", "oob_ok"),
        ("Wi-Fi SSH returned", "wifi_ssh_back"),
        ("Watchdog retired", "watchdog_jobs_retired"),
    ):
        view.add(label, status(receipt.get(key)), "R:/" + key)
    view.add(
        "Protected services unchanged",
        status(final.get("protected_services_unchanged")),
        "R:/final_state/protected_services_unchanged",
    )
    view.notes.append(
        "Forced repairs and grammar-constrained layers do not prove independent diagnosis. "
        "A final HTTPS check using the direct route is not a successful proxy traversal."
    )


def faults(path: Path, view: Transcript) -> None:
    """Read a manifest of exact, unmodified worker artifacts; absent evidence stays unknown."""
    entries = object_json(view.receipt.get("files"))
    allowed = {"state", "inject", "apply_request", "verify", "oracle", "timing"}
    if not set(entries) <= allowed or "state" not in entries:
        raise ContractError("demo_fault_artifact_manifest_required")
    docs: dict[str, dict[str, Json]] = {}
    for label, item in entries.items():
        entry = object_json(item)
        name, sha = entry.get("path"), entry.get("sha256")
        if not isinstance(name, str) or not isinstance(sha, str):
            raise ContractError("demo_artifact_binding_required")
        member = Path(name)
        if member.is_absolute() or ".." in member.parts or not member.parts:
            raise ContractError("demo_artifact_path_refused")
        docs[label], digest = load(path.parent / member, sha)
        view.sources[label] = digest
    state = docs["state"]
    spec = parse_spec(state.get("spec"))
    snapshot_id = state.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise ContractError("demo_snapshot_binding_required")
    view.identity(spec.run_id, spec.fault)
    view.add(
        "Fault / run", FAULT_NAMES[spec.fault] + " / " + spec.run_id, "state:/spec/fault,run_id"
    )
    injected = docs.get("inject", {})
    if injected.get("fault_effective") is True and injected.get("target_online") is not False:
        raise ContractError("demo_fault_measurement_mismatch")
    view.add(
        "Pinned target failed during fault",
        status(docs.get("inject", {}).get("fault_effective")),
        "inject:/fault_effective",
    )
    request = docs.get("apply_request")
    if request is not None:
        if request.get("operation") != "apply" or request.get("spec") != spec.document():
            raise ContractError("demo_action_run_mismatch")
        action = parse_action(
            object_json(request.get("payload")).get("action"), spec, str(state.get("snapshot_id"))
        )
        view.add(
            "Requested action",
            f"{spec.tool}; FORCED single allowed repair; cites {citations(action['evidence_ids'])}",
            "apply_request:/payload/action",
        )
    else:
        view.add("Requested action", UNKNOWN, "R:/files/apply_request (absent)")
    view.oracle(docs.get("oracle"), "oracle:")
    verify = docs.get("verify", {})
    diff = object_json(verify.get("exact_diff", {}))
    if verify.get("verified") is True:
        fields = object_json(diff.get("fields", {}))
        if (
            state.get("lifecycle") != "verified"
            or state.get("reason") != "typed_agent_snapshot_repair"
            or diff.get("before_sha256") != state.get("snapshot_id")
            or diff.get("before_sha256") != diff.get("after_sha256")
            or diff.get("match") is not True
            or not fields
            or any(v is not True for v in fields.values())
            or verify.get("target_online") is not True
        ):
            raise ContractError("demo_verification_state_mismatch")
    view.add("Worker verification recorded", status(verify.get("verified")), "verify:/verified")
    view.add("Exact snapshot diff", status(diff.get("match")), "verify:/exact_diff/match")
    view.add("Pinned target restored", status(verify.get("target_online")), "verify:/target_online")
    timing = docs.get("timing", {})
    elapsed = UNKNOWN
    if timing:
        if (
            timing.get("run_id") != spec.run_id
            or timing.get("clock_basis") != "posix-monotonic-v1"
            or not isinstance(timing.get("boot_id"), str)
            or not timing.get("boot_id")
            or timing.get("boot_id") != state.get("boot_id")
            or timing.get("timing_unproven") is not False
        ):
            raise ContractError("demo_timing_binding_required")
        start, end = number(timing.get("injected_mono")), number(timing.get("restored_mono"))
        if start is None or end is None or end < start:
            raise ContractError("demo_timing_interval_required")
        elapsed = f"{end - start:.3f} s (injection to exact restoration)"
    view.add(
        "Revert time",
        elapsed,
        "timing:/injected_mono,restored_mono" if timing else "R:/files/timing (absent)",
    )
    view.notes.append(
        "Single-destination failure is separate from the general ONLINE verdict. "
        "This legacy manifest records a forced worker request, not an open-model decision. "
        "An apply request alone does not prove execution or recovery."
    )


def model_faults(path: Path, view: Transcript) -> None:
    from offline_agent.uplink.faults_v1_model import CHOICES, VERSION, parse

    row = view.receipt
    if row.get("model_contract") != VERSION or row.get("action_mode") != "open":
        raise ContractError("demo_newfault_model_contract_required")
    view.identity(row.get("run_id"), row.get("fault"))
    files = object_json(row.get("artifact_sha256"))
    if not files or len(files) > 64:
        raise ContractError("demo_bounded_artifact_index_required")
    docs: dict[str, dict[str, Json]] = {}
    for name, expected in files.items():
        if not re.fullmatch(r"[a-z0-9-]+\.json", name) or not isinstance(expected, str):
            raise ContractError("demo_artifact_binding_required")
        docs[name], sha = load(path.parent / name, expected)
        view.sources[name] = sha
    view.add(
        "Evidence class", enum(row.get("evidence_kind"), {"MOCK", "lab_wifi"}), "R:/evidence_kind"
    )
    view.add("Fault / run", FAULT_NAMES[view.fault] + " / " + view.run_id, "R:/fault,run_id")
    measured = docs.get("measure-1.json", {})
    for label, field in (
        ("Pinned target reachable during fault", "target_online"),
        ("Host-route drift observed", "route_changed"),
        ("PF-anchor drift observed", "pf_changed"),
    ):
        view.add(label, status(measured.get(field)), "measure-1.json:/" + field)
    snapshot = docs.get("prepare.json", {}).get("snapshot_id")
    decisions = row.get("decisions")
    if not isinstance(decisions, list) or len(decisions) > 4:
        raise ContractError("demo_decision_list_required")
    for index, item in enumerate(decisions, 1):
        decision = object_json(item)
        name = f"model-{index}.json"
        emission = docs.get(name, {})
        if (
            not isinstance(snapshot, str)
            or not isinstance(emission.get("text"), str)
            or emission.get("action_mode") != "open"
            or emission.get("admitted_tools") != list(CHOICES)
        ):
            raise ContractError("demo_open_emission_required")
        action = parse(str(emission["text"]), snapshot)
        if decision.get("action") != action or emission.get("action") != action:
            raise ContractError("demo_action_emission_mismatch")
        view.add(
            "Chosen action",
            str(action["tool"]) + "; OPEN choice; admission " + status(decision.get("accepted")),
            name + ":/text,action; R:/decisions/" + str(index - 1),
        )
    view.oracle(docs.get("verify-oracle.json"), "verify-oracle.json:")
    root = docs.get("root-receipt.json", {})
    state = object_json(root.get("state", {}))
    if state and object_json(state.get("spec")).get("run_id") != view.run_id:
        raise ContractError("demo_root_run_mismatch")
    timing = object_json(root.get("timing", {}))
    elapsed = UNKNOWN
    if timing.get("timing_unproven") is False:
        start, end = number(timing.get("injected_mono")), number(timing.get("restored_mono"))
        if (
            timing.get("run_id") != view.run_id
            or timing.get("boot_id") != state.get("boot_id")
            or timing.get("clock_basis") != "posix-monotonic-v1"
            or start is None
            or end is None
            or end < start
        ):
            raise ContractError("demo_timing_binding_required")
        elapsed = f"{end - start:.3f} s (injection to exact restoration)"
    view.add("Revert time", elapsed, "root-receipt.json:/timing")
    view.add(
        "Exact snapshot diff",
        status(object_json(root.get("exact_diff", {})).get("match")),
        "root-receipt.json:/exact_diff/match",
    )
    view.add("Handback verified", status(row.get("cleanup_verified")), "R:/cleanup_verified")
    view.add("Trial passed", status(row.get("trial_passed")), "R:/trial_passed")
    view.add(
        "Outcome",
        enum(row.get("outcome"), {"recovered", "failed", "gave_up_restored"}),
        "R:/outcome",
    )
    view.notes.append(
        "Open choice is recorded separately from action admission and verified recovery. "
        "A safety rollback or give_up does not earn model repair credit."
    )


def transcript(path: Path, expected: str) -> str:
    receipt, digest = load(path, expected)
    if type(receipt.get("version")) is not int or receipt["version"] != 1:
        raise ContractError("demo_receipt_version_required")
    view = Transcript(receipt, digest)
    kind = receipt.get("kind")
    if kind == "uplink-real-eval":
        v5(path, view)
    elif kind == "uplink-faults-v1-demo-input":
        faults(path, view)
    elif kind == "uplink-newfault-model-trial":
        model_faults(path, view)
    else:
        raise ContractError("demo_supported_receipt_required")
    load(path, digest)  # Refuse concurrent replacement of the input while rendering.
    return view.render()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rendered = transcript(args.receipt, args.sha256)
        if args.output is None:
            print(rendered, end="")
        else:
            if args.output.suffix != ".md" or any(
                p.is_symlink() for p in (args.output, *args.output.parents)
            ):
                raise ContractError("demo_new_markdown_output_required")
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Input values, OS diagnostics and private paths must not reach the video/log.
        reason = str(exc)
        if not isinstance(exc, ContractError) or not re.fullmatch(
            r"(?:demo|score)_[a-z_]+", reason
        ):
            reason = type(exc).__name__
        parser.exit(2, f"demo_receipt_refused:{reason}\n")


if __name__ == "__main__":
    main()
