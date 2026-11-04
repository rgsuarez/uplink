# Lab F10: frozen 50-task on-device package

Lab names below are placeholders for operator-configured SSH aliases. Set
`LAB_BENCH_ROOT` to the private remote benchmark directory and `LAB_NODE` to the
approved host's short name before an authorized live run. Runtime paths are
relative to that directory; copied evidence lives under `artifacts/`. Neither
variable grants permission to run. The frozen F10 inputs retain their exact bytes
and hashes; the three pinned bench files are resolved under `bench/lab/`.

Prepare only. the operator owns transfer, drain confirmation and launch on lab. No F10
inference has run. The README on-device row remains PENDING until a complete primary
result is imported. This is the exposed planted-mutation benchmark, not SWE-bench.

The PRIMARY arm runs first: all 50 frozen v2 tasks in original order, 120 s/task,
40 repair and 10 impossible denominators. The default-on SECONDARY arm uses the same
tasks and policy at 30 s/task and is non-primary. `--skip-secondary`, also accepted
as `--skip-relaxed`, omits only that secondary arm. `--skip-spike` remains available.

Both reuse F8 `giveup-v2-budget-terminal` exactly: no early stopping, 100% original
budget, at least one completed independent failing final test, matching final source.
Provider errors, missing evidence and green-at-expiry do not become give-ups or repairs.
Raw F9 traces remain untouched; separate `*.policy-v2.json` receipts retain verifier
output and bind the original trace. Final verification is outside the model budget.

All other F9 knobs are unchanged: base Q4_K_M, base, unmodified open weights, native_gbnf, low reasoning,
16 steps, 65,536 aggregate tokens, 2,048 output tokens/action, 3 s/tests, seed 0,
temperature 0, 8,192 context, one slot, append-only history, prompt caching, no retries
or compaction. The runner checks the committed F10 registration SHA-256, original
task-set bytes and freeze, F8 policy/calibration and protected F9 sources before any
model command. The package manifest also refuses changed, missing or extra files.

## Build and transfer (transfer commands are for the operator, not executed here)

From the F10 checkout on the source Mac:

```sh
cd ../offline-agent-f9
PYTHONPATH=src .venv/bin/python scripts/f10_package.py \
  --out artifacts/f10/lab-f10
PYTHONPATH=src .venv/bin/python scripts/f10_package.py \
  --verify artifacts/f10/lab-f10
COPYFILE_DISABLE=1 tar -czf artifacts/f10/lab-f10.tar.gz -C artifacts/f10/lab-f10 .
shasum -a 256 artifacts/f10/lab-f10.tar.gz
ssh bench 'mkdir ${LAB_BENCH_ROOT}/f10'
scp artifacts/f10/lab-f10.tar.gz bench:${LAB_BENCH_ROOT}/lab-f10.tar.gz
```

On bench, verify the archive hash against REPORT-F10.md before extracting into the
new F10 directory. Preserve the existing F9 package and environment:

```sh
cd ${LAB_BENCH_ROOT}/f10
shasum -a 256 ${LAB_BENCH_ROOT}/lab-f10.tar.gz
tar -xzf ${LAB_BENCH_ROOT}/lab-f10.tar.gz
BENCH_ROOT=${LAB_BENCH_ROOT} \
BENCH_REPO=${LAB_BENCH_ROOT}/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash ${LAB_BENCH_ROOT}/f10/run-bench.sh --dry-run
```

The archive stays beside the F10 directory so exact package verification sees no
extra file. Dry mode needs neither GGUF nor GGUF runtime binaries. It validates all 50 task hashes,
runs two sandboxed MOCK trajectories per selected arm (verified repair and exhausted
run converted by F8), constructs the unchanged grammar-probe requests, prints the
server settings, exercises the cap and samples memory. It opens no inference socket
and never loads or kills a model.

## Runtime and launch

F9 source: bench run `20261027-054149-5716`. The 15-task primary-budget mean was
47.203 s/task (max 120.009 s); the parity mean was 29.608 s/task (max 30.183 s).
Linear extrapolation gives 39.34 + 24.67 = 64.01 minutes for task execution only.
This is an estimate from a subset, not a measured F10 runtime or performance claim.
Sources and hashes are in `docs/f10-runtime.json`.

