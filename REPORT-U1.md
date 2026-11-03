# REPORT-U1

Uplink core, the trace/display addenda, and queued items (j)-(m) are implemented. Lint,
strict typechecking and all **820 tests** pass. The Linux worker is staged and verified
inside the existing Lima VM; the bench native adapter compiles and passes its pure
self-test. **No live fault injection or model inference was run.** Real Linux trials and
the three consecutive Mac recoveries are the separately authorized follow-on run.

The release scanner reports three historical findings: one synthetic credential-URL
fixture and two prior report revisions containing name-only exclusion language. Current
release files and licenses have no findings. The scanner and Git history were not
weakened or rewritten.

## North Star check

The goal remains getting back online through Repair, Reroute or Report, with independent
connectivity evidence and a bounded, auditable change. The default diagnostic/planning
Session stays read-only. Separate, explicitly gated runners now implement controlled
Linux evaluation and bench Wi-Fi recovery. A generic ONLINE result is not a claim that
an interrupted cloud task resumed. Mock traces are not real-agent demo footage.

This is private hackathon work using project code and approved components. The work
stayed in the dedicated `offline-agent-f9` worktree on
`feat/u1-uplink-core`; the unrelated edit to `REPORT-F10.md` remains untouched and uncommitted.

Validated code: `2a59be11f99fc4620d3ae3718a420f6ad528d44e`.
Original U1 base: `f34764b9ab56895dc9a8d56f080708223a1056c5`.
All new commits use `Richard Suarez <rgsuarez@gmail.com>`, without co-author trailers.
Publication is limited to `feat/u1-uplink-core` at the verified private
`<repository-owner>/offline-agent` origin.

## Delivered

| Scope | Implemented behavior |
| --- | --- |
| U1 (a)-(f) | Closed typed actions and generated GBNF; independent online oracle; all-interface macOS diagnostics; prior-route evidence and private snapshots; fault catalog; inert planning API; synthetic safety tests. |
| Trace addenda | Version-3 JSONL start/step/finish records, millisecond timestamps, explicit confirmation, watchdog state, configuration diffs, oracle latencies, model text and supplied native timings. Every record has readable display with `«redacted»` spans. |
| Canonical replay path | Evaluation trace paths are hard links to the same flushed files under `artifacts/uplink/traces/`. ANSI replay preserves timing and validates complete records before rendering. |
| NEXT (j), F10 summary | `budget_exhausted` is accepted as a diagnostic event. Results regenerate from unchanged captured data; scoring and green-at-expiry treatment are unchanged. |
| NEXT (k), Linux | All twelve fault inject/undo paths, DHCP, TLS/proxy/portal/application fixtures, alternate route qualification, watchdog, owned cleanup and paired model/baseline controller. Source transfer is verified because the VM has no shared host mount. |
| NEXT (l), provenance | New Uplink traces and evaluation receipts carry model name, GGUF filename/hash, source repo, llama.cpp build, chip and explicit expected/file/server/fixture verification states. Historical F10 receipts remain immutable. |
| NEXT (m), bench | `uplink-real`, default simulation, OOB checks, exact Wi-Fi snapshots, native SystemConfiguration adapter, detached launchd rollback, four fault types, typed snapshot-only repairs, independent Wi-Fi-bound verification and disarm. |

The source package is `src/offline_agent/uplink/`. Usage and contracts are in
`docs/UPLINK.md`, `docs/UPLINK-LINUX-HARNESS.md` and `docs/UPLINK-REAL-RECOVERY.md`.
No dependency was added and no provider or model was started.

