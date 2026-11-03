#!/bin/bash
# F10 full frozen-50 on lab: primary 120 s/task, optional non-primary 30 s/task.
#   bash run-bench.sh --dry-run       # no GGUF, llama binary, socket or inference
#   bash run-bench.sh --confirm-drain # real lab run, explicit approved long window required
# --skip-secondary (alias --skip-relaxed) omits only the 30 s arm; --skip-spike retained.
# Defaults remain 21:00-22:20 CT and cannot fit F10. Set an explicit approved window.
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
MAX_MINUTES="${MAX_MINUTES:-165}"  # 125 min task budgets + 5 verifier + 30 setup + 5 cleanup
export TMPDIR="$REPO/artifacts/tmp" PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 NO_PROXY=ADDRESS_REMOVED,localhost

DRY=0; CONFIRM=0; SKIP_SECONDARY=0; SKIP_SPIKE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --confirm-drain) CONFIRM=1 ;;
    --skip-secondary|--skip-relaxed) SKIP_SECONDARY=1 ;;
    --skip-spike) SKIP_SPIKE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ $DRY -eq 0 ] && [ $CONFIRM -eq 0 ]; then
  echo "Refusing: a real run needs --confirm-drain (the fleet coordinator's drain confirmation). Use --dry-run otherwise." >&2; exit 2
fi
[ $DRY -eq 1 ] && [ $CONFIRM -eq 1 ] && { echo "--dry-run and --confirm-drain are exclusive" >&2; exit 2; }

case "$MAX_MINUTES" in ''|*[!0-9]*) echo "MAX_MINUTES must be a positive integer" >&2; exit 2 ;; esac
[ "$MAX_MINUTES" -gt 0 ] && [ "$MAX_MINUTES" -le 720 ] || { echo "invalid MAX_MINUTES" >&2; exit 2; }
REQUIRED_MINUTES=165; [ $SKIP_SECONDARY -eq 1 ] && REQUIRED_MINUTES=140
STAMP=$(date +%Y%m%d-%H%M%S)-$$
if [ $DRY -eq 1 ]; then OUT="$BENCH/results/f10/dryrun-$STAMP"; else OUT="$BENCH/results/f10/$STAMP"; fi
mkdir -p "$OUT/preflight" "$TMPDIR" || exit 1
[ $DRY -eq 1 ] && touch "$OUT/DRY_RUN"
[ $DRY -eq 0 ] && ln -sfn "$OUT" "$BENCH/results/f10/current"   # follow with: tail -f ${BENCH_ROOT:-lab-bench}/results/current/run.log
exec > >(tee -a "$OUT/run.log") 2>&1
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"; }

epoch_at() { date -j -f "%Y-%m-%d %H:%M:%S" "$(date +%Y-%m-%d) $1:00" +%s; }
NOW=$(date +%s)
START_EPOCH=$(epoch_at "$WINDOW_START") || exit 2
HARD_EPOCH=$(epoch_at "$HARD_STOP") || exit 2
[ "$HARD_EPOCH" -gt "$START_EPOCH" ] || { echo "same-day HARD_STOP must follow WINDOW_START" >&2; exit 2; }
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
  "$PY" "$HELPERS/f10_summarize.py" --root "$REPO" --out "$OUT" || { log "summarize failed"; [ $rc -eq 0 ] && rc=1; }
  ln -sfn "$OUT" "$BENCH/results/f10/latest$([ $DRY -eq 1 ] && echo -dryrun)"
  log "done rc=$rc; results in $OUT (RESULTS.md, f10-results.json)"
  exit $rc
}
trap cleanup EXIT
trap 'log "signal received; stopping"; exit 130' INT TERM

log "F10 lab start (dry_run=$DRY; primary=120 s; secondary=$((1-SKIP_SECONDARY)); required=${REQUIRED_MINUTES} min). Stop at $(date -r $STOP_EPOCH '+%H:%M:%S %Z'); out=$OUT"