Admission uses the conservative cap: 50 x 120 s = 100 minutes, plus 50 x 30 s = 25,
5 minutes final verification, 30 setup, 5 cleanup. `MAX_MINUTES=165` is the default.
Primary only requires 140 minutes. Per-phase caps are 6,300 s primary and 1,800 s
secondary. No task or arm is retried. Partial runs retain explicit incomplete status
and fixed denominators and cannot enter the hero table.

The default CT window stays **21:00-22:20**. It cannot admit F10 and is deliberately
refused. the operator must explicitly supply an approved longer same-day window. These
commands assume launch at 21:00 CT with that extended window approved; otherwise
use the exact separately approved start/stop values. A late launch is refused if
less than 165 minutes remain (140 for primary only).

Both arms:

```sh
WINDOW_START=21:00 HARD_STOP=23:55 MAX_MINUTES=165 \
BENCH_ROOT=${LAB_BENCH_ROOT} \
BENCH_REPO=${LAB_BENCH_ROOT}/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash ${LAB_BENCH_ROOT}/f10/run-bench.sh --confirm-drain
```

Primary only:

```sh
WINDOW_START=21:00 HARD_STOP=23:25 MAX_MINUTES=140 \
BENCH_ROOT=${LAB_BENCH_ROOT} \
BENCH_REPO=${LAB_BENCH_ROOT}/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash ${LAB_BENCH_ROOT}/f10/run-bench.sh --confirm-drain --skip-secondary
```

Only bench may execute live. F9 GGUF/build/Metal pins, free port, existing-process
refusal, at least 20 GiB reclaimable memory, CPU load admission, swap guard and owned
process cleanup remain. The lockfile is unchanged. The runner uses staged F10 source
via PYTHONPATH and the existing environment only; no dependency or model download.
Do not run this package on the source Mac except with `--dry-run`.

## Results and README import (offline, after the operator's run)

Run evidence lives at `${LAB_BENCH_ROOT}/results/f10/<stamp>/`. The
`current` and `latest` links are in that directory's parent. `RESULTS.md` and
`f10-results.json` include per-arm repairs / 40, correct give-ups / 10, false fixed
claims, false give-ups, invalid rate, tokens / solved, mean s/call, cache reuse,
native tok/s, sampled server RSS and sampled system memory. `/usr/bin/time -l`
records the server's run-wide max RSS separately. Two-second memory samples are
filtered to each arm's timestamps; RSS is not total unified-memory usage.

Copy the whole selected run back, retaining raw traces, policy receipts, call
journals, memory samples and preflight alongside the results JSON. Example for the operator
on the source Mac, after selecting the completed run through the `latest` link:

```sh
cd ../offline-agent-f9
mkdir -p artifacts/f10/lab-run
scp -r bench:${LAB_BENCH_ROOT}/results/f10/latest/. artifacts/f10/lab-run/
PYTHONPATH=src .venv/bin/python scripts/f8_results.py \
  --device-results artifacts/f10/lab-run/f10-results.json
PYTHONPATH=src .venv/bin/python scripts/f8_results.py --check
```

Import rechecks raw trace/policy hashes, policy decisions, fixed denominators,
frozen task order, actual M4 Max 36 GB host and complete primary status. It refuses
MOCK, partial, drifted and secondary-only evidence. No inference is performed by
the generator. Native speed/cache summaries exclude cancelled calls without native
timings; provider time includes failed calls. Historical L4 and Spark rows retain
their original policy labels and budgets. Spark 30 s vs local 120 s is disclosed.

## Recovering a completed run's summary

A report-generation failure does not require another inference run. Copy the completed result directory locally, verify the copied source files against their SHA-256 hashes, then run only:

```sh
uv run --frozen --offline python bench/lab/f10_summarize.py --root "$PWD" --out /absolute/path/to/copied/results
```

The F9 agent can emit `budget_exhausted` immediately before `finish` when a provider call reaches the wall deadline. The trace viewer treats that event as a diagnostic, like `provider_error`: it contributes no completed step, token count or green-test evidence. The final record and registered F8 verifier remain authoritative. Unknown events, incomplete traces and inconsistent counters still refuse. Preserve raw traces and policy receipts; never remove events or change scoring to make a summary render.
