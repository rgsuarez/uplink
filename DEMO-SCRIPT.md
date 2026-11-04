# Uplink recording script for Lindsey

Target cut: r12q7, 2:51.90, hard cap 3:00. Use existing recorded evidence only.
This script authorizes no model run, host probe, network change, rendering or upload.

## Exact headline wording

> Uplink runs Meta's unmodified base Muse Glimmer on the device. In our live DNS
> series, all ten trials that reached the model recovered. Median recovery was
> 15.319 seconds, with zero observed false ONLINE. It uses base, unmodified open weights.

On-screen results card:

```text
RECORDED LIVE F04 DNS | BASE MUSE GLIMMER | ON-DEVICE
10/10 model-reached recovered | median 15.319 s | false ONLINE 0
3 mixed-policy segments | 4 pre-model exclusions | warm timings
```

## Timeline and narration

| Beat | Picture | Wording / constraint |
| --- | --- | --- |
| Open | Provenance card | Meta's weights, llama.cpp and Python libraries pre-existed. Uplink's recovery loop, typed tools, watchdog, oracle and evidence harness were built during the hackathon. |
| Problem | Offline laptop | When the connection breaks, cloud help may be unreachable. Base Muse Glimmer stays on the device. |
| Recovery | Existing recorded F04 repair | Show measured DNS drift, restore_dns, then independent verification. Label every shot recorded live evidence; reveal speed is not inference speed. |
| Results | Exact headline card above | Count all ten model-reached trials. Say median 15.319 seconds or about 15.3 seconds. |
| Limits | Segment and exclusion disclosure | Three policy segments, not one unchanged-policy experiment. Four attempts stopped before any model request and remain outside the denominator. Three warm-ups per attempt mean warm timings. |
| Close | Repair / Reroute / Report | Model text cannot declare ONLINE. Independent fresh checks decide. Zero observed false ONLINE is not a reliability guarantee. |

## Segment numbers

| Segment | Passes / model-reached | Median recovery | False ONLINE |
| --- | ---: | ---: | ---: |
| 54b5899ccb66, load policy v1 | 1/1 | 17.541 s | 0 |
| 075540cb0307, v2 | 2/2 | 17.6935 s | 0 |
| 51ffa1727d58, v2, completed | 7/7 | 15.236 s | 0 |
| Mixed-policy aggregate | 10/10 | 15.319 s | 0 |

Sources: [F04 evidence](EVIDENCE-F04-SERIES.md), [committed ledgers](evidence/f04-series/).
An earlier recorded single trial and earlier receipt-only trial are not pooled.
Raw recordings and authority records stay private. Do not show private paths, device
names, addresses, prompts or credentials.

## Simulation remains separate

If retained in the cut, label every simulation shot MODEL_IN_LOOP_SIM. The adopted
21/21 descriptions were rewritten post hoc on those cases. Held-out repair targets were
4/14 (new inputs 2/12), held-policy honest reports 6/6, false ONLINE 0, cleanups 20/20.
These are saved GPU simulation results, not live recoveries or on-device timings.
[Held-out result](RESULT-HELDOUT-EVAL-SUN.md).

## Branch-out additions require committed evidence

F06 stale proxy is now committed: 5/5 model-reached recoveries, median 10.395 s,
false ONLINE 0, six pre-model exclusions. Disclose the two entry limits, 24.5 and
24.75 GiB. Healthy and misleading-status controls are committed as 0/6 PASS:
network safety held, but all six reports failed the required verified-reason check;
five pre-model exclusions remain. Do not present those controls as successes.
See the current [results table](README.md#results) and public ledgers for sources.
Keep F05 wrong gateway, site-only outage and F01 radio-off PENDING until committed.
At the Wednesday 11:00 AM CT testing freeze, recalculate every claimed denominator,
median and false-ONLINE total from the committed ledgers, or omit the row and footage.
Do not infer a count, a winner, an action-selection success or general reliability.

Final video URL, opening-line verification and final owner approval remain pending.
