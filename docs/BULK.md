# Bounded teacher batch 1

Batch 1 makes one fresh attempt per frozen solvable v2 training task, then one attempt per
impossible training task as a separate give-up calibration slice. The order interleaves source
families deterministically. The impossible slice never enters SFT. Both slices share one
reservation ledger and a maximum $15 cap. Default provider is mock; default spend cap is $0.

The pilot's working historical cloud-teacher Standard Responses route is reused: low reasoning,
temperature 0, strict action envelope, explicit final-message selection, 16 steps,
65,536 cumulative tokens, 2,048 completion tokens per call and 30 seconds per task.
Local response caching is disabled. Provider-reported prefix-cache usage remains distinct.

This is historical training-data collection, not a claim about the submission's base
Muse Glimmer recovery model. Original provider identifiers remain in unchanged code
and run records; the historical teacher is not relabeled as Muse Spark Open.

```sh
uv run --frozen --offline offline-agent bulk --provider meta --spend-cap-usd 15
# Execute only after the batch and spend have been explicitly authorized:
set +x
set -a
source "$META_HACKATHON_ENV"
set +a
uv run --frozen --offline offline-agent bulk --provider meta --execute --allow-live \
  --input-usd-per-million 1.25 --output-usd-per-million 4.25 --spend-cap-usd 15
```

Prices shown are the verified batch-1 Standard rates, not permanent defaults. Every call
reserves its input/output upper bound and journals it durably before sending. Reservations
are never refunded. A request that would exceed the cap is refused; no higher-cap rerun or
new ledger is created. `artifacts/bulk1/` must not already exist, which prevents an accidental
restart from resetting reservations or repeating tasks.

There are no automatic retries. A timed-out or incomplete task is recorded with its retained
reservation; the collector may move to the next unique task. Authentication, schema, rate-limit,
secret-echo, accounting-bound and reservation-cap failures stop the batch. A stop can leave
unattempted solvable or calibration tasks. Remaining work needs inspection and a separate batch.

Acceptance oracle: unchanged frozen v2 source/oracle identities, complete transcript replay,
current green tests and an independent final test. Edge oracle: the frozen impossible training
fixtures retain a missing immutable dependency. These gates are not authored by the teacher.

Accepted trajectories are replay-admitted again before export and deduplicated by task hash
plus ordered actions, omitting only the terminal explanation text. Binding the task preserves
different repairs that happen to share a generic tool sequence. Prompt/completion pairs are
also deduplicated. `sft.jsonl` uses the pinned native template and completion-only loss contract.
The existing training data loader and original-trace bundle verifier run without a model or
tokenizer to verify the export. Mock exports remain marked mock and are refused by live loading.

Artifacts include `plan.json`, `calls.jsonl`, `attempts.jsonl`, separate slice directories,
`readmission.jsonl`, `sft.jsonl`, original hash-named `traces/`, `manifest.json`, and `summary.json`.
The manifest records counts, source coverage, task/action/trace hashes, SFT hash and frozen
input/template hashes. The collector never trains, loads weights, launches a GPU, extends
the corpus, sends held-out content, or pushes.
