# Uplink: an on-device agent that gets a broken computer back online

> **Pre-existing work, stated up front:** Meta Muse Glimmer 30B, base, unmodified open weights, llama.cpp
> (`llama-server`, build b10353) and the open-source Python libraries in `uv.lock` existed
> before the hackathon. A private pre-kickoff prototype and design notes were used as
> reference only; none of their code was ported. All Uplink code in this repository
> (agent loop, typed tools, safety gates, watchdog, oracle, evidence harness and replay
> tooling) was written during the event. First commit: `dabff4c`, Oct 26, 2026, 11:40 AM CT.
> Details: [PROVENANCE.md](PROVENANCE.md).

When your connection breaks, every cloud assistant breaks with it, at the exact moment
you need help. Uplink keeps the help on the machine. **Meta Muse Glimmer 30B (base, unmodified open weights) runs
on-device through `llama-server` on localhost**, reads the measured network state,
compares it with a known-good snapshot and picks one typed, reversible fix. An
independent checker, not the model, decides whether the computer is back ONLINE.

**Model:** base `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, SHA-256
`4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`.
Uplink uses base, unmodified open weights.

**Headline (committed evidence):** on a real Mac, Meta Muse Glimmer 30B
recovered **10/10 model-reached DNS trials** (median **15.319 s**) and
**5/5 model-reached proxy trials** (median **10.4 s**, exactly 10.395 s, across
two segments). Both observed **0 false ONLINE**. Pre-model stops: **DNS 4,
proxy 6, not counted** in those denominators. See [Results](#results) for
the segment policies, all exclusions and the failed no-change controls.

**F01 radio-off:** 1/5 model-reached, 1 strict recovery 11.213 s, false ONLINE 0 in the specified three-series continuation; all listed series are 1/6. See the scoped tally below.

## What it does

1. **Observe.** Read-only probes collect interfaces, DNS, proxy and route facts.
2. **Compare.** Facts are diffed against the snapshot captured when the machine was last
   known to be healthy.
3. **Decide.** Meta Muse Glimmer 30B (base, unmodified open weights), constrained by a GBNF grammar built from a closed 13-action
   schema, returns one typed JSON action (for example `restore_dns` or `restore_proxy`),
   or `report` / `give_up` when no safe repair is established.
4. **Act.** A bounded executor applies only snapshot-bound restores. The model has no
   shell and no sudo.
5. **Verify.** An independent oracle requires two DNS lookups, two certificate-validated
   HTTPS fetches and two captive-portal checks over the effective path. Anything missing,
   stale or contradictory fails closed. Model text alone never counts as ONLINE.

Outcomes are **Repair** (restore a drifted setting), **Reroute** (use a previously
qualified alternate path) or **Report** (stop and tell the person the one step they must
take). An honest Report is a valid result; it never earns recovery credit.

## Why it matters (impact)

Losing your connection is one of computing's most ordinary failures and one of its
most frustrating: in a 2026 benchmark of 50,000+ IT help-desk tickets, network
troubleshooting had the highest negative-sentiment rate of any use case (39%)
([Fixify](https://www.fixify.com/it-help-desk-benchmark-report-2026)). A single
resolver failure can make "basically all Internet services" unreachable
([Cloudflare, July 2025](https://blog.cloudflare.com/cloudflare-1-1-1-1-incident-on-july-14-2025/)),
and 2.2 billion people are still offline, with rural connectivity far behind urban
([ITU, 2025](https://www.itu.int/en/mediacentre/Pages/PR-2025-11-17-Facts-and-Figures.aspx)).

It is also exactly when modern help disappears. Cloud assistants need the network you
just lost. Help for getting back online has to run on the device, so Uplink runs
Meta Muse Glimmer 30B locally (base, unmodified open weights), on a laptop-class machine, and lets it propose **one** typed,
reversible fix. The network itself, through six independent checks, decides whether the
fix worked. The model proposes; the network decides. A false "online" is treated as worse
than an honest "I can't fix this".

We think this belongs in every operating system eventually: a narrow specialist whose
only job is keeping you connected, and whose claims you can trust because it is not
allowed to grade itself. Today it runs on one laptop; that is the part we have proven,
and the [Results](#results) count every trial, including the ones it failed.

- **Who:** anyone whose laptop drops offline because of a recoverable configuration
  problem (bad DNS, stale proxy), plus the field technicians and help-desk staff who
  otherwise walk people through blind settings changes.
- **The gap:** cloud assistants are unreachable during exactly this failure. A local
  model stays available.
- **Why trust it:** every change is snapshotted, exactly diffed, verified and
  auto-reverted on failure. Uplink would rather report than guess.
- **Limits:** broad deployment, other device classes, field reliability and user impact
  have not been measured yet. See [Honest caveats](#honest-caveats).

## How it uses Meta AI

| Piece | What Uplink does with it |
| --- | --- |
| Meta Muse Glimmer 30B (base, unmodified open weights, Apache-2.0) | Chooses the recovery action on the device. Unmodified Q4_K_M GGUF, pinned by SHA-256 `4cc57c0f…f60e` |
| `llama-server` (llama.cpp b10353, `f8def7fe1`, Metal) | Serves the model on `localhost` only, on an Apple M4 Max MacBook Pro with 36 GB |
| Glimmer native chat template | Rendered verbatim from `meta-models/Muse-Glimmer-30B` revision `cec94fc…`, hash-pinned in [templates/manifest.json](templates/manifest.json) |
| Grammar-constrained tool use | A GBNF grammar generated from the typed action schema makes every model turn a valid, closed action |

Upstream weights: [huggingface.co/meta-models/Muse-Glimmer-30B-GGUF](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF)
(base repo: [meta-models/Muse-Glimmer-30B](https://huggingface.co/meta-models/Muse-Glimmer-30B)).
The project does not redistribute or modify the weights.

A head-to-head with Muse Spark Open was attempted on the same frozen cases but produced
no results (provider upload failure), so no comparison is claimed
([RESULT-SPARK-VS-GLIMMER-MON.md](RESULT-SPARK-VS-GLIMMER-MON.md)).

## Architecture

Meta Muse Glimmer 30B uses base, unmodified open weights in this bounded loop.

```text
 Read-only probes ──► measured facts ─┐
                                      ├─► Glimmer (llama-server, localhost)
 Known-good snapshot ─────────────────┘            │  one typed JSON action (GBNF)
                                                   ▼
                          parser + authority + state gates (held actions refused)
                                                   │
                                                   ▼
                          snapshot-bound restore primitive (no shell, no sudo)
                                                   │
                                                   ▼
                independent ONLINE oracle (2x DNS, 2x HTTPS+cert, 2x captive) + exact readback
                                                   │
                                   ┌───────────────┴───────────────┐
                                   ▼                               ▼
                          verified recovery                 honest Report / give_up

 Detached root watchdog: restores the snapshot within 60 s on timeout, failure or controller loss.
 OOB control link: a separate path from a second machine, checked before any change and never modified.
