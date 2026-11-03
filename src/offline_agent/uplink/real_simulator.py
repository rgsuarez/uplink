"""Synthetic recovery fixtures. No subprocess, SSH, network request or model call."""

import hashlib
import json
import time

from offline_agent.contracts import Json
from offline_agent.uplink.contracts import Action, Scope, Tool
from offline_agent.uplink.lab_runner import Emission
from offline_agent.uplink.layer_policy import ModelRecord
from offline_agent.uplink.model_identity import expected_model
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.real_protocol import RealSpec, request
from offline_agent.uplink.records import RawObservation
from offline_agent.uplink.remote_settings import RemoteTargets


class SimulatedLab:
    dry = True
    last_probe_record: RawObservation | None = None
    last_wifi_ssh_record: RawObservation | None = None
    last_layer_probe: RawObservation | None = None
    authorization: dict[str, Json] | None = None

    def __init__(self, spec: RealSpec) -> None:
        self.spec = spec
        self.targets = RemoteTargets.from_environment()
        self.last_model_records: list[ModelRecord] = []
        self.last_model_request_count: int | None = 0
        self.calls: list[str] = []
        self.dns_changed = self.proxy_changed = False
        self.wifi_up = True
        self.watchdog = "unarmed"
        self.deadline = 0.0

    def oob(self) -> None:
        self.calls.append("oob")

    def identity(self) -> dict[str, Json]:
        identity = expected_model(provider="mock", active_name="synthetic-policy")
        identity.update(verification="fixture", host_chip="synthetic-fixture")
        return identity

    def wifi_ssh(self, interface: str) -> bool:
        self.calls.append("wifi_ssh")
        return self.wifi_up

    def command(self, op: str, payload: dict[str, object] | None = None) -> tuple[str, ...]:
        if op == "prepare" and self.spec.probe_scripts:
            payload = {**(payload or {}), "probe_scripts": True}
        request(op, self.spec.run_id, payload)
        from offline_agent.uplink.real_transport import LabBackend

        return LabBackend(self.spec).command(op, payload)

    def observation(self) -> dict[str, Json]:
        return {
            "wifi_up": self.wifi_up,
            "wifi_interface": "en0",
            "active_interface": "en0" if self.wifi_up else "en7",
            "interfaces": [{"name": "en0", "up": self.wifi_up}, {"name": "en7", "up": True}],
            "wifi_default_route": self.wifi_up,
            "config": {
                "dns": hashlib.sha256(str(self.dns_changed).encode()).hexdigest(),
                "proxy": hashlib.sha256(str(self.proxy_changed).encode()).hexdigest(),
                "wifi_power": self.wifi_up,
            },
            "dns_changed": self.dns_changed,
            "proxy_changed": self.proxy_changed,
            "baseline_equal": self.wifi_up and not self.dns_changed and not self.proxy_changed,
            "protected_services_unchanged": True,
            "watchdog": self.watchdog,
            "watchdog_ready": self.watchdog == "armed",
            "deadline": self.deadline,
            "fleet_diff_match": self.wifi_up and not self.dns_changed and not self.proxy_changed,
            "restore_duration_ms": 1.0,
            "baseline_restored_after_s": 1.0,
        }

    def call(self, op: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
        if op == "prepare" and self.spec.probe_scripts:
            payload = {**(payload or {}), "probe_scripts": True}
        request(op, self.spec.run_id, payload)
        self.oob()
        self.calls.append(op)
        if op == "retire":
            return {"job_retired": True, "retired_count": 1, "registered_jobs": 0}
        if op == "arm":
            self.watchdog = "armed"
            self.deadline = time.time() + self.spec.seconds
        elif op == "inject":
            self.dns_changed = self.spec.fault in {"wrong_dns", "dns_and_proxy"}
            self.proxy_changed = self.spec.fault in {"stale_proxy", "dns_and_proxy"}
            self.wifi_up = self.spec.fault != "wifi_power_off"
        elif op == "apply":
            tool = (payload or {}).get("tool")
            if tool == "set_dns":
                self.dns_changed = False
            elif tool == "restore_proxy":
                self.proxy_changed = False
            elif tool == "cycle_interface":
                self.wifi_up = True
        elif op == "disarm":
            self.watchdog = "disarmed"
        elif op == "restore":
            self.dns_changed = self.proxy_changed = False
            self.wifi_up = True
            self.watchdog = "reverted"
        return self.observation()

    def probe(self) -> OracleResult:
        self.oob()
        self.calls.append("probe")
        self.last_probe_record = RawObservation(
            ("MOCK", "probe_online"),
            "synthetic probe receipts",
            exit_code=0,
            duration_ms=6,
            source="mock_oracle",
        )
        rows = []
        for name, kind in (
            ("dns-1", "dns"),
            ("dns-2", "dns"),
            ("tls-apple", "tls"),
            ("tls-google", "tls"),
            ("portal-apple", "portal"),
            ("portal-google", "portal"),
        ):
            ok = self.wifi_up and not self.dns_changed and (kind == "dns" or not self.proxy_changed)
            rows.append(
                Evidence(
                    name,
                    kind,
                    ok,
                    "synthetic",
                    "path-fixture",
                    latency_ms=1,
                    target_id="host-fixture",
                    proxy_used="direct",
                    status=204,
                )
            )
        verdict = (
            Verdict.ONLINE
            if all(r.ok for r in rows)
            else Verdict.DEGRADED
            if any(r.ok for r in rows)
            else Verdict.OFFLINE
        )
        return OracleResult(verdict, time.time(), tuple(rows))

    def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
        self.calls.append("choose")
        tool = (
            Tool.SET_DNS
            if self.dns_changed
            else Tool.RESTORE_PROXY
            if self.proxy_changed
            else Tool.CYCLE_INTERFACE
        )
        args: dict[str, Json] = {
            "service_id": scope.services[0],
            "snapshot_id": scope.snapshots[0],
            "why": "snapshot_drift",
            "evidence_ids": ["dns-1"],
        }
        if tool != Tool.CYCLE_INTERFACE:
            args["policy"] = "snapshot"
        return Emission(
            json.dumps(Action(tool, args).as_json()),
            {
                "prompt_n": 5,
                "predicted_n": 2,
                "cached_n": 0,
                "prompt_ms": 1,
                "predicted_ms": 1,
                "prompt_per_second": 5000,
                "predicted_per_second": 2000,
            },
        )
