#!/bin/bash
# lab on-device bench: Muse-Glimmer-30B Q4_K_M on pinned llama.cpp b10353 (Metal).
#   ./run-bench.sh --dry-run          # no model load: preflight, harness MOCK smoke, probe plan, sampler, watchdog, summary
#   ./run-bench.sh --confirm-drain    # REAL run on lab after drain confirmation; 22:20 CT hard stop
# Options: --skip-relaxed (omit the 120 s/task pass), --skip-spike (omit the project's device spike).
# Writes to ${BENCH_ROOT:-lab-bench}/results/<stamp>/. Dry runs never load or kill a model.
set -u -o pipefail
export TZ=America/Chicago
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HELPERS="$SCRIPT_DIR"; [ -f "$HELPERS/common.py" ] || HELPERS="$SCRIPT_DIR/bench"
BENCH="${BENCH_ROOT:-$HOME/lab-bench}"
REPO="${BENCH_REPO:-$BENCH/offline-agent}"
LLAMA_DIR="$BENCH/llama-b10353/llama-b10353"
GGUF="$BENCH/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
PIN_SHA=4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e
PY="${BENCH_PYTHON:-$REPO/.venv/bin/python}"
export PYTHONPATH="$REPO/src"
WINDOW_START="${WINDOW_START:-21:00}"
HARD_STOP="${HARD_STOP:-22:20}"
MAX_MINUTES="${MAX_MINUTES:-70}"                       # whole-run cap (< 75 min requirement)
RELAXED_SECONDS=120                  # per-task wall budget of the optional non-comparable pass
export TMPDIR="$REPO/artifacts/tmp" PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 NO_PROXY=ADDRESS_REMOVED,localhost

DRY=0; CONFIRM=0; SKIP_RELAXED=0; SKIP_SPIKE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --confirm-drain) CONFIRM=1 ;;
    --skip-relaxed) SKIP_RELAXED=1 ;;
    --skip-spike) SKIP_SPIKE=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ $DRY -eq 0 ] && [ $CONFIRM -eq 0 ]; then
  echo "Refusing: a real run needs --confirm-drain (the fleet coordinator's drain confirmation). Use --dry-run otherwise." >&2; exit 2
fi
[ $DRY -eq 1 ] && [ $CONFIRM -eq 1 ] && { echo "--dry-run and --confirm-drain are exclusive" >&2; exit 2; }

STAMP=$(date +%Y%m%d-%H%M%S)-$$
if [ $DRY -eq 1 ]; then OUT="$BENCH/results/dryrun-$STAMP"; else OUT="$BENCH/results/$STAMP"; fi
mkdir -p "$OUT/preflight" "$TMPDIR" || exit 1
[ $DRY -eq 1 ] && touch "$OUT/DRY_RUN"
[ $DRY -eq 0 ] && ln -sfn "$OUT" "$BENCH/results/current"   # follow with: tail -f ${BENCH_ROOT:-lab-bench}/results/current/run.log
exec > >(tee -a "$OUT/run.log") 2>&1
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

epoch_at() { date -j -f "%Y-%m-%d %H:%M:%S" "$(date +%Y-%m-%d) $1:00" +%s; }
NOW=$(date +%s); START_EPOCH=$(epoch_at "$WINDOW_START"); HARD_EPOCH=$(epoch_at "$HARD_STOP")
CAP_EPOCH=$((NOW + MAX_MINUTES * 60))
STOP_EPOCH=$HARD_EPOCH; [ $CAP_EPOCH -lt $STOP_EPOCH ] && STOP_EPOCH=$CAP_EPOCH
if [ $DRY -eq 1 ]; then STOP_EPOCH=$CAP_EPOCH; fi   # dry run loads nothing; window does not apply
remaining() { echo $((STOP_EPOCH - $(date +%s))); }
MAIN_PID=$$
CHILD=""; SAMPLER=""; WATCHDOG=""; CAFF=""; SERVER_WRAP=""

kill_tree() {
  local root_pid="$1" pid
  for pid in $(/usr/bin/pgrep -P "$root_pid" 2>/dev/null); do kill_tree "$pid"; done
  kill -TERM "$root_pid" 2>/dev/null || true
}

kill_llama() {
  [ $DRY -eq 1 ] && return 0
  # Only processes descended from this runner are owned by this run.
  [ -n "$CHILD" ] && kill_tree "$CHILD"
  return 0
}

