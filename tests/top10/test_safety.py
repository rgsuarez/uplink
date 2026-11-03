"""Medium, network-denied tests of the operator's frozen safety invariants."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink import top10_injectors as plans
from offline_agent.uplink.top10_engine import Engine
from offline_agent.uplink.top10_worker import sandbox_engine, start_sandbox

ROOT = Path(__file__).resolve().parents[2]


def wait_exit(engine: Engine) -> None:
    until = time.monotonic() + 4
    while not (engine.directory / "watchdog-exited.json").exists():
        if time.monotonic() >= until:
            pytest.fail("detached watchdog did not exit")
        time.sleep(0.02)


@pytest.fixture
def trial(tmp_path: Path) -> Iterator[Callable[[str], Engine]]:
    engines: list[Engine] = []

    def create(fault: str = "F04") -> Engine:
        engine = start_sandbox(tmp_path / str(len(engines)), f"{len(engines):012x}", fault)
        engines.append(engine)
        return engine

    yield create
    for engine in engines:
        if engine.state()["lifecycle"] in c.ACTIVE:
            engine.restore(actor="controller_cleanup")
        wait_exit(engine)


@pytest.mark.parametrize("fault", c.FAULT_IDS)
def test_every_registered_injector_observes_effect_and_restores(
    trial: Callable[[str], Engine], fault: str
) -> None:
    engine = trial(fault)
    injected = engine.inject()
    assert injected["fault_effective"] is True
    assert injected["watchdog_restore"] is False
    restored = engine.restore(actor="controller_cleanup")
    assert restored["lifecycle"] == "restored"
    assert c.baseline_ok(engine.observe()["facts"])
    assert engine.close()["lifecycle"] == "closed"
    assert engine.state()["completed_model_actions"] == []


def test_watchdog_expiry_restores_without_controller(trial: Callable[[str], Engine]) -> None:
    engine = trial("F08")
    engine.inject()
    engine.advance(46)
    wait_exit(engine)
    state = engine.state()
    assert state["lifecycle"] == "restored"
    assert state["watchdog_restore"] is True
    assert state["controller_cleanup"] is False
    assert c.baseline_ok(engine.observe()["facts"])


def test_controller_process_exit_does_not_kill_watchdog(tmp_path: Path) -> None:
    directory = tmp_path / "crashed-controller"
    source = (
        "import os; from pathlib import Path; "
        "from offline_agent.uplink.top10_worker import start_sandbox; "
        f"e=start_sandbox(Path({str(directory)!r}), 'abcdef123456', 'F01'); "
        "e.inject(); e.advance(46); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-B", "-c", source], timeout=5, check=False)
    assert result.returncode == 0
    engine = sandbox_engine(directory)
    wait_exit(engine)
    assert engine.state()["lifecycle"] == "restored"
    assert engine.state()["watchdog_restore"] is True
    assert c.baseline_ok(engine.observe()["facts"])


def test_restore_failure_is_terminal_and_audited(
    trial: Callable[[str], Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = trial("F04")
    engine.inject()

    def fail(*args: object) -> dict[str, bool]:
        raise c.Refused("synthetic_restore_failure")

    monkeypatch.setattr(plans, "restore", fail)
    with pytest.raises(c.Refused, match="synthetic_restore_failure"):
        engine.restore(actor="controller_cleanup")
    with pytest.raises(c.Refused, match="requires_operator"):
        engine.restore(actor="controller_cleanup")
    assert engine.state()["lifecycle"] == "restore_failed"
    assert engine.state()["last_reason"] == "synthetic_restore_failure"
    assert "synthetic_restore_failure" in (engine.directory / "audit.jsonl").read_text()


def test_duplicate_invocations_cannot_inject_again(trial: Callable[[str], Engine]) -> None:
    engine = trial("F01")
    engine.inject()
    with pytest.raises(c.Refused, match="not_repeatable"):
        engine.inject()
    with pytest.raises(c.Refused, match="new_registered_trial"):
        engine.prepare("000000000000", "F01")
    assert engine.state()["lifecycle"] == "fault_verified"


def test_protected_drift_blocks_injection(trial: Callable[[str], Engine]) -> None:
    engine = trial("F02")
    path = engine.directory / "fake-kernel.private.json"
    state = c.read(path)
    c.save(path, state | {"protected": "changed"})
    try:
        with pytest.raises(c.Refused, match="protected_configuration_changed"):
            engine.inject()
        assert engine.state()["lifecycle"] == "armed"
        assert c.read(path)["interface_up"] is True
    finally:
        c.save(path, state)


def test_f10_rejects_f05_and_unrelated_failure(trial: Callable[[str], Engine]) -> None:
    engine = trial("F10")
    snapshot = engine.snapshot()
    plans.inject(engine.kernel, "F05", snapshot)
    facts = engine.kernel.facts(snapshot)
    assert engine.kernel.negative_control("F10", facts) is False
    plans.inject(engine.kernel, "F10", snapshot)
    facts = engine.kernel.facts(snapshot)
    assert engine.kernel.negative_control("F10", facts) is True
    plans.inject(engine.kernel, "F04", snapshot)
    facts = engine.kernel.facts(snapshot)
    assert engine.kernel.negative_control("F10", facts) is False


def test_probe_does_not_hold_restore_lock(
    trial: Callable[[str], Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = trial("F04")
    original = engine.kernel.facts

    def measuring(snapshot: dict[str, object]) -> dict[str, bool]:
        # A restore in the middle of admission must win and invalidate injection.
        monkeypatch.setattr(engine.kernel, "facts", original)
        engine.restore(actor="watchdog")
        return original(snapshot)

    monkeypatch.setattr(engine.kernel, "facts", measuring)
    with pytest.raises(c.Refused, match="not_repeatable"):
        engine.inject()
    assert engine.state()["lifecycle"] == "restored"


@pytest.mark.parametrize("value", [None, {}, {"radio_on": True}, dict.fromkeys(c.FACTS, 1)])
def test_missing_or_unparsed_measurements_refuse(value: object) -> None:
    with pytest.raises(c.Refused):
        c.measured(value)


def test_existing_frozen_oracles_and_definition_are_unchanged() -> None:
    assert len(c.cases(ROOT)) == 10
    assert c.file_hash(ROOT / "tests/uplink/test_oracle.py") == c.EDGE_ORACLE_SHA256


def test_sandbox_denies_outbound_use() -> None:
    if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        pytest.skip("containment assertion requires the dedicated sandbox wrapper")
    import socket

    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection,
        pytest.raises(PermissionError),
    ):
        connection.connect((_public_fixture("@PUBLIC_IPV4_066@"), 9))


def test_nonfinite_and_symlink_input_refuse(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"deadline":NaN}')
    with pytest.raises(c.Refused, match="nonfinite"):
        c.read(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(c.Refused, match="symlink"):
        c.read(link)


def test_snapshot_tampering_cannot_be_used_for_action(trial: Callable[[str], Engine]) -> None:
    engine = trial("F04")
    engine.inject()
    path = engine.directory / "snapshot.json"
    snapshot = c.read(path)
    c.save(path, snapshot | {"mtu": 1400})
    try:
        with pytest.raises(c.Refused, match="snapshot_changed"):
            engine.apply("restore_dns", str(engine.state()["snapshot_id"]))
        assert engine.state()["completed_model_actions"] == []
    finally:
        c.save(path, snapshot)


def test_stale_proxy_requires_measured_closed_port(tmp_path: Path) -> None:
    from offline_agent.uplink.top10_kernel import MacKernel

    kernel = MacKernel(tmp_path, {})
    facts = {
        name: name
        not in {"ipv4_linklocal", "portal_redirect_seen", "proxy_matches_snapshot", "https_path_ok"}
        for name in c.FACTS
    }
    assert c.fault_effect("F06", facts) is True
    assert kernel.negative_control("F06", facts) is False
    kernel.last_capture["closed_loopback_proxy"] = False
    assert kernel.negative_control("F06", facts) is False
    kernel.last_capture["closed_loopback_proxy"] = True
    assert kernel.negative_control("F06", facts) is True
