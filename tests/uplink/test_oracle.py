"""Independent expected verdicts; fake transport only, never live networking."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import subprocess
from dataclasses import replace
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.oracle import (
    DNS_NAMES,
    PORTAL_ENDPOINTS,
    TLS_ENDPOINTS,
    Endpoint,
    Fetch,
    OnlineOracle,
    ProxyConfig,
    ProxyRoute,
    SystemTransport,
    Verdict,
    proxy_config,
)
from offline_agent.uplink.privacy import Redactor


class FakeTransport:
    def __init__(self) -> None:
        self.dns = {name: True for name in DNS_NAMES}
        self.overrides: dict[str, Fetch] = {}
        self.routes: list[ProxyRoute] = []

    def resolve(self, name: str, timeout: float) -> bool:
        assert 0 < timeout <= 5
        return self.dns[name]

    def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch:
        self.routes.append(route)
        return self.overrides.get(endpoint.id, Fetch(route.id, endpoint.status, True, True))


def fixture(proxy: ProxyConfig | None = None) -> tuple[OnlineOracle, FakeTransport]:
    fake = FakeTransport()
    oracle = OnlineOracle(fake, proxy or ProxyConfig({}, {}), Redactor(b"x" * 32))
    return oracle, fake


def test_online_requires_every_independent_check_and_fresh_latest_receipt() -> None:
    oracle, fake = fixture()
    with pytest.raises(ContractError):
        oracle.declare_online()
    result = oracle.probe()
    assert result.verdict == Verdict.ONLINE and len(result.evidence) == 6
    assert oracle.declare_online(now=result.checked_at + 29) is result
    for now in (result.checked_at - 1, result.checked_at + 31, float("nan")):
        with pytest.raises(ContractError):
            oracle.declare_online(now=now)
    fake.dns[DNS_NAMES[1]] = False
    assert oracle.probe().verdict == Verdict.DEGRADED
    with pytest.raises(ContractError):
        oracle.declare_online()


@pytest.mark.parametrize("name", DNS_NAMES)
def test_partial_dns_never_online(name: str) -> None:
    oracle, fake = fixture()
    fake.dns[name] = False
    assert oracle.probe().verdict == Verdict.DEGRADED


@pytest.mark.parametrize("endpoint", TLS_ENDPOINTS)
@pytest.mark.parametrize(
    "defect", ["cert", "status", "body", "redirect", "proxy_bypass", "timeout"]
)
def test_each_tls_or_proxy_failure_never_online(endpoint: Endpoint, defect: str) -> None:
    oracle, fake = fixture(ProxyConfig({"https": "http://proxy.invalid:8080"}, {}))
    route = oracle.proxies.route(endpoint, oracle.redactor)
    receipt = Fetch(route.id, endpoint.status, True, True)
    fake.overrides[endpoint.id] = {
        "cert": replace(receipt, tls_verified=False),
        "status": replace(receipt, status=503),
        "body": replace(receipt, body_matches=False),
        "redirect": replace(receipt, redirected=True),
        "proxy_bypass": replace(receipt, path_id="direct-bypassed-broken-proxy"),
        "timeout": replace(receipt, error="timeout"),
    }[defect]
    assert oracle.probe().verdict == Verdict.DEGRADED
    assert fake.routes[1].proxy == "http://proxy.invalid:8080"


@pytest.mark.parametrize("endpoint", PORTAL_ENDPOINTS)
@pytest.mark.parametrize("redirect", [True, False])
def test_captive_portal_is_never_online(endpoint: Endpoint, redirect: bool) -> None:
    oracle, fake = fixture()
    route = oracle.proxies.route(endpoint, oracle.redactor)
    fake.overrides[endpoint.id] = Fetch(route.id, 302 if redirect else 200, False, False, redirect)
    assert oracle.probe().verdict == Verdict.CAPTIVE


def test_total_failure_and_compound_dns_proxy_fault() -> None:
    oracle, fake = fixture()
    fake.dns = dict.fromkeys(DNS_NAMES, False)
    for endpoint in (*TLS_ENDPOINTS, *PORTAL_ENDPOINTS):
        route = oracle.proxies.route(endpoint, oracle.redactor)
        fake.overrides[endpoint.id] = Fetch(route.id, error="timeout")
    assert oracle.probe().verdict == Verdict.OFFLINE


@pytest.mark.parametrize("setting", ["ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable"])
def test_pac_and_wpad_cannot_silently_go_direct(setting: str) -> None:
    config = proxy_config(f"<dictionary> {{\n {setting} : 1\n}}", {})
    oracle, fake = fixture(config)
    assert oracle.probe().verdict == Verdict.DEGRADED
    assert not fake.routes


def test_proxy_discovery_failure_is_not_a_direct_success() -> None:
    oracle, fake = fixture(proxy_config(None, {}))
    assert oracle.probe().verdict == Verdict.DEGRADED
    assert not fake.routes


def test_env_proxy_precedence_and_explicit_no_proxy_match() -> None:
    config = proxy_config(
        "<dictionary> {\n HTTPSEnable : 1\n HTTPSProxy : system.invalid\n HTTPSPort : 8080\n}",
        {
            "HTTPS_PROXY": "http://upper.invalid:1",
            "https_proxy": "http://lower.invalid:2",
            "no_proxy": ".gstatic.com",
        },
    )
    r = Redactor(b"x" * 32)
    assert config.route(TLS_ENDPOINTS[0], r).proxy == "http://lower.invalid:2"
    assert config.route(TLS_ENDPOINTS[1], r).proxy is None
    system = replace(config, env={})
    assert system.route(TLS_ENDPOINTS[0], r).proxy == "http://system.invalid:8080"
    assert "invalid" not in repr(config)


@pytest.mark.parametrize(
    "url", ["http://bad.invalid\ninsecure", "x://bad.invalid", "http://x:99999"]
)
def test_invalid_proxy_never_spawns(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Mock(side_effect=AssertionError("unexpected spawn"))
    monkeypatch.setattr("subprocess.run", spy)
    route = ProxyConfig({}, {"https_proxy": url}).route(TLS_ENDPOINTS[0], Redactor(b"x" * 32))
    assert not route.supported
    assert SystemTransport().fetch(TLS_ENDPOINTS[0], route, 1).error
    spy.assert_not_called()


def test_transport_uses_cert_validation_explicit_proxy_and_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Mock(return_value=subprocess.CompletedProcess([], 0, b"\nUPLINK_STATUS:204", b""))
    monkeypatch.setattr("subprocess.run", spy)
    route = ProxyRoute(
        "path-synthetic", "environment", "http://" + "synthetic:credential" + "@proxy.invalid:1"
    )
    result = SystemTransport().fetch(TLS_ENDPOINTS[1], route, 2)
    command = spy.call_args.args[0]
    assert command[:3] == ["/usr/bin/curl", "--disable", "--config"]
    assert "--insecure" not in command and "-k" not in command and "--location" not in command
    assert "--max-time" in command and spy.call_args.kwargs["timeout"] == 2.5
    assert "credential" not in json.dumps(command)
    assert b'noproxy = ""' in spy.call_args.kwargs["input"]
    assert result.tls_verified and result.body_matches and result.path_id == route.id


def test_dns_uses_system_resolver_in_bounded_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Mock(return_value=subprocess.CompletedProcess([], 0, b"True\n", b""))
    monkeypatch.setattr("subprocess.run", spy)
    assert SystemTransport().resolve(DNS_NAMES[0], 1)
    assert "socket.getaddrinfo" in spy.call_args.args[0][3]
    assert spy.call_args.kwargs["timeout"] == 1
    spy.side_effect = subprocess.TimeoutExpired("synthetic", 1)
    assert not SystemTransport().resolve(DNS_NAMES[0], 1)


def test_transport_timeout_and_tls_error_are_value_blind(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Mock(return_value=subprocess.CompletedProcess([], 60, b"secret", b"host secret"))
    monkeypatch.setattr("subprocess.run", spy)
    receipt = SystemTransport().fetch(TLS_ENDPOINTS[0], ProxyRoute("path-a", "system", None), 1)
    assert receipt.error == "tls_or_transport_failed"
    assert "secret" not in repr(receipt)
    spy.side_effect = subprocess.TimeoutExpired("hidden", 1)
    assert SystemTransport().fetch(TLS_ENDPOINTS[0], ProxyRoute("p", "system", None), 1).error


def test_nested_scoped_proxy_is_not_mistaken_for_global_route() -> None:
    config = proxy_config(
        "<dictionary> {\n __SCOPED__ : <dictionary> {\n en0 : <dictionary> {\n"
        " HTTPSEnable : 1\n HTTPSProxy : scoped.invalid\n HTTPSPort : 8080\n }\n }\n}",
        {},
    )
    oracle, fake = fixture(config)
    assert oracle.probe().verdict == Verdict.DEGRADED
    assert not fake.routes


def test_unmodeled_proxy_exclusion_refuses_instead_of_guessing_client_path() -> None:
    oracle, fake = fixture(ProxyConfig({}, {"no_proxy": _public_fixture("@PUBLIC_IPV4_065@/24")}))
    assert oracle.probe().verdict == Verdict.DEGRADED
    assert not fake.routes
