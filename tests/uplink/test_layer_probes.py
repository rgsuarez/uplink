"""Hermetic differential probes: diagnostic evidence never changes lab state or its oracle."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import copy
import json
import subprocess
from pathlib import Path
from typing import Protocol, cast

import pytest

from offline_agent.uplink import lab_worker
from offline_agent.uplink.lab_protocol import (
    ALTERNATE_GW,
    DEAD_DNS,
    DNS_NAMES,
    PRIMARY_GW,
    PRIMARY_IP,
    PROXY_URL,
    SERVER_IP,
    LabSpec,
    request,
)
from offline_agent.uplink.lab_simulator import SimulatedClient


def object_row(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def object_rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [object_row(item) for item in value]


class RunCommand(Protocol):
    def __call__(
        self, argv: tuple[str, ...], *, timeout: float = 8, check: bool = True
    ) -> subprocess.CompletedProcess[str]: ...


def fixture_commands(
    spec: LabSpec, commands: list[tuple[str, ...]], *, gateway: object = PRIMARY_GW
) -> RunCommand:
    def run(
        argv: tuple[str, ...], *, timeout: float = 8, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        assert timeout <= 1 and check is False
        if argv[:3] == ("ip", "-j", "-n"):
            assert argv[3] == spec.client
            if argv[4] == "address":
                value: object = [
                    {
                        "ifname": spec.interfaces[0],
                        "flags": ["UP", "LOWER_UP"],
                        "addr_info": [{"local": PRIMARY_IP}],
                    }
                ]
            else:
                assert argv[4] == "route"
                value = [{"dst": "default", "gateway": gateway}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        assert argv[:4] == ("ip", "netns", "exec", spec.client)
        if "nslookup" in argv:
            ok = argv[-1] != DEAD_DNS
            return subprocess.CompletedProcess(
                argv, 0 if ok else 1, f"Name: {argv[-2]}\nAddress: {SERVER_IP}\n" if ok else "", ""
            )
        if "ping" in argv:
            return subprocess.CompletedProcess(
                argv, 0 if argv[-1] in {PRIMARY_GW, ALTERNATE_GW} else 1, "", ""
            )
        assert argv[4] == "curl" and "--cacert" in argv
        assert "--insecure" not in argv and "-k" not in argv
        proxy = argv[argv.index("--proxy") + 1]
        ok = proxy in ("", PROXY_URL)
        return subprocess.CompletedProcess(
            argv, 0 if ok else 7, "\nSTATUS:204" if ok else "\nSTATUS:000", ""
        )

    return run


def test_layer_probe_rejects_payload_and_dry_run_has_no_effects() -> None:
    with pytest.raises(ValueError, match="invalid_lab_payload"):
        request("layer_probe", "0123456789ab", {"resolver": _public_fixture("@PUBLIC_IPV4_095@")})
    value = lab_worker.dispatch(request("layer_probe", "0123456789ab"), execute=False)
    assert value["effects"] is False


@pytest.mark.parametrize(
    "dns,proxy",
    [
        (DEAD_DNS, PROXY_URL),
        (PRIMARY_GW, f"http://{SERVER_IP}:9"),
        (DEAD_DNS, f"http://{SERVER_IP}:9"),
    ],
)
def test_layer_probe_separates_dns_and_proxy_without_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dns: str, proxy: str
) -> None:
    spec = LabSpec("0123456789ab")
    monkeypatch.setattr(LabSpec, "resolver_directory", property(lambda _: tmp_path))
    resolver = tmp_path / "resolv.conf"
    original = f"nameserver {dns}\n"
    resolver.write_text(original)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(lab_worker, "run", fixture_commands(spec, commands))
    state: dict[str, object] = {"proxy": proxy, "timer": {"state": "armed", "deadline": 123}}
    before = copy.deepcopy(state)
    result = lab_worker.layer_probe(spec, state)
    assert result["link_up"] is True and result["lease_present"] is True
    assert object_row(result["gateway_ping"])["ok"] is True
    assert all(row["ok"] is (dns != DEAD_DNS) for row in object_rows(result["dns_configured"]))
    assert all(row["ok"] is True for row in object_rows(result["dns_alternate"]))
    assert all(row["ok"] is True for row in object_rows(result["https_direct"]))
    assert all(row["ok"] is (proxy == PROXY_URL) for row in object_rows(result["https_proxy"]))
    assert len(commands) == 11 and state == before and resolver.read_text() == original
    assert "verdict" not in result and "oracle" not in result
    assert [row["target"] for row in object_rows(result["dns_configured"])] == list(DNS_NAMES)
    assert all(row["answers"] == [SERVER_IP] for row in object_rows(result["dns_alternate"]))
    direct = [cmd for cmd in commands if "--resolve" in cmd]
    assert len(direct) == 2
    assert all(
        row["path"] == "direct_fixture_address" for row in object_rows(result["https_direct"])
    )


@pytest.mark.parametrize("gateway", [_public_fixture("@PUBLIC_IPV4_003@"), {"unexpected": "shape"}])
def test_layer_probe_refuses_unlisted_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: object
) -> None:
    spec = LabSpec("0123456789ab")
    monkeypatch.setattr(LabSpec, "resolver_directory", property(lambda _: tmp_path))
    (tmp_path / "resolv.conf").write_text(_public_fixture("nameserver @PUBLIC_IPV4_003@\n"))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(lab_worker, "run", fixture_commands(spec, commands, gateway=gateway))
    result = lab_worker.layer_probe(spec, {"proxy": "http://untrusted.test:9000"})
    assert object_row(result["gateway_ping"])["available"] is False
    assert all(row["available"] is False for row in object_rows(result["dns_configured"]))
    assert all(row["available"] is False for row in object_rows(result["https_proxy"]))
    assert all(row["ok"] is True for row in object_rows(result["https_direct"]))
    assert not any(
        "ping" in cmd or _public_fixture("@PUBLIC_IPV4_003@") in cmd or "http://untrusted.test:9000" in cmd for cmd in commands
    )


def test_layer_probe_reports_missing_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LabSpec, "resolver_directory", property(lambda _: tmp_path))
    (tmp_path / "resolv.conf").write_text(f"nameserver {PRIMARY_GW}\n")

    def missing(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("synthetic_missing_dependency")

    monkeypatch.setattr(lab_worker, "run", missing)
    result = lab_worker.layer_probe(LabSpec("0123456789ab"), {"proxy": PROXY_URL})
    assert result["link_up"] is None and result["lease_present"] is None
    for key in ("dns_configured", "dns_alternate", "https_direct", "https_proxy"):
        assert all(
            row["available"] is False and row["ok"] is False for row in object_rows(result[key])
        )


def test_layer_probe_timeout_is_failed_evidence_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = LabSpec("0123456789ab")
    monkeypatch.setattr(LabSpec, "resolver_directory", property(lambda _: tmp_path))
    (tmp_path / "resolv.conf").write_text(f"nameserver {PRIMARY_GW}\n")
    commands: list[tuple[str, ...]] = []
    fake = fixture_commands(spec, commands)

    def timeout(
        argv: tuple[str, ...], *, timeout: float = 8, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ("ip", "-j", "-n"):
            return fake(argv, timeout=timeout, check=check)
        commands.append(argv)
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(lab_worker, "run", timeout)
    result = lab_worker.layer_probe(spec, {"proxy": PROXY_URL})
    assert len(commands) == 11
    assert object_row(result["gateway_ping"])["reason"] == "probe_timeout"
    for key in ("dns_configured", "dns_alternate", "https_direct", "https_proxy"):
        assert all(
            row["available"] is True and row["ok"] is False and row["reason"] == "probe_timeout"
            for row in object_rows(result[key])
        )


def test_missing_busybox_applet_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 127, "", "nslookup: applet not found")

    monkeypatch.setattr(lab_worker, "run", missing)
    result = lab_worker.diagnostic_dns(LabSpec("0123456789ab"), 0, PRIMARY_GW, "configured")
    assert result["available"] is False and result["ok"] is False


@pytest.mark.parametrize("fault", ["wrong_dns", "stale_proxy", "dns_plus_proxy"])
def test_simulated_layer_probe_preserves_fault_and_exposes_differential(fault: str) -> None:
    client = SimulatedClient(LabSpec("0123456789ab"))
    client.call("up")
    client.call("arm", {"seconds": 20})
    client.call("inject", {"fault": fault})
    before = client.call("observe")
    result = client.call("layer_probe")
    assert client.call("observe") == before
    assert result["link_up"] is True and result["lease_present"] is True
    assert all(row["ok"] is True for row in object_rows(result["dns_alternate"]))
    assert all(row["ok"] is True for row in object_rows(result["https_direct"]))
    assert all(
        row["ok"] is (fault == "stale_proxy") for row in object_rows(result["dns_configured"])
    )
    assert all(row["ok"] is (fault == "wrong_dns") for row in object_rows(result["https_proxy"]))
