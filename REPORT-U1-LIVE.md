# REPORT-U1-LIVE

**GATE: PASS.** Three consecutive real Glimmer DNS-plus-proxy recoveries on lab
restored the exact settings in 28.765, 27.285 and 26.694 seconds. Each passed the
independent network oracle, OOB and normal Wi-Fi SSH checks, and retired its single
watchdog. The live Linux paired matrix is complete. The final gate sequence is trials
8 through 10 on source `705ee30`; earlier trials remain recorded below.

The revised Glimmer policy recovered all three target Linux development cases, matching
the scripted baseline's 3/3 recovery count. The baseline remains faster. These repeated
development cases are not held-out performance results. The original 24-case matrix,
including its model failures, remains preserved below.

## North Star check

The objective remains getting online through Repair, Reroute or Report with independent
DNS, TLS/proxy and captive checks, explicit confirmation, durable evidence and rollback.
The real Mac oracle measures Internet connectivity through Wi-Fi and the effective proxy
path. The Linux fixture separately verifies an application handshake. Resumption of an
actual interrupted cloud task has not been demonstrated. The frozen coding evidence is
unchanged. The Wednesday, October 28, 9 PM CT recovery gate was met on October 27
at 15:27 CT. This is private development evidence, not a held-out success-rate claim.

## Authorization and boundaries

The current approval permits only reversible DNS and web/secure proxy faults on lab's
primary Wi-Fi service, matched by hardware serial `configured-device`. The October 27 13:30 CT
scope update supersedes the earlier dedicated-service restriction; fleet clearance was
received at 13:31 CT. The serial, allowed faults, approval and clearance-file digest are
recorded in every real run-start and receipt.

Each trial saves the exact DNS, search-domain and proxy getter output, arms a detached
root launchd watchdog before injection, checks OOB before remote operations, verifies
all saved output after restoration, and proves a fresh normal Wi-Fi SSH connection.
Trials start at least two minutes after the preceding verified restoration and SSH
handback. A mismatch, failed rollback or OOB loss stops with no retry. Wi-Fi power, location, service order, AX88179B/en7, bridges, sshd, firewall,
VPN, keychain and MDM/profile changes remain excluded.

## Final qualifying Mac sequence

Source `705ee30`, pinned Glimmer model, acceptance oracle `uplink-online-v1`.
Each run restored DNS first, then proxy. All 12 model requests have their emitted text,
effective grammar, inputs and native timing data recorded. Every run retired exactly
one registered watchdog and confirmed zero remained. The next fault began more than
120 seconds after the preceding verified-restoration and SSH handback.

Time to ONLINE is bounded using the controller's elapsed clock: the preceding healthy
probe and the first failed probe bracket injection, and the first later completed ONLINE
probe ends the interval. Exact-restoration time is separately measured by the shared
root monotonic clock. These are network timings, not cloud-task resumption latency.

| Log trial | Fault / arm | Time to ONLINE | Exact restoration | Rollback / OOB / Wi-Fi SSH | Jobs after | Trace |
| --- | --- | ---: | ---: | --- | ---: | --- |
| 8 | DNS plus proxy / Glimmer | 25.86 to 29.12 s | 28.765 s | Pass / pass / pass | 0 | trace (private source artifact omitted) |
| 9 | DNS plus proxy / Glimmer | 24.32 to 27.64 s | 27.285 s | Pass / pass / pass | 0 | trace (private source artifact omitted) |
| 10 | DNS plus proxy / Glimmer | 23.56 to 26.74 s | 26.694 s | Pass / pass / pass | 0 | trace (private source artifact omitted) |

These three original traces are the replay material for the demo. From the source checkout,
use `python -m offline_agent.uplink.replay replay <trace-path> --speed 1` with one of the
full paths above. `--speed 0` validates and renders without delays. Historical reconstructed
prompts in DIAG-GLIMMER.md are not captured footage.

## Earlier macOS trials

The append-only handback is TRIAL-LOG.md (private source artifact omitted).