PHASE=""; PHASE_T0=0
phase_begin() { PHASE="$1"; PHASE_T0=$(date +%s); log "=== phase $PHASE (remaining $(remaining)s)"; }
phase_end() { local t1; t1=$(date +%s); printf "%s\t%s\t%s\t%s\t%s\n" "$PHASE" "$1" "$(date -r $PHASE_T0 +%H:%M:%S)" "$(date -r $t1 +%H:%M:%S)" "$((t1-PHASE_T0))" >> "$OUT/phases.tsv"; log "=== phase $PHASE: $1 ($((t1-PHASE_T0))s)"; }
skip() { PHASE="$1"; PHASE_T0=$(date +%s); phase_end "skipped:$2"; }

# Run "$@" in the background with a time cap; trap-interruptible. Returns the command's status (124 on cap).
run_capped() {
  local cap=$1; shift
  "$@" & CHILD=$!
  ( i=0; while [ $i -lt "$cap" ]; do sleep 1; kill -0 "$CHILD" 2>/dev/null || exit 0; i=$((i+1)); done
    kill_tree "$CHILD"; sleep 5; kill -KILL "$CHILD" 2>/dev/null ) & local timer=$!
  wait "$CHILD"; local rc=$?
  kill "$timer" 2>/dev/null; wait "$timer" 2>/dev/null
  CHILD=""
  [ $rc -eq 143 ] || [ $rc -eq 137 ] && rc=124
  return $rc
}

stop_server() {
  if [ -n "$SERVER_WRAP" ]; then
    local pid; pid=$(/usr/bin/pgrep -P "$SERVER_WRAP" 2>/dev/null | head -1)
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null           # TERM the server so /usr/bin/time -l reports
    local i=0; while [ $i -lt 15 ] && kill -0 "$SERVER_WRAP" 2>/dev/null; do sleep 1; i=$((i+1)); done
    kill -KILL "$SERVER_WRAP" 2>/dev/null; SERVER_WRAP=""
  fi
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  [ -n "$CHILD" ] && kill_tree "$CHILD"
  stop_server
  kill_llama
  [ -n "$WATCHDOG" ] && kill "$WATCHDOG" 2>/dev/null
  [ -n "$SAMPLER" ] && kill "$SAMPLER" 2>/dev/null
  [ -n "$CAFF" ] && kill "$CAFF" 2>/dev/null
  /usr/bin/pgrep -x "llama-(server|bench|cli)" > "$OUT/llama-procs-after.txt" 2>/dev/null || true
  if [ -s "$OUT/llama-procs-after.txt" ]; then log "Observed llama PIDs after cleanup:"; cat "$OUT/llama-procs-after.txt"; else log "no llama processes remain"; fi
  "$PY" "$HELPERS/summarize.py" --out "$OUT" --spark-ref "$HELPERS/spark-reference-15.json" || log "summarize failed"
  ln -sfn "$OUT" "$BENCH/results/latest$([ $DRY -eq 1 ] && echo -dryrun)"
  log "done rc=$rc; results in $OUT (RESULTS.md, summary.json)"
  exit $rc
}
trap cleanup EXIT
trap 'log "signal received; stopping"; exit 130' INT TERM

log "lab bench start (dry_run=$DRY). Stop at $(date -r $STOP_EPOCH '+%H:%M:%S %Z'); out=$OUT"

# ---------------- phase 0: preflight (never loads the model) ----------------
phase_begin preflight
fail() { log "PREFLIGHT REFUSED: $*"; phase_end "refused:$1"; exit 3; }
if [ $DRY -eq 0 ]; then
  [ -n "${LAB_NODE:-}" ] && [ "$(hostname -s)" = "$LAB_NODE" ] || fail "lab_only" "model loading is restricted to the configured lab host"
  [ "$NOW" -ge "$START_EPOCH" ] || fail "before_window" "now is before $WINDOW_START CT"
  [ "$(remaining)" -ge 900 ] || fail "too_close_to_hard_stop" "less than 15 min before $HARD_STOP CT"
fi
for f in "$PY" "$REPO/templates/glimmer_native.jinja" "$HELPERS/parity_eval.py" "$REPO/configs/f9-ondevice.json"; do
  [ -e "$f" ] || fail "missing_file" "$f"
done
"$PY" -c 'from offline_agent.agent import Budget; from offline_agent.native_protocol import native_request; import inspect; from offline_agent.evaluation import execute_task; assert "history_mode" in inspect.signature(execute_task).parameters; assert Budget().max_seconds == 30' || fail "stale_harness"
if [ -f "$SCRIPT_DIR/package-manifest.json" ]; then
  "$PY" "$REPO/scripts/f9_package.py" --verify "$SCRIPT_DIR" || fail "package_manifest_mismatch"
