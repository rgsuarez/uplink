"""One out-of-band SSH route, no fallback and no retry. JSON travels on stdin, never a shell."""

import json
import math
import os
import re
import shlex
import subprocess
import time
from typing import cast

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.contracts import Scope
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.lab_runner import Emission
from offline_agent.uplink.layer_policy import ModelRecord, read_model_records
from offline_agent.uplink.model_identity import validate_identity
from offline_agent.uplink.oracle import ORACLE_VERSION, Evidence, OracleResult, Verdict
from offline_agent.uplink.real_fleet import APPROVED_FAULTS, approved_serial, validate_authorization
from offline_agent.uplink.real_protocol import RealSpec, request
from offline_agent.uplink.real_runner import OOBLost, RevertFailed
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.remote_settings import RemoteTargets


def bridge_command() -> str:
    return load_config().bridge_command


def wait_for_receipt_clock(value: Json) -> None:
    """Wait out at most one second of clock skew, without relaxing freshness checks."""
    if type(value) in (int, float) and math.isfinite(cast(float, value)):
        delay = float(cast(float, value)) - time.time()
        if 0 < delay <= 1:
            time.sleep(delay)


def parse_oracle(value: Json) -> OracleResult:
    raw = object_json(value)
    if (
        set(raw) != {"version", "verdict", "checked_at", "evidence"}
        or raw["version"] != ORACLE_VERSION
    ):
        raise ContractError("unrecognized_oracle_profile")
    checked = raw["checked_at"]
    if type(checked) not in (int, float) or not math.isfinite(cast(float, checked)):
        raise ContractError("invalid_oracle_time")
    try:
        verdict = Verdict(str(raw["verdict"]))
    except ValueError as exc:
        raise ContractError("invalid_oracle_verdict") from exc
    expected = {
        "dns-1": "dns",
        "dns-2": "dns",
        "tls-apple": "tls",
        "tls-google": "tls",
        "portal-apple": "portal",
        "portal-google": "portal",
    }
    values = raw["evidence"]
    if not isinstance(values, list) or len(values) != len(expected):
        raise ContractError("incomplete_oracle_evidence")
    evidence = []
    seen = set()
    for value in values:
        row = object_json(value)
        if set(row) != {
            "id",
            "kind",
            "ok",
            "reason",
            "path_id",
            "latency_ms",
            "target_id",
            "status",
            "proxy_used",
        }:
            raise ContractError("invalid_oracle_evidence_fields")
        name, kind, reason = row["id"], row["kind"], row["reason"]
        latency = row["latency_ms"]
        if (
            not isinstance(name, str)
            or name in seen
            or expected.get(name) != kind
            or not isinstance(kind, str)
            or not isinstance(reason, str)
            or type(row["ok"]) is not bool
        ):
            raise ContractError("invalid_oracle_evidence_identity")
        if (
            type(latency) not in (int, float)
            or not math.isfinite(cast(float, latency))
            or cast(float, latency) < 0
        ):
            raise ContractError("invalid_oracle_latency")
        if (
            any(
                row[k] is not None and not isinstance(row[k], str)
                for k in ("path_id", "target_id", "proxy_used")
            )
            or row["status"] is not None
            and type(row["status"]) is not int
        ):
            raise ContractError("invalid_oracle_evidence_values")
        seen.add(name)
        evidence.append(
            Evidence(
                name,
                kind,
                row["ok"] is True,
                reason,
                cast(str | None, row["path_id"]),
                float(cast(float, latency)),
                cast(str | None, row["target_id"]),
                row["status"],
                cast(str | None, row["proxy_used"]),
            )
        )
    result = OracleResult(verdict, float(cast(float, checked)), tuple(evidence))
    wait_for_receipt_clock(checked)
    if verdict == Verdict.ONLINE and not result.fresh_online(time.time()):
        raise ContractError("online_verdict_lacks_fresh_complete_evidence")
    return result