| Trial | Fault / purpose | Outcome | Exact restoration | OOB / Wi-Fi SSH | Trace |
| --- | --- | --- | --- | --- | --- |
| 1 | Proxy / initial expiry proof | Superseded timing evidence | Settings matched; elapsed field untrusted | Pass / pass | trace (private source artifact omitted) |
| Legacy label 2 (not a trial) | Proxy / killed-controller preparation | Refused before any fault | Not needed; no settings comparison performed | OOB pass; post-trial Wi-Fi check not performed | trace (private source artifact omitted) |
| 3 | Proxy / corrected expiry proof | Pass | 40.807 s; all getters match | Pass / pass | trace (private source artifact omitted) |
| 4 | Proxy / controller killed | Pass; controller exit -9 | 40.441 s; all getters match | Pass / pass | trace (private source artifact omitted) |
| 5 | DNS plus proxy / Glimmer | Network recovery passed; repeated for job-retirement evidence | 29.855 s; all getters match | Pass / pass | trace (private source artifact omitted) |
| 6 | DNS plus proxy / Glimmer | Network recovery passed; repeated for job-retirement evidence | 27.736 s; all getters match | Pass / pass | trace (private source artifact omitted) |
| 7 | DNS plus proxy / Glimmer | Network recovery passed; repeated for job-retirement evidence | 27.435 s; all getters match | Pass / pass | trace (private source artifact omitted) |

System Python 3.9.6 used a process-relative `time.monotonic()` origin. This made the
first elapsed-time field unreliable and incorrectly kept the next preparation in
cooldown. No second fault was injected. Source `a508ee6` uses the shared POSIX monotonic
clock, validates boot provenance, and gives legacy terminal records a fresh audited
120-second barrier without overwriting them. Both proofs were repeated successfully
on that source. The earlier log line and its correction are preserved.

The initial pre-clearance preparation `4687a98877c9` was refused before snapshot, active
state, launchd job or fault injection. Only three helper files were copied. It is not a
recovery or watchdog proof.

## Finished-job correction

The first three Glimmer recoveries passed the network oracle and exact settings checks,
but five completed launchd entries remained registered after the corrected watchdog
proofs and those recoveries. None was running at closeout. This did not satisfy the
added one-registered-job-per-trial condition, so those three runs are retained as recovery
evidence and excluded from the final procedural gate. The five jobs were audited and
unloaded at 15:19 CT; a fresh readback confirmed exact settings, protected services,
ONLINE, OOB and normal Wi-Fi SSH.

Source `705ee30` validates the entire set of terminal jobs before retiring any, refuses
arming when a prior registered job remains, verifies exactly one job after bootstrap,
and retires that job after each verified trial. It preserves root snapshots and audit
files. The final three-trial sequence uses this source. The log also now distinguishes
proven pre-fault refusals from attempted faults and anchors the quiet interval to the
verified restoration handback. Historical log lines remain unchanged; the refusal
correction was appended explicitly.

## Revised target-case Linux comparison

Source `bf243c3`, acceptance oracle `uplink-linux-lab-v1`, same fixture and action effects.
Glimmer used `set_dns`, `restore_proxy`, then both in order for the compound case. Every
case completed the fixture application handshake. All six runs have closed state, zero
owned namespaces, completed cleanup and unchanged host-network fingerprints.

The timing range brackets the injection between the recorded preceding healthy probe
and the first observed failure, using the same process's elapsed clock. The first later
ONLINE probe ends the interval. These are network-recovery bounds, not cloud-task latency.

| Fault | Arm | Recovery / application handshake | Time to ONLINE | Rollback verified | Trace |
| --- | --- | --- | ---: | --- | --- |
| `wrong_dns` | Glimmer | Pass / pass | 11.68 to 15.07 s | Yes | trace (private source artifact omitted) |
| `wrong_dns` | Scripted | Pass / pass | 1.28 to 4.69 s | Yes | trace (private source artifact omitted) |
| `stale_proxy` | Glimmer | Pass / pass | 12.23 to 13.39 s | Yes | trace (private source artifact omitted) |
| `stale_proxy` | Scripted | Pass / pass | 1.31 to 2.51 s | Yes | trace (private source artifact omitted) |
| `dns_plus_proxy` | Glimmer | Pass / pass | 24.35 to 27.73 s | Yes | trace (private source artifact omitted) |
| `dns_plus_proxy` | Scripted | Pass / pass | 2.80 to 6.19 s | Yes | trace (private source artifact omitted) |

## Original 24-case Linux development matrix

The model ran on lab, outside each faulted client namespace in the existing
`uplink-harness` Lima VM. The independent oracle is `uplink-linux-lab-v1`. Each accepted
application handshake required the fixture's DNS, certificate-validated TLS through the
configured proxy, captive checks, and application response. The fixture has no public
upstream and installs no CA into a host trust store.

