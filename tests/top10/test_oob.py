"""05:50 owner contract: exact en7 pin, bounded retries and no unsafe admission.

Small fake-clock/transport tests plus bounded local ledger tests. No SSH or host
network command is permitted by the qualification sandbox.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import copy
import subprocess
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_attempt import Attempt
from offline_agent.uplink.top10_bridge import management
from offline_agent.uplink.top10_kernel import usable
from offline_agent.uplink.top10_oob import Qualifier, load_ok, target_ssh, validate_proof


def coordinates() -> dict[str, object]:
    return {
        "oob_interface": "en7",
        "oob_server_ipv4": _public_fixture("@PUBLIC_IPV4_062@"),
        "oob_client_ipv4": _public_fixture("@PUBLIC_IPV4_063@"),
        "manifest_sha256": "a" * 64,
    }


class Readbacks:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.time = 1000.0
        self.timeouts = 0
        self.load_update: dict[str, object] = {}
        self.target_update: dict[str, object] = {}

    def now(self) -> float:
        return self.time

    def pause(self, seconds: float) -> None:
        self.time += seconds

    def probe(self, kind: str, nonce: str, timeout: float) -> dict[str, object]:
        assert 0 < timeout <= 8
        self.calls.append(kind)
        self.time += 0.1
        if kind == "relay":
            return {
                "nonce": nonce,
                "node": "relay",
                "cores": 4,
                "load1": 1.0,
                "load5": 2.0,
            } | self.load_update
        if self.timeouts:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired("synthetic", timeout)
        return {
            "nonce": nonce,
            "client": _public_fixture("@PUBLIC_IPV4_063@"),
            "server": _public_fixture("@PUBLIC_IPV4_062@"),
            "interface": "en7",
            "address": _public_fixture("@PUBLIC_IPV4_062@"),
            "host_verified": True,
        } | self.target_update

    def qualify(self, gate: Qualifier, scope: str = "preflight") -> dict[str, object]:
        return gate.qualify(
            coordinates(),
            scope,
            self.probe,
            now=self.now,
            steady=self.now,
            pause=self.pause,
        )


def test_exact_linklocal_pin_is_management_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_CONNECTION", _public_fixture("@PUBLIC_IPV4_063@ 60000 @PUBLIC_IPV4_062@ 22"))
    calls: list[tuple[str, ...]] = []

    def read(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, "interface: en7\n" if argv[0] == "/sbin/route" else _public_fixture("@PUBLIC_IPV4_062@\n"), ""
        )

    monkeypatch.setattr("subprocess.run", read)
    management(coordinates())
    assert len(calls) == 2
    assert not usable(_public_fixture("@PUBLIC_IPV4_062@"))  # Wi-Fi data admission was not loosened.
    monkeypatch.setenv("SSH_CONNECTION", _public_fixture("@PUBLIC_IPV4_063@ 60000 @PUBLIC_IPV4_061@ 22"))
    with pytest.raises(c.Refused, match="current_ipv4_oob_session"):
        management(coordinates())
    assert len(calls) == 2


def test_three_consecutive_successes_bind_fresh_scope() -> None:
    fake, gate = Readbacks(), Qualifier()
    proof = fake.qualify(gate)
    assert proof["passed"] is True
    assert proof["consecutive_successes"] == 3
    assert proof["failures"] == 0
    assert fake.calls == ["relay", "target"] * 3
    validate_proof(proof, coordinates(), "preflight", fake.now())


@pytest.mark.parametrize(
    "load1,load5,allowed", [(10.5, 14.0, True), (10.501, 14, False), (10.5, 14.001, False)]
)
def test_relay_fourteen_core_limits_are_not_loosened(
    load1: float, load5: float, allowed: bool
) -> None:
    assert c.RELAY_MAX_LOAD1_PER_CORE == 0.75
    assert c.RELAY_MAX_LOAD5_PER_CORE == 1.0
    sample = {"nonce": "synthetic", "node": "relay", "cores": 14, "load1": load1, "load5": load5}
    if allowed:
        load_ok(sample, "synthetic")
    else:
        with pytest.raises(c.Refused, match="relay_load_too_high"):
            load_ok(sample, "synthetic")


def test_one_timeout_then_three_successes_retains_failure_context() -> None:
    fake, gate = Readbacks(), Qualifier()
    fake.timeouts = 1
    proof = fake.qualify(gate)
    attempts = proof["attempts"]
    assert isinstance(attempts, list) and len(attempts) == 4
    assert c.obj(attempts[0])["reason"] == "top10_oob_probe_timeout"
    assert proof["consecutive_successes"] == 3 and proof["failures"] == 1
    assert fake.calls == ["relay", "target"] * 4


def test_flaky_oob_stops_after_second_failure() -> None:
    fake, gate = Readbacks(), Qualifier()
    fake.timeouts = 2
    with pytest.raises(c.Refused, match="flaky_or_incomplete"):
        fake.qualify(gate)
    assert gate.evidence["passed"] is False
    assert gate.evidence["failures"] == 2
    assert fake.calls == ["relay", "target"] * 2


@pytest.mark.parametrize(
    "update",
    [
        {"load1": 3.01},
        {"load5": 4.01},
        {"load1": None},
        {"load5": float("nan")},
        {"cores": 0},
        {"cores": True},
        {"node": "not-relay"},
        {"nonce": "f" * 32},
    ],
)
def test_overloaded_or_unproven_relay_never_contacts_target(update: dict[str, object]) -> None:
    fake, gate = Readbacks(), Qualifier()
    fake.load_update = update
    with pytest.raises(c.Refused):
        fake.qualify(gate)
    assert gate.evidence["passed"] is False
    assert fake.calls == ["relay"]
    assert "reason" in gate.evidence
    c.digest(gate.evidence)  # Invalid external numbers must not break failure persistence.


@pytest.mark.parametrize(
    "update",
    [
        {"server": _public_fixture("@PUBLIC_IPV4_061@")},
        {"interface": "en0"},
        {"address": _public_fixture("@PUBLIC_IPV4_005@")},
        {"client": _public_fixture("@PUBLIC_IPV4_064@")},
        {"host_verified": False},
        {"nonce": "f" * 32},
    ],
)
def test_changed_pin_identity_peer_or_nonce_refuses(update: dict[str, object]) -> None:
    fake, gate = Readbacks(), Qualifier()
    fake.target_update = update
    with pytest.raises(c.Refused, match="pinned_oob_readback"):
        fake.qualify(gate)
    assert fake.calls == ["relay", "target"]


def test_stale_replayed_or_different_scope_cannot_admit() -> None:
    fake, gate = Readbacks(), Qualifier()
    proof = fake.qualify(gate)
    for target, scope, now in (
        (coordinates(), "preflight", fake.now() + 91),
        (coordinates(), "abcdef123456", fake.now()),
        (coordinates() | {"manifest_sha256": "b" * 64}, "preflight", fake.now()),
    ):
        with pytest.raises(c.Refused):
            validate_proof(proof, target, scope, now)
    replay = copy.deepcopy(proof)
    attempts = replay["attempts"]
    assert isinstance(attempts, list)
    attempts[1] = attempts[0]
    with pytest.raises(c.Refused, match="replayed_oob_probe"):
        validate_proof(replay, coordinates(), "preflight", fake.now())


def test_root_ledger_consumes_scope_and_nonces(tmp_path: Path) -> None:
    fake, gate = Readbacks(), Qualifier()
    proof = fake.qualify(gate)
    attempt = Attempt(tmp_path, "a" * 64)
    attempt.path.mkdir()
    attempt.record_oob(proof)
    for replay in (proof, proof | {"scope": "abcdef123456"}):
        with pytest.raises(c.Refused, match="qualification_replay"):
            attempt.record_oob(replay)
    assert len(list(attempt.path.glob("oob-*.json"))) == 1


def test_control_argv_cannot_fall_back_to_wifi_or_reuse_master() -> None:
    argv = target_ssh("synthetic-oob")
    assert argv[argv.index("-J") : argv.index("-J") + 2] == ("-J", "relay")
    for option in (
        _public_fixture("HostName=@PUBLIC_IPV4_062@"),
        "StrictHostKeyChecking=yes",
        "ControlMaster=no",
        "ControlPath=none",
    ):
        assert option in argv
    assert argv[-1] == "synthetic-oob"
    with pytest.raises(c.Refused):
        target_ssh("alias;echo bad")


def test_total_deadline_is_not_extended_by_slow_successes() -> None:
    fake, gate = Readbacks(), Qualifier()

    def slow(kind: str, nonce: str, timeout: float) -> dict[str, object]:
        result = fake.probe(kind, nonce, timeout)
        fake.time += 12
        return result

    with pytest.raises(c.Refused, match="total_timeout"):
        gate.qualify(
            coordinates(), "preflight", slow, now=fake.now, steady=fake.now, pause=fake.pause
        )
    assert gate.evidence["passed"] is False
