"""The stronger baseline uses measured path evidence without changing the oracle."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink.contracts import Scope
from offline_agent.uplink.lab_protocol import ALTERNATE_GW, PRIMARY_GW, PROXY_URL
from offline_agent.uplink.lab_runner import LabState, ScriptedStrongPolicy
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict


def observed(*, link: bool = True, gateway: str | None = None, qualified: bool = True) -> LabState:
    raw: dict[str, Json] = {
        "current": {"primary_up": link, "dns_server": PRIMARY_GW, "proxy_url": PROXY_URL},
        "known_good_snapshot": {"dns_server": PRIMARY_GW, "proxy_url": PROXY_URL},
        "prior_alternate_online": qualified,
    }
    return LabState(raw, PRIMARY_GW, PROXY_URL, link, True, gateway)


@pytest.mark.parametrize(
    "link,gateway", [(True, None), (False, PRIMARY_GW), (True, _public_fixture("@PUBLIC_IPV4_076@")), (True, ALTERNATE_GW)]
)
def test_primary_failure_can_select_a_prequalified_route(link: bool, gateway: str | None) -> None:
    result = OracleResult(
        Verdict.OFFLINE,
        1,
        (
            Evidence("dns-1", "dns", False, "failed"),
            Evidence("dns-2", "dns", False, "failed"),
        ),
    )
    action = ScriptedStrongPolicy().choose(
        Scope(("service",), ("alternate",), ("snapshot",), ("dns-1", "dns-2")),
        observed(link=link, gateway=gateway),
        result,
        (),
    )
    assert json.loads(action.text)["tool"] == "reroute"


@pytest.mark.parametrize("qualified,candidates", [(False, ("alternate",)), (True, ())])
def test_unqualified_or_missing_alternate_is_not_selected(
    qualified: bool,
    candidates: tuple[str, ...],
) -> None:
    action = ScriptedStrongPolicy().choose(
        Scope(("service",), candidates, ("snapshot",), ("dns-1",)),
        observed(qualified=qualified),
        OracleResult(Verdict.OFFLINE, 1, ()),
        (),
    )
    assert json.loads(action.text)["tool"] == "report"


def test_healthy_alternate_is_not_rerouted_again() -> None:
    action = ScriptedStrongPolicy().choose(
        Scope(("service",), ("alternate",), ("snapshot",), ("dns-1",)),
        observed(gateway=ALTERNATE_GW),
        OracleResult(Verdict.ONLINE, 1, ()),
        (),
    )
    assert json.loads(action.text)["tool"] == "declare_online"


def test_duplicate_route_attempt_is_not_admitted() -> None:
    current = observed(gateway=ALTERNATE_GW)
    current.raw["action_history"] = ["reroute"]
    result = OracleResult(
        Verdict.OFFLINE,
        1,
        (
            Evidence("dns-1", "dns", False, "failed"),
            Evidence("dns-2", "dns", False, "failed"),
        ),
    )
    action = ScriptedStrongPolicy().choose(
        Scope(("service",), ("alternate",), ("snapshot",), ("dns-1", "dns-2")),
        current,
        result,
        (),
    )
    assert json.loads(action.text)["tool"] == "report"


def test_malformed_action_history_is_refused() -> None:
    current = observed()
    current.raw["action_history"] = "not-a-list"
    with pytest.raises(ContractError, match="action_history"):
        ScriptedStrongPolicy().choose(Scope(), current, OracleResult(Verdict.OFFLINE, 1, ()), ())
