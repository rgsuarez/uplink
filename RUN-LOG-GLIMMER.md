# Muse Glimmer run log

This index was assembled for the public inspection snapshot from the existing
reports. No file with this name existed in the source tree; this is not a new run.
All rows below are MODEL_IN_LOOP_SIM, not live Wi-Fi measurements.

| Run | Recovered | Honest Report | False ONLINE | Evidence |
| --- | ---: | ---: | ---: | --- |
| Authorized-repair baseline | 15/21 | 6/21 | 0 | RESULT-MODEL-SIM-BASE-FIX-SUN.md |
| Added global proxy instructions and long proxy description | 6/21 | 15/21 | 0 | RESULT-PROXY-REPAIR-SUN.md |
| Long proxy description only | 6/21 | 15/21 | 0 | RESULT-PROXY-REPAIR-SUN.md |
| Adopted snapshot-restore descriptions, post-hoc tuned | 21/21 | 0/21 | 0 | RESULT-MODEL-SIM-PROXY-FIX-SUN.md |
| Frozen compound-fault held-out repair targets | 4/14 | 10/14 | 0 | RESULT-HELDOUT-EVAL-SUN.md |

The held-out run separately passed 6/6 held-policy cases, with 20/20 verified
cleanups overall. Both F10 variants overlap prior diagnostic inputs; excluding
those two cases, recovery was 2/12 (16.7%). Median call latency was 5.389502 seconds.
Metered compute was $0.09670646 with zero cash billed. The registered 4/14 recovery
rate was 28.6%. No retuning or retry followed that run.

A comparison with Muse Spark Open on the same frozen 20 cases was attempted but produced no results because of a provider upload failure, so Uplink ships base Muse Glimmer on its measured numbers. No live recovery is claimed.
