# F10: frozen 50-task on-device package

Prepared and rehearsed offline. No F10 inference was run. The full-50 on-device
README row is PENDING until the operator's measured lab results are imported.

| Field | Value |
| --- | --- |
| Private repository | `<repository-owner>/offline-agent` (privacy verified before closeout) |
| Branch / only permitted push target | `feat/f10-frozen50-device` |
| Starting F9 commit | `62d5671e73e13e25f4ec66ef575fe6b5bbd2d7b0` |
| Package source commit | `1918dda2357f23176c32c175d93c35a57f5b7a00` |
| Preregistration commit | `52c07c9b8d44559468b9d62924b66152008ab643` |
| Preregistration SHA-256 | `ff41ad3c71823a67c4abc4834c06c4adf08af5cc4e5ab3bc5e642b8f4b24ce53` |
| Frozen task-set JSON SHA-256 | `f8185e6a7ea7d41b83b1d143fc06d3d778fc01af2ef933df367162c1301df201` |
| Package directory | `artifacts/f10/lab-f10` |
| Archive | `artifacts/f10/lab-f10.tar.gz` |
| Archive SHA-256 | `b880a4d7aa411fbe12746ad34ea3b9cbf3292a102e4a525d9d39b59661eca88f` |
| Archive bytes / manifested payload files | 499,958 / 437 |
| Manifest SHA-256 | `d5ffdc301f252e5f3cb79b13cdb2bae4353ee26f02c971c3caac57e48799e7fb` |
| Lab target / reused Python | `artifacts/lab-runtime/f10` / `.venv/bin/python` |
| Author and committer | `Richard Suarez <rgsuarez@gmail.com>`; existing history unchanged |

## What changed

1. Committed `configs/f10-ondevice.json`
   and its SHA-256 anchor before packaging. They register both arms, budgets, policy,
   exact task order and task-set hash, scoring and 24 protected F9/F8 inputs.
2. Added the full frozen-50 evaluator. PRIMARY is 120 s/task; default-on SECONDARY
   is 30 s/task and non-primary. Both call the unchanged F9 execution harness with
   append-only history and the exact registered F8 `giveup-v2-budget-terminal` policy.
   Raw traces stay unchanged; separate policy receipts bind the original result,
   trace hash, independent final-source verifier output and v2 decision.
3. Added per-arm reporting: verified repairs / 40, correct give-ups / 10, false fixed
   claims, false give-ups, malformed/invalid actions, tokens per solved task, mean
   seconds/call, native cache reuse and tok/s, sampled arm RSS and system memory.
   Run-wide server max RSS remains separately labeled. Missing timings stay null.
4. Extended the JSON-driven README generator with a PENDING on-device M4 Max 36 GB
   row and an offline `--device-results` importer. Import rechecks raw traces,
   policy receipts, counts, frozen order, actual host and complete primary status.
   MOCK, partial, secondary-only and drifted results cannot fill that hero row.
5. Added the bounded lab runner and SHA-256 package builder. The runner retains
   the original model/build/Metal, drain, port, swap and owned-process safeguards.
   It runs primary first and accepts `--skip-secondary` or `--skip-relaxed` to omit
   only the secondary. The bundle excludes weights, environments, caches, prior
   artifact directories and unrelated untracked files. Manifest checks reject
   changed, missing, extra or symlinked files before model activity.
6. Wrote the complete handoff in
   `docs/LAB-F10.md`.

No agent prompts, policy thresholds, protected harness sources, corpus bytes,
oracles, seed, temperature, context, slot count or retry behavior changed. Both arms
retain 16 steps, 65,536 aggregate tokens, 2,048 output tokens/action, 3 s/tests,
seed 0, temperature 0, low reasoning, 8,192 context, one slot, no compaction and
one attempt per task. Green at expiry remains diagnostic, never repair credit.

## Verification and dry-run evidence

- Full suite: **500 passed**, including all 463 prior tests and 37 new tests, in
  165.85 seconds. Log:
  `artifacts/f10/tests-full.log`.
- Ruff check and format: passed (142 Python files formatted).
- Strict mypy: passed (102 source files).
- Shell syntax and generated README consistency: passed.
- Package verification: 437 files, passed before and after the offline rehearsal.
- Value-blind pre-push source/history, secret, restricted-name and license scan
  receipt: `artifacts/f10/pre-push-scan.json`.
  This gate must pass before the authorized private branch push.

Packaged rehearsal command, executed on the source Mac:

```sh
cd ../offline-agent-f9
BENCH_ROOT=artifacts/f10/rehearsal \
BENCH_REPO=artifacts/f10/lab-f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash artifacts/f10/lab-f10/run-bench.sh --dry-run
```