```

Main code: `src/offline_agent/uplink/` (bridge, worker, grammar, oracle, watchdog and
probe modules), live harness in `harness/` and `scripts/`, evidence in `evidence/`.

## Safety model

- **Out-of-band (OOB) link.** Live trials are driven from a second control machine over a
  separate out-of-band path, not the Wi-Fi path being broken. Each trial checks the OOB identity, nonce, route,
  freshness and timeouts before any network change, and stops with no retry if OOB is
  lost. OOB interfaces, Thunderbolt Bridge, sshd, firewall, VPN, Keychain, MDM and
  network locations are excluded from every change.
- **Snapshot and exact diff.** The exact DNS, search-domain and proxy state (and whatever
  setting a fault touches) is captured before injection. Repairs can only restore captured
  values, and the result is read back and diffed exactly against the snapshot.
- **Revert within 60 s.** A detached root watchdog is armed before injection with a hard
  60-second network deadline (individual series use tighter budgets). If the controller
  dies, times out or verification fails, the watchdog restores the snapshot independently.
- **No free-form power.** Typed actions only, single-use attempts, durable audit records,
  held actions refused by execution gates, and success decided by the independent oracle.
- **Preregistered trials.** Every live series freezes its prompt, model hash, oracle hash,
  stop rules and counting rules in `PREREG.json` before the first trial.

## Results

Every trial that reached the model is counted, pass or fail. Attempts that stopped before
any model request are listed and excluded from the denominator. Only committed evidence
appears here.

### Live, real Mac, Meta Muse Glimmer 30B on-device

Model: base, unmodified open weights.

| Fault (series) | Expected action | Passes / model-reached | Median recovery | False ONLINE | Evidence |
| --- | --- | ---: | ---: | ---: | --- |
| **F04 bad DNS** (3 segments, mixed policy) | `restore_dns` | **10/10** | **15.319 s** (range 15.125-17.695) | **0** | [EVIDENCE-F04-SERIES.md](EVIDENCE-F04-SERIES.md), [evidence/f04-series/](evidence/f04-series/) |
| ↳ segment `54b5899ccb66` (load policy v1) | `restore_dns` | 1/1 | 17.541 s | 0 | `evidence/f04-series/54b5899ccb66/` |
| ↳ segment `075540cb0307` (v2) | `restore_dns` | 2/2 | 17.6935 s | 0 | `evidence/f04-series/075540cb0307/` |
| ↳ segment `51ffa1727d58` (v2, completed) | `restore_dns` | 7/7 | 15.236 s | 0 | `evidence/f04-series/51ffa1727d58/` |
| **F06 stale proxy** (2 segments; entry max raised from 24.5 to 24.75 GiB between them; 6 pre-model headroom refusals excluded from trial count) | `restore_proxy` | **5/5 PASS** (A 2/2 + B 3/3) | **10.395 s** | **0** | [F06-COMBINED-TALLY.md](evidence/f06-series/F06-COMBINED-TALLY.md); [SERIES-LEDGER.json](evidence/f06-series/98e499eebbd7/SERIES-LEDGER.json); [SERIES-LEDGER.json](evidence/f06-series/15a6104a9685/SERIES-LEDGER.json) (committed source `c3dd1ad`; sanitized public derivatives) |
| **F01 Wi-Fi radio off** (specified three-series continuation) | `restore_radio` | **1/5 model-reached PASS** | **1 strict recovery: 11.213 s** (one passing trial) | **0** | [F01 tally](evidence/f01-series/F01-COMBINED-TALLY.md); [F01 ledger](evidence/f01-series/SERIES-LEDGER.json) |
| Control: healthy network | no mutation (0 network changes; 0 false-action requests) | **0/3 FAIL**: `declare_online` lacked the required `verified` reason | n/a | **0** | [SERIES-LEDGER.json](evidence/controls-series/0ea3e4e5eebc/SERIES-LEDGER.json) (committed source `d8b390a`; sanitized public derivatives) |
| Control: misleading status text | no mutation (0 network changes; 0 false-action requests) | **0/3 FAIL**: `declare_online` lacked the required `verified` reason | n/a | **0** | [SERIES-LEDGER.json](evidence/controls-series/0ea3e4e5eebc/SERIES-LEDGER.json) (committed source `d8b390a`; sanitized public derivatives) |

Not in the table yet: F05 wrong gateway not run; site-only outage pending / not yet run in a committed series. Receipts for the pending cases will be added only once their ledgers are committed in this repo.

F01 caveat: small n; radio-off is the hardest measured live repair segment. The specified three-series continuation is 1/5 (one PASS, one FAIL, three ABORTs); all listed series are 1/6 after including an earlier model-reached ABORT. Four pre-model refusals/aborts are excluded and listed in the tally.

In these 6 controls, the base Glimmer model never changed the network under either healthy or
misleading-status conditions (0 network mutations, 0 false-action requests, 0 false ONLINE),
but all 6 health reports failed the oracle's required `verified` reason check (0/6 PASS);
5 pre-model headroom refusals are retained and excluded from the trial denominator.

F04 details: 14 attempts in total. All 10 model-reached trials selected `restore_dns` with
one scored model request each and have strict-reader `VALIDATED` verdicts. Four attempts
stopped before the model and are excluded: `c81f7ae72ef1` (load-gate refusal),
`8d35661dd4a7` and `38eb55e41fd4` (safety/terminal evidence not proven: prior-handback
check unavailable and headroom-probe timeout) and `069f1d061acb` (`entry_headroom_refused`).
They stay in the ledgers. The 10/10 is a mixed-policy aggregate across three segments
(load policy v1, then v2), not one unchanged-policy experiment. Each attempt used three
unscored warm-up calls, so the timings are warm, not cold-start. An earlier separate
recorded baseline, `1ec5c6c66f0a`, passed 1/1 in 17.555 s and is not pooled
([EVIDENCE-F04-FIX-NOW-12-RECORDED.md](EVIDENCE-F04-FIX-NOW-12-RECORDED.md)).

An earlier development gate (Oct 27) recorded three consecutive DNS-plus-proxy recoveries
on the same Mac under a restricted restore-only profile; that sequence, and the trials
before it, are in [REPORT-U1-LIVE.md](REPORT-U1-LIVE.md) and are not pooled with the
series above.

### Simulated fault trials

Separate from the live results above; labeled `MODEL_IN_LOOP_SIM`.

Meta Muse Glimmer 30B (base, unmodified open weights), served by `llama-server` on a Modal A10G GPU, chose actions against
simulated faults. These rows are not live recoveries and not on-device timings.

| Run | Recovered | Honest reports | False ONLINE | Note |
| --- | ---: | ---: | ---: | --- |
| `authorized-repair-v1` baseline | 15/21 | 6 | 0 | Retained baseline |
| `snapshot-restore-descriptions-v1` | 21/21 | 0 | 0 | Adopted; tool descriptions rewritten **post hoc** on these same cases |
| Held-out compound faults (frozen before inference) | 4/14 repair targets | 6/6 held-policy reports | 0 | New repair inputs 2/12; 20/20 verified cleanups |

Two rejected description variants (6/21 each, 0 false ONLINE) are kept in
[RESULT-PROXY-REPAIR-SUN.md](RESULT-PROXY-REPAIR-SUN.md). Sources:
[RESULT-MODEL-SIM-PROXY-FIX-SUN.md](RESULT-MODEL-SIM-PROXY-FIX-SUN.md),
[RESULT-HELDOUT-EVAL-SUN.md](RESULT-HELDOUT-EVAL-SUN.md).

## How to reproduce

Meta Muse Glimmer 30B uses base, unmodified open weights. The ledger and saved-view
commands below only read committed evidence; they do not run the model.

**Recompute the live-series numbers from committed ledgers** (Python 3.12+, no model,
no network, no credentials):

```sh
python3 - <<'PY'
import json
from pathlib import Path
from statistics import median

