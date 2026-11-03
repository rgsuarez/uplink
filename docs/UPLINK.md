# Uplink v1

Uplink collects redacted connectivity evidence and prepares typed repair plans. The default diagnostic/planning Session is read-only. Separate Linux and bench recovery runners are implemented, default to simulation, and require explicit live authorization. The model never receives an arbitrary shell or sudo tool.

## Diagnose on macOS

```sh
uv run --frozen --offline offline-agent uplink diagnose --json
```

This reads all discovered interfaces and configured services, probes connectivity, ranks route candidates and writes private local evidence under ignored `artifacts/uplink/`. It does not infer connectivity from an SSID or association-status string. A missing tool, permission denial, unavailable lease metadata or expired diagnostic budget appears as an explicit observation.

`diagnose.json` is the typed report; `redaction-scan.json` contains only privacy counters and identifier classes. `salt.key` is local mode 0600. Stable salted HMAC handles replace service names, saved network names, proxy hosts and other identifiers at parsing. IP addresses and OS interface IDs remain useful diagnostic fields. Unredacted command output and stderr are never artifacts. A failed privacy scan writes only counters, refuses the report and leaves previous evidence untouched.

ONLINE requires two independent system DNS resolutions, two certificate-validated HTTPS responses with expected content, and two valid captive-portal responses. The transport explicitly applies environment proxy precedence, system HTTP/HTTPS/SOCKS proxies and supported bypass rules. Unresolved PAC/WPAD, scoped proxy settings, complex exclusions, missing proxy evidence, redirects, wrong bodies, TLS failures or partial successes cannot produce ONLINE. Endpoint drift or a blocked check can therefore produce a conservative DEGRADED result on a usable network.

A last-known-good snapshot is written only from a fresh complete ONLINE receipt. Offline pre-change checkpoints are a separate snapshot type and are never promoted to last-known-good. Snapshots contain redacted state, an oracle fingerprint, an immutable ID and a timestamp; writes are atomic. The snapshot itself is the append-only capture event. There is no database or mutable settings projection.

## Action and planning API

`uplink.contracts` owns a closed version 1 action set. `Scope` contains discovery-owned service, candidate, snapshot and evidence handles. `action_schema(scope)` and `grammar.gbnf(scope)` produce the schema and grammar for that exact scope. JSON accepts no unknown keys, tools, raw argv, duplicate keys or model-provided service names. GBNF emits canonical key order and bounded enum arrays; the parser additionally enforces uniqueness and discovery membership.

`Session.dispatch` handles the typed diagnostic actions, oracle checks, snapshots, reports and inert change plans. `declare_online` requires the latest complete oracle result, no more than 30 seconds old; an earlier good result cannot override a later failure. Terminal declarations, reports and give-ups close that session. Start a new observation session to continue watching after a human step.

Every admitted change plan carries a confirmation card with exact argv, why, evidence, undo argv, checkpoint ID and a 20-second auto-revert requirement. Exact argv is for an ephemeral local confirmation UI only. `for_trace` HMACs every argv element. Raw confirmation cards must never be logged or committed. A session requires a fresh pre-change checkpoint matching its observed state and refuses changes when its oracle is already ONLINE.

`execute_change` always refuses. The 20-second value is a future executor requirement, not an active watchdog. Cache eviction and lease renewal have no faithful inverse; their cards explicitly say so and cannot become executable. Service-order changes require a complete discovered order. Managed services, missing evidence, unknown candidates and unbound service names refuse. Saved Wi-Fi entries are useful candidates, but U1 can only plan reroutes between configured services; it cannot select an SSID or make a phone trust/USB permission decision.

`baseline.next_action` is a deterministic reference policy with the same schema. It preserves healthy no-op cases, hands captive portals to the human, proposes one proven snapshot restoration at a time, and otherwise reports or gives up. It does not implement an autonomous retry loop, consent, mutation or rollback.

## Validation and limits

The Uplink tests are hermetic: their default fixture blocks process creation and sockets. Synthetic macOS fixtures cover misleading status text, hidden identities, route evidence, snapshot drift, proxy bypass and refusal boundaries. Grammar tests independently interpret the generated acyclic GBNF; native llama.cpp token-sampling validation remains a U2 integration check and no model was loaded for U1.

Keep tests in the `tests/uplink` Python package so their module names cannot collide with the frozen coding tests. Postpone runtime evaluation of generic argparse type annotations: typing stubs describe generics the runtime class does not implement. Treat abbreviated numeric routes as networks, not private identifier strings, and distinguish proxy IP literals from hostnames so privacy scans do not reject permitted address evidence. Never place credential-shaped URL literals in fixtures: even synthetic examples remain in historical secret scans after later edits.