| Arm | Cases | Fault recoveries | Healthy no-op completions | Verified application handshakes | Model requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Glimmer | 12 | 0 | 2 | 2 | 13 |
| Scripted baseline | 12 | 5 | 2 | 7 | 0 |

The baseline recovered DNS, proxy, the compound fault, upstream failure via the proven
alternate, and the MTU scenario via that alternate. Glimmer repeatedly selected interface
cycling, which did not improve the affected oracle and caused restoration or refusal.
Both arms handed off the captive case and made no network change for the site-only case.
Both healthy and misleading-status controls completed without agent mutations.

The first eleven cases used `4f52db4`; the remaining thirteen used `8d3a35c`, which added
an ONLINE-oracle refusal before any model-requested network mutation. The model prompt,
grammar, oracle and action policies were unchanged across those two revisions. A refused
case remained a recorded failure. After its closed-state and cleanup proof was checked,
the controller moved to unattempted cases; it did not rerun failed cases to fill the table.

These are development trials. Three preparatory wrong-DNS attempts precede this matrix:
one fixture startup refusal and two model timeouts. Prompt/context fixes were made before
the first tabled case. No held-out performance claim is made from these runs.

Time-to-online values below are approximate: VM injection-complete audit time to the
first later ONLINE trace observation, adjusted using the observed host/VM clock offset.
They include the initial failed probe, exclude fixture setup, and are not application
resumption latency. N/A means no credited fault recovery or no fault in a healthy control.
Rollback verification requires closed state, no owned namespaces, completed cleanup and
an unchanged host-network fingerprint.

| Fault | Arm | Outcome | Time to ONLINE | Rollback verified | Evidence |
| --- | --- | --- | ---: | --- | --- |
| `wrong_dns` | Glimmer | reverted | N/A | Yes | trace (private source artifact omitted) |
| `wrong_dns` | Scripted | recovered | ~4.1 s | Yes | trace (private source artifact omitted) |
| `stale_proxy` | Glimmer | reverted | N/A | Yes | trace (private source artifact omitted) |
| `stale_proxy` | Scripted | recovered | ~1.9 s | Yes | trace (private source artifact omitted) |
| `dns_plus_proxy` | Glimmer | reverted | N/A | Yes | trace (private source artifact omitted) |
| `dns_plus_proxy` | Scripted | recovered | ~5.6 s | Yes | trace (private source artifact omitted) |
| `lease_loss` | Glimmer | reverted | N/A | Yes | trace (private source artifact omitted) |
| `lease_loss` | Scripted | reported | N/A | Yes | trace (private source artifact omitted) |
| `bad_default_route` | Glimmer | reverted | N/A | Yes | trace (private source artifact omitted) |
| `bad_default_route` | Scripted | reported | N/A | Yes | trace (private source artifact omitted) |
| `interface_down` | Glimmer | repeated action refused | N/A | Yes | trace (private source artifact omitted) |
| `interface_down` | Scripted | reported | N/A | Yes | trace (private source artifact omitted) |
| `captive_portal` | Glimmer | reported | N/A | Yes | trace (private source artifact omitted) |
| `captive_portal` | Scripted | reported | N/A | Yes | trace (private source artifact omitted) |
| `isp_down_saved_alternate` | Glimmer | watchdog restored; no credit | N/A | Yes | trace (private source artifact omitted) |
| `isp_down_saved_alternate` | Scripted | recovered | ~10.1 s | Yes | trace (private source artifact omitted) |
| `healthy` | Glimmer | healthy no-op | N/A | Yes | trace (private source artifact omitted) |
| `healthy` | Scripted | healthy no-op | N/A | Yes | trace (private source artifact omitted) |
| `site_only_outage` | Glimmer | site_report_required | N/A | Yes | trace (private source artifact omitted) |
| `site_only_outage` | Scripted | site_report_required | N/A | Yes | trace (private source artifact omitted) |
| `mtu_black_hole` | Glimmer | watchdog restored; no credit | N/A | Yes | trace (private source artifact omitted) |
| `mtu_black_hole` | Scripted | recovered | ~12.5 s | Yes | trace (private source artifact omitted) |
| `misleading_status` | Glimmer | healthy no-op | N/A | Yes | trace (private source artifact omitted) |
| `misleading_status` | Scripted | healthy no-op | N/A | Yes | trace (private source artifact omitted) |


## Failure evidence and implementation fixes

