# Recovery receipt

**DNS and proxy settings** | Run `75429ca1ec21`

Recorded evidence only. This transcript does not run or authorize a trial.

| Phase | Recorded result |
| --- | --- |
| Fault / run | DNS and proxy settings / 75429ca1ec21 |
| Evidence class | lab_wifi |
| Evidence | dns_query: completed; measurement negative |
| Chosen action | set_dns; FORCED; layer dns; cites dns-1 |
| Evidence | https_head: failed; measurement unavailable; no returned-measurement credit |
| Chosen action | restore_proxy; FORCED; layer proxy; cites tls-apple |
| Final oracle | ONLINE (recorded) |
| DNS / Apple | PASS |
| DNS / Google | PASS |
| HTTPS / Apple (effective proxy policy) | PASS; HTTP 200; route direct |
| HTTPS / Google (effective proxy policy) | PASS; HTTP 204; route direct |
| Captive check / Apple | PASS; HTTP 200; route direct |
| Captive check / Google | PASS; HTTP 204; route direct |
| Revert time | 33.328 s (injection to exact restoration) |
| Exact snapshot diff | PASS |
| Rollback verified | PASS |
| OOB reachable | PASS |
| Wi-Fi SSH returned | PASS |
| Watchdog retired | PASS |
| Protected services unchanged | PASS |

Forced repairs and grammar-constrained layers do not prove independent diagnosis. A final HTTPS check using the direct route is not a successful proxy traversal.

<details>
<summary>Receipt provenance and exact source hashes</summary>

Sources below identify exact file bytes. Paths are JSON pointers; trace pointers
use zero-based record indexes and `stdout-json` for the embedded episode.
Durations are rounded to milliseconds for display; the source retains full precision.

| Source | SHA-256 |
| --- | --- |
| R | `25b894fde21d6b745eaacb298a6da4c227bcf19a479170299341cfff6e491190` |
| T | `1495b00c4cc06f2239ae713cc3f3494e1d01ed82c19bcbfbac9c58a0bc23f7d7` |

| Phase | Source fields |
| --- | --- |
| Fault / run | `R:/fault,run_id` |
| Evidence class | `R:/evidence_kind` |
| Evidence | `T:/5/observation/stdout-json/execution/rows/0` |
| Chosen action | `T:/5/observation/stdout-json/action,hypothesis_after,action_mode,admitted_tools` |
| Evidence | `T:/14/observation/stdout-json/execution/rows/0` |
| Chosen action | `T:/14/observation/stdout-json/action,hypothesis_after,action_mode,admitted_tools` |
| Final oracle | `R:/oracle/verdict` |
| DNS / Apple | `R:/oracle/evidence/0` |
| DNS / Google | `R:/oracle/evidence/1` |
| HTTPS / Apple (effective proxy policy) | `R:/oracle/evidence/2` |
| HTTPS / Google (effective proxy policy) | `R:/oracle/evidence/3` |
| Captive check / Apple | `R:/oracle/evidence/4` |
| Captive check / Google | `R:/oracle/evidence/5` |
| Revert time | `R:/revert_seconds; R:/final_state/timing_unproven` |
| Exact snapshot diff | `R:/fleet_diff_match` |
| Rollback verified | `R:/rollback_verified` |
| OOB reachable | `R:/oob_ok` |
| Wi-Fi SSH returned | `R:/wifi_ssh_back` |
| Watchdog retired | `R:/watchdog_jobs_retired` |
| Protected services unchanged | `R:/final_state/protected_services_unchanged` |

</details>
