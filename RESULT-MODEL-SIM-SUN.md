# BASE model simulation result, Sunday November 1

BLUF: the real Q4_K_M BASE model recovered **0/21 simulated faults (0%)**. All 21 trials ended in `honest_report`, with **0 false-ONLINE claims** and **0/21 model calls over 8 seconds**. The suite completed; the model did not attempt a repair.

**Evidence: MODEL_IN_LOOP_SIM. Real BASE model, simulated fault engine (FAKE_KERNEL). This is not live Wi-Fi evidence and not the TOP10 result.**

North Star Check: measure the frozen model-driven recovery path honestly, preserve every miss, and keep simulation separate from physical Wi-Fi recovery. The prompt, grammar, fact projection, 8-second model timeout and hash-bound files were not tuned for this result.

| Metric | Recorded value |
| --- | --- |
| Evaluation interval | 2026-11-01 12:02:33 CST to 2026-11-01 12:04:25 CST |
| Model | Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf, base, unmodified open weights |
| Trial population | F03-F07, F09, F10; 3 trials each; 21/21 recorded |
| Recovered / honest report / failed / blocked | 0 / 21 / 0 / 0 |
| False-ONLINE claims | 0 in 21 recorded trials |
| Model calls / calls over 8 seconds | 21 / 0 |
| Median / mean call latency | 5.149 s / 5.191 s |
| Call latency range | 4.773-5.727 s |
| Mean decision steps | 1.000 model call per recorded trial; 0 repair actions |
| Evaluation wall time | 112.004 s |
| Server readiness wait | 10.000 s; separate from evaluation wall time |
| GPU type | A10G, verified from the completed app's provider billing resource. The export's static `modal-L4` label is inaccurate. |
| Provider-metered compute | $0.05236507 for the completed BASE app; excludes earlier attempts and image builds. |

Provider cost and GPU evidence: artifacts/uplink/mil-modal-20261101-120425/provider-billing.json (private source artifact omitted), tied to app `PROVIDER_ACCOUNT_REMOVED` and the source result through the retained launch log. The resource charges are CPU $0.00641177, A10G $0.03727775 and memory $0.00867555. The selected workspace showed $0 billed during preparation; metered usage was covered by credits.

## Per-fault outcomes

Here, a recovery pass means the frozen scorer returned `recovered`. An `honest_report` is a truthful unresolved result, not a recovered fault.

| Fault | Simulated condition | Recovery passes | Honest report | Failed | Blocked | False ONLINE | Calls over 8 s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| F03 | Controlled DHCP/link-local symptom | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F04 | Bad configured DNS | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F05 | Wrong IPv4 gateway | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F06 | Stale proxy | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F07 | Local portal simulation | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F09 | Bad MTU | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| F10 | Stale IPv4 client state | 0/3 (0%) | 3 | 0 | 0 | 0 | 0 |
| Total | Seven simulated faults | 0/21 (0%) | 21 | 0 | 0 | 0 | 0 |

## Every recovery miss

All misses were honest reports where a repair operation exists in the frozen simulator. No miss was caused by an unparseable completion or a model timeout. The reason codes below are what the model returned; they do not establish why it preferred reporting over repair.

