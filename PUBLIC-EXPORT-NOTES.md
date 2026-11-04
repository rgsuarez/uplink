# Base Muse Glimmer submission candidate

This local candidate starts from export commit
`c3686ffa5b89790892cecaa2f8cfa647d8a9a696`. Submission documents and committed
F04 DNS evidence were projected from source commit
`952dbf59048c51999fe6fd46d29efc89e1b7e8db`. The narrative and checklist now use
`c739e9c97b18f45241cf630fd303662933fdee20`. Controls come from committed source
`d8b390a773c455dcc3686a4a55b79bcb628224eb`; F06 comes from
`c3dd1adcd7519aa099ef30af678492ddeeca2d51`. There is no configured remote.
The publication candidate is a fresh local repository containing exactly this
export tree, with one initial commit and no inherited history.

## Model and evidence scope

The submission uses only unmodified base Muse Glimmer 30B Q4_K_M on-device,
through llama.cpp (GGUF runtime), using base, unmodified open weights.
Weights and native binaries are not bundled. Legacy model experiments, launch
paths, comparisons and model cards are omitted. Only the base serving identity is
accepted, with positive evidence of an empty model-modification set. Original
native tokenizer metadata stays byte-for-byte unchanged; its upstream special
token vocabulary is not evidence that a model was modified.

The committed F04 series contains 10/10 model-reached recoveries, median 15.319 s,
and zero observed false ONLINE, across three mixed-policy segments. Four attempts
stopped before a model request and remain outside the denominator. Three unscored
warm-ups per attempt mean warm timings. The earlier recorded baseline and earlier
receipt-only trial are separate, not pooled.

F06 stale proxy passed 5/5 model-reached trials, median 10.395 s, false ONLINE 0,
across two segments with entry limits of 24.5 then 24.75 GiB. Six pre-model
headroom refusals are excluded. Controls passed 0/6: network safety held (zero
network changes, false-action requests and false ONLINE), but every health report
failed the required verified-reason check. Five pre-model refusals are excluded.
These denominators remain separate. F05 is final at 2/2 model-reached PASS, median 13.0595 s, false ONLINE 0. Target 5; only 2 reached. Six pre-model ABORTs are disclosed and excluded. Small n; no continuation. The local F05 evidence addition is publication pending.

Site-only caveat: small n; 1/3 model-reached correct-report PASS, with both earlier ABORTs counted. The fresh run e3cbf14d34b4 chose report for an unavailable application on a healthy network, made zero network/system mutations and was strict-reader VALIDATED. False ONLINE 0; median recovery n/a because reporting is the correct action. This was an owned local 503 stub, not a real outage. One zero-request pre-model refusal/ABORT is excluded. Capture is CAPTURE_INCOMPLETE (viewport changed); the trial was unaffected and videos are not committed. Source commit: 5562b1a99bf0fcdaf6d6c93d9a99a3084ba113b7. [Public-safe tally](evidence/siteonly-series/SITEONLY-COMBINED-TALLY.md), [ledger](evidence/siteonly-series/SERIES-LEDGER.json) and [source hashes](evidence/siteonly-series/SITEONLY-EVIDENCE-MANIFEST.json) are local additions pending publication; raw runtime, approval and media files are omitted.

F01 evidence from committed source 3ba3d74fd79ed615f367199d14e8546d59c97b36 is now
projected into the local candidate: 1/5 model-reached in the specified continuation,
one strict recovery at 11.213 s, false ONLINE 0; all listed series 1/6. Small n;
radio-off is the hardest measured live repair segment. Four pre-model stops are
excluded and listed in [the public-safe tally](evidence/f01-series/F01-COMBINED-TALLY.md).
This local addition is publication pending; raw recordings and approvals are omitted.
The final cut and video URL are not verified.

## Public derivatives and hashes

Privacy projection replaces private host labels with neutral aliases and removes
private recording paths, device coordinates, provider-account identifiers and home
paths. Original recordings, authority records, private configuration and bundles
are not distributed. The manifest records original committed source hashes and
separate public derivative hashes for each retained or generated file. Original
registration, ledger-chain, row, receipt, recording and runtime hashes are retained
as provenance; they do not validate sanitized bytes. Public ledgers support
recomputation of counts and timings, not revalidation of omitted raw recordings or
execution authority. No historical result is rescored using changed runtime code.

## CPU checks

The previous CPU qualification belongs to the earlier export revision. Its counts
do not qualify this base-only filter: legacy experiment code and corresponding
checks have been removed, and native template verification has been retained
without changing the upstream assets or verification rules. Current CPU checks
are recorded in the manifest: 1,486 collected, 1,464 passed, 22 explicit skips,
zero failures and zero errors. The 224 sandbox-required cases were run separately
under the existing OS network-denial profile; they are counted once in that total.
The separate FAKE_KERNEL smoke passed 21/21 synthetic repairs, zero false ONLINE,
with nine held slots, no model calls and no host contacts. These checks exclude native
host execution, GPU execution and private-package reproduction. The ledger
recomputation in README uses only Python's standard library and requires no model,
network, credentials or original private recordings. Model inference, GPU execution
and Linux execution remain unqualified.

Runtime tests use the existing CPU guard to refuse network sockets, host-control
tools and real model weights. The fake-kernel smoke is synthetic, earns no live
recovery credit and does not run Glimmer. No new live trial is authorized here.

## Remaining owner gates

At the Wednesday November 4, 11:00 AM CT testing freeze, replace PENDING rows only
from committed ledgers or omit unrun rows. Set the final video URL and verify the
opening provenance line, narration and captions against the final results. Push,
publication, repository visibility changes, uploads, optional social posts and
submission require separate owner approval. No such action occurred in this export.
