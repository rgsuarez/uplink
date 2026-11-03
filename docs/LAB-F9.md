# Lab F9 rerun package

Lab names below are placeholders for operator-configured SSH aliases. Set
`LAB_BENCH_ROOT` to the private remote benchmark directory and `LAB_NODE` to the
approved host's short name before an authorized live run. Runtime paths are
relative to that directory; copied evidence lives under `artifacts/`. Neither
variable grants permission to run. The frozen F10 inputs retain their exact bytes
and hashes; the three pinned bench files are resolved under `bench/lab/`.

F9 changes prompt reuse and deadline reporting. It does not change frozen tasks,
oracles, the 15-task subset, the primary scoring rule, or either task budget.
No F9 model run has been performed. the operator owns transfer and launch after the
existing run ends and the machine is drained.

Build from the F9 checkout:

```sh
.venv/bin/python \
  scripts/f9_package.py \
  --out artifacts/f9/lab-f9
```

The destination must be new. The bundle contains an updated harness under
`offline-agent`, bench helpers under `bench`, the runner, and a SHA-256 manifest.
It excludes models, environments, caches and prior run artifacts. Verification
fails on missing or changed manifested files before any model command.

Transfer the directory to `${LAB_BENCH_ROOT}/f9` on lab. Preserve
`${LAB_BENCH_ROOT}/offline-agent` and its existing environment. The lockfile
is unchanged; the runner sets PYTHONPATH to the staged F9 source, so the old
environment supplies dependencies without selecting the old harness.

On bench, offline rehearsal:

```sh
BENCH_ROOT=${LAB_BENCH_ROOT} \
BENCH_REPO=${LAB_BENCH_ROOT}/f9/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash ${LAB_BENCH_ROOT}/f9/run-bench.sh --dry-run
```

Dry mode requires no GGUF or llama binaries. It runs real sandboxed mock repairs
and impossible-task checks at both budget labels, constructs all 20 grammar
probe requests, prints server settings, checks swap admission, exercises a
two-second child timeout, samples memory and writes a summary. It sends no
inference requests and never kills existing llama processes.

After the prior run ends, drain confirmation and an approved time window,
the operator can use the same invocation with `--confirm-drain`. The retained defaults
are WINDOW_START=21:00 CT, HARD_STOP=22:20 CT and MAX_MINUTES=70. A previously
approved override must be explicitly supplied; F9 does not infer a new window.
The runner refuses real execution on machines other than lab.

Real phases remain preflight, llama-bench pp512/tg128, device-spike, server-start,
grammar-probe, eval-parity, optional eval-relaxed, server-stop and summary.
GGUF/build/Metal pins, the free-port and existing-process checks, >=20 GiB memory
admission, load admission, swap checks and the hard-stop watchdog are retained.
Cleanup targets processes owned by the run. Optional --skip-spike and
--skip-relaxed remain available.

Parity is 30 s/task; relaxed is 120 s/task and non-comparable. Both retain
16 steps, 65,536 aggregate tokens, 2,048 output tokens/action, 3 s/tests, seed 0,
temperature 0, low reasoning, 8,192 context and a single serial slot.
F8's give-up conversion is not applied. No retries or history compaction occur.

Review the evaluation receipts and per-call journals for:

- `budget_exhausted:wall_budget` for a typed timeout at the task deadline;
- genuine HTTP/response errors retained as provider errors;
- `tests_green_at_expiry`, bound to the latest successful test on the final source;
- `verified_green_at_expiry`, a separate final-oracle diagnostic in aggregate metrics;
- unchanged solved credit: accepted `fixed` terminal plus a green independent oracle;
- native `timings.prompt_n`, `prompt_ms`, `cache_n`, `predicted_n`, `predicted_ms`;
- source, bench and registration hashes so a stale checkout is detectable.

The JSONL call journal retains failed-call duration and all completed native
response timings. Cancelled requests may lack native timing data. b10353's
`tokens_cached` includes generated slot contents; use `timings.cache_n` for
prompt reuse. Initial source prefill, decoding, admission and growing-context
cost remain. A speedup requires a measured bench rerun.
