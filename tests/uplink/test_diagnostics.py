
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
import json
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.diagnostics import (
    IF,
    IP,
    NS,
    SC,
    Inspection,
    MacDiagnostics,
    ReadOnlyRunner,
    ReadResult,
    candidate,
    parse_interface,
    parse_proxy,
    prior_leases,
    rank_candidates,
)
from offline_agent.uplink.privacy import Redactor


def test_all_interfaces_services_and_routing_evidence(inspection: Inspection) -> None:
    assert {i.id for i in inspection.vitals.interfaces} == {
        "en0",
        "en1",
        "en2",
        "en3",
        "lo0",
        "bridge0",
        "utun0",
    }
    assert len(inspection.vitals.services) == 5
    assert {s.kind for s in inspection.vitals.services} == {
        "wifi",
        "ethernet",
        "iphone_usb",
        "thunderbolt",
        "bluetooth",
    }
    assert inspection.vitals.default_interface == "en0"
    assert inspection.vitals.default_gateway == _public_fixture("@PUBLIC_IPV4_066@")
    assert inspection.vitals.system_dns == (_public_fixture("@PUBLIC_IPV4_075@"),)
    for service in inspection.vitals.services:
        assert len(service.proxies) == 5
    assert any(o.status == "command_failed" for o in inspection.vitals.observations)


def test_macos26_not_associated_is_not_a_disconnection(inspection: Inspection) -> None:
    wifi = next(i for i in inspection.vitals.interfaces if i.id == "en0")
    assert wifi.leased and wifi.connection == "leased" and wifi.up
    assert wifi.addresses == (_public_fixture("@PUBLIC_IPV4_067@"),)


def test_stale_lease_and_link_local_are_not_connected() -> None:
    assert not parse_interface("en0", "wifi", "", _public_fixture("@PUBLIC_IPV4_067@"), _public_fixture("yiaddr = @PUBLIC_IPV4_069@")).leased
    assert parse_interface("en0", "wifi", "", _public_fixture("@PUBLIC_IPV4_059@"), "").connection == "unknown"
    assert parse_interface("en0", "wifi", "flags=2<BROADCAST>", "", "").connection == "down"


def test_no_raw_identifiers_survive_parse_or_repr(
    inspection: Inspection, redactor: Redactor
) -> None:
    text = json.dumps(inspection.vitals.as_json()) + repr(inspection)
    for value in (
        "Fictional Research Network",
        "Fictional iPhone Hotspot",
        "Synthetic Wi-Fi",
        _public_fixture("@PUBLIC_MAC_114@"),
        "synthetic-laptop.invalid",
        "proxy.synthetic.invalid",
        "synthetic-person",
        "synthetic.invalid",
    ):
        assert value not in text
    assert redactor.scan(text)["passed"]


def test_candidate_evidence_ranking_and_saved_hotspot(inspection: Inspection) -> None:
    candidates = inspection.vitals.candidates
    assert candidates[0].evidence == ("configured_service", "current_lease")
    assert candidates[0].eligible and candidates[0].available
    saved = [c for c in candidates if c.kind == "saved_wifi"]
    assert len(saved) == 2 and all(c.eligible and not c.available for c in saved)
    assert sum(c.hotspot_possible for c in saved) == 1
    assert not any(c.eligible for c in candidates if c.evidence == ("configured_service",))
    assert rank_candidates(tuple(reversed(candidates))) == candidates


def test_prior_lease_outranks_configured_and_saved(redactor: Redactor) -> None:
    rows = tuple(
        candidate(redactor, "ethernet", None, f"en{i}", None, (e,), False)
        for i, e in enumerate(
            ("saved_network", "configured_service", "prior_lease", "verified_online_snapshot")
        )
    )
    assert [c.score for c in rank_candidates(rows)] == [100, 80, 30, 10]


