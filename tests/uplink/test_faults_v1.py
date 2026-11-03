"""Hermetic fault-boundary pins. No operating-system or provider effects are possible."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import io
import plistlib
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from offline_agent.uplink import faults_v1 as c
from offline_agent.uplink import faults_v1_worker as w
from offline_agent.uplink import real_worker


def spec(fault: str = "test_host_blackhole") -> c.FaultSpec:
    return c.FaultSpec(
        "0123456789ab",
        fault,
        _public_fixture("@PUBLIC_IPV4_094@"),
        _public_fixture("@PUBLIC_IPV6_097@"),
        "en0",
        (_public_fixture("@PUBLIC_IPV4_004@/8"), _public_fixture("@PUBLIC_IPV4_052@/10"), _public_fixture("@PUBLIC_IPV4_077@/16"), _public_fixture("@PUBLIC_IPV6_110@/8")),
        (_public_fixture("@PUBLIC_IPV4_055@"), _public_fixture("@PUBLIC_IPV4_087@")),
    )


def snapshot(s: c.FaultSpec) -> dict[str, str]:
    values = {k: "unchanged\n" for k in c.snapshot_commands(s)}
    values.update(
        default_ipv4="interface: en0\n",
        wifi_info="IPv6: Automatic\n",
        pf_main_rules='anchor "com.apple/*" all\n',
        pf_anchor_rules="",
        pf_anchor_children="",
        pf_anchor_tables="",
    )
    return values


def facts() -> dict[str, object]:
    return {
        **dict.fromkeys(
            (
                "ssh_ipv4_verified",
                "oob_ipv4_verified",
                "tailscale_inventory_verified",
                "protected_inventory_verified",
                "target_dns_binding_verified",
                "target_not_management_or_resolver",
                "target_probe_passed",
                "online_six_passed",
                "protected_services_unchanged",
                "no_existing_target_host_route",
                "pf_enabled",
                "no_target_pf_states",
            ),
            True,
        ),
        "checked_at": time.time(),
    }


def window(s: c.FaultSpec) -> dict[str, object]:
    return {
        "kind": "KAYLIN-OK-FAULTS-V1",
        "authorized": True,
        "spec_sha256": c.digest(s.document()),
        "package_sha256": "a" * 64,
        "exclusive": True,
        "r2_handback_verified": True,
        "starts_at": "2026-10-28T10:00:00-05:00",
        "expires_at": "2026-10-28T11:00:00-05:00",
    }


@pytest.mark.parametrize("fault", c.FAULTS)
def test_packages_bind_exact_repair_snapshot_and_60_second_revert(fault: str) -> None:
    s = spec(fault)
    plan = c.commands(s)
    c.validate_before(s, snapshot(s), facts())
    assert plan["auto_revert_seconds"] == 60
    action = {
        "version": 1,
        "tool": s.tool,
        "snapshot_id": "snapshot-test",
        "policy": "snapshot",
        "evidence_ids": ["fault-v1-target", "fault-v1-drift"],
    }
    assert c.parse_action(action, s, "snapshot-test") == action
    assert c.parse_spec(s.document()) == s
    assert (
        w.dispatch({"operation": "inject", "spec": s.document(), "payload": {}}, execute=False)[
            "effects"
        ]
        is False
    )
    if fault == "test_host_blackhole":
        assert plan["inject"] == [
            "/sbin/route",
            "-n",
            "add",
            "-host",
            "-ifscope",
            s.wifi_interface,
            s.target_ipv4,
            _public_fixture("@PUBLIC_IPV4_056@"),
            "-blackhole",
        ]
        assert "default" not in cast(list[str], plan["inject"])
    elif fault == "wifi_ipv6_off":
        assert plan["revert"] == ["/usr/sbin/networksetup", "-setv6automatic", "Wi-Fi"]
    else:
        assert plan["revert"] == ["/sbin/pfctl", "-a", s.anchor, "-F", "rules"]
        assert " out quick " in str(plan["pf_rule"]) and "port 443" in str(plan["pf_rule"])


@pytest.mark.parametrize(
    "address",
    [_public_fixture("@PUBLIC_IPV4_056@"), _public_fixture("@PUBLIC_IPV4_005@"), _public_fixture("@PUBLIC_IPV4_053@"), _public_fixture("@PUBLIC_IPV4_079@"), _public_fixture("@PUBLIC_IPV4_001@"), _public_fixture("@PUBLIC_IPV4_090@"), _public_fixture("@PUBLIC_IPV4_094@;id")],
)
def test_nonpublic_destinations_are_never_fault_targets(address: str) -> None:
    with pytest.raises(ValueError):
        replace(spec(), target_ipv4=address)


def test_explicit_protection_and_unknown_interface_refuse() -> None:
    with pytest.raises(ValueError, match="protected_network"):
        replace(spec(), protected_networks=(_public_fixture("@PUBLIC_IPV4_093@/24"),))
    for interface in ("en7", "utun0", "bridge0", "en0;id"):
        with pytest.raises(ValueError):
            replace(spec(), wifi_interface=interface)
    with pytest.raises(ValueError):
        replace(spec(), protected_networks=())


@pytest.mark.parametrize("missing", [k for k in facts() if k != "checked_at"])
def test_each_before_state_gate_is_required(missing: str) -> None:
    value = facts()
    value[missing] = False
    with pytest.raises(ValueError):
        c.validate_before(spec("owned_pf_outbound"), snapshot(spec()), value)


def test_ipv6_only_restores_an_admitted_automatic_configuration() -> None:
    s = spec("wifi_ipv6_off")
    state = snapshot(s)
    for value in ("IPv6: Off", "IPv6: Manual", ""):
        state["wifi_info"] = value
        with pytest.raises(ValueError, match="automatic_ipv6"):
            c.validate_before(s, state, facts())


def test_pf_never_installs_a_parent_hook_or_clears_foreign_rules() -> None:
    s = spec("owned_pf_outbound")
    state = snapshot(s)
    state["pf_main_rules"] = ""
    with pytest.raises(ValueError, match="parent_hook"):
        c.validate_before(s, state, facts())
    state = snapshot(s)
    state["pf_anchor_rules"] = "pass in all"
    with pytest.raises(ValueError, match="empty_anchor"):
        c.validate_before(s, state, facts())


def test_empty_snapshot_and_partial_diff_are_not_success() -> None:
    with pytest.raises(ValueError):
        c.validate_before(spec(), {}, facts())
    with pytest.raises(ValueError):
        c.restoration({}, {})
    with pytest.raises(ValueError):
        c.restoration({"a": "x"}, {})
    assert c.restoration({"a": "x"}, {"a": "y"})["match"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"authorized": False},
        {"exclusive": False},
        {"r2_handback_verified": False},
        {"spec_sha256": "0" * 64},
        {"package_sha256": "0" * 64},
        {"kind": "Fleet OK"},
        {"starts_at": "2026-10-28T10:00:00"},
    ],
)
def test_old_broad_or_mismatched_windows_never_enable_new_faults(change: dict[str, object]) -> None:
    s = spec()
    value = window(s)
    value.update(change)
    with pytest.raises(ValueError):
        c.admit_window(value, s, 1793199660, "a" * 64)


def test_valid_window_and_model_actions_remain_exact() -> None:
    from datetime import datetime

    s = spec()
    now = datetime.fromisoformat("2026-10-28T10:01:00-05:00").timestamp()
    c.admit_window(window(s), s, now, "a" * 64)
    for invalid in ({}, {"tool": "shell"}, {"version": 1, "tool": s.tool, "argv": ["id"]}):
        with pytest.raises(ValueError, match="action_refused"):
            c.parse_action(invalid, s, "s")
    with pytest.raises(ValueError):
        c.admit_window(window(s), s, now + 3600, "a" * 64)


@pytest.mark.parametrize("fault", c.FAULTS)
def test_root_repair_runs_only_its_exact_inverse_and_pins_the_diff(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = spec(fault)
    saved = snapshot(s)
    saved.update(target_route="destination: default\ninterface: en0\n", pf_status="Enabled")
    current = dict(saved)
    if fault == "test_host_blackhole":
        current["target_route"] = (
            _public_fixture(f"destination: {s.target_ipv4}\ngateway: @PUBLIC_IPV4_056@\nflags: BLACKHOLE\n")
        )
    elif fault == "wifi_ipv6_off":
        current["wifi_info"] = "IPv6: Off\n"
    else:
        current["pf_anchor_rules"] = "owned block rule\n"
    writes: list[tuple[Path, dict[str, object]]] = []
    events = []
    monkeypatch.setattr(w, "protected", lambda _: None)
    monkeypatch.setattr(w, "read_snapshot", lambda _: saved)
    monkeypatch.setattr(w, "snapshot", Mock(side_effect=[current, saved]))
    command = Mock(return_value="")
    monkeypatch.setattr(w, "run", command)
    monkeypatch.setattr(real_worker, "private", lambda p, v: writes.append((p, dict(v))))
    monkeypatch.setattr(real_worker, "audit", lambda *a: events.append(a))
    state: dict[str, object] = {"spec": s.document(), "lifecycle": "injected", "ready": True}
    result = w.restore(tmp_path, state, "typed_agent_snapshot_repair")
    assert result["match"] is True and state["lifecycle"] == "restored"
    command.assert_called_once_with(tuple(cast(list[str], c.commands(s)["revert"])))
    assert events[0][1:] == ("restore", "typed_agent_snapshot_repair")
    assert any(p.name == "restoration.json" for p, _ in writes)


def test_failed_restore_is_persisted_and_cannot_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, object] = {"spec": spec().document(), "lifecycle": "injected"}
    writes = []
    monkeypatch.setattr(real_worker, "audit", lambda *a: None)
    monkeypatch.setattr(real_worker, "private", lambda *a: writes.append(a))
    monkeypatch.setattr(w, "protected", Mock(side_effect=ValueError("protected_changed")))
    with pytest.raises(ValueError, match="failed_no_retry"):
        w.restore(tmp_path, state, "abort")
    assert state["lifecycle"] == "rollback_failed" and writes
    with pytest.raises(ValueError, match="failed_no_retry"):
        w.restore(tmp_path, state, "again")


def test_armed_readiness_and_remaining_budget_cannot_be_faked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(w, "remaining", lambda _: 19.9)
    with pytest.raises(ValueError):
        w.require_armed({"ready": True, "lifecycle": "armed"})
    monkeypatch.setattr(w, "remaining", lambda _: 55)
    for state in ({"ready": False, "lifecycle": "armed"}, {"ready": True, "lifecycle": "restored"}):
        with pytest.raises(ValueError):
            w.require_armed(state)
    w.require_armed({"ready": True, "lifecycle": "injected"})


def test_six_check_oracle_cannot_pass_on_label_or_missing_evidence(online_result: Any) -> None:
    row = online_result.as_json()
    row["checked_at"] = time.time()
    w.online(row)
    changes: tuple[dict[str, object], ...] = (
        {"evidence": []},
        {"verdict": "ONLINE", "checked_at": 0},
        {"version": "model-guess"},
    )
    for change in changes:
        with pytest.raises(ValueError):
            w.online({**row, **change})


def test_root_execution_is_forbidden_inside_tests() -> None:
    with pytest.raises(ValueError, match="forbidden_in_tests"):
        w.dispatch({"operation": "inject", "spec": spec().document(), "payload": {}}, execute=True)


def test_arm_installs_detached_root_watchdog_once_with_fixed_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = spec()
    directory = tmp_path / "runs" / s.run_id
    directory.mkdir(parents=True)
    state: dict[str, object] = {
        "spec": s.document(),
        "window": window(s),
        "package_sha256": "a" * 64,
        "lifecycle": "prepared",
        "ready": False,
    }
    monkeypatch.setattr(w, "BASE", tmp_path)
    monkeypatch.setattr(real_worker, "require_lab", lambda _: None)
    monkeypatch.setattr(real_worker, "secure_directory", lambda _: None)
    monkeypatch.setattr(
        real_worker, "read", lambda p: {"run_id": s.run_id} if p.name == "active.json" else state
    )
    monkeypatch.setattr(real_worker, "private", lambda *a: None)
    monkeypatch.setattr(real_worker, "audit", lambda *a: None)
    monkeypatch.setattr(real_worker, "steady_time", lambda: 100.0)
    monkeypatch.setattr(c, "admit_window", lambda *a: None)
    command = Mock(return_value="")
    monkeypatch.setattr(w, "run", command)
    result = w.dispatch({"operation": "arm", "spec": s.document(), "payload": {}}, execute=True)
    assert result == {"armed": True, "ready": False, "seconds": 60}
    assert state["deadline_mono"] == 160.0
    with (directory / "watchdog.plist").open("rb") as f:
        job = plistlib.load(f)
    assert job["RunAtLoad"] is True and job["KeepAlive"] == {"SuccessfulExit": False}
    assert job["ProgramArguments"] == [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(directory / "faults_v1_worker.py"),
        "--watchdog",
        s.run_id,
    ]
    command.assert_called_once_with(
        ("/bin/launchctl", "bootstrap", "system", str(directory / "watchdog.plist"))
    )
    with pytest.raises(ValueError, match="cannot_rearm"):
        w.dispatch({"operation": "arm", "spec": s.document(), "payload": {}}, execute=True)
    assert state["deadline_mono"] == 160.0


@pytest.mark.parametrize("clock_error", [False, True])
def test_detached_watchdog_restores_on_expiry_or_unknown_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock_error: bool
) -> None:
    s = spec()
    (tmp_path / "lock").touch()
    state: dict[str, object] = {"spec": s.document(), "lifecycle": "injected", "ready": True}
    monkeypatch.setattr(w, "BASE", tmp_path)
    monkeypatch.setattr(real_worker, "require_lab", lambda _: None)
    monkeypatch.setattr(real_worker, "read", lambda _: state)
    monkeypatch.setattr(real_worker, "private", lambda *a: None)
    monkeypatch.setattr(
        w,
        "remaining",
        Mock(side_effect=ValueError("clock") if clock_error else None, return_value=0),
    )
    restore = Mock(return_value={"match": True})
    monkeypatch.setattr(w, "restore", restore)
    w.watchdog(s.run_id)
    restore.assert_called_once_with(tmp_path / "runs" / s.run_id, state, "detached_deadline_revert")
    assert "finished_at" in state


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("preinject_snapshot_drift", "preinject_snapshot_drift"),
        ("private raw diagnostic with spaces", "ValueError"),
    ],
)
def test_cli_retains_closed_failure_labels_without_echoing_private_diagnostics(
    reason: str, expected: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["fault-worker"])
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"{}")))
    monkeypatch.setattr(w, "dispatch", Mock(side_effect=ValueError(reason)))
    with pytest.raises(SystemExit):
        w.main()
    assert capsys.readouterr().err == "fault_package_refused:" + expected + "\n"


@pytest.mark.parametrize("address", [_public_fixture("@PUBLIC_IPV4_094@"), _public_fixture("@PUBLIC_IPV6_097@")])
def test_recorded_tailscale_control_or_derp_address_is_never_a_target(address: str) -> None:
    with pytest.raises(ValueError, match="tailscale_management"):
        replace(spec(), tailscale_addresses=(address,))
    with pytest.raises(ValueError, match="inventory_required"):
        replace(spec(), tailscale_addresses=())


def test_host_route_uses_the_same_pinned_target_without_resolving_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo", Mock(side_effect=AssertionError("never resolve during inverse"))
    )
    s = spec()
    plan = c.commands(s)
    assert cast(list[str], plan["inject"])[6] == cast(list[str], plan["revert"])[6] == s.target_ipv4
    assert c.snapshot_commands(s)["ipv4_route_table"] == ("/usr/sbin/netstat", "-rn", "-f", "inet")
    assert (
        c.restoration({"ipv4_route_table": "old"}, {"ipv4_route_table": "changed"})["match"]
        is False
    )


def test_status_token_drift_still_flushes_only_owned_anchor_then_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = spec("owned_pf_outbound")
    saved = snapshot(s)
    saved.update(pf_status="Status: Enabled", target_route="unchanged")
    current = {
        **saved,
        "pf_status": "Status: Disabled",
        "pf_anchor_rules": "owned block",
    }
    after = {**current, "pf_anchor_rules": ""}
    writes = []
    monkeypatch.setattr(w, "protected", lambda _: None)
    monkeypatch.setattr(w, "read_snapshot", lambda _: saved)
    monkeypatch.setattr(w, "snapshot", Mock(side_effect=[current, after]))
    command = Mock()
    monkeypatch.setattr(w, "run", command)
    monkeypatch.setattr(real_worker, "private", lambda p, v: writes.append((p, dict(v))))
    monkeypatch.setattr(real_worker, "audit", lambda *a: None)
    state: dict[str, object] = {"spec": s.document(), "lifecycle": "injected"}
    with pytest.raises(ValueError, match="failed_no_retry"):
        w.restore(tmp_path, state, "typed_agent_snapshot_repair")
    command.assert_called_once_with(("/sbin/pfctl", "-a", s.anchor, "-F", "rules"))
    abort = next(v for p, v in writes if p.name == "abort.json")
    assert abort["stop_series"] is abort["operator_notification_required"] is True
    assert abort["automatic_second_fix_forbidden"] is True
    with pytest.raises(ValueError, match="failed_no_retry"):
        w.restore(tmp_path, state, "second_fix")


@pytest.mark.parametrize("status", ["", "state = waiting\npid = 123", "state = running\npid = 124"])
def test_detached_watchdog_requires_the_exact_running_pid(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(w, "run", lambda *a: status)
    with pytest.raises(ValueError):
        w.verify_watchdog(spec(), {"watchdog_pid": 123})
    with pytest.raises(ValueError):
        w.verify_watchdog(spec(), {})


def test_pf_status_scope_ignores_uptime_but_never_enabled_state() -> None:
    assert w.pf_status_token("Status: Enabled for 1 second\nDebug: urgent") == "Status: Enabled"
    assert w.pf_status_token("Status: Enabled for 2 days\nDebug: urgent") == "Status: Enabled"
    assert w.pf_status_token("Status: Disabled\n") == "Status: Disabled"
    with pytest.raises(ValueError):
        w.pf_status_token("unparsed status")


def test_new_interface_or_any_route_table_drift_is_a_failed_diff() -> None:
    commands = c.snapshot_commands(spec())
    assert commands["interface_inventory"] == ("/sbin/ifconfig", "-l")
    assert commands["ipv6_route_table"] == ("/usr/sbin/netstat", "-rn", "-f", "inet6")
    before = {
        "interface_inventory": "en0 en7 utun0",
        "ipv4_route_table": "pinned-v4",
        "ipv6_route_table": "pinned-v6",
    }
    for field in before:
        assert c.restoration(before, {**before, field: "new state"})["match"] is False
