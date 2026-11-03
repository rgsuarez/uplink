"""Hermetic macOS diagnostics: source-bound comparisons, no state changes or raw identifiers."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import subprocess
import time
from typing import cast

import pytest

from offline_agent.contracts import ContractError, Json
from offline_agent.uplink import real_diagnostics
from offline_agent.uplink.oracle import (
    DNS_NAMES,
    TLS_ENDPOINTS,
    Endpoint,
    Evidence,
    Fetch,
    OracleResult,
    ProxyRoute,
    Verdict,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.real_fleet import approved_serial

SOURCE = _public_fixture("@PUBLIC_IPV4_071@")
GATEWAY = _public_fixture("@PUBLIC_IPV4_066@")


def rows(value: Json) -> list[dict[str, Json]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, Json]], value)


def row(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict)
    return value


def oracle(*, checked_at: float | None = None) -> OracleResult:
    return OracleResult(
        Verdict.OFFLINE,
        time.time() if checked_at is None else checked_at,
        (
            Evidence("dns-1", "dns", False, "system_resolution_failed", latency_ms=10.0),
            Evidence("dns-2", "dns", False, "system_resolution_failed", latency_ms=10.0),
            Evidence("tls-apple", "tls", False, "tls_or_transport_failed", latency_ms=10.0),
            Evidence("tls-google", "tls", False, "tls_or_transport_failed", latency_ms=10.0),
        ),
    )


class Commands:
    def __init__(
        self,
        *,
        interface: str = "en0",
        first_route: str = "en0",
        last_route: str = "en0",
        source: str = SOURCE,
    ) -> None:
        self.interface, self.first_route, self.last_route, self.source = (
            interface,
            first_route,
            last_route,
            source,
        )
        self.calls: list[tuple[str, ...]] = []
        self.route_reads = 0

    def __call__(
        self, argv: tuple[str, ...]
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None, float]:
        self.calls.append(argv)
        if argv[-1] == "read":
            output = json.dumps(
                {
                    "interface": self.interface,
                    "serial": approved_serial(),
                    "service_name": "Private Test Service",
                    "effective_proxy": {"HTTPSProxy": "private.invalid"},
                }
            )
        elif argv[:2] == ("/usr/sbin/ipconfig", "getifaddr"):
            output = self.source
        elif argv[0] == "/sbin/ifconfig":
            output = (
                _public_fixture(f"{self.interface}: flags=8863<UP,BROADCAST,RUNNING> mtu 1500\n"
                " status: active\n not associated\n ether @PUBLIC_MAC_114@\n")
            )
        elif argv[0] == "/sbin/route":
            self.route_reads += 1
            interface = self.first_route if self.route_reads == 1 else self.last_route
            output = f"gateway: {GATEWAY}\ninterface: {interface}\n"
        elif argv[0] == "/usr/bin/dig":
            assert argv[1:3] == ("-b", SOURCE) and argv[3] == _public_fixture("@@PUBLIC_IPV4_003@")
            assert argv[4] in DNS_NAMES
            output = _public_fixture("@PUBLIC_IPV4_088@\n")
        elif argv[0] == "/sbin/ping":
            assert argv == (
                "/sbin/ping",
                "-n",
                "-b",
                "en0",
                "-S",
                SOURCE,
                "-c",
                "1",
                "-t",
                "1",
                GATEWAY,
            )
            output = "synthetic gateway replied"
        else:
            raise AssertionError("unregistered synthetic command")
        return subprocess.CompletedProcess(argv, 0, output, ""), None, 1.0


def install(
    monkeypatch: pytest.MonkeyPatch,
    commands: Commands,
) -> list[str]:
    interfaces: list[str] = []
    monkeypatch.setattr("offline_agent.uplink.real_diagnostics.platform.system", lambda: "Darwin")
    monkeypatch.setattr(real_diagnostics, "command", commands)

    class Transport:
        def __init__(self, *, interface: str) -> None:
            interfaces.append(interface)

        def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch:
            assert endpoint in TLS_ENDPOINTS and timeout == 0.8
            assert route.proxy is None and route.supported
            return Fetch(route.id, endpoint.status, True, True)

    monkeypatch.setattr(real_diagnostics, "SystemTransport", Transport)
    return interfaces


def test_mac_comparisons_bind_wifi_and_preserve_current_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = Commands()
    interfaces = install(monkeypatch, commands)
    measured = oracle()
    result = real_diagnostics.layer_probe(measured, Redactor(b"d" * 32))
    assert result["link_up"] is True and result["lease_present"] is True
    assert result["wifi_default_route"] is True and row(result["gateway_ping"])["ok"] is True
    assert interfaces == ["en0", "en0"]
    assert all(item["ok"] is True for item in rows(result["dns_alternate"]))
    assert all(item["ok"] is True for item in rows(result["https_direct"]))
    assert all(item["ok"] is False for item in rows(result["dns_configured"]))
    assert all(item["ok"] is False for item in rows(result["https_proxy"]))
    assert measured.verdict == Verdict.OFFLINE and "verdict" not in result
    assert len(commands.calls) == 8
    serialized = json.dumps(result)
    for private in (
        SOURCE,
        GATEWAY,
        "en0",
        approved_serial(),
        "Private Test Service",
        "private.invalid",
        _public_fixture("@PUBLIC_MAC_114@"),
    ):
        assert private not in serialized


@pytest.mark.parametrize("interface", ["en7", "bridge0", "en0;id"])
def test_mac_comparison_refuses_other_or_untrusted_interfaces(
    monkeypatch: pytest.MonkeyPatch, interface: str
) -> None:
    commands = Commands(interface=interface)
    interfaces = install(monkeypatch, commands)
    with pytest.raises(ContractError, match="diagnostic_wifi_interface_refused"):
        real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert len(commands.calls) == 1 and interfaces == []


@pytest.mark.parametrize(
    "source,first_route", [(SOURCE, "en7"), (_public_fixture("@PUBLIC_IPV4_058@"), "en0"), ("source;id", "en0")]
)
def test_mac_comparisons_require_wifi_source_and_default_route(
    monkeypatch: pytest.MonkeyPatch, source: str, first_route: str
) -> None:
    commands = Commands(source=source, first_route=first_route)
    interfaces = install(monkeypatch, commands)
    result = real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert interfaces == [] and len(commands.calls) == 4
    for group in ("dns_alternate", "https_direct"):
        assert all(
            item["available"] is False and item["ok"] is False for item in rows(result[group])
        )
    assert row(result["gateway_ping"])["available"] is False


def test_mac_route_change_discards_all_comparison_success(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = Commands(last_route="en7")
    install(monkeypatch, commands)
    result = real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert result["wifi_default_route"] is False
    assert row(result["gateway_ping"])["reason"] == "wifi_route_changed"
    for group in ("dns_alternate", "https_direct"):
        assert all(
            item["available"] is False
            and item["ok"] is False
            and item["reason"] == "wifi_route_changed"
            for item in rows(result[group])
        )


def test_mac_missing_native_helper_does_not_attempt_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = Commands()
    interfaces = install(monkeypatch, commands)

    def missing(
        argv: tuple[str, ...],
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None, float]:
        return None, "command_unavailable", 1.0

    monkeypatch.setattr(real_diagnostics, "command", missing)
    result = real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert result["link_up"] is None and interfaces == []
    assert all(item["available"] is False for item in rows(result["dns_alternate"]))


@pytest.mark.parametrize("error", ["probe_timeout", "command_unavailable"])
def test_mac_failed_comparisons_remain_explicit(
    monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    commands = Commands()
    install(monkeypatch, commands)

    def failed(
        argv: tuple[str, ...],
    ) -> tuple[subprocess.CompletedProcess[str] | None, str | None, float]:
        if argv[0] in {"/usr/bin/dig", "/sbin/ping"}:
            commands.calls.append(argv)
            return None, error, 999.0
        return commands(argv)

    monkeypatch.setattr(real_diagnostics, "command", failed)
    result = real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert row(result["gateway_ping"])["ok"] is False
    assert all(
        item["ok"] is False and item["available"] is (error == "probe_timeout")
        for item in rows(result["dns_alternate"])
    )
    assert len(commands.calls) == 8


def test_mac_configured_results_require_fresh_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Commands())
    result = real_diagnostics.layer_probe(oracle(checked_at=time.time() - 60), Redactor(b"d" * 32))
    for group in ("dns_configured", "https_proxy"):
        assert all(
            item["available"] is False and item["ok"] is False for item in rows(result[group])
        )


@pytest.mark.parametrize("failure", [FileNotFoundError, subprocess.TimeoutExpired])
def test_mac_subprocess_timeout_is_bounded_and_no_retry(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    calls: list[object] = []

    def failed(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["timeout"] == 1 and kwargs["check"] is False
        if failure is FileNotFoundError:
            raise FileNotFoundError()
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr("offline_agent.uplink.real_diagnostics.subprocess.run", failed)
    result, error, _ = real_diagnostics.command(("synthetic",))
    assert result is None and len(calls) == 1
    assert error == ("command_unavailable" if failure is FileNotFoundError else "probe_timeout")


def test_mac_comparison_refuses_non_darwin_before_any_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("offline_agent.uplink.real_diagnostics.platform.system", lambda: "Linux")
    monkeypatch.setattr(real_diagnostics, "command", lambda argv: calls.append(argv))
    with pytest.raises(ContractError, match="requires_darwin"):
        real_diagnostics.layer_probe(oracle(), Redactor(b"d" * 32))
    assert calls == []


@pytest.mark.parametrize("failure", ["certificate", "redirect", "path", "body", "unavailable"])
def test_direct_https_comparison_preserves_tls_and_content_contract(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    class Transport:
        def __init__(self, *, interface: str) -> None:
            assert interface == "en0"

        def fetch(self, endpoint: Endpoint, route: ProxyRoute, timeout: float) -> Fetch:
            return Fetch(
                "other-path" if failure == "path" else route.id,
                endpoint.status,
                failure != "body",
                failure != "certificate",
                failure == "redirect",
                "transport_unavailable" if failure == "unavailable" else None,
            )

    monkeypatch.setattr(real_diagnostics, "SystemTransport", Transport)
    result = real_diagnostics.direct_https("en0", 0, ProxyRoute("direct", "system", None))
    assert result["ok"] is False
    assert result["available"] is (failure != "unavailable")
