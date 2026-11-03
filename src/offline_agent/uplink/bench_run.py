"""One reduced F04 observation. Default is offline inspection, never implicit contact."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from pathlib import Path

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.bench_harness import Journal, run_trial
from offline_agent.uplink.bench_package import verify_archive
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.top10_engine import label
from offline_agent.uplink.top10_oob import Qualifier, ssh_probe, target_ssh

ROOT = Path(__file__).resolve().parents[3]


class Session:
    sandbox = False

    def __init__(self, root: Path, archive: Path, window: object, owner: str, richard: str) -> None:
        self.root = root
        self.manifest = b.verify_manifest(root)
        self.run_id = str(self.manifest["run_id"])
        self.manifest_sha = c.file_hash(root / b.MANIFEST_PATH)
        self.package_sha = verify_archive(root, archive)
        self.window = b.authority(
            window, self.manifest, self.manifest_sha, self.package_sha, time.time(), starting=True
        )
        b.approvals(self.window, owner, richard, time.time())
        config_path = root / "src/offline_agent/uplink/lab.local.json"
        if c.file_hash(config_path) != self.window["private_config_sha256"]:
            raise c.Refused("bench_private_config_changed")
        self.config = load_config(config_path)
        self.owner, self.richard = owner, richard
        relative = self.config.bench_relative + "/bench-reduced-" + self.run_id
        self.command = (
            'cd "$HOME"/'
            + shlex.quote(relative)
            + ' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$HOME"/'
            + shlex.quote(self.config.python_relative)
            + " -B -m offline_agent.uplink.bench_bridge"
        )
        self.proof: dict[str, object] = {}
        self.qualifier = Qualifier()
        self.oob: dict[str, object] = {}
        self.consumed = self.lost = False

    def consume(self) -> None:
        directory = self.root / "artifacts/uplink/bench-attempts"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Unique marker survives refusal, crashes, output changes and transport ambiguity.
        c.save(
            directory / (self.run_id + ".json"),
            {
                "state": "consumed",
                "run_id": self.run_id,
                "at": time.time(),
                "manifest_sha256": self.manifest_sha,
                "window_sha256": c.digest(self.window),
            },
            exclusive=True,
        )
        b.sync_directory(directory)
        b.sync_directory(directory.parent)
        self.consumed = True

    def qualify(self, scope: str, *, cleanup: bool = False) -> dict[str, object]:
        if self.lost:
            raise c.Refused("bench_transport_lost_no_reconnect")
        b.authority(
            self.window,
            self.manifest,
            self.manifest_sha,
            self.package_sha,
            time.time(),
            cleanup=cleanup,
        )

        def probe(kind: str, nonce: str, timeout: float) -> dict[str, object]:
            return ssh_probe(
                kind,
                nonce,
                timeout,
                alias=self.config.oob_host,
                window=self.window,
                node=self.config.node,
                serial=self.config.approved_serial,
            )

        try:
            self.oob = self.qualifier.qualify(self.window, scope, probe)
            return self.oob
        except Exception:
            self.lost = True
            raise

    def control(
        self, operation: str, payload: dict[str, object], *, timeout: float = 45
    ) -> dict[str, object]:
        cleanup = operation in {"oracle", "resources", "finish", "stop"} or (
            operation == "root"
            and payload.get("operation") in {"restore", "observe", "close", "retire", "status"}
        )
        b.authority(
            self.window,
            self.manifest,
            self.manifest_sha,
            self.package_sha,
            time.time(),
            cleanup=cleanup,
        )
        if self.lost:
            raise c.Refused("bench_transport_lost_no_reconnect")
        if operation == "reserve":
            self.consume()
            self.qualify("preflight")
        body = {
            "operation": operation,
            "payload": payload,
            "manifest_sha256": self.manifest_sha,
            "package_sha256": self.package_sha,
            "window": self.window,
            "owner": self.owner,
            "richard": self.richard,
            "oob": self.oob,
        }
        if timeout <= 0:
            raise c.Refused("bench_controller_budget_exhausted")
        try:
            result = subprocess.run(
                (*target_ssh(self.config.oob_host), self.command),
                input=json.dumps(body, allow_nan=False),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode or len(result.stdout.encode()) > 2_000_000:
                raise c.Refused("bench_transport_failed_or_oversize")
            reply = c.parse_json(result.stdout)
            if reply.get("ok") is False and set(reply) == {"ok", "reason"}:
                reason = c.text(reply["reason"], r"[A-Za-z][A-Za-z0-9_]{0,119}")
            elif reply.get("ok") is True and set(reply) == {"ok", "result"}:
                return c.obj(reply["result"])
            else:
                raise c.Refused("bench_transport_reply_unparsed")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.lost = True
            raise c.Refused("bench_transport_indeterminate_no_reconnect") from exc
        raise c.Refused(reason)


class Backend:
    sandbox = False

    def __init__(self, session: Session) -> None:
        self.session = session
        self.deadline = time.monotonic() + 110

    @property
    def lost(self) -> bool:
        return self.session.lost

    @property
    def oob_evidence(self) -> dict[str, object]:
        return self.session.qualifier.evidence

    def call(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        return self.session.control(
            operation, payload, timeout=min(45, self.deadline - time.monotonic())
        )

    def preflight(self) -> dict[str, object]:
        self.session.qualify(self.session.run_id)
        return self.resources() | {"oob": self.session.oob}

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
        time.sleep(seconds)


def run(session: Session, output: Path) -> dict[str, object]:
    journal = Journal(output)
    journal.write(
        "intent.json",
        {
            "kind": b.KIND,
            "run_id": session.run_id,
            "manifest_sha256": session.manifest_sha,
            "package_sha256": session.package_sha,
            "planned_trials": 1,
        },
    )
    receipt: dict[str, object] | None = None
    failure: str | None = None
    try:
        journal.write("reservation.json", session.control("reserve", {}))
        journal.write("preflight-oob.json", session.oob)
        session.proof = session.control(
            "preflight",
            {},
            timeout=min(600, b.timestamp(session.window["start"]) + 600 - time.time()),
        )
        journal.write("preflight.json", session.proof)
        slot_started = time.monotonic()
        receipt = run_trial(
            Backend(session),
            b.slot(session.manifest),
            output / session.run_id,
            expected_pid=int(str(session.window["server_pid"])),
        )
        if session.lost:
            raise c.Refused("bench_transport_lost_manual_handback_required")
        work_seconds = time.monotonic() - slot_started
        journal.write(
            "slot-timing.json",
            {"work_seconds": work_seconds, "cooldown_seconds": 40, "slot_limit_seconds": 150},
        )
        if work_seconds > 110:
            raise c.Refused("bench_trial_slot_budget_exceeded")
        time.sleep(40)
        session.qualify(session.run_id, cleanup=True)
        journal.write("handback-oob.json", session.oob)
        journal.write(
            "handback-resources.json", session.control("resources", {"preflight": session.proof})
        )
        session.control("finish", receipt)
        if receipt.get("cleanup_verified") is not True or receipt.get("failure") is not None:
            raise c.Refused("bench_trial_not_safely_completed")
    except BaseException as exc:
        failure = label(exc)
        journal.write("failure.json", {"reason": failure, "attempt_consumed": session.consumed})
        if session.consumed and not session.lost:
            try:
                session.control("stop", {"reason": failure})
            except Exception as stop_exc:
                journal.write("stop-failure.json", {"reason": label(stop_exc)})
    result: dict[str, object] = {
        "kind": b.KIND,
        "run_id": session.run_id,
        "planned_trials": 1,
        "recorded_trials": int(receipt is not None),
        "top10_complete": False,
        "attempt_consumed": session.consumed,
        "failure": failure,
        "receipt": receipt,
        "complete": failure is None and receipt is not None,
        "profile_sha256": session.manifest["profile_sha256"],
        "prompt_sha256": b.PROMPT_SHA256,
        "manifest_sha256": session.manifest_sha,
        "package_sha256": session.package_sha,
    }
    journal.write("reduced-result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--richard", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.execute:
        manifest = b.verify_manifest(ROOT)
        print(
            json.dumps(
                {
                    "kind": b.KIND,
                    "mode": "OFFLINE_PLAN_ONLY",
                    "run_id": manifest["run_id"],
                    "planned_trials": 1,
                    "target_contacts": 0,
                    "model_calls": 0,
                }
            )
        )
        return
    if not all((args.package, args.authority, args.owner, args.richard, args.output)):
        parser.error(
            "exact package, authority, owner, Richard approval and new output are required"
        )
    try:
        session = Session(
            ROOT,
            args.package,
            c.read(args.authority, private=True),
            c.read_bytes(args.owner, private=True).decode(),
            c.read_bytes(args.richard, private=True).decode(),
        )
        result = run(session, args.output)
        print(
            json.dumps(
                {k: result[k] for k in ("kind", "run_id", "complete", "failure", "top10_complete")}
            )
        )
        if result["complete"] is not True:
            raise SystemExit(2)
    except Exception as exc:
        print(json.dumps({"refused": label(exc)}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