@pytest.mark.parametrize(
    "exception,status",
    [
        (FileNotFoundError(), "missing_tool"),
        (PermissionError(), "permission_denied"),
        (subprocess.TimeoutExpired("synthetic", 2), "timeout"),
        (OSError(), "unavailable"),
    ],
)
def test_unavailable_tools_are_typed_evidence(
    exception: Exception, status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("subprocess.run", Mock(side_effect=exception))
    assert ReadOnlyRunner().read((SC, "--dns")) == ReadResult(status=status)


@pytest.mark.parametrize(
    "argv",
    [
        (NS, "-setdnsservers", "Synthetic Wi-Fi", _public_fixture("@PUBLIC_IPV4_095@")),
        (IP, "set", "en0", "DHCP"),
        (IF, "en0", "down"),
        ("/bin/sh", "-c", "id"),
        ("sudo", NS, "-getinfo", "Synthetic Wi-Fi"),
        ("/sbin/route", "add", "default", _public_fixture("@PUBLIC_IPV4_066@")),
    ],
)
def test_read_runner_cannot_spawn_mutations(
    argv: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Mock(side_effect=AssertionError("spawn forbidden"))
    monkeypatch.setattr("subprocess.run", spy)
    with pytest.raises(ContractError):
        ReadOnlyRunner().read(argv)
    spy.assert_not_called()


def test_empty_diagnostics_and_undiscovered_args_fail_closed(redactor: Redactor) -> None:
    class Empty:
        def read(self, argv: tuple[str, ...]) -> ReadResult:
            return ReadResult(status="permission_denied")

    adapter = MacDiagnostics(Empty(), redactor)
    result = adapter.inspect(env={})
    assert not result.vitals.interfaces and not result.vitals.services and not result.proxies.known
    assert all(o.status == "permission_denied" for o in result.vitals.observations)
    with pytest.raises(ContractError):
        adapter.read((NS, "-getinfo", "Unseen service"))


def test_proxy_off_unknown_and_malformed_port(redactor: Redactor) -> None:
    assert parse_proxy("wpad", "Auto Proxy Discovery: Off", redactor).enabled is False
    assert parse_proxy("http", None, redactor).enabled is None
    proxy = parse_proxy("https", "Enabled: Yes\nServer: hidden.invalid\nPort: nonsense", redactor)
    assert proxy.port is None and proxy.host_id and "hidden" not in repr(proxy)


def test_prior_lease_metadata_is_read_only_and_redacted(tmp_path: Path, redactor: Redactor) -> None:
    (tmp_path / "en1-synthetic.plist").write_bytes(
        plistlib.dumps(
            {
                "IPAddress": _public_fixture("@PUBLIC_IPV4_069@"),
                "SSID": "Fictional lease network",
                "Hostname": "fictional.invalid",
            }
        )
    )
    (tmp_path / "en2-broken.plist").write_text("not a plist")
    found, status = prior_leases(tmp_path, redactor)
    assert found == {"en1"} and status == "available"
    assert prior_leases(tmp_path / "absent", redactor) == (set(), "unavailable")


def test_abbreviated_numeric_routes_do_not_poison_identifier_scan(redactor: Redactor) -> None:
    from dataclasses import asdict

    from offline_agent.uplink.diagnostics import parse_routes

    routes = parse_routes(
        _public_fixture("192.0.2 @PUBLIC_IPV4_066@ UCS en0\n169.254 link#4 UCS en1"), {"en0", "en1"}, redactor
    )
    assert routes[0].destination == _public_fixture("@PUBLIC_IPV4_065@/24")
    assert routes[1].destination == _public_fixture("@PUBLIC_IPV4_057@/16")
    text = json.dumps(
        {"routes": [asdict(r) for r in routes], "addresses": [_public_fixture("@PUBLIC_IPV4_067@"), _public_fixture("@PUBLIC_IPV4_059@")]}
    )
    assert redactor.scan(text)["passed"]


def test_proxy_ip_and_ipv6_are_not_hostname_or_mac_leaks(redactor: Redactor) -> None:
    proxy = parse_proxy("http", _public_fixture("Enabled: No\nServer: @PUBLIC_IPV4_066@\nPort: 8080"), redactor)
    assert proxy.host_id and _public_fixture("@PUBLIC_IPV4_066@") not in repr(proxy)
    assert redactor.scan(_public_fixture("@PUBLIC_IPV4_066@ @PUBLIC_MAC_117@:78:90"))["passed"]
    redactor.handle("network", "Fictional Secret SSID")
    scan = redactor.scan("Fictional Secret SSID")
    assert not scan["passed"] and scan["identifier_kinds"] == ["network"]
    assert "Fictional" not in json.dumps(scan)


def test_interface_gateway_does_not_become_private_identity(redactor: Redactor) -> None:
    from dataclasses import asdict

    from offline_agent.uplink.diagnostics import parse_routes

    routes = parse_routes("default utun12 UGSc utun12\n", {"utun12"}, redactor)
    assert routes[0].gateway == redactor.handle("interface", "utun12", remember=False)
    report = json.dumps({"interfaces": [{"id": "utun12"}], "routes": [asdict(r) for r in routes]})
    assert redactor.scan(report)["passed"]


def test_empty_proxy_server_does_not_consume_next_field(redactor: Redactor) -> None:
    raw = "Enabled: No\nServer: \nPort: 0\nAuthenticated Proxy Enabled: 0\n"
    value = parse_proxy("http", raw, redactor)
    assert value.enabled is False and value.host_id is None and value.port is None
    assert redactor.scan(raw)["passed"]


def test_link_indexes_are_structural_not_private_gateway_names(redactor: Redactor) -> None:
    from offline_agent.uplink.diagnostics import parse_routes

    text = "192.0.2 link#17 UCS en0\n"
    rows = parse_routes(text, {"en0"}, redactor)
    assert rows[0].gateway == redactor.handle("link", "link#17", remember=False)
    assert redactor.scan(text)["passed"]
