# F05 wrong gateway: final evidence tally

BLUF: **FINAL at 2/2 model-reached PASS, median recovery 13.0595 s, false ONLINE 0. The target was 5; only 2 trials reached the model.** No continuation or new trial follows this finalization. Model: BASE Meta Muse Glimmer 30B, unmodified open weights.

Small n: two observed successes do not establish general field reliability. Both passing runs selected restore_gateway and were strict-reader VALIDATED. Timings include the preregistered warm-ups and are not cold-start results. Trial 3 is an uncounted pre-model ABORT; the five earlier pre-model aborts remain disclosed and excluded.

| Series | Run | Population | Original model reached / requests | Outcome | Recovery seconds | Strict reader | Denominator |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| 372d7304785e | bd4685c92ffc | prior_pre_model | false / 0 | ABORT | n/a | REFUSED | Excluded, pre-model |
| 07feb82455e7 | d93397b1a7f5 | prior_pre_model | unknown / unknown | ABORT | n/a | REFUSED | Excluded, zero-model terminal closure |
| 2fb95641a8b5 | d06d739b2d5b | prior_pre_model | false / 0 | ABORT | n/a | REFUSED | Excluded, pre-model |
| 46b49c0793cc | eb5066f07b6b | prior_pre_model | false / 0 | ABORT | n/a | REFUSED | Excluded, pre-model |
| 61ab0007254a | 511da249465d | prior_pre_model | false / 0 | ABORT | n/a | REFUSED | Excluded, pre-model |
| cd4fea26e41a | 2227959ce71d | final_series | true / 1 | PASS | 13.941 | VALIDATED | Counted |
| cd4fea26e41a | 8a36250baf8f | final_series | true / 1 | PASS | 12.178 | VALIDATED | Counted |
| cd4fea26e41a | 21f5d24f0824 | final_series | false / 0 | ABORT | n/a | REFUSED | Excluded, pre-model |

Series 07feb82455e7 preserves its original null model counters. Its retained terminal closure proves zero model requests and no target contact; the unknown fields are not silently rewritten. Every confirmed model-reached trial remains counted once. The two validated recovery times are 13.941 s and 12.178 s; their median is 13.0595 s.

The final series stopped on trial 3 with f05_unowned_route_change, before a model request. Immediate cleanup could not prove exact restoration and remained restore_failed. Later read-only comparisons prove an empty exact semantic baseline diff without a repair; they do not rewrite the original ABORT or prove historical bounded handback. The exact rejected transient route was not retained. No continuation is prepared.

Operator-video capture for the second PASS and final ABORT is CAPTURE_INCOMPLETE; this is disclosed separately from the mandatory terminal/strict-reader evidence. No clip is represented as uninterrupted or newly reviewed. Video and terminal media files are not committed; their redacted-root paths and original hashes are retained below and in the machine-readable indexes.

Public derivative: local storage roots and private host/address fields are redacted using the existing export rules. Video filenames and SHA-256 values are retained; the source archive retains exact local paths. Embedded historical hashes identify private original records, not the redacted bytes. No media is distributed, and no outcome is regraded.

## Operator-video paths and SHA-256

