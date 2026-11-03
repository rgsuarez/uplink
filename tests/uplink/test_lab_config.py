"""Bounded file fixtures: private coordinates cannot broaden live device authority."""

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from offline_agent.uplink import lab_config
from offline_agent.uplink.lab_config import configured_host, load_config, parse_config
from offline_agent.uplink.real_fleet import approved_serial, require_exception
from offline_agent.uplink.remote_settings import RemoteTargets


def test_config_binds_transport_and_device_without_public_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config()
    monkeypatch.setattr("platform.node", lambda: "lab.example.test")
    assert configured_host()
    assert RemoteTargets.from_environment().oob_host == "lab-oob"
    assert RemoteTargets.from_environment().wifi_host == "lab-wifi"
    assert approved_serial() == "LABTEST0001"
    assert 'cd "$HOME"/lab-bench/uplink-u1' in config.bridge_command
    monkeypatch.setenv("UPLINK_OOB_HOST", "override-oob")
    assert RemoteTargets.from_environment().oob_host == "override-oob"
    assert approved_serial() == "LABTEST0001"  # Transport overrides cannot approve a device.


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"node": "-oProxyCommand=id"},
        {"oob_host": "lab-wifi"},
        {"wifi_host": "host;id"},
        {"bench_relative": "../escape"},
        {"source_relative": "/absolute"},
        {"python_relative": "tool/$(id)"},
        {"approved_serial": ""},
        {"approved_by": ""},
        {"approved_at": ""},
        {"clearance_file": "../go.md"},
        {"unexpected": "field"},
    ],
)
def test_invalid_private_config_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_config({**asdict(load_config()), **change})


@pytest.mark.parametrize("kind", ["missing", "empty", "invalid", "public", "symlink"])
def test_unusable_config_cannot_admit_a_host_or_managed_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    target = tmp_path / "lab.local.json"
    if kind == "symlink":
        target.symlink_to(lab_config.CONFIG_PATH)
    elif kind != "missing":
        target.write_text(
            ""
            if kind == "empty"
            else "{"
            if kind == "invalid"
            else json.dumps(asdict(load_config()))
        )
        target.chmod(0o644 if kind == "public" else 0o600)
    monkeypatch.setattr(lab_config, "CONFIG_PATH", target)
    monkeypatch.setattr("platform.node", lambda: "lab")
    with pytest.raises(ValueError):
        load_config()
    assert not configured_host()
    with pytest.raises(ValueError):
        require_exception("LABTEST0001", "wrong_dns", None)


def test_copied_run_config_stays_bound_after_source_changes(tmp_path: Path) -> None:
    # The watchdog reads its run-local snapshot, not a mutable controller setting.
    target = tmp_path / "lab.local.json"
    shutil.copyfile(lab_config.CONFIG_PATH, target)
    target.chmod(0o600)
    changed = {**asdict(load_config()), "approved_serial": "OTHERLAB001"}
    lab_config.CONFIG_PATH.write_text(json.dumps(changed))
    assert load_config(target).approved_serial == "LABTEST0001"
    assert load_config().approved_serial == "OTHERLAB001"
