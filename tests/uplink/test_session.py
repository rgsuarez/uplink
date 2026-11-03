
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.baseline import next_action
from offline_agent.uplink.contracts import Scope, Tool
from offline_agent.uplink.diagnostics import Inspection
from offline_agent.uplink.oracle import (
    DNS_NAMES,
    TLS_ENDPOINTS,
    Endpoint,
    Fetch,
    OnlineOracle,
    OracleResult,
    ProxyConfig,
    ProxyRoute,
    Verdict,
)
from offline_agent.uplink.plans import ConfirmCard
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.session import Session
from offline_agent.uplink.snapshots import SnapshotStore


class HealthyTransport:
    def resolve(self, name: str, timeout: float) -> bool:
        assert name in DNS_NAMES
        return True

    def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch:
        return Fetch(route.id, endpoint.status, True, endpoint in TLS_ENDPOINTS)


def command(tool: str, **args: Json) -> str:
    return json.dumps({"version": 1, "tool": tool, "args": args})


def test_session_dispatches_diagnostics_and_oracle_gates_declaration(
    inspection: Inspection, redactor: Redactor
) -> None:
    session = Session(
        inspection.vitals, OnlineOracle(HealthyTransport(), ProxyConfig({}, {}), redactor), redactor
    )
    with pytest.raises(ContractError, match="fresh_latest"):
        session.dispatch(command("declare_online"))
    for tool in ("read_vitals", "read_dns", "read_proxy", "read_routes", "list_route_candidates"):
        assert session.dispatch(command(tool)).data
    assert session.dispatch(command("probe_online")).data["verdict"] == "ONLINE"
    assert session.dispatch(command("declare_online")).data["verdict"] == "ONLINE"
    assert redactor.scan(json.dumps(session.trace))["passed"]
    assert session.oracle.latest
    session.oracle.latest = replace(session.oracle.latest, checked_at=time.time() - 31)
    with pytest.raises(ContractError):
        session.dispatch(command("declare_online"))


def test_confirmation_trace_redacts_all_argv(inspection: Inspection, redactor: Redactor) -> None:
    card = ConfirmCard(
        "set_dns",
        (("/usr/sbin/networksetup", "-setdnsservers", "Fictional Network", _public_fixture("@PUBLIC_IPV4_003@")),),
        "dns_failed",
        ("dns-1",),
        (("restore", "Fictional Network"),),
        "snapshot-a",
    )
    assert card.for_confirmation()["argv"]
    trace = json.dumps(card.for_trace(redactor))
    assert "Fictional" not in trace and "networksetup" not in trace
    assert "Fictional" not in repr(card)


def test_baseline_healthy_is_noop_and_compound_is_one_change_at_a_time(
    inspection: Inspection, redactor: Redactor, tmp_path: Path
) -> None:
    oracle = OnlineOracle(HealthyTransport(), ProxyConfig({}, {}), redactor)
    healthy = oracle.probe()
    saved = SnapshotStore(tmp_path).save(inspection.vitals, healthy)
    scope = Scope(tuple(s.id for s in inspection.vitals.services), (), (saved.id,), ("dns-1",))
    assert (
        next_action(
            inspection.vitals,
            healthy,
            scope,
            now=time.time(),
            last_good=saved,
            prechange_snapshot_id=saved.id,
        ).tool
        == Tool.DECLARE_ONLINE
    )
    service = inspection.vitals.services[0]
    changed = replace(
        service,
        dns=(_public_fixture("@PUBLIC_IPV4_089@"),),
        proxies=tuple(replace(p, enabled=False) for p in service.proxies),
    )
    vitals = replace(inspection.vitals, services=(changed, *inspection.vitals.services[1:]))
    failed = OracleResult(Verdict.DEGRADED, time.time(), ())
    assert (
        next_action(
            vitals, failed, scope, now=time.time(), last_good=saved, prechange_snapshot_id=saved.id
        ).tool
        == Tool.SET_DNS
    )
    changed = replace(changed, dns=service.dns)
    vitals = replace(vitals, services=(changed, *vitals.services[1:]))
    assert (
        next_action(
            vitals, failed, scope, now=time.time(), last_good=saved, prechange_snapshot_id=saved.id
        ).tool
        == Tool.RESTORE_PROXY
    )


def test_baseline_empty_or_unexplained_evidence_does_not_reset_network(
    inspection: Inspection,
) -> None:
    failed = OracleResult(Verdict.OFFLINE, time.time(), ())
    assert (
        next_action(
            inspection.vitals,
            failed,
            Scope(),
            now=time.time(),
            last_good=None,
            prechange_snapshot_id=None,
        ).tool
        == Tool.GIVE_UP
    )
    assert (
        next_action(
            inspection.vitals,
            failed,
            Scope(evidence=("dns-1",)),
            now=time.time(),
            last_good=None,
            prechange_snapshot_id=None,
        ).tool
        == Tool.REPORT
    )


def test_change_dispatch_requires_fresh_checkpoint_and_keeps_trace_private(
    inspection: Inspection,
    redactor: Redactor,
    tmp_path: Path,
) -> None:
    from offline_agent.uplink.plans import PlanContext, ServiceSettings

    oracle = OnlineOracle(HealthyTransport(), ProxyConfig({}, {}), redactor)
    checkpoint = SnapshotStore(tmp_path).checkpoint(inspection.vitals)
    session = Session(inspection.vitals, oracle, redactor, checkpoint=checkpoint)
    sid = inspection.vitals.services[0].id
    text = command(
        "set_dns",
        snapshot_id=checkpoint.id,
        why="dns_failed",
        evidence_ids=["read-001"],
        service_id=sid,
        policy="cloudflare",
    )
    with pytest.raises(ContractError, match="bindings_required"):
        session.dispatch(text)
    settings = tuple(
        ServiceSettings(s.id, inspection.service_names[s.id], s.interface or "en0", s.dns)
        for s in inspection.vitals.services
    )
    session.plan_context = PlanContext(
        session.scope(), settings, checkpoint.id, settings, inspection.vitals.service_order
    )
    outcome = session.dispatch(text)
    assert outcome.card and not outcome.data["executable"]
    assert "Synthetic" not in json.dumps(session.trace)
    assert session.dispatch(command("read_dns")).data
    session.checkpoint = replace(checkpoint, created_at=time.time() - 31)
    with pytest.raises(ContractError, match="stale_or_mismatched"):
        session.dispatch(text)
    session.checkpoint = checkpoint
    oracle.probe()
    with pytest.raises(ContractError, match="healthy_network"):
        session.dispatch(text)


def test_terminal_report_stops_session_and_captive_baseline_hands_off(
    inspection: Inspection, redactor: Redactor
) -> None:
    oracle = OnlineOracle(HealthyTransport(), ProxyConfig({}, {}), redactor)
    session = Session(inspection.vitals, oracle, redactor)
    result = next_action(
        inspection.vitals,
        OracleResult(Verdict.CAPTIVE, time.time(), ()),
        session.scope(),
        now=time.time(),
        last_good=None,
        prechange_snapshot_id=None,
    )
    assert result.tool == Tool.REPORT and result.text("component") == "portal"
    session.dispatch(json.dumps(result.as_json()))
    with pytest.raises(ContractError, match="session_terminal"):
        session.dispatch(command("read_vitals"))
