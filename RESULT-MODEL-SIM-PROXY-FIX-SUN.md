# BASE Q4 proxy-repair experiment, Sunday November 1

BLUF: the final balanced tool-description candidate recovered **21/21 simulated faults**, versus the `authorized-repair-v1` baseline's **15/21**, with **zero false ONLINE, zero calls over eight seconds and 21/21 verified cleanup**. F06/F07 both improved from 0/3 to 3/3; all five previously recovered classes remained 3/3. **The operator adopted this candidate after explicitly reviewing its six-description scope**, resolving the later packet's one-proxy-line restriction.

**MODEL_IN_LOOP_SIM only. Real unadapted Muse Glimmer Q4 inference, simulated faults. Not live Wi-Fi, not a TOP10 field result, and not held-out generalization.** The descriptions were tuned after inspecting F06/F07 failures. Two regressing candidates are retained below, not omitted or pooled into the final result.

North Star Check: obtain model-selected proxy repairs without losing existing recoveries or weakening the independent safety gates. The final candidate meets the measured recovery/safety target on this known simulated population. The selected overlay is now `snapshot-restore-descriptions-v1`, using the unchanged `authorized-repair-v1` system text. This profile selection does not imply live integration or host authority.

## Baseline versus final candidate

| Fault | Simulated condition | Prior baseline | Final candidate | Final model action, all three trials |
| --- | --- | ---: | ---: | --- |
| F03 | Controlled DHCP/link-local | 3/3 | 3/3 | `restore_ipv4` |
| F04 | Bad configured DNS | 3/3 | 3/3 | `restore_dns` |
| F05 | Wrong IPv4 gateway | 3/3 | 3/3 | `restore_gateway` |
| F06 | Stale proxy | 0/3, three honest reports | 3/3 | `restore_proxy` |
| F07 | Owned local portal/proxy simulation | 0/3, three honest reports | 3/3 | `restore_proxy` |
| F09 | Bad MTU | 3/3 | 3/3 | `restore_mtu` |
| F10 | Stale IPv4 client state | 3/3 | 3/3 | `restore_gateway` |
| Total | Seven faults, three trials each | **15/21** | **21/21** | 21 model-selected repairs |

| Metric | Prior baseline | Final candidate |
| --- | ---: | ---: |
| Recovered / honest report / failed / blocked | 15 / 6 / 0 / 0 | 21 / 0 / 0 / 0 |
| False ONLINE | 0 | 0 |
| Scored model calls | 21 | 21 |
| Calls over eight seconds | 0 | 0 |
| Median / mean scored call | 5.020 / 5.001 s | 4.891 / 4.949 s |
| Scored call range | 4.476-5.605 s | 4.585-5.650 s |
| Scored evaluation wall time | 108.370 s | 106.504 s |
| Whole-job wall time | 165.463 s | 132.053 s |
| Unscored diagnostics within that job | 4 | 0 |
| GPU | Modal A10G | Modal A10G; device reports NVIDIA A10, compute 8.6 |
| CPU / memory | 4 CPUs / 32 GiB | 2 CPUs / 32 GiB, hard limits |
| Job compute cost | $0.07240661 | $0.05318870 |

The CPU allocation and diagnostic counts differ, so the timing/cost columns are descriptive, not a matched speed comparison. The model file, quantization, template, context 8192, seed 0, temperature 0, token limit 192, full 13-action grammar and eight-second call limit are unchanged. No adapter was loaded.

Recovery credit came from the frozen verifier after the selected repair and before controller/watchdog cleanup. All 21 historical recovery proofs replayed at their recorded verification timestamps. No separate model ONLINE declaration was needed for recovery credit. Every final reason code was `snapshot_repair`; that enum is not proof of a reliable natural-language diagnosis. `restore_gateway` uses the existing IPv4 restore/DHCP primitives, which also resolve this simulator's F10 variant.

## What the evidence says about the misses

The original F06/F07 records admitted `restore_proxy`; the model returned a valid `report` with `no_safe_action`, without attempting it. The projected facts showed proxy drift and failed HTTPS alongside healthy configured DNS/IPv4 facts. All six synthetic ONLINE check booleans were false. Those facts/checks were preserved throughout this work; none was corrected, hidden or relaxed to improve the score.

Eight unscored calls tested four variants on the exact same recorded F06/F07 states and snapshot IDs. All kept the same grammar and fact/check/history projection:

