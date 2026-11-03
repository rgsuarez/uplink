# F9: on-device latency and honest budget expiry

**F9 is implemented and verified without inference: 463 tests pass, and the bench
rerun package passes its offline dry-run. Repeated prompt work and deadline
classification are fixed in code. No improved model speed or repair rate has
been measured; the operator's bench rerun is still required.**

North Star Check: preserve frozen tasks, oracles, budgets and primary scoring;
reuse stable native prompt prefixes; report expiry separately from server failure;
prepare a bounded bench rerun without loading weights on this Mac. Tracking: N/A,
private hackathon work. No external posts or cloud evaluation are part of F9.

Branch: `feat/f9-ondevice-latency`, based on `feat/f8-giveup-policy` at
`878aa1efaffe889cc94db8c76684e014b2accfd6`. Runtime/package source commit:
`ea896fbe2bdf279ddb46921fd64e46832f7d65ef`. Publication target is the existing
private `<repository-owner>/offline-agent` origin, this feature branch only. The author
identity follows the explicit F9 brief: Richard Suarez <rgsuarez@gmail.com>.

## Measured snapshot facts

The snapshot indexed in `docs/f9/source-receipts.json` was copied while the run
was active; its raw hardware snapshot remains private. It is not a completed relaxed evaluation.
The retained llama-server build is b10353, `f8def7fe`, on bench's 36 GB M4 Max.

| Measure | Observed value |
| --- | ---: |
| llama-bench pp512 | 199.845 tok/s |
| llama-bench tg128 | 19.011 tok/s |
| Server launches in copied log | 139 |
| Completed prefill/decode timing pairs | 123 |
| Prefill in those pairs | 523.668 s, 66,786 tokens |
| Decode in those pairs | 275.031 s, 4,295 tokens |
| Prefill share of paired native time | 65.6% |
| Double-BOS warnings | 127 |
| Logged cancellations associated with launches | 19 |
| Logged HTTP 500 exceptions | 4, matching chat-GBNF probe failures |
| Parity results at snapshot | 0/12 solvable fixed; 15/15 provider-error terminals |
| Parity wall time | 31.041 to 31.269 s/task against 30 s budgets |
| Parity final-green sources without fixed terminals | 6, uncredited |
| Relaxed completed rows at snapshot | 4: two fixed, two provider errors |
| Relaxed provider-error wall time | 121.067 s against 120 s budgets |

The timing totals include the grammar probe and partial relaxed pass. Missing
timings for cancelled or active work are missing data, not zero-cost generations.
The snapshot's failing HTTP wrapper did not retain typed timeout evidence, so
individual legacy errors cannot all be conclusively classified after the fact.
The deadline-shaped durations and server cancellations support the expiry
diagnosis. Original results have not been relabeled.

Every launch, including missing timings, is tabulated in
`docs/f9/server-calls.csv`.
Aggregate facts and all 81 snapshot file hashes are retained in
`docs/f9/snapshot-analysis.json`
and `docs/f9/source-receipts.json`.

## Root causes

The first relaxed stringcase task shows the history-window failure directly:

| Turn / server task | Action | Evaluated prefill tokens | Prefill s | Decode s | Slot-selection f_keep |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 / 3119 | list_files | 517 | 4.668 | 0.976 | 0.713 |
| 2 / 3141 | read_file | 40 | 0.629 | 1.090 | 1.000 |
| 3 / 3165 | search | 1,435 | 10.551 | 1.735 | 1.000 |
| 4 / 3196 | run_tests | 59 | 0.691 | 0.660 | 1.000 |
| 5 / 3212 | apply_patch | 433 | 3.199 | 18.350 | 1.000 |
| 6 / 3425 | run_tests | 2,681 | 19.210 | 0.692 | 0.195 |
| 7 / 3443 | declare_done | 1,447 | 10.413 | 3.255 | 0.195 |

These seven calls account for 49.361 s prefill and 26.759 s decode. The task's
recorded wall time is 83.783 s. `f_keep` is a slot-selection diagnostic, not a
direct measurement of reused prompt tokens. Earlier turns demonstrate real
suffix-only prefill; it is incorrect to say every step reprocessed the full prompt.

The existing harness drops the oldest tool exchanges starting at turn six
(`history[:2] + history[2:][-8:]`). This changes the prefix. The F9 regression
fixture confirms that legacy mode loses the prefix while the new mode preserves
it through eight turns. The immutable template is unchanged.

The captured bench independently rendered and sent the template's BOS on the
raw completion route. b10353 adds BOS to string prompts for this model. This
explains the warnings, but a repeated BOS alone does not explain the changing
prefix or prove a large latency penalty. The captured wrapper bypassed the
repository's existing server-owned-BOS helper. The device-spike wire path had
the same duplication and is corrected too.

The supplied evidence does **not** contain the old checkout SHA or its harness
source. Its exact harness version cannot be established without additional
provenance; an old checkout must not be inferred merely from the duplicated BOS
in the bench wrapper. F9 receipts add source/bench/registration hashes, and the
rerun bundle verifies exact manifested bytes before model commands.

Swap admission samples for about one second before each inference call. The
old provider then passed its original remaining timeout to HTTP, permitting
roughly that extra second. The bench also rounded subsecond HTTP allowances up
to one second. Generic transport failures erased the timeout type, and failed
calls were omitted from accumulated provider duration.

The green-at-expiry hunch needs correction: the captured bench already ran
independent final verification after all outcomes, including provider errors.
Six green sources were therefore visible. They correctly failed the primary
metric because no accepted `fixed` terminal existed. F9 preserves this rule.

## Implemented behavior