The pinned artifact is Muse Glimmer 30B,
`Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, SHA-256
`4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`, from
`meta-models/Muse-Glimmer-30B-GGUF`, with llama.cpp `10353 (f8def7fe1)` on Apple M4 Max.
File/build/chip attestation was verified read-only on lab. A stopped server cannot
produce server-verified inference evidence.

## Verification and evidence

| Check | Result |
| --- | --- |
| `make lint` | Passed. |
| `make typecheck` | Passed, strict, 151 source files. |
| `make test` | 820 passed: 500 original tests, 6 F10 regressions and 314 Uplink tests. |
| Paired Linux dry matrix | 12 faults times 2 arms, 24 MOCK cases. |
| Mac recovery dry gate | 3 synthetic consecutive DNS-plus-proxy trials; real gate correctly remains false. |
| Trace/replay/privacy checks | All 27 new dry traces parse, replay, pass privacy validation and resolve to the canonical inbox as the same file. |
| Native bench adapter | Compiled on bench; self-test passes with zero network reads/writes. Exact empty/disabled proxy round trips and stable digests verified. |
| Real read-only bench oracle | ONLINE, all 6 checks passed through Wi-Fi, with 6 redacted raw transport receipts retained. This was not a recovery trial. |
| VM placement | Lima copies and verifies the four stdlib worker files plus wrapper. The staged worker's no-effect probe passes without root. |
| Source and runtime status | Lab staged source hashes match the validated code. At 2026-10-27 16:54:14 UTC, localhost model server was unavailable and `/var/db/uplink-real` did not exist. |
| Frozen coding scope | No change to corpus, coding oracles, F8 policy, F10 configuration/package, or scoring. The explicitly requested watcher fix is separate. |
| Release scan | Current files/license coverage clean; three classified historical findings remain. |

Evidence paths below are relative to this repository. They are ignored local artifacts,
not pushed network data:

| Evidence | Path |
| --- | --- |
| Final lint/types/tests | `artifacts/uplink/closeout-lint.txt`, `artifacts/uplink/closeout-types.txt`, `artifacts/uplink/closeout-tests.txt` |
| Linux dry output directory | `artifacts/uplink/closeout-lab-location.json` |
| Mac dry receipts | `artifacts/uplink/closeout-real/summary.json` |
| All 27 replay/privacy/canonical checks | `artifacts/uplink/closeout-trace-validation.json` |
| Lab staged-source receipt/status | `artifacts/uplink/closeout-stage-receipt.json`, `artifacts/uplink/closeout-lab-status.json` |
| Lab raw redacted probe receipt | `artifacts/uplink/lab-final-probe-receipt.json` |
| Verified VM worker placement | `artifacts/uplink/vm-final-stage.json` |
| File/build/chip attestation | `artifacts/uplink/model-provenance-lab.json` |
| Historical scan adjudication | `artifacts/uplink/scan-adjudication.json`, `artifacts/uplink/report-name-adjudication.json` |

The historical finding is `secret-pattern-6` in blob `3ea45154f03f`, an older synthetic
`tests/uplink/test_oracle.py` fixture. Prior value-blind equality checks established that
it is test data, not a real credential. Two later report-only findings, blobs `fa319ddb974c` and `4da33089b15c`, are exact
matches to the same authored exclusion sentence, with no implementation or data inclusion.
The automated release gate remains red; this is not represented as scanner clearance.

## Recovered F10 results

Run `20261027-072448-60629` contained two primary and 38 secondary `budget_exhausted`
events that the frozen watcher could not render. The fix is committed on the local F10
branch as `5d2f607ef33b08b1095b192fa69063e48ec5d675` and is also integrated on U1. The F10
branch was not pushed. No inference was rerun and no captured source result was changed.

`RESULTS.md` and `f10-results.json` were regenerated at:

`docs/results/f10-bench/`

| Metric | Primary, 120 s/task | Secondary, 30 s/task |
| --- | ---: | ---: |
| Tasks evaluated | 50/50 | 50/50 |
| Verified repairs | 38/40 | 11/40 |
| Correct give-ups | 10/10 | 10/10 |
| False fixed claims | 0 | 0 |
| False give-ups | 2 | 20 |
| Green at expiry, uncredited | 0 | 9 |
| Invalid actions / steps | 13/400 | 5/268 |
| Mean seconds per provider call | 5.76 | 4.83 |
| Native decode tokens/s | 15.02 | 17.11 |
| Native cache reuse | 81.5% | 69.0% |

Secondary is non-primary and cannot replace the hero row. Green at expiry remains
uncredited, exactly as the frozen protocol requires.

## Original local Mac capture qualification

The earlier authorized local diagnostic capture remains qualified: it captured 30
interfaces, 7 services and 112 candidates in 190 steps, but exited at its post-capture
privacy check. Its oracle was DEGRADED because the effective scoped proxy path was
unresolved. Parser corrections were verified against that same capture and tests;
no additional local live diagnosis was run.

The captured trace is
`artifacts/uplink/traces/uplink-e4f6ad98d12f4423944f86cc6f14622f.jsonl`.
`artifacts/uplink/final-capture-validation.json` records zero remaining identifier or MAC
hits after the recorded corrections. That offline validation is not a fabricated fresh
live result. The later bench ONLINE probe is separate evidence on a different machine.

## Live gates and remaining limits

The explicit live test go was received after these preparation checks. The follow-on
run will have its own `REPORT-U1-LIVE.md` and must follow the separately supplied sequence:
model server, watchdog proof, three consecutive real DNS-plus-proxy recoveries, then the
paired Linux matrix. This preparation evidence does not establish those live behaviors.

The bench runner owns only the Wi-Fi test surface. It checks `lab-oob` before each trial
and remote operation, uses a 40-second detached watchdog with bounded native commands,
and stops without retries on OOB loss or failed rollback. It never changes AX88179B/en7,
Thunderbolt Bridge, sshd, firewall, VPN, MDM, profiles, keychain or passwords. DNS caching
that masks an injected fault aborts the trial; it cannot earn combined-fault credit.

Saved hotspot/USB feasibility, broad Mac recovery outside this bounded runner, native
GBNF model sampling and interrupted-cloud-request resumption still require live work.
No new LoRA, paid API usage, Modal spend or training was introduced.

No workstation network setting or model load occurred. Lab received unprivileged
source/build artifacts and read-only checks; the VM received verified source files and
a no-effect probe. No privileged recovery helper was installed. There was no merge,
public publication, external post, production deploy, database write, Vercel/env change,
billing/identity change, credential rotation/issuance, history rewrite, mailmap edit or
hook bypass. Git publication is restricted to the authorized private U1 branch.