| Series | Run | Original video path | SHA-256 | Capture status |
| --- | --- | --- | --- | --- |
| 372d7304785e | bd4685c92ffc | `PRIVATE_HOME/uplink-branchout-runs/f05-series-372d7304785e/attempts/01-bd4685c92ffc/operator-video-001.mov` | `560132da95a4dbe91c03ba749a20eb8209c8b66f937be859a9fb3ea41cef2210` | RECORDED |
| 372d7304785e | bd4685c92ffc | `PRIVATE_HOME/uplink-branchout-runs/f05-series-372d7304785e/attempts/01-bd4685c92ffc/operator-video-002.mov` | `a1ee4e829ac4d4a8fd60821301dbf3462efefcb41b6d4409f8a9e60ab5bf1483` | RECORDED |
| 372d7304785e | bd4685c92ffc | `PRIVATE_HOME/uplink-branchout-runs/f05-series-372d7304785e/attempts/01-bd4685c92ffc/operator-video-003.mov` | `eccc7e07c2e73c75537193acaa131614aef3c4039405f229bb12d5d0e40aca50` | RECORDED |
| 372d7304785e | bd4685c92ffc | `PRIVATE_HOME/uplink-branchout-runs/f05-series-372d7304785e/attempts/01-bd4685c92ffc/operator-video-004.mov` | `2472e491278f71aadca0373c1842cdd60e0d7043d63098ac50c0445fddd0bc65` | RECORDED |
| 07feb82455e7 | d93397b1a7f5 | `PRIVATE_HOME/uplink-branchout-runs/f05-series-07feb82455e7/attempts/01-d93397b1a7f5/operator-video-001.mov` | `851014734490a0103067a60cac5fded1b03e3831a475f1db5993394070c81d77` | RECORDED |
| 2fb95641a8b5 | d06d739b2d5b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-2fb95641a8b5/attempts/01-d06d739b2d5b/operator-video-001.mov` | `507aa9e326701364b22d778346d9d543b8777690ac1ba5d6cb0f70d3812fa184` | RECORDED |
| 2fb95641a8b5 | d06d739b2d5b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-2fb95641a8b5/attempts/01-d06d739b2d5b/operator-video-002.mov` | `f85944f1b2f42f65b0019d0a3ed8232ea1b175751fdefafee92ccb34bb570642` | RECORDED |
| 2fb95641a8b5 | d06d739b2d5b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-2fb95641a8b5/attempts/01-d06d739b2d5b/operator-video-003.mov` | `97d8a13af3f215a665d359495456f2cf440540fec9e06c82ebb0939ed83076b9` | RECORDED |
| 2fb95641a8b5 | d06d739b2d5b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-2fb95641a8b5/attempts/01-d06d739b2d5b/operator-video-004.mov` | `73ce719242025afca1da40041c594140c57a9e90a4361f281c132770021c06a0` | RECORDED |
| 61ab0007254a | 511da249465d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-61ab0007254a/attempts/01-511da249465d/operator-video-001.mov` | `febdf08053dfb7a23903a056ced69fed062379150148362b6167ddfb064ae340` | RECORDED |
| 61ab0007254a | 511da249465d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-61ab0007254a/attempts/01-511da249465d/operator-video-002.mov` | `7c949aefc1c8738df9f9b1ecf31873da3e2131028440af19ed4722b76177c125` | RECORDED |
| 61ab0007254a | 511da249465d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-61ab0007254a/attempts/01-511da249465d/operator-video-003.mov` | `90d12eee77ce41d992169a3bf206e400dd99930ac10b170ab1ee1a3f949d2163` | RECORDED |
| 61ab0007254a | 511da249465d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-61ab0007254a/attempts/01-511da249465d/operator-video-004.mov` | `b67ac46d5019f21c8bc7d0b76f6490543572b3cd6e42802c98b5b7f0e8341559` | RECORDED |
| cd4fea26e41a | 2227959ce71d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/01-2227959ce71d/operator-video-001.mov` | `434b751ab9174843c55a516e5aeeebcf459bb6b70fc1c417932960a2e3fa2ce6` | RECORDED |
| cd4fea26e41a | 2227959ce71d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/01-2227959ce71d/operator-video-002.mov` | `9a55e76032d1e48bc495182af9c8bdfab1f9ff81d1229ea8f9ba17242d840152` | RECORDED |
| cd4fea26e41a | 2227959ce71d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/01-2227959ce71d/operator-video-003.mov` | `c7d3a1d60e2b1f387de90b8c48d2a75ec4b977344a72dca423e158db2d644c51` | RECORDED |
| cd4fea26e41a | 2227959ce71d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/01-2227959ce71d/operator-video-004.mov` | `a060f1f0378997d5636e78f634eaf6c56a4d1608389d30b28a25be7e690b3641` | RECORDED |
| cd4fea26e41a | 8a36250baf8f | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/02-8a36250baf8f/operator-video-001.mov` | `1b0847c6f4a6d0953344f3320ddc9c097dfb8a6ce31d63b48f8eb31881aa18f1` | CAPTURE_INCOMPLETE |
| cd4fea26e41a | 8a36250baf8f | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/02-8a36250baf8f/operator-video-002.mov` | `2b2d11cb1d6eaf19166b03f7cb38d574f5a9c30300f57c8c22584701214a32ed` | CAPTURE_INCOMPLETE |
| cd4fea26e41a | 21f5d24f0824 | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/03-21f5d24f0824/operator-video-001.mov` | `1dfc58ff28b0dcd188d03c008544c0c71a42f06afb7fb4ca62a613ca9655e415` | CAPTURE_INCOMPLETE |
| cd4fea26e41a | 21f5d24f0824 | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/03-21f5d24f0824/operator-video-002.mov` | `6e20f6060f33309f1780e6ffc97f484cf926a8144f522a8cda15a4da2cdca3be` | CAPTURE_INCOMPLETE |

