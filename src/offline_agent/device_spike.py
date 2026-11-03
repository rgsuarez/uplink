"""Explicitly gated on-device rehearsal. F2 does not execute this path."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import contextlib
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from offline_agent.agent import SYSTEM
from offline_agent.contracts import Json, object_json
from offline_agent.device_metrics import (
    SpikeRefused,
    check_growth,
    parse_native_timing,
    require_device_opt_in,
    rss_from_ps,
)
from offline_agent.eval_worker import LocalServer, server_command
from offline_agent.artifact_files import run_directory
from offline_agent.live_settings import file_digest
from offline_agent.memory import SwapGuard, SwapRefused, swap_used
from offline_agent.native_protocol import native_prompt, native_request
from offline_agent.providers import HttpTransport, Message
from offline_agent.runtime_config import GGUF_SHA256


@dataclass(frozen=True)
class DeviceSettings:
    gguf: Path
    server: Path
    allow_model_load: bool = False
    repeats: int = 3
    max_output_tokens: int = 256
    max_seconds: float = 180.0
    max_rss_bytes: int = 28 * 1024**3
    max_swap_growth_bytes: int = 64 * 1024**2

    def validate(self) -> None:
        require_device_opt_in(self.allow_model_load)
        if not 1 <= self.repeats <= 5 or not 32 <= self.max_output_tokens <= 512:
            raise SpikeRefused("device_request_bounds_refused")
        if not 30 <= self.max_seconds <= 300 or not 0 < self.max_rss_bytes <= 32 * 1024**3:
            raise SpikeRefused("device_resource_bounds_refused")
        if not 0 <= self.max_swap_growth_bytes <= 64 * 1024**2:
            raise SpikeRefused("device_swap_growth_limit_refused")


class Monitor:
    def __init__(
        self, settings: DeviceSettings, output: Path, sample_swap: Callable[[], int] = swap_used
    ) -> None:
        self.settings = settings
        self.output = output
        self.sample_swap = sample_swap
        self.baseline: int | None = None
        self.peak_rss = 0
        self.peak_swap_growth = 0
        self.failure: str | None = None
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.deadline = time.monotonic() + settings.max_seconds

    def observe(self, process: subprocess.Popen[bytes], server: LocalServer) -> None:
        if time.monotonic() >= self.deadline:
            raise SpikeRefused("device_wall_budget_exhausted")
        if process.poll() is not None:
            raise SpikeRefused("device_server_exited")
        if server.admission is None:
            raise SpikeRefused("device_admission_receipt_missing")
        if self.baseline is None:
            self.baseline = server.admission.after_bytes
        sample = subprocess.run(
            ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        rss = rss_from_ps(sample.stdout)
        current_swap = self.sample_swap()
        growth = max(0, current_swap - self.baseline)
        self.peak_rss = max(self.peak_rss, rss)
        self.peak_swap_growth = max(self.peak_swap_growth, growth)
        self.output.write_text(
            json.dumps(
                {
                    "peak_server_rss_bytes": self.peak_rss,
                    "swap_growth_bytes": growth,
                    "peak_swap_growth_bytes": self.peak_swap_growth,
                    "baseline_swap_bytes": self.baseline,
                    "model_process_pid": process.pid,
                    "current_swap_bytes": current_swap,
                },
                indent=2,
            )
            + "\n"
        )
        check_growth(self.baseline, current_swap, self.settings.max_swap_growth_bytes)
        if rss > self.settings.max_rss_bytes:
            raise SpikeRefused("device_server_rss_limit_exceeded")

    def start(self, server: LocalServer) -> None:
        if server.process is None:
            raise SpikeRefused("device_server_missing")
        process = server.process

        def watch() -> None:
            while not self.stopped.wait(0.25):
                try:
                    self.observe(process, server)
                except (SpikeRefused, SwapRefused, OSError, subprocess.SubprocessError) as exc:
                    self.failure = (
                        str(exc)
                        if isinstance(exc, (SpikeRefused, SwapRefused))
                        else type(exc).__name__
                    )
                    # Terminate first. A full disk must never prevent the resource stop.
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    self.output.write_text(
                        json.dumps(
                            {
                                "refused": self.failure,
                                "peak_server_rss_bytes": self.peak_rss,
                                "peak_swap_growth_bytes": self.peak_swap_growth,
                            },
                            indent=2,
                        )
                        + "\n"
                    )
                    return

        self.thread = threading.Thread(target=watch, name="device-resource-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=3)


def spike_prompt(root: Path) -> str:
    return native_prompt(root, spike_messages())


def spike_messages() -> list[Message]:
    return [
        Message("system", SYSTEM),
        Message("user", "Emit one run_tests tool call with empty args. Do not emit prose."),
    ]


def spike_request(root: Path, max_output_tokens: int) -> dict[str, Json]:
    body = native_request(root, spike_messages(), max_output_tokens)
    # The microbenchmark intentionally measures cold prefill on each repeat.
    body["cache_prompt"] = False
    return body


def render_spike_report(report: dict[str, Json]) -> str:
    return (
        "# Device spike\n\nMeasured by an explicitly authorized on-device run.\n\n"
        "| Measurement | Value |\n| --- | --- |\n"
        f"| GGUF SHA-256 | `{report['gguf_sha256']}` |\n"
        f"| Grammar SHA-256 | `{report['grammar_sha256']}` |\n"
        f"| Repetitions | {report['repeats']} |\n"
        f"| Median grammar-constrained generation tok/s | "
        f"{report['median_generation_tokens_per_s']} |\n"
        f"| Median prompt-eval tok/s | {report['median_prompt_tokens_per_s']} |\n"
        f"| Sampled peak server RSS bytes | {report['peak_server_rss_bytes']} |\n"
        f"| Peak swap growth bytes | {report['peak_swap_growth_bytes']} |\n"
        f"| Evidence | `{report['evidence_directory']}` |\n\n"
        "RSS is sampled process residency, not total unified-memory accounting. Native timings\n"
        "cover prompt evaluation and generation separately. No task-repair pass rate is claimed.\n"
    )


def run_device_spike(root: Path, settings: DeviceSettings) -> Path:
    settings.validate()  # First operation. No stat/hash, process, socket or write before opt-in.
    if sys.platform != "darwin":
        raise SpikeRefused("device_spike_requires_macOS")
    if not settings.gguf.is_file() or not settings.server.is_file():
        raise SpikeRefused("existing_gguf_and_server_required_no_download")
    if file_digest(settings.gguf) != GGUF_SHA256:
        raise SpikeRefused("device_requires_same_pinned_gguf")
    version = subprocess.run(
        [str(settings.server), "--version"], capture_output=True, text=True, check=True, timeout=5
    )
    if (
        "b10353" not in version.stdout + version.stderr
        and "f8def7fe" not in version.stdout + version.stderr
    ):
        raise SpikeRefused("pinned_llama_cpp_runtime_required")
    output = run_directory(root, "device-spike")
    monitor = Monitor(settings, output / "resource-monitor.json")
    server = LocalServer(
        server_command(root, settings.gguf, binary=settings.server),
        output / "server.log",
        SwapGuard(max_growth_bytes=settings.max_swap_growth_bytes),
        startup_seconds=min(120, settings.max_seconds),
    )
    server.poll_hook = lambda process: monitor.observe(process, server)
    start = time.monotonic()
    timings: list[Json] = []
    try:
        server.start()
        monitor.start(server)
        transport = HttpTransport()
        for repeat in range(settings.repeats):
            remaining = settings.max_seconds - (time.monotonic() - start)
            if remaining <= 0 or monitor.failure is not None:
                raise SpikeRefused(monitor.failure or "device_wall_budget_exhausted")
            response = transport.post(
                _public_fixture("http://@PUBLIC_IPV4_056@:8080/completion"),
                spike_request(root, settings.max_output_tokens),
                {},
                min(remaining, 60),
            )
            timing = parse_native_timing(response)
            timings.append(timing.as_json())
            (output / f"response-{repeat + 1}.json").write_text(
                json.dumps(response, indent=2) + "\n"
            )
        if server.process is None:
            raise SpikeRefused("missing_server_process")
        monitor.stop()
        monitor.observe(server.process, server)
        if monitor.failure is not None:
            raise SpikeRefused(monitor.failure)
        rows = [object_json(row) for row in timings]
        from offline_agent.corpus import sha256

        report: dict[str, Json] = {
            "measured": True,
            "gguf_sha256": GGUF_SHA256,
            "grammar_sha256": sha256(root / "schemas/tools.gbnf"),
            "repeats": settings.repeats,
            "median_generation_tokens_per_s": statistics.median(
                float(str(row["generated_tokens_per_s"])) for row in rows
            ),
            "median_prompt_tokens_per_s": statistics.median(
                float(str(row["prompt_tokens_per_s"])) for row in rows
            ),
            "peak_server_rss_bytes": monitor.peak_rss,
            "peak_swap_growth_bytes": monitor.peak_swap_growth,
            "timings": timings,
            "evidence_directory": str(output.relative_to(root)),
        }
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        (root / "docs/SPIKE.md").write_text(render_spike_report(report))
        return output
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        reason = monitor.failure or (
            str(exc) if isinstance(exc, (SpikeRefused, SwapRefused)) else type(exc).__name__
        )
        failure: dict[str, Json] = {
            "measured": False,
            "reason": reason,
            "completed_repeats": len(timings),
            "peak_server_rss_bytes": monitor.peak_rss,
        }
        (output / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
        (root / "docs/SPIKE.md").write_text(
            "# Device spike\n\nLatest authorized attempt refused; no new valid measurement.\n\n"
            f"Reason: `{reason}`. Evidence: `{output.relative_to(root)}/failure.json`.\n"
        )
        raise SpikeRefused("device_spike_refused_see_persisted_failure") from exc
    finally:
        monitor.stop()
        server.stop()