- Registered `f9-ondevice-v1` uses opt-in `append-only-v1` history. Legacy rolling
  selection remains available for prior experiment configurations. No compaction,
  early give-up threshold, task retry or F8 terminal conversion was added.
- Native evaluation and the grammar probe use server-owned BOS. Native requests
  retain `cache_prompt=true`, add explicit slot 0, eager GBNF and the native EOT
  stop. The device spike deliberately retains cold-cache repeats.
- Local providers subtract admission and prompt-building duration before HTTP.
  If admission consumes the allowance, no HTTP request is sent. The swap guard
  is retained; it can itself consume the final fraction of a task's wall budget.
- A typed timeout at the task deadline becomes `budget_exhausted:wall_budget`.
  An early timeout remains a provider error. HTTP 500s and malformed/other
  transport failures remain provider errors even near the deadline.
- `tests_green_at_expiry` records current successful test evidence, invalidated
  by later edits. The bench separately reports independent final-green expiry
  counts. Neither receives solved credit. Late fixed responses are refused.
- Trace and call journals retain failed-call duration, error cause codes, and
  completed native prefill/decode/cache counters. Cancelled token usage is not
  invented. Reuse uses `timings.cache_n`; b10353's `tokens_cached` includes
  generated slot contents and is unsuitable as the reuse numerator.

The wire behavior and counter semantics were checked against the pinned
[b10353 server documentation](https://github.com/ggml-org/llama.cpp/blob/f8def7fe/tools/server/README.md)
and [b10353 server source](https://github.com/ggml-org/llama.cpp/blob/f8def7fe/tools/server/server-context.cpp).

## Preserved evidence and verification

All 397 original tracked files were fingerprinted before implementation. Exactly
six originals changed: the agent, evaluation entry point, native provider, local
provider transport, device-spike implementation and license manifest. The other
391 match byte-for-byte, including F7/F8 reports, registrations, policy source,
corpora, task hashes, frozen oracles, grammar and template. The original F7/F8
artifact directories remain in the F8 worktree and were not written by this lane.

All four freeze lists verify: 18 v1 train entries, 17 v1 held-out entries,
149 v2 train entries and 57 v2 held-out entries. Their hashes are pinned in
`configs/f9-ondevice.json`.

Parity remains 30 s/task and relaxed remains 120 s/task, non-comparable.
Both retain 16 steps, 65,536 aggregate tokens, 2,048 output tokens/action,
3 s/tests, seed 0, temperature 0, low reasoning and 8,192 context.

`make check` passed lint, formatting, strict mypy and **463 tests in 128.22 s**.
Baseline: 428; delta: 35 new cases. These cover prefix retention beyond the
rolling boundary, BOS on both wire paths, a loopback fake llama-server, native
timings, timeout typing, admission-inclusive deadlines, genuine server failures,
green/stale/late expiry evidence, unchanged scoring, package drift and runner
admission. Tests are isolated unit/contract checks or bounded local HTTP,
filesystem and sandbox integration checks. No test loaded model weights.

After the full gate, CSV output was normalized to LF; the focused analyzer test
and formatting/lint checks passed again. Shell syntax checks passed. No gate
or assertion was weakened to admit the changes. The bench files were added to
the existing Apache-2.0 license manifest.

## Lab package and dry-run

Package: `artifacts/f9/lab-f9.tar.gz`.
It contains 420 manifested files and no weights, environment, cache or prior
run artifacts. Archive size: 447,180 bytes. SHA-256:
`a16ad856d6e023576c25892167eff4c1c43d1be3a0ebcbbfc4d93fdaaa3447a9`.
The bundle pins runtime commit `ea896fbe2bdf279ddb46921fd64e46832f7d65ef`;
this report is a separate documentation commit.

The packaged runner's dry-run completed with exit 0 at 05:26:30 CT on
2026-10-27. Both budget-label mock harness checks, all 20 offline probe requests,
server command construction, swap admission and the two-second child cap passed.
No GGUF or llama binary was present at the staged benchmark paths. Existing
unrelated llama PIDs were only observed and left untouched.

Dry-run evidence:
`artifacts/f9/final-dry/results/dryrun-20261027-052621-46685`.
The child timeout was exercised; the wall-clock hard-stop event was not triggered.
The existing hard-stop watchdog remains in the runner.

The runner retains the real phases, primary/non-comparable labels, build/model/
Metal pins, process/port checks, memory/load/swap admission and hard stop. Defaults
remain 21:00 CT start, 22:20 CT hard stop and a 70-minute cap. An approved override
must be supplied by the operator. Transfer and launch instructions are in
`docs/LAB-F9.md`.

## Estimate and remaining live evidence

**Estimate only:** replacing an affected 2,681-token repeated prefill with a
40-to-60-token suffix could avoid roughly 18 seconds of prefill in a call like
turn six above. This assumes the server retains and reuses the extended prefix.
It is not a measured F9 speedup or a predicted repair count. Initial source
prefill, long patch decoding, swap admission and growing-context cost remain.
Full history can still encounter the unchanged context or aggregate-token cap;
there is no silent compaction to hide that limit.

the operator must measure actual cache counters, per-call latency, primary repairs and
separate green-at-expiry counts on lab. The snapshot cannot support a claim
that this implementation now solves the 30-second parity tasks.

Operational lesson captured: a cache flag alone does not prove reuse. Retain the
exact harness identity, per-call cache/prefill/decode counters, failed-call time
and diagnostic reason. Include safety-admission time in request deadlines.

No bench SSH, model load by F9, Modal spend, merge, production action, public
change, external post, database write, credential creation or billing change
occurred. Authorized publication is limited to this private feature branch.
