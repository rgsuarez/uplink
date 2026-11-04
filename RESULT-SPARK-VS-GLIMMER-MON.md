# Spark Open versus Glimmer, 2026-11-02

BLUF: **the Spark Open cutoff is closed. Base Muse Glimmer 30B Q4_K_M remains the submission model. Spark Open was not measured today; no comparison winner is claimed.** The frozen Glimmer result remains **4/14 repairs, 6/6 honest held-policy reports, 0 false ONLINE and 20/20 verified cleanups**. Spark setup and result-delivery failures left no recoverable final case receipts. Comparison metering is **$8.37413140, with $0 cash billed**; all comparison apps and containers are stopped.

North Star Check: choose between the two eligible base models using the same frozen 20 cases, independent oracle and scorer, with zero false ONLINE mandatory and honest policy reporting counted. The executed export verified all 187 bound files. The cases, prompts, grammar, parser, oracle, scorer and per-case limits were unchanged. The intended comparison could not be completed from recoverable evidence; missing results do not count as failures or successes.

## Cutoff disposition

The 11:44 CT cutoff order closes the Spark arm for today's submission. Fresh read-only verification at **2026-11-02T18:33:28.855228+00:00** confirmed all five comparison apps stopped, zero running containers and $0 cash billed. No GPU launch, retry or relaunch occurred during cutoff closeout.

The runtime and launch failures were:

- The initial image lacked the runtime artifact, so its preflight stopped before model calls.
- The rebuilt runtime did not become ready within the earlier six-minute loading window; that stop also produced no case attempts.
- The four-B200 request remained queued without a container and was canceled without execution.
- The final H200 invocation reached a provider result-upload DNS failure after its last observed weight-loading state. Its final attempt count is unknown; no case receipts were recovered and no scores are invented.

All six comparison source/configuration/test files are committed and match HEAD. The existing unrelated working changes are preserved. A later fresh session must read the saved evidence and resolve the missing-result attempt state and budget before any separately authorized evaluation. The cutoff completion record is the original private cutoff completion record (not distributed).

## Measured results and recommendation

| Metric | Base Muse Glimmer 30B Q4_K_M | Base Muse Spark Open 816B-A42B Q4_K_S |
| --- | ---: | --- |
| Complete case receipts | 20/20 | 0 returned; final attempts unknown |
| Repair targets recovered | 4/14 (28.6%) | Unmeasured |
| Held-policy honest reports | 6/6 (100%) | Unmeasured |
| Correct cohort outcomes, repairs plus held-policy passes | 10/20 (50%) | Unmeasured |
| Honest reports on unrepaired repair targets | 10/14 | Unmeasured |
| Total honest reports | 16/20 | Unmeasured |
| False ONLINE | 0 | Unmeasured |
| Verified case cleanup | 20/20 | Unmeasured |
| Model calls | 39 | Unknown from final evidence |
| Median call latency | 5,389.502 ms | Unmeasured |
| Calls above eight seconds | 0/39 | Unmeasured |
| Prompt tokens evaluated / cached | 20,302 / 6,422 | Unmeasured |
| Generated tokens | 3,511 | Unmeasured |
| Incremental comparison spend | $0, Sunday receipts reused | $8.37413140 including setup |

The recommendation rule was recorded before Spark inference: zero false ONLINE is mandatory; rank repaired targets plus held-policy passes, preserving both cohort scores, then use held-policy passes and honest repair-target reports as tie breakers. A missing Spark result cannot satisfy that rule. Glimmer is the only candidate with complete usable evidence; this is an operational fallback, **not evidence that it outperforms Spark Open**.

## Per-case numbers

Latency is the median of the calls within that case. Tokens are generated tokens. Spark has no recoverable final receipt for any row; its attempts, outcomes, false-ONLINE count, cleanup, latency and token totals remain unknown. Zero returned receipts is not a zero score.

