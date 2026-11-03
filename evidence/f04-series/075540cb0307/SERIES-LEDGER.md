# F04 SERIES-LEDGER 075540cb0307

State: STOPPED. Passes / model-reached: 2/2.

Preserve original series 54b5899ccb66 as STOPPED/ABORT with all three rows. Run nine new model-reached DNS trials with fresh IDs under relay-load-policy-v2. Report the original relay-load-policy-v1 segment (1/1) and the continuation segment separately, plus a clearly labeled mixed-policy aggregate toward ten model-reached trials. Count every confirmed model-reached trial, including failures and aborts; retain pre-model refusals and aborts without counting them. Unknown reach remains unknown. No reopened output, reused ID, dropped row or rerun-to-replace; stop on any ABORT. The aggregate measures progress toward the owner objective, not one unchanged-policy experiment.

| Cohort | Policy | Passes / model-reached |
| --- | --- | --- |
| Original, STOPPED / ABORT | relay-load-policy-v1 | 1/1 |
| Continuation | relay-load-policy-v2 | 2/2 |
| Mixed-policy progress toward ten | v1 + v2 | 3/3 |

The mixed-policy aggregate is not one unchanged-policy experiment.

| Index | Run ID | Outcome | Model requests | Action chosen | Recovery s | False ONLINE | Strict reader | Recording path | Recording SHA-256 | Result SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | edc3c02c085b | PASS | 1 | restore_dns | 17.695 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/01-edc3c02c085b/terminal.typescript | ce4f75673705683aed124fcc963437cb2a3f91f073051b77ccab2b1afecfb2b8 | 9ddd71aaa9b1c5b5b19dee6dcbeae0fd3d632b7711ac2d1d8ee2cd3dcd4d7c33 |
| 2 | a435d119fad8 | PASS | 1 | restore_dns | 17.692 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/02-a435d119fad8/terminal.typescript | 8b00c9e549ffbd2fe2ecec35b677528ffadd1854d1fbf0878b6e7f5540ce6716 | e1330870eb5549955df7bfedb11d3ba5f429b511e39e51a89a5a4b3d96539a90 |
| 3 | 38eb55e41fd4 | ABORT | 0 |  | unknown | 0 | REFUSED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/03-38eb55e41fd4/terminal.typescript | 75472c0e86b4967e2e68b4601fb671fdacef6b97f8f072af32d400395176e468 | unknown |

Median recovery (PASS rows): 17.6935 seconds.
False ONLINE total: 0; complete: True.
Denominator complete: True. Refusals are retained and excluded from the denominator.