class LabBackend:
    dry = False

    def __init__(
        self,
        spec: RealSpec,
        *,
        execute: bool = False,
        authorization: dict[str, Json] | None = None,
        targets: RemoteTargets | None = None,
        action_mode: str = "forced",
        serving: dict[str, Json] | None = None,
    ) -> None:
        if (
            action_mode not in {"forced", "open"}
            or action_mode == "open"
            and not spec.probe_scripts
        ):
            raise ContractError("invalid_probe_action_mode")
        from offline_agent.uplink.probe_serving import binding

        self.action_mode = action_mode
        self.serving = binding(serving) if serving is not None else None
        self.serving_attestation: dict[str, Json] | None = None
        self.spec, self.execute = spec, execute
        self.authorization = authorization
        self.targets = targets or RemoteTargets.from_environment()
        self.last_model_records: list[ModelRecord] = []
        self.last_model_request_count: int | None = 0
        self.last_layer_probe: RawObservation | None = None
        self.lost = False
        self.last_probe_record: RawObservation | None = None
        self.last_wifi_ssh_record: RawObservation | None = None

    def guard(self) -> None:
        if not self.execute or "PYTEST_CURRENT_TEST" in os.environ:
            raise ContractError("live_transport_requires_explicit_execution_outside_tests")
        if self.lost:
            raise OOBLost("oob_lost_no_retry")
        if self.spec.fault not in APPROVED_FAULTS:
            raise ContractError("managed_exception_dns_proxy_only")
        try:
            validate_authorization(self.authorization)
        except ValueError as exc:
            raise ContractError("fleet_clearance_required_before_fault") from exc

    def oob(self) -> None:
        self.guard()
        try:
            result = subprocess.run(
                (*self.targets.oob_ssh, "true"), capture_output=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.lost = True
            raise OOBLost("oob_lost_no_retry") from exc
        if result.returncode:
            self.lost = True
            raise OOBLost("oob_lost_no_retry")

    def rpc(
        self, value: dict[str, Json], *, timeout: float = 22, argv: tuple[str, ...] | None = None
    ) -> dict[str, Json]:
        self.oob()
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv or (*self.targets.oob_ssh, bridge_command()),
                input="" if argv else json.dumps(value),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.lost = True
            raise OOBLost("oob_result_indeterminate_no_retry") from exc
        if result.returncode == 255:
            self.lost = True
            raise OOBLost("oob_lost_no_retry")
        try:
            reply = object_json(read_json(result.stdout, max_bytes=131072))
        except (ContractError, ValueError) as exc:
            self.lost = True
            raise OOBLost("oob_result_indeterminate_no_retry") from exc
        if reply.get("ok") is not True or result.returncode:
            reason = reply.get("error")
            safe = (
                reason
                if isinstance(reason, str) and re.fullmatch(r"[a-zA-Z0-9_]+", reason)
                else "remote_operation_refused"
            )
            if safe in {"restore_failed_no_retry", "rollback_already_attempted_no_retry"}:
                raise RevertFailed(safe)
            raise ContractError(safe)
        answer = object_json(reply.get("result"))
        if value.get("bridge") == "root":
            answer["control_latency_ms"] = (time.monotonic() - started) * 1000
        return answer

    def identity(self) -> dict[str, Json]:
        record = self.rpc(
            {"bridge": "attest", **({"serving": self.serving} if self.serving else {})},
            timeout=120,
        )
        if self.serving:
            self.serving_attestation = object_json(record["serving"])
            record = object_json(record["identity"])
        wait_for_receipt_clock(record.get("verified_at"))
        return validate_identity(record, live=True)

    def command(self, op: str, payload: dict[str, object] | None = None) -> tuple[str, ...]:
        if op == "prepare" and self.spec.probe_scripts:
            payload = {**(payload or {}), "probe_scripts": True}
        if op == "prepare" and self.authorization is not None:
            payload = {**(payload or {}), "authorization": self.authorization}
        value = request(op, self.spec.run_id, payload)
        # The only shell argument is generated from the closed root-operation schema.
        return (
            *self.targets.oob_ssh,
            bridge_command() + " --root-request " + shlex.quote(json.dumps(value, sort_keys=True)),
        )

    def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
        if op == "prepare" and self.spec.probe_scripts:
            payload = {**(payload or {}), "probe_scripts": True}
        if op == "prepare" and self.authorization is not None:
            payload = {**(payload or {}), "authorization": self.authorization}
        value = request(op, self.spec.run_id, payload)
        return self.rpc(
            {"bridge": "root", "request": cast(dict[str, Json], value)},
            argv=self.command(op, payload),
        )

    def wifi_ssh(self, interface: str) -> bool:
        if not re.fullmatch(r"en[0-9]+", interface) or interface == "en7":
            raise ContractError("wifi_ssh_interface_refused")
        self.oob()
        code = (
            "import json,os,subprocess; from pathlib import Path; "
            f"i=subprocess.run([str(Path.home()/{load_config().source_relative!r}/"
            "'src/offline_agent/uplink/uplink-network'),'identity'],"
            "capture_output=True,text=True,timeout=2); "
            f"r=subprocess.run(['/usr/sbin/ipconfig','getifaddr',{interface!r}],"
            "capture_output=True,text=True,timeout=2); "
            "c=os.environ.get('SSH_CONNECTION','').split(); "
            f"s=i.returncode==0 and json.loads(i.stdout).get('serial')=={approved_serial()!r}; "
            "print(json.dumps({'wifi_ssh':s and r.returncode==0 and len(c)==4 "
            "and bool(r.stdout.strip()) and c[2]==r.stdout.strip()}))"
        )
        command = (
            *self.targets.wifi_ssh,
            "python3",
            "-c",
            shlex.quote(code),
        )
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=7, check=False)
        except (OSError, subprocess.SubprocessError):
            self.last_wifi_ssh_record = RawObservation(
                command,
                stderr="normal_wifi_ssh_unavailable",
                duration_ms=(time.monotonic() - started) * 1000,
                source="normal_wifi_ssh",
            )
            return False
        self.last_wifi_ssh_record = RawObservation(
            command,
            result.stdout,
            result.stderr,
            result.returncode,
            (time.monotonic() - started) * 1000,
            source="normal_wifi_ssh",
        )
        try:
            value = object_json(read_json(result.stdout))
        except (ContractError, ValueError):
            return False
        return result.returncode == 0 and value == {"wifi_ssh": True}

    def probe(self) -> OracleResult:
        started = time.monotonic()
        value = self.rpc({"bridge": "probe"}, timeout=15)
        observations = value.get("observations")
        if (
            set(value) != {"oracle", "observations"}
            or not isinstance(observations, list)
            or not 2 <= len(observations) <= 6
        ):
            raise ContractError("invalid_probe_receipt")
        for item in observations:
            row = object_json(item)
            if set(row) != {
                "argv",
                "display",
                "stdout",
                "stderr",
                "exit_code",
                "duration_ms",
                "source",
            }:
                raise ContractError("invalid_probe_observation")
            args = row["argv"]
            elapsed = row["duration_ms"]
            if (
                not isinstance(args, list)
                or not all(isinstance(x, str) for x in args)
                or any(
                    not isinstance(row[k], str) for k in ("display", "stdout", "stderr", "source")
                )
                or row["exit_code"] is not None
                and type(row["exit_code"]) is not int
            ):
                raise ContractError("invalid_probe_observation")
            if (
                type(elapsed) not in (int, float)
                or not math.isfinite(cast(float, elapsed))
                or cast(float, elapsed) < 0
            ):
                raise ContractError("invalid_probe_observation_latency")
        result = parse_oracle(value["oracle"])
        self.last_probe_record = RawObservation(
            (*self.targets.oob_ssh, bridge_command()),
            json.dumps(observations),
            exit_code=0,
            duration_ms=(time.monotonic() - started) * 1000,
            source="wifi_probes",
        )
        return result

    def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
        self.last_model_records = []
        self.last_model_request_count = None
        self.last_layer_probe = None
        extra: dict[str, Json] = (
            {"probe_scripts": True, "run_id": self.spec.run_id} if self.spec.probe_scripts else {}
        )
        if self.action_mode == "open":
            extra["action_mode"] = self.action_mode
        if self.serving is not None:
            extra["serving"] = self.serving
        value = self.rpc(
            {
                **extra,
                "bridge": "choose",
                "scope": {
                    "services": list(scope.services),
                    "snapshots": list(scope.snapshots),
                    "evidence": list(scope.evidence),
                    "profile": scope.profile,
                },
                "observed": observed,
                "oracle": oracle.as_json(),
            },
            timeout=52 if self.spec.probe_scripts else 35,
        )
        expected = {"text", "timings", "records", "error", "layer_probes"}
        if set(value) != expected | ({"raw_text"} if self.spec.probe_scripts else set()):
            raise ContractError("invalid_model_reply")
        self.last_layer_probe = RawObservation(
            (),
            json.dumps(object_json(value["layer_probes"])),
            exit_code=0,
            source="generated_probe_episode" if self.spec.probe_scripts else "wifi_layer_probes",
        )
        self.last_model_records = (
            []
            if value["records"] == [] and isinstance(value["error"], str)
            else read_model_records(value["records"])
        )
        self.last_model_request_count = len(self.last_model_records)
        if value["error"] is not None:
            reason = value["error"]
            if not isinstance(reason, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", reason):
                reason = "remote_model_failed"
            if reason in {"probe_settings_changed_no_retry", "probe_wifi_route_changed_no_retry"}:
                raise RevertFailed(reason)
            raise ContractError(reason)
        if not isinstance(value["text"], str):
            raise ContractError("invalid_model_reply")
        raw_text = value.get("raw_text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise ContractError("invalid_raw_model_text")
        return Emission(value["text"], object_json(value["timings"]), raw_text=raw_text)
