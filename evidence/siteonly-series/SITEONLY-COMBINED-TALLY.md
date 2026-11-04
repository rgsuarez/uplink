# Site-only app server down: public evidence tally

BLUF: **1/3 model-reached correct-report PASS, false ONLINE 0; median recovery n/a.** Final series f9f29057920d is COMPLETED with stop reason THREE_MODEL_REACHED. Model: BASE Meta Muse Glimmer 30B, unmodified open weights.

Site-only caveat: small n; 1/3 model-reached correct-report PASS, with both earlier ABORTs counted. The fresh run e3cbf14d34b4 chose report for an unavailable application on a healthy network, made zero network/system mutations and was strict-reader VALIDATED. False ONLINE 0; median recovery n/a because reporting is the correct action. This was an owned local 503 stub, not a real outage. One zero-request pre-model refusal/ABORT is excluded. Capture is CAPTURE_INCOMPLETE (viewport changed); the trial was unaffected and videos are not committed.

| Series | Run | Model reached / requests | Outcome | Action | Strict reader | False ONLINE |
| --- | --- | --- | --- | --- | --- | ---: |
| 4b893e2e8b7d | a49b8c17c262 | false / 0 | ABORT | none recorded | REFUSED | 0 |
| 9042643b6fb5 | 09e573d85f51 | true / 1 | ABORT | none recorded | REFUSED | 0 |
| 9e7b815aafa0 | 8d4bce963a74 | true / 1 | ABORT | none recorded | REFUSED | 0 |
| f9f29057920d | e3cbf14d34b4 | true / 1 | PASS | report | VALIDATED | 0 |

Both earlier model-reached ABORTs remain failures in the denominator. The zero-request pre-model ABORT is excluded. No recovery or general reliability estimate is inferred.

[Public ledger](SERIES-LEDGER.json); [source and derivative hashes](SITEONLY-EVIDENCE-MANIFEST.json). Source commit: 5562b1a99bf0fcdaf6d6c93d9a99a3084ba113b7. Original source files and videos are not distributed. No new trial, model call, capture or media verification is claimed.