series = ("54b5899ccb66", "075540cb0307", "51ffa1727d58")
rows = []
for name in series:
    ledger = json.loads(Path(f"evidence/f04-series/{name}/SERIES-LEDGER.json").read_text())
    rows.extend(ledger["attempts"])
assert len({r["run_id"] for r in rows}) == len(rows)
trials = [r for r in rows if r["model_reached"]]
passes = [r for r in trials if r["outcome"] == "PASS"]
print(f"Passes/model-reached: {len(passes)}/{len(trials)}")
print(f"Median recovery: {median(r['recovery_seconds'] for r in passes):.3f} s")
print(f"False ONLINE: {sum(r['false_online'] for r in trials)}")
for r in rows:
    if not r["model_reached"]:
        print("Excluded before model:", r["run_id"], r["reason"], r["model_requests"])
PY
```

Expected: `10/10`, `15.319 s`, `0`, and four excluded attempts.

**Recompute F06 and no-change controls** (ledgers committed in this repo, no model or network):

```sh
python3 - <<'PY'
import json
from pathlib import Path
from statistics import median

rows = []
for sid in ("98e499eebbd7", "15a6104a9685"):
    rows.extend(json.loads(Path(f"evidence/f06-series/{sid}/SERIES-LEDGER.json").read_text())["attempts"])
