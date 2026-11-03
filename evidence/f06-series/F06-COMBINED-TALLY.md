# F06 stale proxy, combined tally (two segments, BASE model only)

Segment A, series 98e499eebbd7 (prereg f1a7aca8...fdcd, 24.5 GiB entry max): 2/2 model-reached PASS (12.866 s, 10.395 s), 6 pre-model entry_headroom refusals (not counted), stopped on CONSECUTIVE_REFUSAL_CAP.
Segment B, series 15a6104a9685 (versioned continuation, prereg 9200d5dd...40b9, 24.75 GiB entry max approved by Richard 2026-11-03 3:46 PM CT, 25.5 GiB runtime ceiling and 1 GiB reserve unchanged): 3/3 model-reached PASS (12.389 s, 10.39 s, 10.34 s), 0 refusals, 0 aborts.

Combined, labeled as two segments under different entry limits: 5/5 model-reached PASS, every action restore_proxy, false_online 0, aborts 0.
Combined median recovery over the 5 strictly validated recoveries: 10.395 s (segment medians 11.63 s and 10.39 s).