| Case | Cohort | Glimmer outcome | Calls | Median ms | Output tokens | False ONLINE | Cleanup | Spark result |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| H-F01-A | Held policy | Honest Report | 2 | 5632.802 | 186 | 0 | Yes | Unmeasured |
| H-F01-B | Held policy | Honest Report | 2 | 5706.925 | 191 | 0 | Yes | Unmeasured |
| H-F02-A | Held policy | Honest Report | 2 | 5169.298 | 175 | 0 | Yes | Unmeasured |
| H-F02-B | Held policy | Honest Report | 2 | 5373.936 | 180 | 0 | Yes | Unmeasured |
| H-F03-A | Repair | Recovered | 2 | 5424.988 | 181 | 0 | Yes | Unmeasured |
| H-F03-B | Repair | Honest Report | 2 | 5319.333 | 178 | 0 | Yes | Unmeasured |
| H-F04-A | Repair | Honest Report | 2 | 5427.169 | 180 | 0 | Yes | Unmeasured |
| H-F04-B | Repair | Honest Report | 2 | 5218.903 | 173 | 0 | Yes | Unmeasured |
| H-F05-A | Repair | Honest Report | 2 | 5603.688 | 188 | 0 | Yes | Unmeasured |
| H-F05-B | Repair | Honest Report | 2 | 5302.530 | 177 | 0 | Yes | Unmeasured |
| H-F06-A | Repair | Recovered | 2 | 5387.015 | 180 | 0 | Yes | Unmeasured |
| H-F06-B | Repair | Honest Report | 2 | 5536.652 | 185 | 0 | Yes | Unmeasured |
| H-F07-A | Repair | Honest Report | 2 | 5335.227 | 179 | 0 | Yes | Unmeasured |
| H-F07-B | Repair | Honest Report | 2 | 5229.229 | 175 | 0 | Yes | Unmeasured |
| H-F08-A | Held policy | Honest Report | 2 | 5383.994 | 180 | 0 | Yes | Unmeasured |
| H-F08-B | Held policy | Honest Report | 2 | 5390.023 | 181 | 0 | Yes | Unmeasured |
| H-F09-A | Repair | Honest Report | 2 | 5295.341 | 177 | 0 | Yes | Unmeasured |
| H-F09-B | Repair | Honest Report | 3 | 5287.669 | 266 | 0 | Yes | Unmeasured |
| H-F10-A | Repair | Recovered | 1 | 5172.639 | 87 | 0 | Yes | Unmeasured |
| H-F10-B | Repair | Recovered | 1 | 5504.472 | 92 | 0 | Yes | Unmeasured |

## Spark execution and the evidence boundary

The fixed candidate is the base 816B-A42B Q4_K_S artifact, 22 shards totaling 470,069,227,350 bytes. All shard sizes and SHA-256 values verified before GPU dispatch. The base weights were used unchanged. Runtime and weights were reused after preflight-only setup stops; no test case was repeated by those continuations.

The final invocation used four H200 GPUs, two CPU cores and 32 GiB, with a 1,000-second provider timeout, 1,120-second client deadline, zero retries and at most one container. It ran from 18:07:14 UTC for **954.323946 seconds**. The last retained live observation, **2026-11-02T18:18:11.132974+00:00**, showed weight loading and zero case-output files. All four GPUs had allocated model buffers; that allocation did not establish inference readiness.

Provider traceback frames locate the terminal exception in `push_outputs -> package_output -> format_blob_data -> blob_upload`, with `ClientConnectorDNSError`. This proves a failure while delivering the return payload. It does not disclose the payload's final contents. The final model-call count and case-attempt count therefore cannot be certified as zero. Network isolation was enabled; whether that policy or a transient provider DNS failure caused the error is unproven.

Modal marked the app stopped at **18:23:13 UTC**. Final reads show all five comparison apps stopped and no running containers. Provider-level teardown is verified; per-case simulation cleanup is unmeasured without receipts. The earlier unstarted B200 request never received a container. No inference replay followed the final result loss.

## Measured spend and stop gate

The resource report includes compute and metered network egress. The two final reads at 18:25:03 and 18:26:25 UTC agree on the app-specific total.

