# Recovery receipt

**DNS and proxy settings** | Run `6c04d80f1c00`

Recorded evidence only. This transcript does not run or authorize a trial.

| Phase | Recorded result |
| --- | --- |
| Fault / run | DNS and proxy settings / 6c04d80f1c00 |
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
| Revert time | 34.215 s (injection to exact restoration) |
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
| R | `cb0cc15161cfa21e6f8c7e001245bf70a552806cb0ce42b9adedd0e5949d8895` |
| T | `0fda3d7d9f922ce231afd1be1bd267ebfe22fea8b583e3838dc36ad32bef2f79` |

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