# ---------------- phase 0: preflight (never loads the model) ----------------
phase_begin preflight
fail() { log "PREFLIGHT REFUSED: $*"; phase_end "refused:$1"; exit 3; }
if [ $DRY -eq 0 ]; then
  [ -n "${LAB_NODE:-}" ] && [ "$(hostname -s)" = "$LAB_NODE" ] || fail "lab_only" "model loading is restricted to the configured lab host"
  [ "$NOW" -ge "$START_EPOCH" ] || fail "before_window" "now is before $WINDOW_START CT"
  [ "$MAX_MINUTES" -ge "$REQUIRED_MINUTES" ] || fail "insufficient_cap" "need ${REQUIRED_MINUTES} minutes"
  [ "$((HARD_EPOCH - NOW))" -ge "$((REQUIRED_MINUTES * 60))" ] || fail "explicit_longer_window_required" "need ${REQUIRED_MINUTES} minutes before $HARD_STOP CT; default window is deliberately unchanged"
fi
for f in "$PY" "$REPO/templates/glimmer_native.jinja" "$HELPERS/frozen_eval.py" "$REPO/configs/f10-ondevice.json"; do
  [ -e "$f" ] || fail "missing_file" "$f"
done
"$PY" -c 'import sys; from pathlib import Path; from offline_agent.f10 import registration; registration(Path(sys.argv[1])); print("F10 registration, F8 policy and frozen-50 hashes verified")' "$REPO" || fail "registration_hash_mismatch"
if [ -f "$SCRIPT_DIR/package-manifest.json" ]; then
  "$PY" "$REPO/scripts/f10_package.py" --verify "$SCRIPT_DIR" || fail "package_manifest_mismatch"
fi
if [ $DRY -eq 0 ]; then
[ -f "$SCRIPT_DIR/package-manifest.json" ] || fail "verified_f10_package_required"
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
  phase_begin dry-harness-primary-mock
  run_capped 300 "$PY" "$HELPERS/frozen_eval.py" --root "$REPO" --out "$OUT/eval-primary" --arm primary --deadline "$STOP_EPOCH" --dry-run
  [ $? -eq 0 ] && phase_end ok || { phase_end failed; DRY_FAILED=1; }
  if [ $SKIP_SECONDARY -eq 0 ]; then
    phase_begin dry-harness-secondary-mock
    run_capped 300 "$PY" "$HELPERS/frozen_eval.py" --root "$REPO" --out "$OUT/eval-secondary" --arm secondary --deadline "$STOP_EPOCH" --dry-run
    [ $? -eq 0 ] && phase_end ok || { phase_end failed; DRY_FAILED=1; }
  else skip dry-harness-secondary-mock flag; fi
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

# ---------------- phase 5: PRIMARY, full frozen 50, 120 s/task, registered F8 v2 ----------------
RUN_FAILED=0
SECONDARY_RESERVE=0; [ $SKIP_SECONDARY -eq 0 ] && SECONDARY_RESERVE=1800
if [ $SERVER_OK -eq 1 ] && [ -f "$OUT/grammar-probe/verdict.json" ] && [ "$(remaining)" -gt "$((6300 + SECONDARY_RESERVE + 60))" ]; then
  phase_begin eval-primary
  run_capped 6300 "$PY" "$HELPERS/frozen_eval.py" --root "$REPO" --out "$OUT/eval-primary" --arm primary \
    --allow-lab --route-file "$OUT/grammar-probe/verdict.json" --deadline "$STOP_EPOCH"
  rc=$?; [ $rc -eq 0 ] && phase_end ok || { phase_end "failed(rc=$rc)"; RUN_FAILED=1; }
else skip eval-primary "server_probe_or_full_arm_deadline"; RUN_FAILED=1; fi

# ---------------- phase 6: optional SECONDARY, 30 s/task, non-primary, same policy ----------------
if [ $SKIP_SECONDARY -eq 0 ] && [ $RUN_FAILED -eq 0 ] && [ "$(remaining)" -gt 1860 ]; then
  phase_begin eval-secondary
  run_capped 1800 "$PY" "$HELPERS/frozen_eval.py" --root "$REPO" --out "$OUT/eval-secondary" --arm secondary \
    --allow-lab --route-file "$OUT/grammar-probe/verdict.json" --deadline "$STOP_EPOCH"
  rc=$?; [ $rc -eq 0 ] && phase_end ok || { phase_end "failed(rc=$rc)"; RUN_FAILED=1; }
else skip eval-secondary "flag_primary_or_full_arm_deadline"
  [ $SKIP_SECONDARY -eq 0 ] && RUN_FAILED=1
fi

phase_begin server-stop; stop_server; kill_llama; phase_end ok
exit "$RUN_FAILED"