fi
if [ $DRY -eq 0 ]; then
for f in "$GGUF" "$LLAMA_DIR/llama-server" "$LLAMA_DIR/llama-bench"; do
  [ -e "$f" ] || fail "missing_file" "$f"
done
/usr/bin/pgrep -x "llama-(server|bench|cli)" > "$OUT/preflight/llama-procs-before.txt" 2>/dev/null
[ -s "$OUT/preflight/llama-procs-before.txt" ] && fail "llama_already_running" "$(cat "$OUT/preflight/llama-procs-before.txt")"
/usr/sbin/lsof -nP -iTCP:8080 -sTCP:LISTEN > "$OUT/preflight/port8080.txt" 2>/dev/null && fail "port_8080_busy" "$(cat "$OUT/preflight/port8080.txt")"
"$LLAMA_DIR/llama-server" --version > "$OUT/preflight/llama-server-version.txt" 2>&1 || fail "llama_server_version"
grep -q "10353 (f8def7fe" "$OUT/preflight/llama-server-version.txt" || fail "llama_build_not_pinned"
"$LLAMA_DIR/llama-bench" --list-devices > "$OUT/preflight/llama-bench-devices.txt" 2>&1 || fail "llama_bench_devices"
grep -q "Apple M4 Max" "$OUT/preflight/llama-bench-devices.txt" || fail "metal_device_missing"
WS_MB=$(awk '/recommendedMaxWorkingSetSize/{print $(NF-1)}' "$OUT/preflight/llama-bench-devices.txt" | head -1)
log "Metal recommendedMaxWorkingSetSize=${WS_MB} MB"
log "hashing GGUF (read-only)..."
SHA=$(/usr/bin/shasum -a 256 "$GGUF" | awk '{print $1}')
echo "$SHA" > "$OUT/preflight/gguf.sha256"
[ "$SHA" = "$PIN_SHA" ] || fail "gguf_sha_mismatch" "$SHA"
{ sw_vers; sysctl hw.model hw.memsize vm.swapusage iogpu.wired_limit_mb; pmset -g batt; memory_pressure -Q; vm_stat; } > "$OUT/preflight/system.txt" 2>&1
ps -axm -o pid,rss,comm | head -16 > "$OUT/preflight/top-rss.txt"
# available = physical - (anonymous + wired + compressor-occupied); file cache counts as available
RECLAIM_GIB=$(vm_stat | awk -F: -v mem="$(sysctl -n hw.memsize)" '/Anonymous pages|Pages wired down|occupied by compressor/{gsub(/[ .]/,"",$2); s+=$2} END{printf "%.1f", (mem - s*16384)/1073741824}')
LOAD1=$(sysctl -n vm.loadavg | awk '{print $2}')
ps -axr -o pid,%cpu,rss,comm | head -12 > "$OUT/preflight/top-cpu.txt"
log "available (physical - anon - wired - compressor) = ${RECLAIM_GIB} GiB; load1=${LOAD1}; swap: $(sysctl -n vm.swapusage)"
if [ $DRY -eq 0 ]; then
  awk -v r="$RECLAIM_GIB" 'BEGIN{exit !(r >= 20)}' || fail "not_drained" "only ${RECLAIM_GIB} GiB available; need >= 20 GiB (model 15.6 GiB + KV/compute). See preflight/top-rss.txt"
  awk -v l="$LOAD1" 'BEGIN{exit !(l <= 12)}' || fail "not_drained_cpu" "1-min load ${LOAD1} > 12 (CI jobs still running?). See preflight/top-cpu.txt"
  awk -v l="$LOAD1" 'BEGIN{exit !(l > 4)}' && log "WARNING: load ${LOAD1} > 4; tok/s may be depressed"
fi
"$PY" - "$OUT/preflight.json" "$SHA" "$WS_MB" "$RECLAIM_GIB" <<'PYEOF' || fail "preflight_json"
import json, platform, subprocess, sys
def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout.strip()
out, sha, ws, rec = sys.argv[1:5]
json.dump({"model": sh("sysctl", "-n", "hw.model"), "chip": sh("sysctl", "-n", "machdep.cpu.brand_string"),
           "memsize_gib": int(sh("sysctl", "-n", "hw.memsize")) / 2**30, "macos": sh("sw_vers", "-productVersion"),
           "gguf_sha256": sha, "metal_recommended_working_set_mb": float(ws) if ws else None,
           "reclaimable_gib_at_start": float(rec), "swap_at_start": sh("sysctl", "-n", "vm.swapusage"),
           "llama_server_version": open(out.rsplit("/", 1)[0] + "/preflight/llama-server-version.txt").read().strip().splitlines()[-2:]},
          open(out, "w"), indent=2)
