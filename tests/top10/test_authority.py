"""Small boundary tests plus local package roundtrip. No target/model access."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
import types
from pathlib import Path

import pytest

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.top10_bridge import owner_handback
from offline_agent.uplink.top10_cli import plan
from offline_agent.uplink.top10_package import freeze, package
from offline_agent.uplink.top10_stage_receiver import (
    approved_module,
    check_authority,
    checked_members,
)

ROOT = Path(__file__).resolve().parents[2]


def window() -> dict[str, object]:
    return {
        "version": 1,
        "kind": "KAYLIN-OK-TOP10-FAULTS",
        "authorized": True,
        "approved_by": "Kaylin",
        "manifest_sha256": "a" * 64,
        "arm": "BASE",
        "server_pid": c.SERVER_PID,
        "server_birth": "Thu Oct 29 01:00:00 2026",
        "start": c.TIMELINE["start"],
        "end": c.TIMELINE["end"],
        "owner_handback_sha256": "b" * 64,
        "private_config_sha256": "c" * 64,
        "oob_interface": "en7",
        "oob_server_ipv4": _public_fixture("@PUBLIC_IPV4_062@"),
        "root_approval_sha256": "d" * 64,
        "oob_client_ipv4": _public_fixture("@PUBLIC_IPV4_010@"),
    }


def approval(row: dict[str, object], **updates: object) -> str:
    value = {
        "version": 1,
        "kind": "RICHARD-OK-TOP10-PRIVILEGED",
        "authorized": True,
        "approved_by": "Richard Suarez",
        "manifest_sha256": row["manifest_sha256"],
        "private_config_sha256": row["private_config_sha256"],
        "start": row["start"],
        "end": row["end"],
        "scopes": list(c.PRIVILEGED_SCOPES),
        "manual_recovery_authorized": False,
        "window_binding_sha256": c.digest(
            {k: v for k, v in row.items() if k != "root_approval_sha256"}
        ),
    } | updates
    text = json.dumps(value)
    row["root_approval_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return text


@pytest.mark.parametrize(
    "update",
    [
        {"authorized": False},
        {"approved_by": "Amanda"},
        {"arm": "unsupported"},
        {"version": True},
        {"server_pid": 1234},
        {"start": "2026-10-30T21:30:00"},
        {"oob_client_ipv4": _public_fixture("@PUBLIC_IPV4_056@")},
        {"extra": "ignored"},
        {"manifest_sha256": "d" * 64},
    ],
)
def test_live_authority_refuses_ambiguity(update: dict[str, object]) -> None:
    with pytest.raises(c.Refused):
        c.validate_window(
            window() | update, {"server_pid": c.SERVER_PID}, "a" * 64, c.WINDOW_START + 1
        )


def test_exact_window_and_post_window_cleanup() -> None:
    c.validate_window(window(), {"server_pid": c.SERVER_PID}, "a" * 64, c.WINDOW_START + 1)
    with pytest.raises(c.Refused, match="outside_window"):
        c.validate_window(window(), {"server_pid": c.SERVER_PID}, "a" * 64, c.WINDOW_END)
    c.validate_window(
        window(), {"server_pid": c.SERVER_PID}, "a" * 64, c.WINDOW_END + 1, cleanup=True
    )


def test_owner_release_is_fresh_and_bound() -> None:
    row = window()
    owner = {
        "kind": "TOP10-OWNER-HANDBACK",
        "released": True,
        "approved_by": "Kaylin",
        "manifest_sha256": "a" * 64,
        "arm": "BASE",
        "pid": c.SERVER_PID,
        "birth": row["server_birth"],
        "released_at": c.WINDOW_START - 1200,
    }
    text = json.dumps(owner)
    row["owner_handback_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    owner_handback(text, row, c.WINDOW_START + 1)
    envelope: dict[str, object] = {
        "window": row,
        "owner": text,
        "manifest_sha256": "a" * 64,
        "root_approval": approval(row),
    }
    check_authority(envelope, c.WINDOW_START + 1)
    with pytest.raises(ValueError, match="preflight_window"):
        check_authority(envelope, c.WINDOW_START + 601)
    with pytest.raises(c.Refused, match="binding_changed"):
        owner_handback(text + " ", row, c.WINDOW_START + 1)


@pytest.mark.parametrize(
    "released_offset,now_offset,allowed",
    [
        (-1500, 0, True),
        (-1500, 299, True),
        (-1500, 300, False),
        (-1501, 0, False),
        (-900, 899, True),
        (-900, 900, False),
        (-899, 0, False),
        (-1200, -1, False),
        (1, 0, False),
    ],
)
def test_friday_launch_boundaries_are_strict(
    released_offset: int, now_offset: int, allowed: bool
) -> None:
    assert c.WINDOW_END - c.WINDOW_START == 6000
    assert c.LATEST_START - c.WINDOW_START == 900
    assert c.OWNER_LOOKBACK_SECONDS == 1800
    now, released = c.WINDOW_START + now_offset, c.WINDOW_START + released_offset
    if allowed:
        c.validate_launch(now, released)
    else:
        with pytest.raises(c.Refused):
            c.validate_launch(now, released)


def test_stale_owner_blocks_start_but_not_running_cleanup() -> None:
    row = window()
    owner = {
        "kind": "TOP10-OWNER-HANDBACK",
        "released": True,
        "approved_by": "Kaylin",
        "manifest_sha256": "a" * 64,
        "arm": "BASE",
        "pid": c.SERVER_PID,
        "birth": row["server_birth"],
        "released_at": c.WINDOW_START - 1500,
    }
    raw = json.dumps(owner)
    row["owner_handback_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    owner_handback(raw, row, c.WINDOW_START, starting=True)
    with pytest.raises(c.Refused, match="expired_before_start"):
        owner_handback(raw, row, c.WINDOW_START + 300, starting=True)
    owner_handback(raw, row, c.WINDOW_END + 1)
    envelope = {
        "window": row,
        "owner": raw,
        "manifest_sha256": "a" * 64,
        "root_approval": approval(row),
    }
    with pytest.raises(ValueError, match="owner_release_required"):
        check_authority(envelope, c.WINDOW_START + 300)


@pytest.mark.parametrize(
    "birth,command,allowed",
    [
        ("Thu Oct 29 01:00:00 2026", "/synthetic/llama-server --port 8080", True),
        ("Thu Oct 29 01:01:00 2026", "/synthetic/llama-server", False),
        ("Thu Oct 29 01:00:00 2026", "am", False),
        ("Thu Oct 29 01:00:00 2026", "/usr/bin/python3 /tmp/llama-server", False),
        ("", "/synthetic/llama-server", False),
        ("Thu Oct 29 01:00:00 2026", "", False),
    ],
)
def test_root_server_guard_uses_full_command(
    monkeypatch: pytest.MonkeyPatch, birth: str, command: str, allowed: bool
) -> None:
    from offline_agent.uplink import top10_worker as worker

    queries: list[tuple[str, ...]] = []

    def read(argv: tuple[str, ...]) -> str:
        queries.append(argv)
        return {"comm=,lstart=": "am " + birth, "lstart=": birth, "command=": command}[argv[-1]]

    monkeypatch.setattr(worker, "run", read)
    if allowed:
        worker.server_guard(window())
    else:
        with pytest.raises(c.Refused, match="pid_19904_identity_changed"):
            worker.server_guard(window())
    assert queries == [
        ("/bin/ps", "-p", "19904", "-o", "lstart="),
        ("/bin/ps", "-p", "19904", "-o", "command="),
    ]


def test_duplicate_authority_fields_are_rejected() -> None:
    with pytest.raises(c.Refused, match="duplicate_json_key"):
        c.parse_json('{"authorized":false,"authorized":true}')


def test_richard_approval_is_exact_and_not_manual_recovery_authority() -> None:
    row = window()
    raw = approval(row)
    c.validate_root_approval(raw, row)
    with pytest.raises(c.Refused, match="binding_changed"):
        c.validate_root_approval(raw + " ", row)
    updates: tuple[dict[str, object], ...] = (
        {"authorized": False},
        {"approved_by": "Kaylin"},
        {"scopes": []},
        {"manual_recovery_authorized": True},
        {"manifest_sha256": "f" * 64},
        {"private_config_sha256": "e" * 64},
        {"window_binding_sha256": "d" * 64},
    )
    for update in updates:
        changed = window()
        with pytest.raises(c.Refused, match="exact_richard_privileged"):
            c.validate_root_approval(approval(changed, **update), changed)


def test_missing_approval_cannot_construct_live_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from offline_agent.uplink.top10_cli import live

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("missing approval reached transport")

    monkeypatch.setattr("subprocess.run", forbidden)
    with pytest.raises(c.Refused, match="richard_root_approval_required"):
        live(Path("GO-synthetic.json"), Path("owner.json"))


def test_root_reservation_refuses_unapproved_before_root_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink import top10_worker as worker

    row = window()
    raw = approval(row, authorized=False)
    touched: list[Path] = []
    monkeypatch.setattr(worker, "require_root", lambda _: None)
    monkeypatch.setattr(worker, "secure", touched.append)
    monkeypatch.setattr(c, "verify_manifest", lambda *_: {"server_pid": c.SERVER_PID})
    monkeypatch.setattr(c, "file_hash", lambda *_: "a" * 64)
    monkeypatch.setattr(time, "time", lambda: c.WINDOW_START + 1)
    with pytest.raises(c.Refused, match="exact_richard_privileged"):
        worker.dispatch(
            {
                "version": 1,
                "operation": "reserve_suite",
                "run_id": "abcdef123456",
                "payload": {
                    "source_root": "/synthetic",
                    "manifest_sha256": "a" * 64,
                    "window": row,
                    "root_approval": raw,
                    "oob_proof": {},
                    "detail": {},
                },
            },
            execute=True,
        )
    assert touched == []


def test_refused_oob_consumes_client_attempt_and_persists_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink.top10_backends import LiveSession
    from offline_agent.uplink.top10_suite import run

    source = tmp_path / "src/offline_agent/uplink"
    source.mkdir(parents=True)
    c.save(
        source / "lab.local.json",
        {
            "version": 1,
            "node": "synthetic",
            "oob_host": "synthetic-oob",
            "wifi_host": "synthetic-wifi",
            "bench_relative": "synthetic",
            "source_relative": "synthetic/source",
            "python_relative": "synthetic/python",
            "approved_serial": "SYNTHETIC123",
            "approved_by": "fixture",
            "approved_at": "2026-10-29T00:00:00-05:00",
            "fleet_clearance_at": "2026-10-29T00:00:00-05:00",
            "clearance_file": "fixture.md",
        },
    )
    row = window()
    row["private_config_sha256"] = c.file_hash(source / "lab.local.json")
    owner = json.dumps(
        {
            "kind": "TOP10-OWNER-HANDBACK",
            "released": True,
            "approved_by": "Kaylin",
            "manifest_sha256": "a" * 64,
            "arm": "BASE",
            "pid": c.SERVER_PID,
            "birth": row["server_birth"],
            "released_at": c.WINDOW_START - 1200,
        }
    )
    row["owner_handback_sha256"] = hashlib.sha256(owner.encode()).hexdigest()
    raw_approval = approval(row)
    manifest: dict[str, object] = {
        "server_pid": c.SERVER_PID,
        "slots": [
            {"fault_id": fault, "trial": trial, "run_id": c.digest([fault, trial])[:12]}
            for fault in c.FAULT_IDS
            for trial in (1, 2, 3)
        ],
    }
    monkeypatch.setattr(time, "time", lambda: c.WINDOW_START + 1)
    contacts: list[str] = []

    def high_load(kind: str, nonce: str, timeout: float, **kwargs: object) -> dict[str, object]:
        assert (tmp_path / "artifacts/uplink/top10-attempts" / ("a" * 64 + ".json")).is_file()
        contacts.append(kind)
        return {"nonce": nonce, "node": "relay", "cores": 4, "load1": 10, "load5": 1}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("refused OOB reached actual SSH or sudo")

    monkeypatch.setattr("offline_agent.uplink.top10_backends.ssh_probe", high_load)
    monkeypatch.setattr("subprocess.run", forbidden)
    with pytest.raises(c.Refused, match="kaylin_hash_verification_missing_no_run"):
        LiveSession(tmp_path, manifest, "a" * 64, row, owner, raw_approval)
    assert contacts == []
    # This fixture isolates the existing OOB/load gate; review binding has its own tests.
    monkeypatch.setattr(
        "offline_agent.uplink.top10_backends.require_hash_verification", lambda *_: None
    )
    with pytest.raises(c.Refused, match="friday_load_checkpoint_missing"):
        LiveSession(tmp_path, manifest, "a" * 64, row, owner, raw_approval)
    checkpoint = tmp_path / "TONIGHT/FRI-2130"
    checkpoint.mkdir(parents=True)
    c.save(
        checkpoint / "relay-1415.json",
        {
            "kind": "TOP10-FRIDAY-1415-LOAD-v1",
            "manifest_sha256": "a" * 64,
            "checked_at": c.RELAY_LOAD_CHECK_AT,
            "nonce": "b" * 32,
            "sample": {"nonce": "b" * 32, "node": "relay", "cores": 14, "load1": 1, "load5": 2},
            "passed": True,
        },
    )
    session = LiveSession(tmp_path, manifest, "a" * 64, row, owner, raw_approval)
    result = run(session, manifest, tmp_path / "first")
    assert result["attempt_consumed"] is True
    assert result["recorded_trials"] == 0
    assert result["failure"] == "top10_relay_load_too_high"
    assert c.read(tmp_path / "first/oob-stop.json")["reason"] == "top10_relay_load_too_high"
    assert contacts == ["relay"]
    second = LiveSession(tmp_path, manifest, "a" * 64, row, owner, raw_approval)
    result = run(second, manifest, tmp_path / "second")
    assert result["failure"] == "top10_client_attempt_already_consumed"
    assert contacts == ["relay"]


def test_detached_cleanup_does_not_require_fresh_oob_or_live_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from offline_agent.uplink import top10_worker as worker

    run_id = "abcdef123456"
    (tmp_path / "runs" / run_id).mkdir(parents=True)
    row = window()
    raw = approval(row)
    files: dict[str, dict[str, object]] = {
        "payload-hashes.json": {name: "e" * 64 for name in worker.PAYLOAD},
        "window.json": row,
        "manifest.json": {"server_pid": c.SERVER_PID},
        "root-approval.json": {"raw": raw},
        "state.json": {"binding": {"manifest_sha256": "a" * 64}},
    }

    def read(path: Path, **kwargs: object) -> dict[str, object]:
        if path.name == "oob-proof.json":
            pytest.fail("detached cleanup tried to requalify controller's OOB path")
        return files[path.name]

    monkeypatch.setattr(worker, "BASE", tmp_path)
    monkeypatch.setattr(worker, "secure", lambda _: None)
    monkeypatch.setattr(c, "read", read)
    monkeypatch.setattr(c, "file_hash", lambda _: "e" * 64)
    monkeypatch.setattr(time, "time", lambda: c.WINDOW_END + 1)
    monkeypatch.setattr(worker, "server_guard", lambda _: pytest.fail("cleanup gated on server"))
    engine = worker.load_engine(run_id, cleanup=True)
    assert engine.directory == tmp_path / "runs" / run_id


def test_stage_requires_separate_richard_approval() -> None:
    with pytest.raises(ValueError, match="richard_approval_required"):
        check_authority({"window": window(), "owner": "{}"}, c.WINDOW_START + 1)


@pytest.mark.parametrize("response", ["", '{"ok":true,"result":[]}', '{"ok":true}'])
def test_indeterminate_control_reply_never_reconnects(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    from offline_agent.uplink.remote_settings import RemoteTargets
    from offline_agent.uplink.top10_backends import LiveSession

    session = LiveSession.__new__(LiveSession)
    session.window = window()
    session.manifest, session.manifest_sha = {"server_pid": c.SERVER_PID}, "a" * 64
    session.root_approval, session.owner, session.oob_proof = "synthetic", "synthetic", {}
    session.lost = False
    session.command = "synthetic-command-never-executed"
    session.targets = RemoteTargets("synthetic-oob", "synthetic-wifi")
    monkeypatch.setattr(time, "time", lambda: c.WINDOW_START + 1)
    calls: list[object] = []

    def reply(argv: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess("synthetic", 0, response, "")

    monkeypatch.setattr("subprocess.run", reply)
    with pytest.raises(c.Refused, match="reply_indeterminate_no_reconnect"):
        session.control("oracle", {}, {})
    assert session.lost is True
    with pytest.raises(c.Refused, match="lost_no_automatic_reconnect"):
        session.control("oracle", {}, {})
    assert len(calls) == 1


def test_plan_never_needs_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("plan attempted a host contact")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    result = plan()
    assert result["target_contacts"] == 0
    assert result["base_model_calls"] == 0
    assert result["portal_is_local_simulation"] is True


@pytest.fixture
def frozen_copy(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for name in c.runtime_files(ROOT):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    freeze(root)
    return root


def test_package_roundtrip_has_only_frozen_members(frozen_copy: Path, tmp_path: Path) -> None:
    output = tmp_path / "review.tar"
    package(frozen_copy, output)
    manifest = frozen_copy / "harness/top10/FROZEN.json"
    members = checked_members(output.read_bytes(), c.file_hash(manifest))
    assert set(members) == c.runtime_files(frozen_copy) | {"harness/top10/FROZEN.json"}
    assert not any("lab.local.json" in name for name in members)


def test_staging_bootstrap_uses_approved_root_and_oob_guards(
    frozen_copy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "bootstrap.tar"
    package(frozen_copy, output)
    members = checked_members(
        output.read_bytes(), c.file_hash(frozen_copy / "harness/top10/FROZEN.json")
    )
    for name in ("top10_contract", "top10_oob"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    contract = approved_module("top10_contract", members)
    oob = approved_module("top10_oob", members)
    row = window()
    contract.validate_root_approval(approval(row), row)
    with pytest.raises(ValueError, match="root_approval_required"):
        contract.validate_root_approval(None, row)
    with pytest.raises(ValueError, match="scope_bound_oob_proof_required"):
        oob.validate_proof({}, row, "stage", c.WINDOW_START + 1)
    assert _public_fixture("HostName=@PUBLIC_IPV4_062@") in oob.target_ssh("synthetic-oob")


def test_changed_source_and_missing_required_member_refuse(frozen_copy: Path) -> None:
    manifest = frozen_copy / "harness/top10/FROZEN.json"
    value = c.read(manifest)
    files = c.obj(value["files"])
    files.pop("src/offline_agent/uplink/top10_worker.py")
    c.save(manifest, value)
    with pytest.raises(c.Refused, match="file_count"):
        c.verify_manifest(frozen_copy, manifest)


def test_source_change_invalidates_freeze(frozen_copy: Path) -> None:
    path = frozen_copy / "src/offline_agent/uplink/top10_score.py"
    path.write_text(path.read_text() + "\n# mutated test fixture\n")
    with pytest.raises(c.Refused, match="frozen_runtime_changed"):
        c.verify_manifest(frozen_copy, frozen_copy / "harness/top10/FROZEN.json")


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "a/../escape", "a//escape"])
def test_archive_paths_cannot_escape(name: str) -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="archive_member_refused"):
        checked_members(stream.getvalue(), "a" * 64)