| Fault / trial | One-line diagnosis | Call time |
| --- | --- | ---: |
| F03 / 1 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 5.727 s |
| F03 / 2 | Selected `report` (`no_safe_action`, evidence `associated_with_snapshot`) without attempting `restore_ipv4`. | 5.149 s |
| F03 / 3 | Selected `report` (`no_safe_action`, evidence `configured_dns_ok`) without attempting `restore_ipv4`. | 5.043 s |
| F04 / 1 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_dns`. | 5.460 s |
| F04 / 2 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_dns`. | 4.923 s |
| F04 / 3 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_dns`. | 4.913 s |
| F05 / 1 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 5.468 s |
| F05 / 2 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 5.080 s |
| F05 / 3 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 4.773 s |
| F06 / 1 | Selected `report` (`no_safe_action`, evidence `configured_dns_ok`) without attempting `restore_proxy`. | 5.518 s |
| F06 / 2 | Selected `report` (`no_safe_action`, evidence `configured_dns_ok`) without attempting `restore_proxy`. | 4.965 s |
| F06 / 3 | Selected `report` (`no_safe_action`, evidence `associated_with_snapshot`) without attempting `restore_proxy`. | 5.176 s |
| F07 / 1 | Selected `report` (`connectivity_unverified`, evidence `dns-1`) without attempting `restore_proxy`. | 5.626 s |
| F07 / 2 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_proxy`. | 4.866 s |
| F07 / 3 | Selected `report` (`no_safe_action`, evidence `configured_dns_ok`) without attempting `restore_proxy`. | 5.043 s |
| F09 / 1 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_mtu`. | 5.577 s |
| F09 / 2 | Selected `report` (`no_safe_action`, evidence `associated_with_snapshot`) without attempting `restore_mtu`. | 5.243 s |
| F09 / 3 | Selected `report` (`no_safe_action`, evidence `configured_dns_ok`) without attempting `restore_mtu`. | 5.330 s |
| F10 / 1 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 5.328 s |
| F10 / 2 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 4.836 s |
| F10 / 3 | Selected `report` (`no_safe_action`, evidence `dns-1`) without attempting `restore_ipv4`. | 4.969 s |

## Evidence and limits

- Completed run: artifacts/uplink/mil-modal-20261101-120425/result.json (private source artifact omitted). SHA-256: `93f6bfeff215aa1c2a9c971aa464c05afed0e0c3a52b58f5f08cdf2414ee9269`.
- Frozen manifest: [harness/top10/FROZEN.json](harness/top10/FROZEN.json), SHA-256 `856d66142767432191caae850ddafa860db8d6ca06b5d5e56e26665cf880924e`. All 173 locally bound files still matched their recorded hashes at report preparation.
- Acceptance oracle: `uplink-online-v1`, SHA-256 `8ada6a245ee5929d08b393d5f9077ba46712aa9302e31109d443f035a7755d42`; edge oracle SHA-256 `b8d14901a4f82e5673d290bc102f2c526a6e29932a28e2c016477bcff4635f12`.
- The export contains per-trial actions, outcomes and call timing, but no full receipt bundle or actual GPU identity. The A10G identity was recovered independently from provider billing. The export does not independently attest every remote file hash; the retained source result remains the authority for model observations.
- F01, F02 and F08 are excluded: the frozen admission contract rejects them, and the frozen model interface marks their repair tools held. Extending them would require changing that contract; they are not scored as successes or failures here.
- F07 is a simulated local portal/proxy condition. It makes no real portal authentication or recovery claim.
- Three deterministic-seed trials per fault are descriptive evidence for this configuration, not a population reliability estimate.

## Preserved setup failures

These earlier attempts are retained separately and are not pooled into the completed 21-trial result.

| Attempt receipt | Observed result | Interpretation |
| --- | --- | --- |
| artifacts/uplink/mil-modal-20261101-114547/result.json (private source artifact omitted) | `server_not_ready`; CUDA reported no kernel image available for the execution device; no trial summary | Runtime startup failure, not a model recovery result. SHA-256 `34c5c08334e53652131198347922172019fbbf6b81445ed1dc6792842da29030`. |
| artifacts/uplink/mil-modal-20261101-120152/result.json (private source artifact omitted) | F03 trial 1 failed with `FileNotFoundError`; suite stopped; 0 model calls | Packaging failure before inference. The prior image omitted the edge-oracle test file required by registry validation; adding the unchanged test files allowed the next run to complete. SHA-256 `0d0ef24105877f74c8b98a041af443b50a36d7ea8fe16c1487f226d7437c0e11`. |

Packaging lesson: a frozen evaluator must carry its oracle dependencies and record the actual GPU. Neither a process exit code of zero nor the label `modal-L4` is sufficient evaluation provenance.

No live Wi-Fi trial, bench/relay contact, frozen-file edit, push or publication was performed for this report.