PYEOF
else
  echo '{"dry_run":true,"model_weights_loaded":false,"admission":"real preflight retained; model files and binaries not required for dry run"}' > "$OUT/preflight.json"
fi
phase_end ok

# background helpers: memory sampler, sleep prevention, hard-stop watchdog
"$HELPERS/mem_sampler.sh" "$OUT/mem-samples.csv" 2 "$LLAMA_DIR" & SAMPLER=$!
/usr/bin/caffeinate -i -w $MAIN_PID & CAFF=$!
( while sleep 5; do
    kill -0 $MAIN_PID 2>/dev/null || exit 0
    if [ "$(date +%s)" -ge "$STOP_EPOCH" ]; then
      echo "hard stop $(date)" > "$OUT/HARD_STOP"; kill_llama; kill -TERM $MAIN_PID; exit 0
    fi
  done ) & WATCHDOG=$!

if [ $DRY -eq 1 ]; then
  DRY_FAILED=0
  phase_begin dry-harness-mock
  run_capped 300 "$PY" "$HELPERS/parity_eval.py" --root "$REPO" --out "$OUT/eval-dryrun" --deadline "$STOP_EPOCH" --dry-run
  [ $? -eq 0 ] && phase_end ok || { phase_end failed; DRY_FAILED=1; }
  phase_begin dry-harness-relaxed-mock
  run_capped 300 "$PY" "$HELPERS/parity_eval.py" --root "$REPO" --out "$OUT/eval-relaxed-dryrun" --label relaxed --max-seconds 120 --deadline "$STOP_EPOCH" --dry-run
  [ $? -eq 0 ] && phase_end ok || { phase_end failed; DRY_FAILED=1; }
  phase_begin dry-probe-plan
  run_capped 60 "$PY" "$HELPERS/grammar_probe.py" --root "$REPO" --out "$OUT/grammar-probe-plan" --deadline "$STOP_EPOCH" --dry-run
  [ $? -eq 0 ] && phase_end ok || { phase_end failed; DRY_FAILED=1; }
  phase_begin dry-server-command
  "$PY" -c 'import json,sys; from pathlib import Path; from offline_agent.eval_worker import server_command; print(json.dumps(["/usr/bin/time","-l"]+server_command(Path(sys.argv[1]), Path(sys.argv[2]), binary=Path(sys.argv[3]))))' "$REPO" "$GGUF" "$LLAMA_DIR/llama-server" > "$OUT/server-command.json" && cat "$OUT/server-command.json"
  echo "$LLAMA_DIR/llama-bench -m $GGUF -ngl 99 -p 512 -n 128 -r 3 -o json" > "$OUT/llama-bench-command.txt"
  phase_end ok
  phase_begin dry-swap-guard
  "$PY" -c 'from offline_agent.memory import SwapGuard; print(SwapGuard().check())' && phase_end ok || phase_end "refused(would block load)"
  phase_begin dry-watchdog-and-cap
  run_capped 2 /bin/sleep 30; rc=$?; [ $rc -eq 124 ] && phase_end "ok(cap fired)" || { phase_end "failed(rc=$rc)"; DRY_FAILED=1; }
  sleep 4   # let the sampler record a few rows
  exit "$DRY_FAILED"
fi

# ---------------- phase 1: llama-bench (pp512 / tg128, -ngl 99) ----------------
if [ "$(remaining)" -gt 720 ]; then
  phase_begin llama-bench
  "$PY" -c 'from offline_agent.memory import SwapGuard; print(SwapGuard().check())' > "$OUT/llama-bench.admission" 2>&1
  if [ $? -ne 0 ]; then phase_end "refused:swap_admission"; else
    run_capped 600 /usr/bin/time -l "$LLAMA_DIR/llama-bench" -m "$GGUF" -ngl 99 -p 512 -n 128 -r 3 -o json \
      > "$OUT/llama-bench.json" 2> "$OUT/llama-bench.stderr"
    rc=$?; [ $rc -eq 0 ] && phase_end ok || phase_end "failed(rc=$rc)"
  fi
  kill_llama; sleep 10
else skip llama-bench deadline; fi

