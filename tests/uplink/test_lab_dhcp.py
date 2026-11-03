"""Bounded DHCP handshakes and durable setup diagnostics, without live networking."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.uplink import lab_worker
from offline_agent.uplink.lab_protocol import LabSpec


@pytest.mark.parametrize("response", ("delayed_offer", "no_offer", "timeout"))
def test_dhcp_waits_for_delayed_offer_but_never_retries_failed_acquisition(
    monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    spec = LabSpec("0123456789ab")
    calls: list[tuple[str, ...]] = []

    def execute(argv: tuple[str, ...], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:4] == ("ip", "netns", "exec", spec.client)
        assert argv[4:6] == ("busybox", "udhcpc")
        wait = int(argv[argv.index("-t") + 1]) * int(argv[argv.index("-T") + 1])
        timeout = options["timeout"]
        assert isinstance(timeout, (int, float)) and wait < timeout <= 10
        assert argv[argv.index("-s") + 1] == str(spec.state / "lease-hook.sh")
        if response == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout, stderr=b"DHCP server stalled")
        # The recorded offer arrived at 3.2 s. Preserve at least one second of margin.
        accepted = response == "delayed_offer" and wait >= 5
        return subprocess.CompletedProcess(
            argv, 0 if accepted else 1, "lease acquired" if accepted else "", "no lease"
        )

    monkeypatch.setattr(subprocess, "run", execute)
    if response == "delayed_offer":
        lab_worker.renew(spec)
    else:
        with pytest.raises(lab_worker.LabCommandFailed) as failure:
            lab_worker.renew(spec)
        assert failure.value.details["stderr"] == (
            "DHCP server stalled" if response == "timeout" else "no lease"
        )
    assert len(calls) == 1


@pytest.mark.parametrize("log", ("present", "oversize", "missing"))
def test_setup_failure_persists_bounded_server_diagnostics_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: str
) -> None:
    spec = LabSpec("0123456789ab")
    monkeypatch.setattr(LabSpec, "state", property(lambda _: tmp_path))
    state: dict[str, object] = {}
    error = lab_worker.LabCommandFailed(
        ("busybox", "udhcpc"), subprocess.CompletedProcess([], 1, "", "no lease")
    )
    contents = "DHCPOFFER without DHCPREQUEST\n"
    if log != "missing":
        (tmp_path / "dns.log").write_text(("x" * 9000 if log == "oversize" else "") + contents)

    def fail_setup(_: LabSpec, owner: str) -> dict[str, object]:
        state["setup_owner"] = owner
        (tmp_path / "state.json").write_text("{}")
        raise error

    def cleanup(*args: object, **kwargs: object) -> dict[str, object]:
        persisted = json.loads((tmp_path / "setup-failure.json").read_text())
        assert persisted == error.details
        assert persisted["stderr"] == "no lease"
        if log == "missing":
            assert persisted["dns_log_failure"] == "FileNotFoundError"
        else:
            assert persisted["dns_log"].endswith(contents)
            assert len(persisted["dns_log"]) <= 8192
        return {"closed": True}

    cleaned = Mock(side_effect=cleanup)
    monkeypatch.setattr(lab_worker, "setup_owned", fail_setup)
    monkeypatch.setattr(lab_worker, "load", lambda _: state)
    monkeypatch.setattr(lab_worker, "down", cleaned)
    with pytest.raises(lab_worker.LabCommandFailed) as raised:
        lab_worker.setup(spec)
    assert raised.value is error
    cleaned.assert_called_once_with(spec, state, partial=True)
