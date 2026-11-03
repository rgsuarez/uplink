# F04 DNS reliability series: evidence (committed 2026-11-03 by Amanda)

Goal (Richard, 2026-11-03 11:30 AM CT): 10 recorded, model-reached F04 DNS trials on bench, honest tally, every model-reached trial counted.

## Result
- Mixed-policy aggregate: **10 passes / 10 model-reached trials**, false_online 0, all strict reader VALIDATED, action restore_dns each time, 1 model request each.
- Median recovery across all 10 passes: 15.319 s (range 15.125 to 17.695 s).
- Final segment 51ffa1727d58 (relay-load-policy-v2): 7/7, median 15.236 s, state COMPLETED.
- Earlier segments preserved as STOPPED/ABORT: 54b5899ccb66 (v1) 1/1, median 17.541 s; 075540cb0307 (v2) 2/2, median 17.6935 s.
- Not counted (0 model requests, retained in ledgers): 54b5899ccb66 #2 c81f7ae72ef1 REFUSED (relay-load gate), #3 8d35661dd4a7 ABORT (prior_handback_check_unavailable); 075540cb0307 #3 38eb55e41fd4 ABORT (headroom-probe timeout); 51ffa1727d58 #7 069f1d061acb REFUSED (entry_headroom_refused).
- Caveat: the 10/10 is a mixed-policy aggregate across three segments (relay-load gate v1 then v2), not one unchanged-policy experiment.

## Files
- Ledgers, summaries, preregistrations: evidence/f04-series/<series-id>/
- Recordings (terminal.typescript per attempt, sha256 in ledgers): PRIVATE_HOME/uplink-f04-runs/f04-series-<series-id>/attempts/
