"""One credit-capped Function on the adopted cached BASE image; no endpoints."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent if modal.is_local() else Path("/workspace/modal")
sys.path.insert(0, str(HERE))
import model_sim_app as committed_base  # noqa: E402

ROOT = committed_base.ROOT
CACHED_IMAGE_ID = "im-SCX0LIf6ErRaTfpEQCG06W"
SOURCE_APP_ID = "PROVIDER_ACCOUNT_REMOVED"
FUNCTION_SECONDS = 1000
STARTUP_SECONDS = 30
SCALEDOWN_SECONDS = 2
COST_CAP = Decimal("0.50")
RESERVE = Decimal("0.02")
MANIFEST = "data/model-sim-heldout-sun/manifest.json"
PROFILE_CONTRACT = "configs/model-sim-proxy-fix.v3.json"

# This image is the read-back image ID of the adopted 21/21 Function. Using its
# immutable ID is intentional: a cache miss must refuse, never trigger a build.
image = modal.Image.from_id(CACHED_IMAGE_ID)
if modal.is_local():
    for name in (
        "src",
        "data",
        "eval",
        "schemas",
        "templates",
        "scripts",
        "configs",
        "harness",
        "tests/uplink",
        "tests/top10",
        "modal",
    ):
        image = image.add_local_dir(
            str(ROOT / name), "/workspace/" + name, ignore=["__pycache__", "*.pyc"]
        )
    image = image.add_local_file(
        str(ROOT / "tests/test_model_sim_heldout.py"),
        "/workspace/tests/test_model_sim_heldout.py",
    )
    for name in ("LICENSE", "docs/UPLINK-FAULTS.json", "pyproject.toml", "uv.lock"):
        image = image.add_local_file(str(ROOT / name), "/workspace/" + name)
app = modal.App("uplink-heldout-compound-sun")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def billing(kind: str) -> dict:
    proc = subprocess.run(
        ["modal", "billing", kind, "--json", "--profile", "rgsuarez"],
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    return json.loads(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", proc.stdout))


def budget(rates: dict, before: dict) -> Decimal:
    billed, metered = (Decimal(str(before.get(k, "NaN"))) for k in ("billed_cost", "metered_cost"))
    unit_rates = [
        Decimal(str(rates.get(k, "NaN")))
        for k in ("gpu_hour_cost_a10g", "cpu_hour_cost", "mem_gib_hour_cost")
    ]
    if not all(rate.is_finite() and rate > 0 for rate in unit_rates):
        raise ValueError("heldout_credit_or_cost_cap_refused")
    hourly = sum(rate * count for rate, count in zip(unit_rates, (1, 2, 32), strict=True))
    ceiling = hourly * (FUNCTION_SECONDS + STARTUP_SECONDS + SCALEDOWN_SECONDS) / 3600 + RESERVE
    # The account-specific credit decision is recorded in the registration. This
    # fresh check detects a changed billed state or loss of its $30-plan margin.
    if (
        not all(v.is_finite() for v in (billed, metered, hourly, ceiling))
        or billed != 0
        or not 0 <= metered <= Decimal("29.50")
        or hourly <= 0
        or ceiling > COST_CAP
    ):
        raise ValueError("heldout_credit_or_cost_cap_refused")
    return ceiling


def start_server(model: Path, log_path: Path, deadline: float):
    log = log_path.open("xb")
    process = subprocess.Popen(
        [
            "/opt/llama.cpp/build/bin/llama-server",
            "--model",
            str(model),
            "--host",
            _public_fixture("@PUBLIC_IPV4_056@"),
            "--port",
            "8080",
            "--ctx-size",
            "8192",
            "--parallel",
            "1",
            "--seed",
            "0",
            "--temp",
            "0",
            "--n-gpu-layers",
            "99",
            "--no-warmup",
            "--jinja",
            "--chat-template-file",
            "/workspace/templates/glimmer_native.jinja",
            "--chat-template-kwargs",
            json.dumps({"reasoning_strength": "low"}),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    started = time.monotonic()
    until = min(deadline - 30, started + 60)
    while time.monotonic() < until and process.poll() is None:
        try:
            with urllib.request.urlopen(_public_fixture("http://@PUBLIC_IPV4_056@:8080/health"), timeout=2) as response:
                if response.status == 200:
                    return process, log, True, round(time.monotonic() - started, 3)
        except OSError:
            time.sleep(1)
    return process, log, False, round(time.monotonic() - started, 3)


@app.function(
    image=image,
    gpu="A10G",
    cpu=(2, 2),
    memory=(32768, 32768),
    timeout=FUNCTION_SECONDS,
    startup_timeout=STARTUP_SECONDS,
    retries=0,
    max_containers=1,
    min_containers=0,
    scaledown_window=SCALEDOWN_SECONDS,
    single_use_containers=True,
    block_network=True,
    volumes={"/assets": committed_base.volume.with_mount_options(read_only=True)},
)
def run_heldout(freeze_commit: str, manifest_sha256: str) -> str:
    started = time.monotonic()
    deadline = started + FUNCTION_SECONDS - 20
    result = {
        "evidence_kind": "MODEL_IN_LOOP_SIM_HELDOUT_COMPOUND",
        "freeze_commit": freeze_commit,
        "manifest_sha256": manifest_sha256,
        "profile_id": "snapshot-restore-descriptions-v1",
        "adapter_enabled": False,
        "shared_endpoints_used": False,
        "cached_image_id": CACHED_IMAGE_ID,
        "llama_revision": committed_base.LLAMA,
        "started_at": datetime.now(UTC).isoformat(),
    }
    out = Path("/root/heldout-out")
    log_path = Path("/root/heldout-server.log")
    process = log = None
    phase = "verify_frozen_inputs"
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", freeze_commit):
            raise ValueError("heldout_freeze_commit_required")
        if digest(ROOT / MANIFEST) != manifest_sha256:
            raise ValueError("heldout_manifest_changed")
        sys.path.insert(0, str(ROOT / "scripts"))
        from model_in_loop_heldout import verify_frozen

        verify_frozen(ROOT)
        contract = json.loads((ROOT / PROFILE_CONTRACT).read_text())
        model = Path("/assets/glimmer-gguf") / contract["model"]
        if (
            model.stat().st_size != contract["model_bytes"]
            or digest(model) != contract["model_sha256"]
        ):
            raise ValueError("heldout_base_model_identity_mismatch")
        result["model"] = {k: contract[k] for k in ("model", "model_sha256", "model_bytes")}
        result["verification_s"] = round(time.monotonic() - started, 3)
        phase = "server_start"
        process, log, ready, load_s = start_server(model, log_path, deadline)
        result["load_s"] = load_s
        if not ready:
            raise RuntimeError("heldout_server_not_ready")
        phase = "heldout_inference"
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("heldout_budget_deadline")
        proc = subprocess.run(
            [
                sys.executable,
                "/workspace/scripts/model_in_loop_heldout.py",
                "--output",
                str(out),
                "--freeze-commit",
                freeze_commit,
            ],
            cwd="/workspace",
            capture_output=True,
            text=True,
            timeout=remaining,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "UPLINK_TOP10_SANDBOX": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        result.update(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except Exception as exc:
        result.update(error=type(exc).__name__, failed_phase=phase)
        if isinstance(exc, (ValueError, RuntimeError, TimeoutError)):
            result["failure_reason"] = str(exc)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log is not None:
            log.close()
    summary = out / "summary.json"
    result["summary"] = json.loads(summary.read_text()) if summary.exists() else None
    # Return all synthetic receipts and audit rows, including partial failures.
    result["files"] = (
        {
            str(p.relative_to(out)): p.read_text(errors="replace")
            for p in sorted(out.rglob("*"))
            if p.is_file() and (p.suffix in {".json", ".jsonl", ".log"})
        }
        if out.exists()
        else {}
    )
    result["server_log"] = log_path.read_text(errors="replace") if log_path.exists() else ""
    result.update(
        elapsed_s=round(time.monotonic() - started, 3), finished_at=datetime.now(UTC).isoformat()
    )
    return json.dumps(result)


@app.local_entrypoint()
def main(freeze_commit: str, output: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", freeze_commit):
        raise ValueError("heldout_freeze_commit_required")
    out = Path(output).resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    rates, before = billing("rates"), billing("summary")
    ceiling = budget(rates, before)
    manifest_sha256 = digest(ROOT / MANIFEST)
    intent = {
        "app_id": app.app_id,
        "freeze_commit": freeze_commit,
        "manifest_sha256": manifest_sha256,
        "billing_before": before,
        "rates": rates,
        "compute_plus_reserve_ceiling_usd": str(ceiling),
        "cost_cap_usd": str(COST_CAP),
        "cached_image_id": CACHED_IMAGE_ID,
        "cached_image_source_app": SOURCE_APP_ID,
        "function_seconds": FUNCTION_SECONDS,
        "startup_seconds": STARTUP_SECONDS,
        "scaledown_seconds": SCALEDOWN_SECONDS,
        "max_containers": 1,
        "retries": 0,
        "gpu": "A10G",
        "cpu_request_and_limit": 2,
        "memory_request_and_limit_mib": 32768,
        "shared_endpoints_allowed": False,
        "block_network": True,
        "model_volume_read_only": True,
        "started_at": datetime.now(UTC).isoformat(),
    }
    (out / "intent.json").write_text(json.dumps(intent, indent=2) + "\n")
    print("INTENT", out, flush=True)
    try:
        payload = run_heldout.remote(freeze_commit, manifest_sha256)
    except Exception as exc:
        payload = json.dumps({"error": "remote_call_failed", "exception_type": type(exc).__name__})
    (out / "result.json").write_text(payload)
    result = json.loads(payload)
    summary = result.get("summary") or {}
    print(
        json.dumps(
            {
                "error": result.get("error"),
                "failed_phase": result.get("failed_phase"),
                "failure_reason": result.get("failure_reason"),
                **{
                    k: summary.get(k)
                    for k in (
                        "complete",
                        "repair_target",
                        "held_policy",
                        "false_online",
                        "cleanup_verified",
                    )
                },
            }
        )
    )
    print("WROTE", out, flush=True)


if __name__ == "__main__":
    raise SystemExit("Use modal run --profile rgsuarez modal/model_sim_heldout_app.py")
