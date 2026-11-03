"""Credit-capped BASE Q4 balanced-description final attempt; all 21 frozen trials."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

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
import model_sim_base_fix_app as base  # noqa: E402

ROOT = base.ROOT
image = base.image
if modal.is_local():
    image = image.add_local_file(
        str(HERE / "model_sim_base_fix_app.py"), "/workspace/modal/model_sim_base_fix_app.py"
    )
app = modal.App("uplink-proxy-fix-model-sim")
CONTRACT_PATH = "configs/model-sim-proxy-fix.v3.json"
CONTRACT_SHA256 = "ac9824ec7bc67812e001ae65a5bf01ec865c6ee5cf09197011aed2ca379ec1cf"
PREVIOUS_COST = Decimal("0.07240661")
COST_CAP = PREVIOUS_COST * 3
PRIOR_MISSION_COST = Decimal("0.15074343")
FUNCTION_SECONDS = 145
STARTUP_SECONDS = 10


def start_server(model_path: Path, log_path: Path, deadline: float):
    log = log_path.open("wb")
    process = subprocess.Popen(
        [
            "/opt/llama.cpp/build/bin/llama-server",
            "--model",
            str(model_path),
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
    until = min(deadline - 20, started + 60)
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
    scaledown_window=2,
    single_use_containers=True,
    volumes={"/assets": base.volume},
)
def run_proxy_fix() -> str:
    started = time.monotonic()
    deadline = started + FUNCTION_SECONDS - 5
    result = {
        "evidence_kind": "MODEL_IN_LOOP_SIM",
        "variant": "BASE_Q4_BALANCED_RESTORE_DESCRIPTIONS",
        "profile_id": "snapshot-restore-descriptions-v1",
        "adapter_enabled": False,
        "disclaimer": (
            "Post-hoc prompt/description revision on known simulated faults, not live Wi-Fi."
        ),
        "started_at": datetime.now(UTC).isoformat(),
        "diagnostics": [],
        "diagnostic_false_online": 0,
        "llama_revision": base.LLAMA,
    }
    process = log = None
    out = Path("/root/proxy-fix-out")
    phase = "input_verification"
    try:
        contract_path = ROOT / CONTRACT_PATH
        if base.digest(contract_path) != CONTRACT_SHA256:
            raise ValueError("proxy_contract_hash_mismatch")
        contract = json.loads(contract_path.read_text())
        for name, expected in contract["files"].items():
            if base.digest(ROOT / name) != expected:
                raise ValueError("proxy_input_hash_mismatch:" + name)
        frozen_path = ROOT / "harness/top10/FROZEN.json"
        if base.digest(frozen_path) != contract["frozen_manifest_sha256"]:
            raise ValueError("frozen_manifest_hash_mismatch")
        verified = {}
        for name, expected in json.loads(frozen_path.read_text())["files"].items():
            if name.startswith(("src/", "configs/", "harness/", "tests/uplink/", "tests/top10/")):
                if base.digest(ROOT / name) != expected:
                    raise ValueError("frozen_runtime_hash_mismatch:" + name)
                verified[name] = expected
        model = Path("/assets/glimmer-gguf", contract["model"])
        if (
            model.stat().st_size != contract["model_bytes"]
            or base.digest(model) != contract["model_sha256"]
        ):
            raise ValueError("base_model_identity_mismatch")
        result.update(
            profile_contract=contract,
            profile_contract_sha256=CONTRACT_SHA256,
            verified_frozen_runtime_inputs=verified,
            model={
                "filename": model.name,
                "bytes": model.stat().st_size,
                "sha256": contract["model_sha256"],
            },
        )
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result["gpu"] = gpu.stdout.strip() if gpu.returncode == 0 else "unavailable"
        result["verification_s"] = round(time.monotonic() - started, 3)
        # The eight preceding ablations are retained separately and are not repeated.
        phase = "scored_server_start"
        process, log, ready, load_s = start_server(model, Path("/root/proxy-scored.log"), deadline)
        result["scored_load_s"] = load_s
        if not ready:
            raise RuntimeError("scored_server_not_ready")
        phase = "full_21_trial_simulation"
        remaining = int(deadline - time.monotonic() - 2)
        if remaining < 1:
            raise TimeoutError("credit_budget_deadline")
        proc = subprocess.run(
            [
                sys.executable,
                "/workspace/scripts/model_in_loop_proxy_fix.py",
                "--output",
                str(out),
                "--label",
                "modal-BASE-Q4-snapshot-restore-descriptions-v1",
            ],
            cwd="/workspace",
            capture_output=True,
            text=True,
            timeout=remaining,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        result.update(rc=proc.returncode, stdout=proc.stdout[-3000:], stderr=proc.stderr[-3000:])
    except Exception as exc:
        result.update(error=type(exc).__name__, failed_phase=phase)
        if isinstance(exc, (ValueError, RuntimeError, TimeoutError)):
            result["failure_reason"] = str(exc)
    finally:
        base.stop_server(process, log)
    summary = out / "MODEL-IN-LOOP-SUMMARY.json"
    result["summary"] = json.loads(summary.read_text()) if summary.is_file() else None
    result["receipts"] = [
        json.loads(p.read_text()) for p in sorted((out / "receipts").glob("*/receipt.json"))
    ]
    result["trial_records"] = {}
    for receipt in result["receipts"]:
        directory = out / "receipts" / receipt["run_id"]
        result["trial_records"][receipt["run_id"]] = {
            p.name: json.loads(p.read_text())
            for p in sorted(directory.glob("*.json"))
            if re.fullmatch(
                r"(?:model-[0-9]+|observe-[0-9]+|oracle-[0-9]+|verify-observation-[0-9]+|verify-oracle-[0-9]+)\.json",
                p.name,
            )
        }
    for name in ("proxy-probes", "proxy-scored"):
        path = Path("/root") / (name + ".log")
        result[name + "_server_log"] = path.read_text(errors="replace") if path.exists() else ""
    result.update(
        elapsed_s=round(time.monotonic() - started, 3), finished_at=datetime.now(UTC).isoformat()
    )
    return json.dumps(result)


def billing(kind: str):
    proc = subprocess.run(
        ["modal", "billing", kind, "--json", "--profile", "rgsuarez"],
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    return json.loads(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", proc.stdout))


@app.local_entrypoint()
def main() -> None:
    rates, before = billing("rates"), billing("summary")
    billed, metered = (Decimal(str(before.get(k, "NaN"))) for k in ("billed_cost", "metered_cost"))
    hourly = sum(
        Decimal(str(rates[k])) * count
        for k, count in (("gpu_hour_cost_a10g", 1), ("cpu_hour_cost", 2), ("mem_gib_hour_cost", 32))
    )
    ceiling = hourly * (FUNCTION_SECONDS + STARTUP_SECONDS + 2) / 3600 + Decimal("0.002")
    if (
        not all(x.is_finite() for x in (billed, metered, hourly, ceiling))
        or billed != 0
        or not 0 <= metered < 28
        or hourly <= 0
        or ceiling + PRIOR_MISSION_COST > COST_CAP
    ):
        raise ValueError("credit_or_cost_cap_refused")
    out = ROOT / "artifacts/uplink" / ("mil-proxy-fix-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "intent.json").write_text(
        json.dumps(
            {
                "app_id": app.app_id,
                "profile": "rgsuarez",
                "adapter_enabled": False,
                "profile_id": "snapshot-restore-descriptions-v1",
                "diagnostic_calls": 0,
                "planned_trials": 21,
                "previous_cost_usd": str(PREVIOUS_COST),
                "cost_cap_usd": str(COST_CAP),
                "prior_mission_cost_usd": str(PRIOR_MISSION_COST),
                "cumulative_ceiling_usd": str(ceiling + PRIOR_MISSION_COST),
                "compute_plus_reserve_ceiling_usd": str(ceiling),
                "rates": rates,
                "billing_before": before,
                "function_timeout_s": FUNCTION_SECONDS,
                "startup_timeout_s": STARTUP_SECONDS,
                "gpu": "A10G",
                "cpu_request_and_limit": 2,
                "memory_request_and_limit_mib": 32768,
                "launcher_sha256": base.digest(Path(__file__)),
                "contract_sha256": CONTRACT_SHA256,
            },
            indent=2,
        )
        + "\n"
    )
    print("INTENT", out, flush=True)
    try:
        payload = run_proxy_fix.remote()
    except Exception as exc:
        payload = json.dumps({"error": "remote_call_failed", "exception_type": type(exc).__name__})
    (out / "result.json").write_text(payload)
    result = json.loads(payload)
    summary = result.get("summary") or {}
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("error", "failed_phase", "failure_reason", "gpu", "elapsed_s")
            }
        )
    )
    print(
        json.dumps(
            {
                k: summary.get(k)
                for k in (
                    "suite_complete",
                    "suite_failure",
                    "counts",
                    "false_online",
                    "model_calls",
                )
            }
        )
    )
    print("WROTE", out, flush=True)
    if (
        result.get("error")
        or result.get("rc") != 0
        or summary.get("suite_complete") is not True
        or summary.get("false_online") != 0
        or result.get("diagnostic_false_online") != 0
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit("Use modal run --profile rgsuarez modal/model_sim_proxy_fix_app.py")