assert len({r["run_id"] for r in rows}) == len(rows)
trials = [r for r in rows if r["model_reached"]]
passes = [r for r in trials if r["outcome"] == "PASS"]
print(f"F06 passes/model-reached: {len(passes)}/{len(trials)}")
print(f"F06 median recovery: {median(r['recovery_seconds'] for r in passes):.3f} s")
print(f"F06 false ONLINE: {sum(r['false_online'] for r in trials)}")
print(f"F06 pre-model exclusions: {sum(not r['model_reached'] for r in rows)}")
controls = json.loads(Path("evidence/controls-series/0ea3e4e5eebc/SERIES-LEDGER.json").read_text())["attempts"]
trials = [r for r in controls if r["model_reached"]]
print(f"Controls passes/model-reached: {sum(r['outcome'] == 'PASS' for r in trials)}/{len(trials)}")
print(f"Controls false ONLINE: {sum(r['false_online'] for r in trials)}")
print(f"Controls network changes: {sum(r['network_mutations_executed'] for r in trials)}")
print(f"Controls false-action requests: {sum(r['false_action_requests'] for r in trials)}")
print(f"Controls pre-model exclusions: {sum(not r['model_reached'] for r in controls)}")
PY
```

Expected F06: `5/5`, `10.395 s`, zero false ONLINE, six pre-model exclusions.
Expected controls: `0/6` PASS, zero false ONLINE, zero network changes, zero
false-action requests, five pre-model exclusions. All six health reports failed
the required `verified` reason check; they are not recovery successes.

**Recompute the scoped F01 result** from the committed public derivative (no model or network):

```sh
python3 - <<'PY'
import json
from pathlib import Path

