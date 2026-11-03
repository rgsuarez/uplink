# Proxy-repair decision result, Sunday November 1

BLUF: a balanced six-description candidate achieved **21/21 simulated recoveries, zero false ONLINE and no fault below its 15/21 baseline**. The operator **adopted this exact six-description candidate** after reviewing the later packet's one-proxy-line restriction. The selected profile is `snapshot-restore-descriptions-v1`, with the original `authorized-repair-v1` system text unchanged.

| Candidate | Recovered | Honest reports | False ONLINE | Decision |
| --- | ---: | ---: | ---: | --- |
| Prior `authorized-repair-v1` | 15/21 | 6 | 0 | Historical baseline |
| Added global proxy instructions plus long proxy description | 6/21 | 15 | 0 | Rejected |
| Long proxy description only | 6/21 | 15 | 0 | Rejected |
| Balanced concise descriptions for six non-held restores | 21/21 | 0 | 0 | Adopted by explicit operator approval |

All three new evaluations covered the full 21-trial population. Total metered compute was **$0.20393213**, within the **$0.21721983** cap; cash billed was **$0**, and all apps are stopped. The final result SHA-256 is `a3074b9f0c8ddc5e190f50b1e6f0bfbe8d98487c1ac6ec7e7a6af1164d8b1ed0`.

The [detailed report](RESULT-MODEL-SIM-PROXY-FIX-SUN.md) contains the per-fault comparison, both rejected attempts, exact inputs, proof replays, cost receipts and limitations. This is post-hoc simulation evidence, not live Wi-Fi or held-out generalization. The decision request (private source artifact omitted) is resolved. Live packaging and its fresh owner/window/OOB/KAYLIN-OK gates remain separate; the profile approval does not authorize host contact. No host contact, push or publication occurred.
