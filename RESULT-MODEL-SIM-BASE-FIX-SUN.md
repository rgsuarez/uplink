# BASE Q4 prompt-fix result, Sunday November 1

BLUF: the revised BASE-only prompt recovered **15/21 simulated faults (71.4%)**, versus **0/21** with the original prompt, with **0 false-ONLINE claims**. F06 and F07 remained unresolved, producing six honest reports. All 21 trials completed with verified cleanup.

**Evidence: MODEL_IN_LOOP_SIM, profile `authorized-repair-v1`. Real Muse-Glimmer BASE Q4 inference against simulated faults. This is not live Wi-Fi evidence and not the TOP10 result.**

North Star Check: obtain real model-selected repairs without relaxing the independent safety checks. The model retained the full 13-action grammar; no repair was selected or substituted by a scripted fallback.

## Fix and controlled diagnosis

The original interface made repairs reachable but left room for over-deferral: it requested a safe change using a known-good snapshot while exposing only snapshot-match facts, and allowed reporting when evidence appeared insufficient. The revised prompt explicitly states that the executor holds the validated snapshot, supplies native settings, and authorizes non-held restore operations for observed drift. It separates permission to repair from the all-checks-passing condition required to declare ONLINE.

Four unscored calls tested the original and revised system instructions on the same F04/F05 states. User payload, snapshot id, grammar, seed and generation limits were identical. Only system instructions changed:

| Fixed diagnostic state | Original prompt | Revised prompt |
| --- | --- | --- |
| F04, bad DNS | `report`, `no_safe_action` | `restore_dns`, `snapshot_repair` |
| F05, wrong gateway | `report`, `no_safe_action` | `restore_gateway`, `snapshot_repair` |

This supports a prompt-contract fix for the observed over-deferral. It does not establish which individual phrase caused the change or reveal the model's internal reasoning. The revised prompt is a separately versioned, post-hoc policy evaluated on the already-known fault population. The original frozen-prompt Q4 result remains 0/21.

A fresh server was started for the subsequent scored run. The four probes received no recovery credit.

## Scored results

| Fault | Simulated condition | Recovered | Honest report | Failed / blocked | Model action in all three trials |
| --- | --- | ---: | ---: | ---: | --- |
| F03 | Controlled DHCP/link-local | 3/3 | 0 | 0 / 0 | `restore_ipv4` |
| F04 | Bad configured DNS | 3/3 | 0 | 0 / 0 | `restore_dns` |
| F05 | Wrong IPv4 gateway | 3/3 | 0 | 0 / 0 | `restore_gateway` |
| F06 | Stale proxy | 0/3 | 3 | 0 / 0 | `report` |
| F07 | Local portal simulation | 0/3 | 3 | 0 / 0 | `report` |
| F09 | Bad MTU | 3/3 | 0 | 0 / 0 | `restore_mtu` |
| F10 | Stale IPv4 client state | 3/3 | 0 | 0 / 0 | `restore_gateway` |
| Total | Seven simulated faults | 15/21 | 6 | 0 / 0 | 21 model decisions |

| Metric | Measured value |
| --- | --- |
| False ONLINE | 0 in 21 scored trials |
| Scored model calls / mean per trial | 21 / 1.000 |
| Additional diagnostic calls | 4, excluded from scored outcomes |
| Calls over 8 seconds | 0/21 scored; all four probes also completed within 8 seconds |
| Median / mean scored call latency | 5.020 s / 5.001 s |
| Scored call latency range | 4.476-5.605 s |
| Scored evaluation wall time | 108.370 s |
| Whole job wall time | 165.463 s, including verification, server starts and probes |
| Job interval | 2026-11-01 13:21:32-13:24:17 CST |
| GPU | NVIDIA A10G, compute capability 8.6 |
| Provider-metered job cost | $0.07240661, including setup and diagnostics |
| Cash billed | $0 in the postflight workspace snapshot; covered by credits |
| Final app state | Stopped, zero tasks |

Recovery credit came from the frozen independent verifier after a model-selected restore, before watchdog or controller cleanup. All 15 recorded recovery proofs replayed successfully at their recorded verification timestamps. This historical replay is not a claim about current connectivity. The model did not need to make a separate ONLINE declaration to receive recovery credit.

## Remaining misses and limits

All six F06/F07 trials selected `report` with `no_safe_action`; `restore_proxy` remained available and was not attempted. These were not timeouts or unparseable completions. F07 is an owned local portal/proxy simulation, not evidence of real portal authentication or recovery.

### F06/F07 expected-outcome verdict

**Verdict: missed repairs in the controlled simulator.** `report` truthfully describes non-recovery, but it is not the expected successful outcome for either simulated fault. A safe snapshot restore was available; neither fault depended on a held tool.