## Terminal recording paths and SHA-256

| Series | Run | Original recording path | SHA-256 |
| --- | --- | --- | --- |
| 372d7304785e | bd4685c92ffc | `PRIVATE_HOME/uplink-branchout-runs/f05-series-372d7304785e/attempts/01-bd4685c92ffc/terminal.typescript` | `55e84b1f803e6bdf18dc21439defe8ec656db2c0f3a545eb037249995fcec850` |
| 07feb82455e7 | d93397b1a7f5 | `PRIVATE_HOME/uplink-branchout-runs/f05-series-07feb82455e7/attempts/01-d93397b1a7f5/terminal.typescript` | `1796e05ea3162639e8b7d5a5b013c6f3dbc84066f8d100d49e8307552704c2a4` |
| 2fb95641a8b5 | d06d739b2d5b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-2fb95641a8b5/attempts/01-d06d739b2d5b/terminal.typescript` | `18976e927e79b96720ce7699fe48a711d9f0902e2273660c1c77070770a62e51` |
| 46b49c0793cc | eb5066f07b6b | `PRIVATE_HOME/uplink-branchout-runs/f05-series-46b49c0793cc/attempts/01-eb5066f07b6b/terminal.typescript` | `123b3954a772f9780005400ef392abff49dda71ac7ccee96447c257a8c82cd6a` |
| 61ab0007254a | 511da249465d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-61ab0007254a/attempts/01-511da249465d/terminal.typescript` | `22b6c6d1bfae5a6e5fff13b04eeaf1e15da00c032ab851d549babd4b9d8d3607` |
| cd4fea26e41a | 2227959ce71d | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/01-2227959ce71d/terminal.typescript` | `08c4636a071995f0d8a0bb758209b95b102deb5649325ed19a00a4444336a5f4` |
| cd4fea26e41a | 8a36250baf8f | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/02-8a36250baf8f/terminal.typescript` | `2360662339f207c653ba5d531b5c361ae15585fc2a4209b830a3d9f48546adcd` |
| cd4fea26e41a | 21f5d24f0824 | `PRIVATE_HOME/uplink-branchout-runs/f05-series-cd4fea26e41a/attempts/03-21f5d24f0824/terminal.typescript` | `e6cc72dd95e4a621aa1c94a3f21bba1191d938194aade624c93d77cb348d89ba` |

[Combined ledger](SERIES-LEDGER.json), [summary](SUMMARY.json), [video index](VIDEO-INDEX.json), [recording index](RECORDING-INDEX.json), [original and copied hashes](F05-EVIDENCE-MANIFEST.json), and [trial-3 closeout](cd4fea26e41a/TERMINAL-CLOSURE-TRIAL-03.json). Each series directory retains its original ledgers, summary, preregistration and available strict/closeout records.

This is an evidence/documentation closeout. No new trial, model call, host contact, repair, watchdog action, capture, media playback, upload, push or publication is claimed. Hash verification does not upgrade incomplete capture or a refused strict verdict.