The implemented isolated Linux worker and paired evaluation runner are described in `docs/UPLINK-LINUX-HARNESS.md`; the catalog is `docs/UPLINK-FAULTS.md`. The bench Wi-Fi runner, native configuration adapter and detached launchd watchdog are described in `docs/UPLINK-REAL-RECOVERY.md`. Dry runs and native compilation are preparation, not measured recovery evidence. Live model sampling, real fault trials, rollback under agent death, hotspot/USB feasibility and interrupted-cloud-request resumption remain separate acceptance work. Generic ONLINE is not application resumption.

## Run journal

Every diagnostic CLI run, planning Session and Linux dry run writes `artifacts/uplink/traces/<run_id>.jsonl`, mode 0600, flushing each record. Use `Session` as a context manager or call `close()` when abandoning a nonterminal planning session; terminal actions finish it automatically. The first record identifies the schema, initial grammar hash, model provenance (no active model when none was called) and mode. The last record records the verdict and totals, including an error finish when a privacy gate refuses a report.

Each step includes a millisecond ISO timestamp with offset, monotonic elapsed time, ordinal, phase, validated action, redacted raw command receipt, configuration diff, oracle sub-results, model emission, controller decision, network state and performance. Unknown interfaces or an unprobed oracle are null/unknown, never fabricated as down/offline. Probe latencies are measured around the real transport calls. HTTP response bodies are opaque HMAC handles because an unexpected portal page can contain private text; status, content verdict and proxy-path evidence remain structured.

Planning records a proposed before/after diff (after is null when the inert executor cannot know the effect), followed by a separate confirm record with the full redacted card. U1 records `not_requested_u1` and a cleared timer with reason `u1_execution_disabled`; no human consent or armed watchdog is invented. Apply and revert transitions are supported for labeled test fixtures and the separately gated `linux_eval` and `real_eval` runners. The reader rejects apply without matching plan, explicit approval and an armed timer.

Model text and token IDs are preserved exactly when safe. If text requires redaction, the record says so and omits token IDs that could reconstruct it. Performance comes only from supplied llama.cpp timing fields: prompt/eval/cached counts, prompt/eval rates and durations. Missing timing values and incomplete token totals remain null. Supply the actual model identity when creating a Session that records model emissions. Diagnostic and scripted steps have null model/performance fields. No U1 diagnostic run calls a model.

## Replay a recorded run

```sh
uv run --frozen --offline offline-agent uplink trace replay artifacts/uplink/traces/RUN_ID.jsonl --speed 2
uv run --frozen --offline offline-agent uplink trace replay artifacts/uplink/traces/RUN_ID.jsonl --speed 0 --no-color
```

The helper and its validator use only the Python standard library. Replay validates the whole trace before rendering, preserves record order, and sleeps recorded monotonic gaps divided by speed. Speed 0 disables sleeping. ANSI colors distinguish phases; `--no-color` emits plain text, and untrusted terminal escapes are always rendered as literal escapes. A malformed record reports its line and a value-blind reason. Replay never runs a command from the trace, probes a network, changes a setting, or calls a model.

## Readable display (trace version 2)

New records retain the structured HMAC evidence and add a `display` string on start, every step and finish. It shows fixed command arguments and non-sensitive service labels verbatim. SSIDs, BSSIDs, MACs, hostnames, public IP addresses, usernames and credential values become the literal `«redacted»` marker. Replay prints this field. Version 1 traces remain readable without inventing missing raw arguments; they retain their original records.

## Model provenance (trace version 3)

Every new run-start and Uplink evaluation receipt carries the model name, GGUF filename and SHA-256, source repository, llama.cpp build, host chip, and an explicit verification state. The pinned artifact is Muse Glimmer 30B, `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, SHA-256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`, from `meta-models/Muse-Glimmer-30B-GGUF`, with llama.cpp `10353 (f8def7fe1)`.

`expected` describes the configured artifact, not an inference result. `file_verified` hashes the actual file and checks the binary version on lab. `server_verified` hashes the file and binds the running server's model path and build stamp to the pins. This avoids launching a second version process while the server holds device resources. Real evaluation requires fresh server evidence and the Apple M4 Max host identity. Baseline and no-model runs have no active model and no model-use claim; a fixture is labeled as such. Replay shows both the artifact badge and verification state.

