"""MODEL_IN_LOOP_SIM on one Modal GPU with L4, A10G, L40S fallback.

Real Glimmer BASE GGUF from the existing volume, with the frozen simulated harness.
Run: modal run modal/model_sim_app.py. Function cap: 45 minutes, estimated compute
under $2 at current rates, plus the cached CPU image build. No secrets, no adapter.
"""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import base64
import json
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1] if modal.is_local() else Path("/workspace")
CFG = json.loads((ROOT / "configs/remote-toolchain.json").read_text())
LLAMA = CFG["native_sources"]["ggml-org/llama.cpp"]["revision"]
PATCH = shlex.quote(base64.b64encode((ROOT / "configs/llama-glimmer-layout.patch").read_bytes()).decode())

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
    "src", "data", "eval", "schemas", "templates", "scripts", "configs", "harness",
    "tests/uplink", "tests/top10",
):
    if (ROOT / name).is_dir():
        image = image.add_local_dir(
            str(ROOT / name), "/workspace/" + name, ignore=["__pycache__", "*.pyc"]
        )

app = modal.App("uplink-model-in-loop-sim")
volume = modal.Volume.from_name("offline-agent-f4-sanity", create_if_missing=False)


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
def run_sim() -> str:
    ggufs = sorted(Path("/assets/glimmer-gguf").glob("*.gguf"))
    assert len(ggufs) == 1, ggufs
    log = open("/root/server.log", "wb")
    server = subprocess.Popen(
        [
            "/opt/llama.cpp/build/bin/llama-server", "--model", str(ggufs[0]),
            "--host", _public_fixture("@PUBLIC_IPV4_056@"), "--port", "8080", "--ctx-size", "8192", "--parallel", "1",
            "--seed", "0", "--temp", "0", "--n-gpu-layers", "99", "--no-warmup", "--jinja",
            "--chat-template-file", "/workspace/templates/glimmer_native.jinja",
            "--chat-template-kwargs", json.dumps({"reasoning_strength": "low"}),
        ],
        stdout=log, stderr=subprocess.STDOUT,
    )
    started = time.time()
    ready = False
    while time.time() - started < 900 and server.poll() is None:
        try:
            with urllib.request.urlopen(_public_fixture("http://@PUBLIC_IPV4_056@:8080/health"), timeout=3) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(5)
    load_s = round(time.time() - started, 1)
    if not ready:
        return json.dumps({"error": "server_not_ready", "load_s": load_s,
                           "log_tail": Path("/root/server.log").read_text()[-3000:]})
    out = "/root/mil-out"
    proc = subprocess.run(
        [sys.executable, "/workspace/scripts/model_in_loop_sim.py", "--output", out,
         "--label", "modal-L4-" + ggufs[0].name],
        cwd="/workspace", capture_output=True, text=True, timeout=1800,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root"},
    )
    server.terminate()
    summary = Path(out, "MODEL-IN-LOOP-SUMMARY.json")
    return json.dumps({
        "load_s": load_s, "rc": proc.returncode, "stdout": proc.stdout[-3000:],
        "stderr": proc.stderr[-3000:],
        "summary": json.loads(summary.read_text()) if summary.exists() else None,
        "server_log_tail": Path("/root/server.log").read_text()[-1500:],
    })


@app.local_entrypoint()
def main() -> None:
    res = run_sim.remote()
    out = ROOT / "artifacts/uplink" / ("mil-modal-" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)
    (out / "result.json").write_text(res)
    print(res[:4000])
    print("WROTE", out)