rows = json.loads(Path("evidence/f01-series/SERIES-LEDGER.json").read_text())["attempts"]
assert len({r["run_id"] for r in rows}) == len(rows)
trials = [r for r in rows if r["population"] == "specified_continuation" and r["model_reached"]]
passes = [r for r in trials if r["outcome"] == "PASS"]
all_trials = [r for r in rows if r["model_reached"]]
excluded = [r for r in rows if not r["model_reached"]]
print(f"F01 continuation: {len(passes)}/{len(trials)} model-reached PASS")
print("Strict recovery:", ", ".join(f"{r['recovery_seconds']:.3f} s" for r in passes))
print(f"All listed: {sum(r['outcome'] == 'PASS' for r in all_trials)}/{len(all_trials)} model-reached PASS")
print("False ONLINE:", sum(r["false_online"] for r in all_trials))
print("Pre-model exclusions:", len(excluded))
for r in excluded:
    print(r["run_id"], r["outcome"], r["reason"])
PY
```

Expected: continuation 1/5, one strict recovery 11.213 s, all listed 1/6, false ONLINE 0, and four listed pre-model exclusions.

**Replay recorded simulation trajectories** (no model, no network changes):

```sh
python3 -c "from pathlib import Path; print(Path('demo/model-sim/adopted-F04.md').read_text())"
```

Saved, sanitized replay views are in [demo/model-sim/](demo/model-sim/README.md).
Original private recordings and receipt bundles are not distributed. Ledgers committed in this repo
retain the original evidence hashes and carry separate public derivative hashes.

**Run the model yourself** (any 24 GB+ Apple Silicon or GPU machine):

```sh
hf download meta-models/Muse-Glimmer-30B-GGUF --include "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf" --local-dir models/
shasum -a 256 models/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf   # expect 4cc57c0f…f60e
./llama-server -m models/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf --jinja --host localhost --port 8080
```

Live fault injection changes real network settings and needs a dedicated test machine
with an out-of-band link; it is not part of the quick reproduction.

## Demo video

3-minute demo r13 (2:45.23): `VIDEO_URL_PLACEHOLDER` (placeholder token; no public video URL yet). It opens with the
pre-existing-work line, shows a real recorded recovery and ends on the honest limits.
Storyboard: [DEMO-SCRIPT.md](DEMO-SCRIPT.md).

## Judging map

| Criterion | Where to look |
| --- | --- |
| Impact (30%) | [Why it matters](#why-it-matters-impact): a clear user, a real outage-time job, honest limits |
| Use of Meta AI tooling (25%) | [How it uses Meta AI](#how-it-uses-meta-ai): Meta Muse Glimmer 30B (base, unmodified open weights) on-device via `llama-server`, native template, grammar-constrained typed tools, hash-pinned weights |
| Technical depth (20%) | [Architecture](#architecture), [Safety model](#safety-model), preregistered evidence in `evidence/` |
| Demo (25%) | [Demo video](#demo-video) and the reproducible results above |
| Bonus: Best Build on Meta Open Weights | Unmodified open Glimmer weights on our own hardware, pinned by SHA-256, served locally with no cloud |
| Bonus: Best Agentic Use Case | Observe → typed action → bounded execution → independent verification, with auto-revert and honest Report |

## Honest caveats

- Live coverage includes F04 DNS, F06 proxy, F01 radio-off and healthy/misleading-status controls.
  Control safety held, but all six health reports failed the required reason check.
  F05 wrong gateway was not run; site-only outage is pending / not yet run in a committed series.
- F01 caveat: small n; radio-off is the hardest measured live repair segment. The specified three-series continuation is 1/5 (one PASS, one FAIL, three ABORTs); all listed series are 1/6 after including an earlier model-reached ABORT. Four pre-model refusals/aborts are excluded and listed in the tally.
- Ten passes on one fault class show the loop works on real hardware; they do not
  establish general field reliability.
- The F04 aggregate spans three policy segments, and timings are warm (three warm-ups).
- "0 false ONLINE" is what we observed, not a guarantee.
- The 21/21 simulation result came after rewriting the tool descriptions post hoc on those same cases; the held-out simulation result is 4/14.
- Model: Meta Muse Glimmer 30B, base, unmodified open weights.

## License and provenance

Code is Apache-2.0 ([LICENSE](LICENSE)). Upstream assets keep their terms, listed in
[licenses/manifest.json](licenses/manifest.json) and [PROVENANCE.md](PROVENANCE.md).
Model weights are not included in this repository. Private device identifiers,
snapshots, credentials and approval records are kept out of the repository and video.