Evaluation receipts count `model_calls_attempted`, including timed-out requests that
return no completion. Such a confirmed request sets `model_used` to true.
A remote transport loss with no request receipt leaves the attempt count unknown;
`model_calls_confirmed` and `model_call_accounting` distinguish recorded attempts
from an incomplete remote result. A diagnostic refusal before inference counts zero.
Trace emission counts
and token totals cover returned output only; a failed request records its reason and
elapsed time without inventing model text or timings.

A read-only attestation uses `python -m offline_agent.uplink.model_identity --output <ignored-path>` on lab. `--file-only` performs no inference and does not start a server. Historical coding receipts remain immutable; these requirements apply to new Uplink runs and receipts. Trace readers retain compatibility with versions 1 and 2.

## Recovery runner evidence

`uplink-real` is a separate, lab-only execution surface; it cannot enable mutation through
the original `Session` or `execute_change` API. Every independent Wi-Fi oracle call carries
redacted raw DNS/curl transport receipts with exit status and duration. HTTP bodies remain
opaque handles. Configuration diffs contain salted handles of native configuration digests,
observed before/after snapshot IDs and power state. Proposed repairs predict only the selected
component; controller timing and watchdog metadata are not configuration changes.

The Linux controller transfers and verifies its stdlib worker bundle through Lima rather
than depending on a host mount. Both runners preserve source model identity and label mock
results explicitly. Their dry traces must not be presented as real recovery footage.

Evaluation folders keep their own trace path and publish a hard link to the same flushed
file in `artifacts/uplink/traces/`. The canonical replay inbox therefore includes diagnostic,
Linux and bench runs without duplicating their contents. Explicit output directories
outside the repository's Uplink artifact tree remain isolated.

## Layer diagnosis before repair

Live Glimmer evaluations first emit a closed diagnosis: a layer (`link`, `ip`, `dns`,
`proxy`, `captive` or `upstream`) and one to three observed evidence IDs. A second
request uses a grammar restricted to actions for that layer. Runtime parsing enforces
the same subset. Interface cycling is last, costs ten relative to one for a snapshot
repair, and is absent unless the link is independently observed down. An ONLINE oracle
allows only terminal declarations or handoff, never a network change.

The fixed read-only probe stage includes link/address state, gateway reachability,
configured versus alternate DNS, direct versus configured-proxy HTTPS, and the existing
captive results. Alternate DNS and direct HTTPS are diagnostic comparisons only. They
cannot grant ONLINE or replace the application's actual proxy path. Missing tools,
timeouts and unavailable evidence remain explicit. Linux direct TLS pins the isolated
fixture address while preserving certificate validation; macOS direct TLS still uses
configured DNS. The prompt receives measured facts and captured drift, never a fault name.

Each model request records its rendered prompt, effective grammar, returned text and
native timings in the run trace through the normal privacy filter. A failed second stage
retains the first diagnosis and both request inputs. These are development-policy changes;
repeated cases are not held-out evaluation.

## Approved primary Wi-Fi trials (trace version 4)

The live runner's DNS/proxy profile permits only restoring captured DNS and web/secure
proxy settings on the explicitly allowlisted device. The native adapter preserves other
DNS keys during injection. All other managed devices and fault types remain refused.
The trace records the exact scoped approval and clearance-file digest; replay validates
that record using only the standard library and does not authorize any operation.

Before each trial the root helper saves all eight DNS/search/proxy getter outputs.
It compares them exactly after restoration, preserves protected-service and location/order
fingerprints, arms a detached launchd watchdog before injection, and enforces a two-minute
quiet interval. A diff mismatch, failed restore or OOB loss stops further operations.
Success requires an independently measured oracle, exact restoration within 60 seconds,
and a new normal Wi-Fi SSH connection after the trial. The log does not reuse pretrial
SSH success as post-trial evidence. Every trial appends an idempotent fleet handback line.

SSH aliases are configuration: `UPLINK_OOB_HOST` and `UPLINK_WIFI_HOST`, or the live CLI's
`--oob-host` and `--wifi-host`. The paths must be distinct. Alias changes never expand the
hardware-serial or fault allowlist. Model inference and heavy verification remain on the
approved remote machine. Real fault runs require the separately recorded live clearance.


Worker bundles require an exact file manifest. Run read-only Python diagnostics against
those bundles with `python3 -B`; an unqualified import can create `__pycache__` and correctly
trigger the staging guard. Retain accidental generated files outside the immutable bundle,
verify its original source hashes, and reconcile cleanup before another trial. A staging
refusal is not a model failure or a network-recovery attempt.