| Fault | Recorded evidence in all three trials | Available expected repair | Verdict |
| --- | --- | --- | --- |
| F06 | Proxy differs from snapshot; HTTPS fails; DNS and IPv4 pass; no portal redirect | `restore_proxy` restores the captured proxy configuration from `closed-loopback` | Three missed repairs, three honest reports |
| F07 | Proxy differs from snapshot; HTTPS fails; DNS and IPv4 pass; owned portal redirect is visible | `restore_proxy` restores the captured proxy configuration from `owned-portal` | Three missed repairs, three honest reports |

The frozen [tool contract](src/offline_agent/uplink/top10_contract.py:43) excludes `restore_proxy` from `HELD_REPAIRS`. The [model tool description](src/offline_agent/uplink/top10_model.py:26) offers it as a captured web/secure-proxy restore, and all six stored model records include it in `admitted_tools`. The frozen [repair plan](src/offline_agent/uplink/top10_injectors.py:31) maps it to `native:restore_proxy`; the [simulated primitive](src/offline_agent/uplink/top10_kernel.py:663) copies the proxy from the snapshot.

A separate, unscored in-memory check applied those unchanged inject/repair functions, intercepted file and audit writes, and denied socket/subprocess calls. All 18 fault facts matched each of the six recorded observations. Applying `restore_proxy` then restored the entire simulated state exactly to its snapshot, passed the frozen baseline predicate, and returned ONLINE from the unchanged synthetic oracle in all six checks. This establishes repair availability and effect in this simulator. It adds no model recovery credit and makes no live-network claim.

Evidence: F06/F07 expected-outcome receipt (private source artifact omitted), SHA-256 `82d9f6582beeacc01cd5058b4603848222bfd37d2549fa51b3fbb6d317ecd9a4`. The receipt binds the original result hash and frozen source hashes. The original result, parser, safety gates and scoring remain unchanged: **15/21 recovered, six honest reports, zero false ONLINE**. A real captive portal may require human authentication; that is not the fault represented by this owned local F07 simulation.

F03 trial 3 selected and successfully executed `restore_ipv4` but emitted the contradictory reason code `no_safe_action`. Recovery is supported by action/effect evidence; that reason code is not a reliable diagnosis. The frozen `restore_gateway` plan restores IPv4 configuration and requests DHCP, which also resolves this simulator's F10 stale-client-state variant. No separate inferred lease-repair plan is claimed.

F01/F02/F08 remain outside the 21-trial population. Three deterministic-seed trials per fault, using a prompt revised after inspecting earlier failures, do not establish held-out generalization. The revised profile is gated to Linux simulation and is not wired into the live bridge; no new live window or host authority follows from this result.

## Evidence and verification

- Result, including diagnostic and scored model inputs, outputs, observations, oracles and receipts: artifacts/uplink/mil-base-fix-20261101-132128/result.json (private source artifact omitted). SHA-256 `06859b248badb41a6070bfbe024375017cb0c88feef88e2315284a73afc71455`.
- Provider receipt: artifacts/uplink/mil-base-fix-20261101-132128/provider-billing.json (private source artifact omitted), app `PROVIDER_ACCOUNT_REMOVED`.
- Historical proof reconciliation: artifacts/uplink/mil-base-fix-20261101-132128/reconciliation.json (private source artifact omitted).
- Input contract: [configs/model-sim-base-fix.v1.json](configs/model-sim-base-fix.v1.json), SHA-256 `b5eab09c3643887e21095901005aee996b7d8b875ee8912c11635fa4e5802f1d`.
- Prompt: [harness/model-sim/authorized-repair-v1.txt](harness/model-sim/authorized-repair-v1.txt), SHA-256 `f68fac36a29da410753ba66e6507f70dbc251b324e76ee86f6befb0f7aa03949`.
- Q4 model: `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, 16,756,683,904 bytes, SHA-256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`; base, unmodified open weights.

The original 173 frozen local files remained byte-identical. The worker verified 150 relevant frozen runtime inputs and reused the cached multi-architecture image. The original system safety instructions remain the prefix of the revised prompt. Grammar, parser, fact projection, scorer, ONLINE oracle, watchdog and cleanup behavior were unchanged.

Offline validation from `PRIVATE_HOME/projects/meta-hackathon/offline-agent-uplink-recovery`:

```text
.venv/bin/python -B -m pytest tests/test_model_sim_profile.py -q -p no:cacheprovider
Result: 8 passed.
.venv/bin/python -B -m mypy src/offline_agent/uplink/model_sim_profile.py
Result: no issues found.
.venv/bin/python -B -m ruff check src/offline_agent/uplink/model_sim_profile.py scripts/model_in_loop_base_fix.py modal/model_sim_base_fix_app.py tests/test_model_sim_profile.py --output-format concise
Result: all checks passed.
```

Checks covered prompt-only projection parity, unchanged grammar/budget, valid repair output, invalid/unknown/truncated outputs, missing measurements, transport failure, local-inference refusal and rejection of unsupported ONLINE claims. There was one new BASE Q4 job,  no host contact, no push and no publication.
