"""Deterministic no-effect lab transport for dry runs. Never presented as live evidence."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import time
from collections.abc import Sequence

from offline_agent.contracts import Json
from offline_agent.providers import Message
from offline_agent.uplink.contracts import Scope
from offline_agent.uplink.lab_protocol import (
    ALTERNATE_GW,
    DEAD_DNS,
    DNS_NAMES,
    LAB_VERSION,
    PRIMARY_GW,
    PRIMARY_IP,
    PROXY_URL,
    SERVER_IP,
    LabSpec,
    request,
)
from offline_agent.uplink.lab_runner import Emission, LabState, ScriptedPolicy
from offline_agent.uplink.oracle import OracleResult


class SimulatedClient:
    def __init__(self, spec: LabSpec) -> None:
        self.spec = spec
        self.calls: list[dict[str, object]] = []
        self.created = False
        self.restore()

    def restore(self) -> None:
        self.dns = PRIMARY_GW
        self.proxy = PROXY_URL
        self.up = True
        self.address = True
        self.gateway = PRIMARY_GW
        self.upstream = True
        self.stub = "healthy"
        self.mtu = False
        self.timer: dict[str, Json] | None = None

    def command(self, operation: str, payload: dict[str, object] | None = None) -> tuple[str, ...]:
        import json

        return (
            "python3",
            "lab_worker.py",
            "--execute",
            "--request",
            json.dumps(request(operation, self.spec.run_id, payload), separators=(",", ":")),
        )

    def call(self, operation: str, payload: dict[str, object] | None = None) -> dict[str, Json]:
        self.calls.append(request(operation, self.spec.run_id, payload))
        payload = payload or {}
        if operation == "up":
            if self.created:
                raise ValueError("fixture_collision")
            self.created = True
        if not self.created:
            raise ValueError("fixture_not_created")
        if operation == "arm":
            self.timer = {"state": "armed", "deadline": time.time() + 20}
            return self.timer
        if operation == "disarm":
            self.timer = {"state": "cleared", "reason": "verified"}
        if operation == "inject":
            fault = payload["fault"]
            if fault in {"wrong_dns", "dns_plus_proxy"}:
                self.dns = DEAD_DNS
            if fault in {"stale_proxy", "dns_plus_proxy"}:
                self.proxy = _public_fixture("http://@PUBLIC_IPV4_086@:9")
            if fault == "lease_loss":
                self.address = False
            if fault == "interface_down":
                self.up = False
            if fault == "bad_default_route":
                self.gateway = _public_fixture("@PUBLIC_IPV4_084@")
            if fault == "isp_down_saved_alternate":
                self.upstream = False
            if fault == "captive_portal":
                self.stub = "captive"
            if fault == "site_only_outage":
                self.stub = "site_down"
            if fault == "mtu_black_hole":
                self.mtu = True
        if operation == "apply":
            tool, policy = payload["tool"], payload["policy"]
            if tool == "set_dns":
                self.dns = PRIMARY_GW
            if tool == "restore_proxy":
                self.proxy = "" if policy == "off" else PROXY_URL
            if tool == "renew_lease":
                self.address = True
            if tool == "cycle_interface":
                self.up = True
            if tool == "reroute":
                self.gateway = self.dns = ALTERNATE_GW if policy == "alternate" else PRIMARY_GW
        if operation == "undo":
            self.restore()
        if operation == "down":
            self.created = False
        if operation == "observe":
            c0, _, c1, _ = self.spec.interfaces
            return {
                "addresses": [
                    {
                        "ifname": c0,
                        "flags": ["UP"] if self.up else [],
                        "addr_info": [{"local": PRIMARY_IP}] if self.address else [],
                    },
                    {"ifname": c1, "flags": ["UP"], "addr_info": [{"local": _public_fixture("@PUBLIC_IPV4_072@")}]},
                ],
                "routes": [
                    {
                        "dst": "default",
                        "gateway": self.gateway,
                        "dev": c1 if self.gateway == ALTERNATE_GW else c0,
                    }
                ],
                "resolver": f"nameserver {self.dns}\n",
                "proxy": self.proxy,
                "prior_alternate_online": False,
                "timer": self.timer,
                "profile": LAB_VERSION,
            }
        alternate = self.gateway == ALTERNATE_GW
        connected = (
            alternate or self.up and self.address and self.upstream and self.gateway == PRIMARY_GW
        )
        if operation == "layer_probe":

            def dns_rows(path: str, resolver: str, ok: bool) -> list[Json]:
                return [
                    {
                        "id": f"dns-{path}-{i + 1}",
                        "target": name,
                        "resolver": resolver,
                        "available": True,
                        "ok": ok,
                        "answers": [SERVER_IP] if ok else [],
                        "latency_ms": 1.0,
                        "reason": "observed" if ok else "expected_dns_answer_missing",
                    }
                    for i, name in enumerate(DNS_NAMES)
                ]

            def https_rows(path: str, ok: bool) -> list[Json]:
                return [
                    {
                        "id": f"https-{path}-{i + 1}",
                        "target": name,
                        "path": "direct_fixture_address"
                        if path == "direct"
                        else "configured_proxy",
                        "available": True,
                        "ok": ok,
                        "status": 204 if ok else 0,
                        "tls_verified": ok,
                        "latency_ms": 1.0,
                        "reason": "observed" if ok else "https_probe_failed",
                    }
                    for i, name in enumerate(DNS_NAMES)
                ]

            direct_ok = connected and not self.mtu
            proxy_ok = direct_ok and self.proxy in (PROXY_URL, "")
            if self.proxy == "":
                proxy_ok = proxy_ok and self.dns != DEAD_DNS
            gateway_ok = alternate or self.up and self.address and self.gateway == PRIMARY_GW
            return {
                "version": 1,
                "kind": "uplink-layer-evidence",
                "link_up": self.up,
                "lease_present": self.address,
                "default_gateway": self.gateway,
                "gateway_ping": {
                    "available": True,
                    "ok": gateway_ok,
                    "latency_ms": 1.0,
                    "reason": "observed" if gateway_ok else "probe_failed",
                },
                "dns_configured": dns_rows(
                    "configured",
                    self.dns,
                    self.dns != DEAD_DNS and (self.up and self.address or self.dns == ALTERNATE_GW),
                ),
                "dns_alternate": dns_rows("alternate", ALTERNATE_GW, True),
                "https_direct": https_rows("direct", direct_ok),
                "https_proxy": https_rows("proxy", proxy_ok),
                "elapsed_ms": 2.0,
            }
        if operation == "resolve":
            return {"ok": connected and self.dns != DEAD_DNS, "latency_ms": 1.0}
        if operation == "fetch":
            target = int(str(payload["target"]))
            good = connected and self.proxy in (PROXY_URL, "") and (target >= 2 or not self.mtu)
            status = (
                (
                    200
                    if self.stub == "captive" and target in (2, 3)
                    else 503
                    if self.stub == "site_down" and target == 4
                    else 204
                )
                if good
                else 0
            )
            return {
                "ok": status == 204,
                "status": status,
                "body_matches": status == 204,
                "tls_verified": status > 0 and target in (0, 1, 4),
                "proxy": self.proxy,
                "latency_ms": 2.0,
                "error": None if status else "fixture_transport_failed",
            }
        return {"ok": True}


class SimulatedPolicy(ScriptedPolicy):
    name = "uplink"

    def choose(
        self, scope: Scope, observed: LabState, oracle: OracleResult, history: Sequence[Message]
    ) -> Emission:
        # Same deterministic policy only to exercise controller wiring, never model performance.
        selected = super().choose(scope, observed, oracle, history)
        return Emission(selected.text, {})
