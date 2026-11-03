"""Plan locally by default; execute exactly two approved IPv4 fault/model trials once."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.faults_v1 import FaultSpec, digest
from offline_agent.uplink.faults_v1_authority import specs, validate_window
from offline_agent.uplink.faults_v1_controller import paths_ok, reason, recover
from offline_agent.uplink.lab_config import load_config
from offline_agent.uplink.probe_trial_window import read_object, runtime_digest, save
from offline_agent.uplink.remote_settings import SSH_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
PROOF = (
    "import json,os,platform;print(json.dumps({'node':platform.node().split('.')[0],"
    "'connection':os.environ.get('SSH_CONNECTION','')}))"
)


def reserve_attempt(packet_path: Path, packet: dict[str, Json], window: dict[str, Json]) -> None:
    if packet_path.stat().st_mode & 0o077 or any(
        p.is_symlink() for p in (packet_path, *packet_path.parents)
    ):
        raise ContractError("newfault_private_packet_required")
    # One packet/window gets one invocation, including a refused preflight.
    # Changing the output directory cannot turn a consumed attempt into a retry.
    save(
        packet_path.parent / "execution-attempt.json",
        {
            "packet_sha256": digest(packet),
            "window_sha256": digest(window),
            "started_at": time.time(),
            "retry_allowed": False,
        },
    )


class RemoteBackend:
    dry = False
    lost = False

    def __init__(
        self,
        packet: dict[str, Json],
        window: dict[str, Json],
        spec: FaultSpec,
        *,
        execute: bool = False,
    ) -> None:
        self.execute = execute
        self.packet, self.window, self.spec = packet, window, spec
        self.config = load_config()
        self.server: dict[str, Json] = {}
        self.baseline_swap: Json = None
        self.command = (
            'cd "$HOME"/'
            + shlex.quote(self.config.source_relative)
            + ' && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" "$HOME"/'
            + shlex.quote(self.config.python_relative)
            + " -B -m offline_agent.uplink.faults_v1_bridge"
        )

    def now(self) -> float:
        return time.time()

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def guard(self, *, cleanup: bool = False, admission: bool = False) -> None:
        if not self.execute or "PYTEST_CURRENT_TEST" in os.environ or self.lost:
            raise ContractError("newfault_live_transport_refused")
        validate_window(
            self.window,
            self.packet,
            digest(self.packet),
            self.now(),
            cleanup=cleanup,
            admission=admission,
        )
        if load_config() != self.config:
            raise ContractError("newfault_private_config_changed")

    def run(self, argv: tuple[str, ...], stdin: str, timeout: float) -> dict[str, Json]:
        try:
            result = subprocess.run(
                argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.lost = True
            raise ContractError("newfault_transport_indeterminate_no_retry") from exc
        if result.returncode == 255:
            self.lost = True
            raise ContractError("newfault_oob_lost_no_retry")
        if result.returncode:
            raise ContractError("newfault_remote_command_refused")
        return object_json(read_json(result.stdout, max_bytes=2_000_000))

    def rpc(
        self,
        operation: str,
        payload: dict[str, Json],
        *,
        cleanup: bool = False,
        timeout: float = 20,
    ) -> dict[str, Json]:
        self.guard(cleanup=cleanup)
        request = {
            "operation": operation,
            "spec": self.spec.document(),
            "packet": self.packet,
            "packet_sha256": digest(self.packet),
            "window": self.window,
            "payload": payload,
        }
        row = self.run(
            (*SSH_OPTIONS, "-4", self.config.oob_host, self.command), json.dumps(request), timeout
        )
        if row.get("ok") is not True:
            label = row.get("error")
            raise ContractError(str(label) if isinstance(label, str) else "newfault_bridge_failed")
        return object_json(row.get("result"))

    def paths(self) -> dict[str, Json]:
        import ipaddress

        self.guard(cleanup=True)
        for alias in (self.config.oob_host, self.config.wifi_host):
            command = "python3 -c " + shlex.quote(PROOF)
            row = self.run(
                (
                    *SSH_OPTIONS,
                    "-4",
                    "-o",
                    "ControlMaster=no",
                    "-o",
                    "ControlPath=none",
                    alias,
                    command,
                ),
                "",
                5,
            )
            connection = row.get("connection")
            if (
                row.get("node") != self.config.node
                or not isinstance(connection, str)
                or len(connection.split()) != 4
            ):
                raise ContractError("newfault_ssh_identity_unproven")
            if any(ipaddress.ip_address(connection.split()[i]).version != 4 for i in (0, 2)):
                raise ContractError("newfault_actual_ipv4_required")
        return {"checked_at": self.now(), "oob_ipv4_verified": True, "ssh_ipv4_verified": True}

    def preflight(self, spec: FaultSpec, paths: dict[str, Json]) -> dict[str, Json]:
        self.guard(admission=True)
        paths_ok(paths, self.now())
        row = self.rpc("preflight", {}, timeout=150)
        self.server = object_json(row.get("serving"))
        self.baseline_swap = object_json(row.get("resources")).get("swap_mib")
        return row

    def call(self, spec: FaultSpec, operation: str, payload: dict[str, Json]) -> dict[str, Json]:
        if spec != self.spec:
            raise ContractError("newfault_transport_spec_changed")
        return self.rpc(
            "root",
            {"operation": operation, "payload": payload},
            cleanup=operation in {"restore", "retire", "observe", "receipt"},
        )

    def oracle(self) -> dict[str, Json]:
        return self.rpc("oracle", {})

    def resources(self) -> dict[str, Json]:
        row = self.rpc("resources", {})
        serving = object_json(row.get("serving"))
        if self.server and any(
            serving.get(k) != self.server.get(k)
            for k in ("pid", "birth", "arm", "base_gguf_sha256", "adapter_gguf_sha256")
        ):
            raise ContractError("newfault_server_changed_during_trial")
        return row

    def choose(
        self,
        measured: dict[str, Json],
        oracle: dict[str, Json],
        history: list[Json],
        timeout: float,
    ) -> dict[str, Json]:
        return self.rpc(
            "choose",
            {
                "measured": measured,
                "oracle": oracle,
                "history": history,
                "timeout": timeout,
                "expected_server": self.server,
                "baseline_swap": self.baseline_swap,
            },
            timeout=timeout + 12,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--window", type=Path)
    parser.add_argument("--handback", type=Path)
    parser.add_argument("--sealed", type=Path)
    parser.add_argument("--eval-handback", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        packet = read_object(args.packet)
        trials = specs(packet)
        if runtime_digest(ROOT) != packet.get("runtime_sha256"):
            raise ContractError("newfault_local_runtime_mismatch")
        for name, expected in object_json(packet.get("entrypoints_sha256")).items():
            path = ROOT / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ContractError("newfault_entrypoint_changed")
        if not args.execute:
            print(
                json.dumps(
                    {
                        "mode": "desk_plan",
                        "effects": False,
                        "trials": 2,
                        "faults": [s.fault for s in trials],
                        "action_mode": "open",
                        "packet_sha256": digest(packet),
                        "package_sha256": packet["package_sha256"],
                        "requires": [
                            "exact_window",
                            "verified_owner_handback",
                            "staged_native_hash_match",
                        ],
                    }
                )
            )
            return
        if args.window is None or args.handback is None or args.output is None:
            raise ContractError("newfault_window_handback_output_required")
        window = read_object(args.window)
        validate_window(window, packet, digest(packet), time.time(), admission=True)
        if (
            args.handback.is_symlink()
            or not args.handback.is_file()
            or args.handback.stat().st_size > 4_000_000
            or hashlib.sha256(args.handback.read_bytes()).hexdigest()
            != window["owner_handback_sha256"]
        ):
            raise ContractError("newfault_current_handback_hash_required")
        target = args.output.absolute()
        if (
            not target.is_relative_to(ROOT / "artifacts")
            or target.exists()
            or any(p.is_symlink() for p in (target, *target.parents))
        ):
            raise ContractError("newfault_new_private_series_output_required")
        reserve_attempt(args.packet, packet, window)
        target.mkdir(mode=0o700, parents=True)
        save(target / "series-intent.json", {"packet_sha256": digest(packet), "window": window})
        results: list[Json] = []
        for index, spec in enumerate(trials):
            validate_window(window, packet, digest(packet), time.time(), admission=True)
            backend = RemoteBackend(packet, window, spec, execute=True)
            result = recover(backend, spec, window, target / spec.run_id)
            results.append(
                {
                    "run_id": spec.run_id,
                    "trial_passed": result["trial_passed"],
                    "outcome": result["outcome"],
                    "cleanup_verified": result["cleanup_verified"],
                    "receipt_sha256": hashlib.sha256(
                        (target / spec.run_id / "receipt.json").read_bytes()
                    ).hexdigest(),
                }
            )
            if result["trial_passed"] is not True:
                break
            if index == 0:
                until = time.monotonic() + 120
                while time.monotonic() < until:
                    validate_window(window, packet, digest(packet), time.time(), admission=True)
                    time.sleep(max(0, min(1, until - time.monotonic())))
        save(
            target / "series-result.json",
            {
                "trials": results,
                "completed": len(results) == 2
                and all(object_json(r)["trial_passed"] is True for r in results),
            },
        )
        print(
            json.dumps(
                {
                    "trial_results": results,
                    "handback_verified": bool(results)
                    and all(object_json(r)["cleanup_verified"] is True for r in results),
                    "server_lifecycle_changed": False,
                }
            )
        )
    except (ValueError, OSError, RuntimeError, KeyboardInterrupt) as exc:
        parser.exit(2, "newfault_runner_refused:" + reason(exc) + "\n")


if __name__ == "__main__":
    main()
