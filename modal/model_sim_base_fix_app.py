"""BASE Q4 prompt-fix probes and a separately scored simulation; no adapters."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import base64
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1] if modal.is_local() else Path("/workspace")
CFG = json.loads((ROOT / "configs/remote-toolchain.json").read_text())
LLAMA = CFG["native_sources"]["ggml-org/llama.cpp"]["revision"]
PATCH = shlex.quote(
    base64.b64encode((ROOT / "configs/llama-glimmer-layout.patch").read_bytes()).decode()
)

BUILD = (
    "git init /opt/llama.cpp && git -C /opt/llama.cpp remote add origin "
    "https://github.com/ggml-org/llama.cpp.git && git -C /opt/llama.cpp fetch --depth 1 origin "
    + LLAMA
    + " && git -C /opt/llama.cpp checkout --detach FETCH_HEAD && printf %s "
    + PATCH
    + " | base64 --decode | git -C /opt/llama.cpp apply - && mkdir -p /opt/cuda-link && "
    "ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /opt/cuda-link/libcuda.so.1 && "
    "cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DGGML_CUDA=ON -DLLAMA_CURL=OFF "
    "-DCMAKE_BUILD_TYPE=Release '-DCMAKE_CUDA_ARCHITECTURES=80;86;89;90' "
    "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-rpath-link,/opt/cuda-link && "
    "cmake --build /opt/llama.cpp/build --target llama-server -j 8"
)

image = (
    modal.Image.from_registry(CFG["cuda_image"], add_python="3.12")
    .apt_install("git", "cmake", "ninja-build", "build-essential", "pkg-config")
    .pip_install("jsonschema==4.26.0", "jinja2==3.1.6", "numpy==2.2.6")
    .run_commands(BUILD)
)
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
):
    if (ROOT / name).is_dir():
        image = image.add_local_dir(
            str(ROOT / name), "/workspace/" + name, ignore=["__pycache__", "*.pyc"]
        )


app = modal.App("uplink-base-fix-model-sim")
volume = modal.Volume.from_name("offline-agent-f4-sanity", create_if_missing=False)
CONTRACT_PATH = "configs/model-sim-base-fix.v1.json"
CONTRACT_SHA256 = "b5eab09c3643887e21095901005aee996b7d8b875ee8912c11635fa4e5802f1d"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def start_server(model_path: Path, log_path: Path):
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
    ready = False
    while time.monotonic() - started < 900 and process.poll() is None:
        try:
            with urllib.request.urlopen(_public_fixture("http://@PUBLIC_IPV4_056@:8080/health"), timeout=3) as response:
                if response.status == 200:
                    ready = True
                    break
        except OSError:
            time.sleep(5)
    return process, log, ready, round(time.monotonic() - started, 3)


def stop_server(process, log) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if log is not None:
        log.close()


@app.function(
    image=image,
    gpu=["L4", "A10G", "L40S"],
    cpu=4,
    memory=32768,
    timeout=2700,
    retries=0,
    max_containers=1,
    volumes={"/assets": volume},
)
def run_base_fix() -> str:
    started = time.monotonic()
    deadline = started + 2640
    result = {
        "evidence_kind": "MODEL_IN_LOOP_SIM",
        "variant": "BASE_Q4_PROMPT_FIX",
        "profile_id": "authorized-repair-v1",
        "adapter_enabled": False,
        "disclaimer": "Real BASE Q4 model, simulated faults. Not live Wi-Fi and not TOP10.",
        "started_at": datetime.now(UTC).isoformat(),
        "diagnostics": [],
        "llama_revision": LLAMA,
        "gpu_fallback": ["L4", "A10G", "L40S"],
    }
    process = log = None
    probe_log = Path("/root/base-fix-probes-server.log")
    scored_log = Path("/root/base-fix-scored-server.log")
    out = Path("/root/base-fix-out")
    phase = "input_verification"
    try:
        contract_file = ROOT / CONTRACT_PATH
        if digest(contract_file) != CONTRACT_SHA256:
            raise ValueError("profile_contract_hash_mismatch")
        contract = json.loads(contract_file.read_text())
        for name, expected in contract["files"].items():
            if digest(ROOT / name) != expected:
                raise ValueError("profile_input_hash_mismatch:" + name)
        frozen_path = ROOT / "harness/top10/FROZEN.json"
        if digest(frozen_path) != contract["frozen_manifest_sha256"]:
            raise ValueError("frozen_manifest_hash_mismatch")
        frozen_files = json.loads(frozen_path.read_text())["files"]
        verified = {}
        for name in frozen_files:
            if name.startswith(("src/", "configs/", "harness/", "tests/uplink/", "tests/top10/")):
                if digest(ROOT / name) != frozen_files[name]:
                    raise ValueError("frozen_runtime_hash_mismatch:" + name)
                verified[name] = frozen_files[name]
        model_path = Path("/assets/glimmer-gguf", contract["model"])
        if model_path.stat().st_size != contract["model_bytes"]:
            raise ValueError("base_model_size_mismatch")
        model_hash = digest(model_path)
        if model_hash != contract["model_sha256"]:
            raise ValueError("base_model_hash_mismatch")
        result["model"] = {
            "filename": model_path.name,
            "bytes": model_path.stat().st_size,
            "sha256": model_hash,
        }
        result["profile_contract"] = contract
        result["profile_contract_sha256"] = CONTRACT_SHA256
        result["verified_frozen_runtime_inputs"] = verified
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        result["gpu"] = gpu.stdout.strip() if gpu.returncode == 0 else "unavailable"
        result["verification_s"] = round(time.monotonic() - started, 3)

        phase = "diagnostic_server_start"
        process, log, ready, load_s = start_server(model_path, probe_log)
        result["diagnostic_load_s"] = load_s
        if not ready:
            raise RuntimeError("diagnostic_server_not_ready")
        sys.path.insert(0, str(ROOT / "src"))
        os.environ["UPLINK_TOP10_SANDBOX"] = "1"
        from offline_agent.uplink import model_sim_profile as revised
        from offline_agent.uplink import top10_model as original

        probes = json.loads((ROOT / "data/model-sim-base-fix/probes-v1.json").read_text())["cases"]
        if len(probes) != 2 or {p["fault_id"] for p in probes} != {"F04", "F05"}:
            raise ValueError("exact_diagnostic_cases_required")
        phase = "fixed_state_diagnostics"
        for probe in probes:
            pair = {
                "fault_id": probe["fault_id"],
                "trial": 1,
                "unscored": True,
                "expected_safe_repairs": probe["expected_safe_repairs"],
            }
            for name, provider in (("original", original), ("revised", revised)):
                oracle = copy.deepcopy(probe["oracle"])
                oracle["checked_at"] = time.time()
                before = time.monotonic()
                pair[name] = provider.generate(
                    ROOT,
                    probe["snapshot_id"],
                    probe["facts"],
                    oracle,
                    [],
                    8,
                )
                pair[name]["call_wall_ms"] = round((time.monotonic() - before) * 1000, 3)
            pair["grammar_identical"] = pair["original"]["grammar"] == pair["revised"]["grammar"]
            result["diagnostics"].append(pair)
        stop_server(process, log)
        process = log = None

        # Fresh server for the scored 21-trial run; diagnostics receive no recovery credit.
        phase = "scored_server_start"
        process, log, ready, load_s = start_server(model_path, scored_log)
        result["scored_load_s"] = load_s
        if not ready:
            raise RuntimeError("scored_server_not_ready")
        phase = "scored_simulation"
        remaining = min(1800, int(deadline - time.monotonic() - 15))
        if remaining <= 0:
            raise TimeoutError("function_budget_exhausted")
        proc = subprocess.run(
            [
                sys.executable,
                "/workspace/scripts/model_in_loop_base_fix.py",
                "--output",
                str(out),
                "--label",
                "modal-BASE-Q4-authorized-repair-v1",
            ],
            cwd="/workspace",
            capture_output=True,
            text=True,
            timeout=remaining,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root"},
        )
        result.update(rc=proc.returncode, stdout=proc.stdout[-3000:], stderr=proc.stderr[-3000:])
    except Exception as exc:
        result.update(error=type(exc).__name__, failed_phase=phase)
        if isinstance(exc, (ValueError, RuntimeError, TimeoutError)):
            result["failure_reason"] = str(exc)
    finally:
        stop_server(process, log)
    summary = out / "MODEL-IN-LOOP-SUMMARY.json"
    result["summary"] = json.loads(summary.read_text()) if summary.is_file() else None
    result["receipts"] = [
        json.loads(path.read_text()) for path in sorted((out / "receipts").glob("*/receipt.json"))
    ]
    result["trial_records"] = {}
    for receipt in result["receipts"]:
        directory = out / "receipts" / receipt["run_id"]
        result["trial_records"][receipt["run_id"]] = {
            path.name: json.loads(path.read_text())
            for path in sorted(directory.glob("*.json"))
            if re.fullmatch(
                r"(?:model-[0-9]+|observe-[0-9]+|oracle-[0-9]+|verify-observation-[0-9]+|verify-oracle-[0-9]+)\.json",
                path.name,
            )
        }
    result["diagnostic_server_log"] = (
        probe_log.read_text(errors="replace") if probe_log.exists() else ""
    )
    result["scored_server_log"] = (
        scored_log.read_text(errors="replace") if scored_log.exists() else ""
    )
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    result["finished_at"] = datetime.now(UTC).isoformat()
    return json.dumps(result)


@app.local_entrypoint()
def main() -> None:
    billing_process = subprocess.run(
        ["modal", "billing", "summary", "--json", "--profile", "rgsuarez"],
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    billing = json.loads(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", billing_process.stdout))
    billed = Decimal(str(billing.get("billed_cost", "NaN")))
    metered = Decimal(str(billing.get("metered_cost", "NaN")))
    if not billed.is_finite() or not metered.is_finite() or billed != 0 or not 0 <= metered < 28:
        raise ValueError("free_credit_headroom_required")
    out = ROOT / "artifacts/uplink" / ("mil-base-fix-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "intent.json").write_text(
        json.dumps(
            {
                "profile_id": "authorized-repair-v1",
                "adapter_enabled": False,
                "profile": "rgsuarez",
                "volume": "offline-agent-f4-sanity",
                "billing_before": billing,
                "diagnostic_calls": 4,
                "planned_trials": 21,
                "function_timeout_seconds": 2700,
                "gpu_fallback": ["L4", "A10G", "L40S"],
                "estimated_max_compute_usd": "1.7964",
                "launcher_sha256": digest(Path(__file__)),
                "profile_contract_sha256": CONTRACT_SHA256,
            },
            indent=2,
        )
        + "\n"
    )
    try:
        payload = run_base_fix.remote()
    except Exception as exc:
        payload = json.dumps({"error": "remote_call_failed", "exception_type": type(exc).__name__})
    (out / "result.json").write_text(payload)
    result = json.loads(payload)
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("error", "failed_phase", "failure_reason", "gpu", "elapsed_s")
            }
        )
    )
    summary = result.get("summary") or {}
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
    print("WROTE", out)
    if (
        result.get("error")
        or result.get("rc") != 0
        or summary.get("suite_complete") is not True
        or summary.get("false_online") != 0
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit("Use modal run --profile rgsuarez modal/model_sim_base_fix_app.py")