Evidence directory:
`artifacts/f10/rehearsal/results/f10/dryrun-20261027-070222-81321`.
Top-level log:
`artifacts/f10/package-dry-run.log`.

Exit status was 0. Both arms checked the full frozen task set and ran two sandboxed
MOCK trajectories: an independently verified repair and a step-exhausted trajectory
converted by the registered F8 policy. Neither MOCK arm is complete held-out or
model-performance evidence. The unchanged grammar probe built 20 request bodies
and sent none. The two-second cap fired, memory sampling and summarization passed.
The rehearsal area contained no GGUF or GGUF runtime binaries. No model was loaded,
started, stopped or queried by this preparation or rehearsal.

## Runtime estimate and window

F9 lab run `20261027-054149-5716` recorded 47.203 s/task at the primary budget and
29.608 s/task at parity across 15 tasks. Extrapolating those means to 50 tasks gives
39.34 + 24.67 = **64.01 minutes of task execution**, before setup and final verification.
This is an estimate, not a measured F10 runtime. Exact source paths and hashes:
`docs/f10-runtime.json`.

The conservative full-run cap is **165 minutes**: 100 primary + 25 secondary +
5 final verification + 30 setup + 5 cleanup. Primary only requires **140 minutes**.
Per-phase caps are 6,300 and 1,800 seconds. There are no retries or automatic
resubmissions. Partial arms remain incomplete and cannot become hero evidence.

Default `WINDOW_START=21:00`, `HARD_STOP=22:20` remains unchanged and cannot admit
this run. the operator must explicitly supply the approved longer same-day window.
The example launch commands below assume 21:00 CT and a separately approved
extension; late launches are refused if the full required allowance no longer fits.

## Exact transfer and lab commands (not executed here)

From the source Mac:

```sh
cd ../offline-agent-f9
shasum -a 256 artifacts/f10/lab-f10.tar.gz
ssh lab 'mkdir artifacts/lab-runtime/f10'
scp artifacts/f10/lab-f10.tar.gz lab:artifacts/lab-runtime/lab-f10.tar.gz
```

On lab, compare the archive hash above before extraction, then rehearse:

```sh
cd artifacts/lab-runtime/f10
shasum -a 256 artifacts/lab-runtime/lab-f10.tar.gz
tar -xzf artifacts/lab-runtime/lab-f10.tar.gz
BENCH_ROOT=artifacts/local-source/lab-bench \
BENCH_REPO=artifacts/lab-runtime/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash artifacts/lab-runtime/f10/run-bench.sh --dry-run
```

Keep the archive beside the package, not inside it, so exact file-set verification
sees no extra file. Reuse the environment; install and download nothing.

Both arms, after lab is drained and the extended window is approved:

```sh
WINDOW_START=21:00 HARD_STOP=23:55 MAX_MINUTES=165 \
BENCH_ROOT=artifacts/local-source/lab-bench \
BENCH_REPO=artifacts/lab-runtime/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash artifacts/lab-runtime/f10/run-bench.sh --confirm-drain
```

Primary only:

```sh
WINDOW_START=21:00 HARD_STOP=23:25 MAX_MINUTES=140 \
BENCH_ROOT=artifacts/local-source/lab-bench \
BENCH_REPO=artifacts/lab-runtime/f10/offline-agent \
BENCH_PYTHON=.venv/bin/python \
bash artifacts/lab-runtime/f10/run-bench.sh --confirm-drain --skip-secondary
```

Results will be under `artifacts/lab-results/f10/<stamp>/`, including
`f10-results.json`, `RESULTS.md`, both arm receipts, raw traces, policy receipts,
call journals, memory samples and preflight evidence. The handoff guide includes
the offline copy-back and `scripts/f8_results.py --device-results` import commands.

## Local commits and boundaries

Implementation steps were committed individually: `52c07c9` preregistration,
`929fd90` policy/arm wiring, `35e4567` reporting/PENDING hero,
`cb9bd1a` bounded runner, `1918dda` packaging and handoff. This report is a separate
closeout commit. The immutable bundle was created from the package-source commit
above before this report, avoiding a self-referential archive hash.

No model inference, lab SSH/SCP, remote GPU work, paid API calls, public repository
changes, external posts, merge, deploy, database write, resource provisioning,
credential creation, history rewrite, mailmap change or AI co-author trailer occurred.
The sole authorized remote mutation is the push of `feat/f10-frozen50-device` to
the verified private origin after scanning. No other worktree was changed.