DIAG-GLIMMER.md (private source artifact omitted)
contains three reconstructed historical prompts and exact captured replies. Their grammar
hashes match the original traces. The historical HTTP requests were not captured, and the
report states reconstruction limits. The original action guide listed `cycle_interface`
first alphabetically; no layer diagnosis constrained it.

The current policy emits a layer diagnosis first, then selects an action using a grammar
restricted to that layer. Interface cycling requires observed link failure and is absent
from the approved Mac DNS/proxy profile. Fixed read-only probes separate link, IP, DNS,
proxy and captive evidence. Alternate DNS and direct TLS are diagnostic comparisons only;
they cannot grant ONLINE or bypass the application's proxy requirement.

The first two-stage development attempt correctly chose DNS restoration but repeated
evidence IDs. The existing validator refused it before any repair. Source `bf243c3`
restricts each action to one decisive citation without deduplicating output or relaxing
the validator. Two subsequent staging attempts were refused before fixture creation
because a diagnostic import created bytecode inside an immutable worker bundle. The two
generated cache files were retained outside the bundle, all source hashes matched, and
later diagnostics use `python3 -B`. Staging now preserves known value-blind refusal causes.

All 14 requests in the successful revised Linux comparison have recorded emissions,
inputs, effective grammars and native timings. The initial rejected two-stage attempt
also retains both outputs. In the original matrix, three replies were absent after a
later guard rejected them: the second interface-down action, upstream-alternate timeout,
and MTU timeout. Those original traces remain incomplete at that point. Source `0c326ec`
fixes future recording; missing historical output was not fabricated.

Other preparation fixes cover pinned server attestation, bounded receipt clock skew,
AppArmor-compatible proxy setup, compact model context, healthy-network mutation refusal,
preservation of unrelated DNS keys, exact fleet comparisons, post-trial SSH checks,
request-count uncertainty and durable trial handback. The independent acceptance oracle
and frozen coding corpus/scoring were not weakened.

## Model and verification

Muse Glimmer 30B, `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, from
`meta-models/Muse-Glimmer-30B-GGUF`, SHA-256
`4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`.
Serving build: llama.cpp `10353 (f8def7fe1)` on Apple M4 Max, localhost:8080,
Serving configuration: one slot, context 8192, seed 0, temperature 0 and eager per-request GBNF; base Muse Glimmer weights were unchanged.

Source `705ee30` passed 983 full-suite tests, 477 Uplink tests, lint and strict
typechecking on lab. The final hardening step added 33 tests, matching the expected
delta from 950 to 983. The checks were `make lint`, `make typecheck`, `make test`,
and the focused `pytest tests/uplink -q`, using the existing lab Python environment.
The native bridge compiled and passed its self-test with zero network reads or writes.
Cross-process checks against the installed system Python confirmed a shared clock and
matching boot identity. All heavy verification runs after the workstation memory warning
ran on lab. The old workstation verification clone was removed at explicit direction.

Final readback at 15:27 CT confirmed all eight saved getter outputs still match,
protected services are unchanged, the Wi-Fi oracle is ONLINE, both SSH paths work,
and no Uplink launchd job remains registered. The owned model server was stopped at
15:27:51 CT after matching PID and birth time; localhost port 8080 has no listener.
All 47 captured traces validate, replay without errors and pass the privacy and secret
scans. The unchanged release scanner retains three previously classified historical
findings: synthetic test-URL blob `3ea45154f03f` and two report-only wording blobs
`fa319ddb974c` and `4da33089b15c`. The automated release gate is not represented as
cleared. Current-file, license and independent clean-clone results are recorded in the
release receipt below. No public publication,
PR, merge, deployment, database write, external post, identity change, credential creation
or history rewrite is part of this lane. The unrelated `REPORT-F10.md` edit is preserved.

## Evidence locations

- Original matrix summary (private source artifact omitted)
- Revised comparison (private source artifact omitted)
- Original Linux cleanup audits (private source artifact omitted)
- Original transfer manifest (private source artifact omitted)
- Clock diagnosis (private source artifact omitted)
- Corrected runtime clock proof (private source artifact omitted)
- Model server provenance (private source artifact omitted)

- Qualifying three-run summary (private source artifact omitted)
- Final host readback (private source artifact omitted)
- Job and server cleanup (private source artifact omitted)
- Final model-server provenance (private source artifact omitted)
- Trace replay/privacy validation (private source artifact omitted)
- Release scan and clean-clone receipt (private source artifact omitted)
