"""Read-only Relay/Bench qualification. No fallback to the Wi-Fi control path."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

if __package__:
    from . import top10_contract as c
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]


SSH = (
    "/usr/bin/ssh",
    "-4",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=3",
    "-o",
    "ServerAliveInterval=2",
    "-o",
    "ServerAliveCountMax=1",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
    "-o",
    "StrictHostKeyChecking=yes",
)
RELAY_SCRIPT = """import json,os,platform,sys
request=json.load(sys.stdin)
load=os.getloadavg()
print(json.dumps(dict(nonce=request['nonce'],node=platform.node().split('.')[0].lower(),
    cores=os.cpu_count(),load1=load[0],load5=load[1])))
"""
TARGET_SCRIPT = _public_fixture("""import json,os,platform,re,subprocess,sys
request=json.load(sys.stdin)
connection=os.environ.get('SSH_CONNECTION','').split()
if len(connection)!=4 or connection[0]!=request['client'] or connection[2]!='@PUBLIC_IPV4_062@':
    raise ValueError('top10_current_pinned_oob_session_required')
def read(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=2,check=True)
    return p.stdout
route=read(['/sbin/route','-n','get','-host',connection[0]])
interfaces=re.findall(r'^\\s*interface:\\s*(\\S+)\\s*$',route,re.M)
address=read(['/usr/sbin/ipconfig','getifaddr','en7']).strip()
hardware=read(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice'])
serial=re.search(r'"IOPlatformSerialNumber"\\s*=\\s*"([A-Za-z0-9]+)"',hardware)
print(json.dumps(dict(nonce=request['nonce'],client=connection[0],server=connection[2],
    interface=interfaces[0] if len(interfaces)==1 else '',address=address,
    host_verified=platform.node().split('.')[0]==request['node'] and
        serial is not None and serial[1]==request['serial'])))
""")


def target_ssh(alias: str) -> tuple[str, ...]:
    c.text(alias, r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,253}")
    # Explicit HostName and jump defeat an alias pointing back to the en0 route.
    return (*SSH, "-J", c.OOB_JUMP_HOST, "-o", "HostName=" + c.OOB_SERVER_IPV4, alias)


def command(source: str) -> str:
    return "/usr/bin/python3 -I -B -c " + shlex.quote(source)


def load_ok(value: object, nonce: str) -> dict[str, object]:
    row = c.obj(value)
    if set(row) != {"nonce", "node", "cores", "load1", "load5"}:
        raise c.Refused("top10_relay_load_missing_or_invalid")
    if row["nonce"] != nonce or row["node"] != c.OOB_JUMP_HOST:
        raise c.Refused("top10_relay_identity_or_nonce_changed")
    if type(row["cores"]) is not int:
        raise c.Refused("top10_relay_core_count_invalid")
    try:
        cores = c.number(row["cores"], 1, 1024)
        load1, load5 = (c.number(row[key], 0, 1_000_000) for key in ("load1", "load5"))
    except c.Refused as exc:
        raise c.Refused("top10_relay_load_missing_or_invalid") from exc
    if load1 / cores > c.RELAY_MAX_LOAD1_PER_CORE or load5 / cores > c.RELAY_MAX_LOAD5_PER_CORE:
        raise c.Refused("top10_relay_load_too_high")
    return row


def target_ok(value: object, window: dict[str, object], nonce: str) -> dict[str, object]:
    row = c.obj(value)
    if (
        set(row) != {"nonce", "client", "server", "interface", "address", "host_verified"}
        or row["nonce"] != nonce
        or row["client"] != window["oob_client_ipv4"]
        or row["server"] != c.OOB_SERVER_IPV4
        or row["interface"] != c.OOB_INTERFACE
        or row["address"] != c.OOB_SERVER_IPV4
        or row["host_verified"] is not True
        or window["oob_interface"] != c.OOB_INTERFACE
        or window["oob_server_ipv4"] != c.OOB_SERVER_IPV4
    ):
        raise c.Refused("top10_pinned_oob_readback_refused")
    return row


def validate_friday_checkpoint(value: object, manifest_sha: str, now: float) -> None:
    row = c.obj(value)
    if (
        set(row) != {"kind", "manifest_sha256", "checked_at", "nonce", "sample", "passed"}
        or row["kind"] != "TOP10-FRIDAY-1415-LOAD-v1"
        or row["manifest_sha256"] != manifest_sha
        or row["passed"] is not True
    ):
        raise c.Refused("top10_friday_load_checkpoint_required")
    checked = c.number(row["checked_at"], c.RELAY_LOAD_CHECK_AT, now)
    if checked >= c.RELAY_LOAD_CHECK_AT + 60:
        raise c.Refused("top10_friday_load_checkpoint_too_late")
    nonce = c.text(row["nonce"], r"[0-9a-f]{32}")
    sample = load_ok(row["sample"], nonce)
    if (
        c.number(sample["load1"], 0, 1_000_000) > 10.5
        or c.number(sample["load5"], 0, 1_000_000) > 14
    ):
        raise c.Refused("top10_relay_load_too_high")


def require_friday_checkpoint(base: Path, manifest_sha: str, now: float) -> None:
    if (base / "ABORT.json").exists():
        raise c.Refused("top10_friday_aborted_no_retry")
    try:
        value = c.read(base / "relay-1415.json", private=True)
    except FileNotFoundError as exc:
        raise c.Refused("top10_friday_load_checkpoint_missing") from exc
    validate_friday_checkpoint(value, manifest_sha, now)


def validate_proof(
    value: object, window: dict[str, object], scope: str, now: float
) -> dict[str, object]:
    row = c.obj(value)
    if (
        row.get("kind") != "TOP10-RELAY-OOB-v1"
        or row.get("passed") is not True
        or row.get("scope") != scope
        or row.get("window_sha256") != c.digest(window)
        or row.get("jump_host") != c.OOB_JUMP_HOST
    ):
        raise c.Refused("top10_fresh_scope_bound_oob_proof_required")
    c.text(row.get("qualification_id"), r"[0-9a-f]{32}")
    started = c.number(row.get("started_at"), 0, now)
    checked = c.number(row.get("checked_at"), started, now)
    if now - checked > c.OOB_FRESH_SECONDS or checked - started > 60:
        raise c.Refused("top10_stale_oob_proof_refused")
    attempts = row.get("attempts")
    if not isinstance(attempts, list) or not c.OOB_REQUIRED <= len(attempts) <= c.OOB_MAX_ATTEMPTS:
        raise c.Refused("top10_oob_attempt_count_refused")
    seen: set[str] = set()
    streak = failures = 0
    for raw in attempts:
        attempt = c.obj(raw)
        nonce = c.text(attempt.get("nonce"), r"[0-9a-f]{32}")
        if nonce in seen:
            raise c.Refused("top10_replayed_oob_probe_refused")
        seen.add(nonce)
        c.number(attempt.get("at"), started, checked)
        if attempt.get("ok") is True:
            load_ok(attempt.get("relay"), nonce)
            target_ok(attempt.get("target"), window, nonce)
            streak += 1
        elif attempt.get("ok") is False and attempt.get("reason") in {
            "top10_oob_probe_timeout",
            "top10_oob_probe_transport_failed",
        }:
            failures += 1
            streak = 0
        else:
            raise c.Refused("top10_oob_probe_not_qualified")
    if streak != c.OOB_REQUIRED or failures > c.OOB_MAX_FAILURES:
        raise c.Refused("top10_oob_flaky_or_incomplete")
    if row.get("consecutive_successes") != streak or row.get("failures") != failures:
        raise c.Refused("top10_oob_summary_mismatch")
    return row


class Qualifier:
    def __init__(self) -> None:
        self.evidence: dict[str, object] = {}

    def qualify(
        self,
        window: dict[str, object],
        scope: str,
        probe: Callable[[str, str, float], dict[str, object]],
        *,
        now: Callable[[], float] = time.time,
        steady: Callable[[], float] = time.monotonic,
        pause: Callable[[float], None] = time.sleep,
    ) -> dict[str, object]:
        attempts: list[dict[str, object]] = []
        self.evidence = {
            "kind": "TOP10-RELAY-OOB-v1",
            "passed": False,
            "scope": scope,
            "qualification_id": uuid.uuid4().hex,
            "window_sha256": c.digest(window),
            "jump_host": c.OOB_JUMP_HOST,
            "started_at": now(),
            "attempts": attempts,
        }
        deadline = steady() + 60
        streak = failures = 0
        try:
            for index in range(c.OOB_MAX_ATTEMPTS):
                nonce = uuid.uuid4().hex
                attempt: dict[str, object] = {"nonce": nonce, "at": now(), "ok": False}
                attempts.append(attempt)
                try:
                    # High/missing load is terminal, not a transient transport retry.
                    budget = min(c.OOB_TIMEOUT, deadline - steady())
                    if budget <= 0:
                        raise c.Refused("top10_oob_total_timeout")
                    sample = probe("relay", nonce, budget)
                    load_ok(sample, nonce)
                    attempt["relay"] = sample
                    budget = min(c.OOB_TIMEOUT, deadline - steady())
                    if budget <= 0:
                        raise c.Refused("top10_oob_total_timeout")
                    target = probe("target", nonce, budget)
                    target_ok(target, window, nonce)
                    attempt["target"] = target
                except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                    failures += 1
                    streak = 0
                    attempt["reason"] = (
                        "top10_oob_probe_timeout"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "top10_oob_probe_transport_failed"
                    )
                    if failures > c.OOB_MAX_FAILURES:
                        raise c.Refused("top10_oob_flaky_or_incomplete") from exc
                else:
                    attempt["ok"] = True
                    streak += 1
                if steady() > deadline:
                    raise c.Refused("top10_oob_total_timeout")
                if streak == c.OOB_REQUIRED:
                    self.evidence.update(
                        passed=True,
                        checked_at=now(),
                        consecutive_successes=streak,
                        failures=failures,
                    )
                    return validate_proof(self.evidence, window, scope, now())
                if index + 1 < c.OOB_MAX_ATTEMPTS:
                    pause(min(1.0, max(0.0, deadline - steady())))
            raise c.Refused("top10_oob_flaky_or_incomplete")
        except Exception as exc:
            reason = str(exc) if isinstance(exc, c.Refused) else "top10_oob_invalid_response"
            self.evidence.update(
                passed=False,
                checked_at=now(),
                reason=reason,
                consecutive_successes=streak,
                failures=failures,
            )
            if attempts:
                attempts[-1].setdefault("reason", reason)
            raise c.Refused(reason) from exc


def ssh_probe(
    kind: str,
    nonce: str,
    timeout: float,
    *,
    alias: str,
    window: dict[str, object],
    node: str,
    serial: str,
) -> dict[str, object]:
    request = {"nonce": nonce}
    argv: tuple[str, ...]
    if kind == "relay":
        argv = (*SSH, c.OOB_JUMP_HOST, command(RELAY_SCRIPT))
    elif kind == "target":
        request.update(client=str(window["oob_client_ipv4"]), node=node, serial=serial)
        argv = (*target_ssh(alias), command(TARGET_SCRIPT))
    else:
        raise c.Refused("top10_oob_probe_kind_refused")
    result = subprocess.run(
        argv,
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    if len(result.stdout.encode()) > 8192:
        raise c.Refused("top10_oob_response_size_refused")
    return c.parse_json(result.stdout)