# ---------------- phase 2: the project's device spike (native /completion + GBNF, 3 repeats) ----------------
if [ $SKIP_SPIKE -eq 0 ] && [ "$(remaining)" -gt 600 ]; then
  phase_begin device-spike
  spike_cmd() { cd "$REPO" && exec "$PY" scripts/device_spike.py --allow-model-load --gguf "$GGUF" --server "$LLAMA_DIR/llama-server"; }
  run_capped 300 spike_cmd > "$OUT/device-spike.log" 2>&1; rc=$?
  latest=$(ls -td "$REPO"/artifacts/device-spike/*/ 2>/dev/null | head -1)
  [ -n "$latest" ] && cp -R "$latest" "$OUT/device-spike" && cp "$REPO/docs/SPIKE.md" "$OUT/device-spike/SPIKE.md"
  [ $rc -eq 0 ] && phase_end ok || phase_end "failed(rc=$rc)"
  kill_llama; sleep 10
else skip device-spike "flag_or_deadline"; fi

# ---------------- phase 3: llama-server (eval settings) under /usr/bin/time -l ----------------
SERVER_OK=0
if [ "$(remaining)" -gt 900 ]; then
  phase_begin server-start
  "$PY" -c 'import json,sys; from pathlib import Path; from offline_agent.eval_worker import server_command; sys.stdout.write("\0".join(server_command(Path(sys.argv[1]), Path(sys.argv[2]), binary=Path(sys.argv[3]))))' \
    "$REPO" "$GGUF" "$LLAMA_DIR/llama-server" > "$OUT/server-command.nul"
  CMD=(); while IFS= read -r -d '' a; do CMD+=("$a"); done < <(cat "$OUT/server-command.nul"; printf '\0')
  printf '%q ' "${CMD[@]}" > "$OUT/server-command.txt"; echo >> "$OUT/server-command.txt"
  if "$PY" -c 'from offline_agent.memory import SwapGuard; print(SwapGuard().check())' > "$OUT/server.admission" 2>&1; then
    ( cd "$OUT" && exec /usr/bin/time -l "${CMD[@]}" > "$OUT/server.log" 2> "$OUT/server.time" ) & SERVER_WRAP=$!
    i=0; while [ $i -lt 180 ]; do
      kill -0 $SERVER_WRAP 2>/dev/null || break
      [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 http://ADDRESS_REMOVED:8080/health)" = "200" ] && { SERVER_OK=1; break; }
      sleep 1; i=$((i+1))
    done
    [ $SERVER_OK -eq 1 ] && phase_end "ok(${i}s to healthy)" || { phase_end "failed(see server.log)"; stop_server; }
  else phase_end "refused:swap_admission"; fi
else skip server-start deadline; fi

# ---------------- phase 4: grammar-route probe ----------------
if [ $SERVER_OK -eq 1 ] && [ "$(remaining)" -gt 780 ]; then
  phase_begin grammar-probe
  run_capped 420 "$PY" "$HELPERS/grammar_probe.py" --root "$REPO" --out "$OUT/grammar-probe" --deadline "$STOP_EPOCH"
  rc=$?; [ $rc -eq 0 ] && phase_end ok || phase_end "failed(rc=$rc)"
else skip grammar-probe "server_or_deadline"; fi

# ---------------- phase 5: base-low, 15 tasks, harness Budget() (30 s/task) ----------------
if [ $SERVER_OK -eq 1 ] && [ -f "$OUT/grammar-probe/verdict.json" ] && [ "$(remaining)" -gt 400 ]; then
  phase_begin eval-parity
  run_capped 720 "$PY" "$HELPERS/parity_eval.py" --root "$REPO" --out "$OUT/eval-parity" --label parity \
    --route-file "$OUT/grammar-probe/verdict.json" --deadline "$STOP_EPOCH"
  rc=$?; [ $rc -eq 0 ] && phase_end ok || phase_end "failed(rc=$rc)"
else skip eval-parity "server_probe_or_deadline"; fi

# ---------------- phase 6: optional relaxed pass (120 s/task, labeled non-comparable) ----------------
if [ $SKIP_RELAXED -eq 0 ] && [ $SERVER_OK -eq 1 ] && [ -f "$OUT/eval-parity/evaluation-receipt.json" ] && [ "$(remaining)" -gt 600 ]; then
  phase_begin eval-relaxed
  run_capped 2000 "$PY" "$HELPERS/parity_eval.py" --root "$REPO" --out "$OUT/eval-relaxed" --label relaxed \
    --max-seconds $RELAXED_SECONDS --route-file "$OUT/grammar-probe/verdict.json" --deadline "$STOP_EPOCH"
  rc=$?; [ $rc -eq 0 ] && phase_end ok || phase_end "failed(rc=$rc)"
else skip eval-relaxed "flag_parity_or_deadline"; fi

phase_begin server-stop; stop_server; kill_llama; phase_end ok
exit 0
