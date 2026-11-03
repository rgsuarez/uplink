"""Concrete BASE/OOB and explicitly synthetic backends for the same trial harness."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.oracle import Evidence, OracleResult, Verdict
from offline_agent.uplink.remote_settings import RemoteTargets
from offline_agent.uplink.top10_attempt import Attempt
from offline_agent.uplink.top10_engine import Engine
from offline_agent.uplink.top10_friday import require_hash_verification
from offline_agent.uplink.top10_oob import (
    Qualifier,
    require_friday_checkpoint,
    ssh_probe,
    target_ssh,
)
from offline_agent.uplink.top10_worker import request_restore, start_sandbox


class LiveSession:
    sandbox = False

    def __init__(
        self,
        root: Path,
        manifest: dict[str, object],
        manifest_sha: str,
        window: dict[str, object],
        owner: str,
        root_approval: str,
        *,
        cleanup: bool = False,
    ) -> None:
        from .top10_bridge import owner_handback

        c.validate_window(window, manifest, manifest_sha, time.time(), cleanup=cleanup)
        owner_handback(owner, window, time.time(), starting=not cleanup)
        c.validate_root_approval(root_approval, window)
        if not cleanup:
            require_hash_verification(root, manifest_sha, window, owner, root_approval, time.time())
            require_friday_checkpoint(
                root / "TONIGHT/FRI-2130", manifest_sha, time.time()
            )
        config = load_config(root / "src/offline_agent/uplink/lab.local.json")
        if (
            c.file_hash(root / "src/offline_agent/uplink/lab.local.json")
            != window["private_config_sha256"]
        ):
            raise c.Refused("top10_private_config_binding_changed")
        self.targets = RemoteTargets(config.oob_host, config.wifi_host)
        self.config = config
        self.root = root
        self.root_approval = root_approval
        self.relative = config.bench_relative + "/top10-" + manifest_sha[:12]
        self.command = (
            'cd "$HOME"/'
            + shlex.quote(self.relative)
            + ' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$HOME"/'
            + shlex.quote(config.python_relative)
            + " -B -m offline_agent.uplink.top10_bridge"
        )
        self.manifest, self.manifest_sha, self.window, self.owner = (
            manifest,
            manifest_sha,
            window,
            owner,
        )
        self.proof: dict[str, object] = {}
        self.qualifier = Qualifier()
        self.oob_proof: dict[str, object] = {}
        self.consumed = False
        self.lost = False
        self.reservation_path = root / "artifacts/uplink/top10-attempts" / (manifest_sha + ".json")

    def consume(self, *, staging: bool = False) -> None:
        path = self.reservation_path
        if staging:
            path = path.with_name(path.stem + "-stage.json")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            c.save(
                path,
                {
                    "scope": "staging" if staging else "client_invocation",
                    "state": "consumed",
                    "manifest_sha256": self.manifest_sha,
                    "window_sha256": c.digest(self.window),
                    "at": time.time(),
                },
                exclusive=True,
            )
        except FileExistsError as exc:
            raise c.Refused("top10_client_attempt_already_consumed") from exc
        self.consumed = True

    def qualify(self, scope: str) -> dict[str, object]:
        c.validate_window(self.window, self.manifest, self.manifest_sha, self.now())
        c.validate_root_approval(self.root_approval, self.window)

        def probe(kind: str, nonce: str, timeout: float) -> dict[str, object]:
            return ssh_probe(
                kind,
                nonce,
                timeout,
                alias=self.targets.oob_host,
                window=self.window,
                node=self.config.node,
                serial=self.config.approved_serial,
            )

        try:
            self.oob_proof = self.qualifier.qualify(self.window, scope, probe, now=self.now)
            return self.oob_proof
        except Exception:
            self.lost = True
            raise

    def control(
        self,
        operation: str,
        slot: dict[str, object],
        payload: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        cleanup = (
            operation in {"oracle", "resources", "stop_suite", "finish_slot"}
            or operation == "root"
            and payload.get("operation") in {"observe", "restore", "close", "retire", "status"}
        )
        c.validate_window(
            self.window, self.manifest, self.manifest_sha, self.now(), cleanup=cleanup
        )
        if self.lost:
            raise c.Refused("top10_oob_lost_no_automatic_reconnect")
        if operation == "reserve_suite":
            require_hash_verification(
                self.root,
                self.manifest_sha,
                self.window,
                self.owner,
                self.root_approval,
                self.now(),
            )
            self.consume()
            self.qualify("preflight")
        body = {
            "operation": operation,
            "slot": slot,
            "payload": payload,
            "manifest_sha256": self.manifest_sha,
            "window": self.window,
            "owner_handback": self.owner,
            "root_approval": self.root_approval,
            "oob_proof": self.oob_proof,
        }
        timeout: float = 600 if operation == "preflight" else 45
        if operation == "preflight":
            timeout = min(timeout, c.WINDOW_START + 600 - self.now())
        if timeout_seconds is not None:
            timeout = min(timeout, timeout_seconds)
        if timeout <= 0:
            raise c.Refused("top10_aggregate_operation_budget_exhausted")
        try:
            result = subprocess.run(
                (
                    *target_ssh(self.targets.oob_host),
                    self.command,
                ),
                input=json.dumps(body),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.lost = True
            raise c.Refused("top10_oob_transport_lost") from exc
        if result.returncode:
            self.lost = True
            raise c.Refused("top10_oob_transport_failed")
        try:
            if len(result.stdout.encode()) > 2_000_000:
                raise c.Refused("top10_oob_response_size_refused")
            row = c.parse_json(result.stdout)
            if row.get("ok") is False and set(row) == {"ok", "reason"}:
                reason = c.text(row["reason"], r"[A-Za-z][A-Za-z0-9_]{0,119}")
            elif row.get("ok") is True and set(row) == {"ok", "result"}:
                return c.obj(row["result"])
            else:
                raise c.Refused("top10_oob_envelope_refused")
        except (ValueError, UnicodeError) as exc:
            self.lost = True
            raise c.Refused("top10_oob_reply_indeterminate_no_reconnect") from exc
        raise c.Refused(reason)

    def now(self) -> float:
        return time.time()

    @property
    def oob_evidence(self) -> dict[str, object]:
        return self.qualifier.evidence

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def backend(self, slot: dict[str, object]) -> LiveBackend:
        return LiveBackend(self, slot)


class LiveBackend:
    sandbox = False

    def __init__(self, session: LiveSession, slot: dict[str, object]) -> None:
        self.session, self.slot = session, slot
        self.deadline = time.monotonic() + c.TRIAL_SECONDS - 40

    def call(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        return self.session.control(
            operation, self.slot, payload, timeout_seconds=self.deadline - time.monotonic()
        )

    @property
    def lost(self) -> bool:
        return self.session.lost

    @property
    def oob_evidence(self) -> dict[str, object]:
        return self.session.oob_evidence

    def preflight(self) -> dict[str, object]:
        if not self.session.proof:
            raise c.Refused("top10_suite_preflight_required")
        proof = self.session.qualify(str(self.slot["run_id"]))
        return self.resources() | {"oob": proof}

    def root(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        return self.call("root", {"operation": operation, "payload": payload})

    def oracle(self) -> dict[str, object]:
        return self.call("oracle", {})

    def resources(self) -> dict[str, object]:
        return self.call("resources", {"preflight": self.session.proof})

    def choose(
        self, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
    ) -> dict[str, object]:
        return self.call(
            "choose",
            {
                "snapshot_id": snapshot_id,
                "facts": facts,
                "oracle": oracle,
                "history": history,
                "timeout": timeout,
                "preflight": self.session.proof,
            },
        )

    def pause(self, seconds: float) -> None:
        self.session.pause(seconds)


class SandboxSession:
    """No model: a deterministic test policy exercises the 21 selected slots."""

    sandbox = True
    lost = False
    consumed = False
    oob_evidence: dict[str, object] = {}

    def __init__(
        self, base: Path, manifest: dict[str, object], *, behavior: str = "recover"
    ) -> None:
        if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
            raise c.Refused("top10_os_sandbox_required")
        self.base, self.manifest, self.behavior = base, manifest, behavior
        self.attempt = Attempt(base / "attempts", c.digest(manifest))
        self.proof: dict[str, object] = {}
        self.clock = c.WINDOW_START + 600

    def now(self) -> float:
        return self.clock

    def pause(self, seconds: float) -> None:
        self.clock += seconds

    def control(
        self, operation: str, slot: dict[str, object], payload: dict[str, object]
    ) -> dict[str, object]:
        if operation == "reserve_suite":
            result = self.attempt.reserve({"kind": "SYNTHETIC_NO_LIVE_AUTHORITY"}, self.manifest)
            self.consumed = True
            return result
        if operation == "preflight":
            self.attempt.begin_preflight()
            if self.behavior == "preflight_refused":
                raise c.Refused("top10_synthetic_preflight_refused")
            self.proof = {
                "base_verified": True,
                "pid": c.SERVER_PID,
                "kind": "FAKE_KERNEL",
                "model_calls": 0,
            }
            self.attempt.admit(self.proof)
            return self.proof
        if operation == "finish_slot":
            return self.attempt.finish(payload, self.manifest)
        if operation == "stop_suite":
            return self.attempt.stop(str(payload["reason"]))
        raise c.Refused("top10_synthetic_suite_operation_refused")

    def backend(self, slot: dict[str, object]) -> SandboxBackend:
        c.require_bench_fault(slot.get("fault_id"))
        self.attempt.require_slot(slot, self.manifest)
        return SandboxBackend(self.base / "runs" / str(slot["run_id"]), slot, self.behavior)


class SandboxBackend:
    sandbox = True
    lost = False
    oob_evidence: dict[str, object] = {}

    def __init__(self, directory: Path, slot: dict[str, object], behavior: str = "recover") -> None:
        self.directory, self.slot, self.behavior = directory, slot, behavior
        self.engine: Engine | None = None

    def require_engine(self) -> Engine:
        if self.engine is None:
            raise c.Refused("top10_synthetic_prepare_required")
        return self.engine

    def preflight(self) -> dict[str, object]:
        return {"base_verified": True, "pid": c.SERVER_PID, "kind": "FAKE_KERNEL"}

    def root(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "prepare":
            self.engine = start_sandbox(
                self.directory, str(self.slot["run_id"]), str(self.slot["fault_id"])
            )
            return self.engine.public(self.engine.state())
        engine = self.require_engine()
        if operation in {"arm", "status"}:
            return engine.public(engine.state())
        if operation == "inject":
            return engine.inject()
        if operation == "verify":
            return engine.verify_fault()
        if operation == "observe":
            return engine.observe()
        if operation == "apply":
            return engine.apply(str(payload["tool"]), str(payload["snapshot_id"]))
        if operation == "restore":
            return request_restore(engine)
        if operation == "close":
            return engine.close()
        if operation == "retire":
            until = time.monotonic() + 3
            while not (self.directory / "watchdog-exited.json").exists():
                if time.monotonic() > until:
                    raise c.Refused("top10_synthetic_watchdog_did_not_exit")
                time.sleep(0.01)
            return {"retired": True}
        raise c.Refused("top10_synthetic_root_operation_refused")

    def oracle(self) -> dict[str, object]:
        if self.behavior == "missing_oracle":
            return {}
        facts = c.measured(self.require_engine().observe()["facts"])
        good = c.baseline_ok(facts)
        portal = facts["portal_redirect_seen"]
        result = OracleResult(
            Verdict.ONLINE if good else Verdict.OFFLINE,
            time.time(),
            tuple(
                Evidence(
                    name,
                    "dns"
                    if name.startswith("dns")
                    else "tls"
                    if name.startswith("tls")
                    else "portal",
                    good,
                    "explicit_synthetic_measurement",
                    "path-synthetic",
                    status=302 if portal and name.startswith("portal") else None,
                    proxy_used="proxy-" + "a" * 24
                    if portal and not name.startswith("dns")
                    else None,
                )
                for name in c.CHECK_IDS
            ),
        )
        return dict(result.as_json())

    def resources(self) -> dict[str, object]:
        return self.preflight()

    def choose(
        self, snapshot_id: str, facts: object, oracle: object, history: list[str], timeout: float
    ) -> dict[str, object]:
        measured = c.measured(facts)
        tool = "declare_online"
        for fact, repair in (
            ("radio_on", "restore_radio"),
            ("interface_up", "restore_interface"),
            ("saved_network_matches_snapshot", "restore_saved_network"),
            ("associated_with_snapshot", "restore_saved_network"),
            ("ipv4_usable", "restore_ipv4"),
            ("dhcp_lease_present", "renew_lease"),
            ("gateway_matches_snapshot", "restore_gateway"),
            ("dns_matches_snapshot", "restore_dns"),
            ("proxy_matches_snapshot", "restore_proxy"),
            ("mtu_matches_snapshot", "restore_mtu"),
        ):
            if not measured[fact]:
                tool = repair
                break
        if self.behavior == "honest_report":
            tool = "report"
        if self.behavior == "false_online":
            tool = "declare_online"
        action = {
            "version": 1,
            "tool": tool,
            "snapshot_id": snapshot_id,
            "evidence_id": "ipv4_path_ok",
            "reason": "needs_human" if tool == "report" else "snapshot_repair",
        }
        return {
            "kind": "SYNTHETIC_POLICY_NO_MODEL",
            "raw_text": json.dumps(action),
            "request_count": 1,
            "failure": None,
            "base_model_calls": 0,
        }

    def pause(self, seconds: float) -> None:
        return