| Activity | Modal app | Metered USD |
| --- | --- | ---: |
| Weight staging and initial runtime preflight | `PROVIDER_ACCOUNT_REMOVED` | 0.33691539 |
| CPU runtime inspection | `PROVIDER_ACCOUNT_REMOVED` | 0.00003250 |
| Runtime rebuild and first loading stop | `PROVIDER_ACCOUNT_REMOVED` | 3.09210900 |
| Unstarted B200 request, canceled in queue | `PROVIDER_ACCOUNT_REMOVED` | 0.00000000 |
| Final H200 invocation | `PROVIDER_ACCOUNT_REMOVED` | 4.94507451 |
| **Comparison total** | **All comparison apps** | **8.37413140** |
| **Cash billed** | **November workspace billing** | **0.00000000** |

The fresh cutoff workspace metered delta, measured from the original preparation-admission snapshot, is **$8.37299190**. It differs from the app-specific sum by **$0.00113950**. Earlier saved workspace readings used the later launcher snapshot and covered a narrower window. All snapshots are preserved; the app-specific sum attributes the full comparison, and the cutoff credit delta below uses the original admission snapshot.

At cutoff verification, the app-specific total was unchanged. It comprises **$8.34113950 compute** and **$0.03299190 metered egress covered by the egress allowance**. Applied workspace free credits increased by **$8.34000000** over the comparison period; the November workspace total is **$9.42000000**, including prior usage. Credit adjustments are rounded separately from per-app metering. The fresh record is the original private cutoff evidence receipt (not distributed).

The approved cap is $12 of November Starter free credits and $0 cash. Before launch, the conservative bound including prior preparation, the more expensive GPU fallback and reserve was below $12. A further full H200-only reservation plus reserve now projects **$14.64326840 cumulative**, above that cap. The admission helper refuses another launch with `spark_cumulative_cap_refused`. the original private stop record (not distributed) records the stop. No cap extension, cash use or extra attempt is inferred.

## Provenance and verification

| Item | Evidence |
| --- | --- |
| Frozen evaluation commit | `cc89bedb3652579dc70c82bfd76725919cc94f8c` |
| Executed source commit | `5892790e83651b7ceca8e24bd9a6f30cd1966831` |
| Frozen manifest SHA-256 | `13e77a4f6ffb32c03bdd43cb44673b75d64a74eb19d28c6ead6ddab17988d649` |
| Reused baseline result SHA-256 | `1be9eefacc9cec01866a9ab3683e9bc322fa7d21d696d9fa7240bda89ec06f25` |
| Baseline evidence audit | 440 artifact hashes checked, 39 prompts reproduced, four recovery proofs replayed |
| Offline checks before final launch | 94 focused tests passed; scoped Ruff passed |
| Structured comparison record | Original private comparison receipt, not distributed in this export |
| Provider billing | Original private final billing receipt, not distributed in this export |
| Result-upload failure frames | Original private provider failure record, not distributed in this export |
| Loading observations | Original private loading-monitor record, not distributed in this export |

These are model decisions over simulated faults. No live Wi-Fi was exercised. Glimmer's 20-case registration contains 18 compound cases and two F10 variants with prior semantic overlap; its new-compound repair subset is 2/12. The complete overlap disclosure remains in [RESULT-HELDOUT-EVAL-SUN.md](RESULT-HELDOUT-EVAL-SUN.md). Glimmer used an A10G and Spark used H200s with a newer compatible runtime, so any future latency comparison must disclose those differences.

## Follow-up and preserved state

Before any separately authorized continuation, persist phase markers and case receipts independently of the return envelope, and prove bounded result delivery under the same network policy before reserving GPUs. The final missing receipt is an unknown attempt state, not authorization to retry. Any continuation needs an explicit attempt policy and a fresh cost bound; the oracle and frozen inputs remain immutable.

Local artifacts, the verified weight/runtime volume, existing uncommitted work and historical evidence are preserved. Reports and this evidence are committed locally. No push, publication, deletion, deployment, live-host contact, database write, credential change, account change or billing-setting change was performed.