| Input change | F06 choice | F07 choice |
| --- | --- | --- |
| Prior `authorized-repair-v1` baseline | `report` | `report` |
| Added global proxy instructions only | `restore_proxy` | `restore_proxy` |
| Longer proxy tool description only | `restore_proxy` | `restore_proxy` |
| Both additions | `restore_proxy` | `restore_proxy` |

This establishes that wording can change proxy action selection without making a new tool reachable or changing its execution gate. It does not expose the model's internal reasoning or isolate which phrase resolved its deferral. The full-population evaluations then exposed a regression that the two-state probes could not catch.

## Rejected attempts, preserved

| Candidate | Full population result | False ONLINE | Disposition |
| --- | --- | ---: | --- |
| Combined global instructions plus long proxy description | 6/21 recovered, 15 honest reports; only F06/F07 recovered | 0 | Rejected: all five prior recovery classes regressed |
| Long proxy description only, original system instructions | 6/21 recovered, 15 honest reports; only F06/F07 recovered | 0 | Rejected: same regression |
| Balanced concise restore descriptions, original system instructions | 21/21 recovered, zero honest reports | 0 | Adopted after explicit operator scope approval |

The rejected global paragraph included an over-broad instruction to report when the proxy already matched its snapshot. That is inappropriate for unrelated faults. However, removing the paragraph did not remove the regression: the description-only run also scored 6/21. It would therefore be incorrect to claim that sentence was the sole cause.

The final candidate keeps the entire `authorized-repair-v1` system text and gives six non-held restore tools the same concise description style: captured-state restoration plus the matching measured predicate. It names `ipv4_usable`/`ipv4_linklocal`, `dhcp_lease_present`, `dns_matches_snapshot`, `gateway_matches_snapshot`, `proxy_matches_snapshot` and `mtu_matches_snapshot` in the corresponding descriptions. The three held repair descriptions and the four non-repair tools remain unchanged. No action is selected, filtered or substituted by script.

Current renderer output was compared byte-for-byte with all 21 actual final prompts. Each matches. The system text and all non-tool projection fields match the prior provider, and exactly those six descriptions differ. The dedicated simulation entrypoint selects the adopted balanced profile. It is not connected to the live bridge; the historical baseline runner is preserved.

## Cost, limits and provider receipts

The prior job cost $0.07240661. The task cap was enforced cumulatively across all three new jobs: **$0.21721983**. Actual metered compute was **$0.20393213**, leaving **$0.01328770**. Workspace cash billed remained **$0**, covered by credits. All three apps are stopped with zero tasks. No further inference is authorized by this closeout.

| Run | Worker interval, CST | App | Metered compute |
| --- | --- | --- | ---: |
| Combined | 14:17:11-14:20:32 | `PROVIDER_ACCOUNT_REMOVED` | $0.09091770 |
| Long description only | 14:26:22-14:28:39 | `PROVIDER_ACCOUNT_REMOVED` | $0.05982573 |
| Balanced concise descriptions | 14:38:45-14:40:57 | `PROVIDER_ACCOUNT_REMOVED` | $0.05318870 |
| Total | 63 scored calls plus eight unscored diagnostic calls | Personal Modal profile `rgsuarez` | **$0.20393213** |

Current provider rates were read before dispatch. The final attempt used a 145-second function limit, ten-second container-startup limit, one single-use container, no retries, two CPUs and 32 GiB. Its calculated compute ceiling plus reserve and preceding metered spend was $0.2160057078, below the task cap. GPU work stayed on Modal; no local inference ran. Existing model assets were read, not changed.

## Primary evidence

- Prior 15/21 result: artifacts/uplink/mil-base-fix-20261101-132128/result.json (private source artifact omitted), SHA-256 `06859b248badb41a6070bfbe024375017cb0c88feef88e2315284a73afc71455`.
- Final 21/21 result: artifacts/uplink/mil-proxy-fix-20261101-143834/result.json (private source artifact omitted), SHA-256 `a3074b9f0c8ddc5e190f50b1e6f0bfbe8d98487c1ac6ec7e7a6af1164d8b1ed0`.
- Final reconciliation: artifacts/uplink/mil-proxy-fix-20261101-143834/reconciliation.json (private source artifact omitted), SHA-256 `728c0daf6e0dbfd5802b5c90998b28ce80e697723aab7c4e66003962326b6bac`.
- Final billing/stopped-app receipt: artifacts/uplink/mil-proxy-fix-20261101-143834/provider-billing.json (private source artifact omitted), SHA-256 `75066fb5821cd20f8d8d6229e1358e3db1d9f9d5d68159dc3481f6045c5920bb`.
- Rejected combined result: artifacts/uplink/mil-proxy-fix-20261101-141659/result.json (private source artifact omitted), SHA-256 `ee25f372da39f47838783cedf4951535bace1d6cc077d7d7a8308c32cd2902b9`. Its adjacent `inputs/`, `reconciliation.json`, `provider-billing.json` and `disposition.json` preserve the exact candidate, eight ablations, six proofs and rejection reason.
- Rejected long-description result: artifacts/uplink/mil-proxy-fix-20261101-142618/result.json (private source artifact omitted), SHA-256 `05e969c633701ece1ab98b604e04df1f74233becfd0682e175442db46ab1a2fb`. Its adjacent files preserve the same evidence categories, with no new diagnostics.
- Final input contract: [configs/model-sim-proxy-fix.v3.json](configs/model-sim-proxy-fix.v3.json), SHA-256 `ac9824ec7bc67812e001ae65a5bf01ec865c6ee5cf09197011aed2ca379ec1cf`.
- Balanced provider: [src/offline_agent/uplink/model_sim_proxy_profile.py](src/offline_agent/uplink/model_sim_proxy_profile.py), SHA-256 `a994d50e8a0fb2e36b5626061145eeba140183775827aaea369664a739fa76a5`.

The system prompt remains SHA-256 `f68fac36a29da410753ba66e6507f70dbc251b324e76ee86f6befb0f7aa03949`. The BASE Q4 model remains 16,756,683,904 bytes, SHA-256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`. Each job verified the model and 150 frozen runtime inputs; all 173 original local frozen files remain byte-identical. The unchanged acceptance oracle is `uplink-online-v1`, SHA-256 `8ada6a245ee5929d08b393d5f9077ba46712aa9302e31109d443f035a7755d42`; its edge-test SHA-256 is `b8d14901a4f82e5673d290bc102f2c526a6e29932a28e2c016477bcff4635f12`.

## Local checks and adoption

```text
.venv/bin/python -B -m pytest tests/test_model_sim_proxy_profile.py tests/test_model_sim_profile.py -q -p no:cacheprovider --basetemp=artifacts/uplink/proxy-fix-sun-20261101/pytest-balanced
17 passed: 9 final-overlay tests plus 8 prior-profile tests.
.venv/bin/python -B -m mypy --cache-dir=/dev/null src/offline_agent/uplink/model_sim_proxy_profile.py
Success: no issues found in 1 source file.
.venv/bin/python -B -m ruff check src/offline_agent/uplink/model_sim_proxy_profile.py scripts/model_in_loop_proxy_fix.py modal/model_sim_proxy_fix_app.py tests/test_model_sim_proxy_profile.py --output-format concise
All checks passed.
```

Tests preserve the original system text, facts, checks, history, tool admission, grammar and call budget; cover valid repair, invalid/empty responses, transport failure, non-sandbox refusal and rejection of unsupported ONLINE. The exploratory tests were narrowed with the implementation; the final suite has nine new tests, not the thirteen used during ablation. No frozen test or safety gate changed.

The 14:33 decision packet (private source artifact omitted) was first read after the final run. It permits at most one further variant after the two 6/21 runs, with an unchanged `authorized-repair-v1` text and **only an added narrowly scoped `restore_proxy` line**. The final run satisfies its numeric gates (>=17/21, no fault below baseline, zero false ONLINE), its one-more-run limit and the cumulative cost cap, but changes six descriptions rather than one proxy line. Adoption was held until the operator reviewed that exact difference and explicitly selected the 21/21 balanced candidate. That direct approval resolves the one-line scope restriction for this candidate. The adoption event (private source artifact omitted) records the selection; NEEDS-AMANDA.md (private source artifact omitted) is resolved. The 15/21 run remains the historical baseline.

No push, publication, Bench/Relay contact, live package binding, adapter/FT run, credential creation, GitHub authentication change or source/evidence deletion occurred. No new model job remains active. The queued live-package work has not started.
