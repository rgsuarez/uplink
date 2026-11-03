2026-10-27 14:29:11 CT trial 1 | stale_proxy | watchdog_expiry_PASS | diff match yes | revert time 41.300 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 14:31:21 CT trial 2 | stale_proxy | watchdog_killed_FAILED_trial_cooldown_active | diff match no | revert time unverified s | OOB ok yes | Wi-Fi ssh back no

Timing correction: trial 1 restored the exact settings, but its 41.300 s value used a process-relative clock and is not qualifying timing evidence. Both watchdog proofs will be repeated with the shared system clock. Trial 2 was refused before fault injection.
2026-10-27 14:46:15 CT trial 3 | stale_proxy | watchdog_expiry_PASS | diff match yes | revert time 40.807 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 14:49:08 CT trial 4 | stale_proxy | watchdog_killed_PASS | diff match yes | revert time 40.441 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 14:51:47 CT trial 5 | dns_and_proxy | PASS | diff match yes | revert time 29.855 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 14:54:25 CT trial 6 | dns_and_proxy | PASS | diff match yes | revert time 27.736 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 14:57:02 CT trial 7 | dns_and_proxy | PASS | diff match yes | revert time 27.435 s | OOB ok yes | Wi-Fi ssh back yes

Correction to the 14:31:21 CT line labeled trial 2: refused_no_fault; no fault applied and no model call; diff n/a (no change), Wi-Fi SSH recovery n/a (no change). This refusal is unnumbered for trial counting and excluded from the gate; the original line is preserved.
2026-10-27 15:21:54 CT trial 8 | dns_and_proxy | PASS | diff match yes | revert time 28.765 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 15:24:32 CT trial 9 | dns_and_proxy | PASS | diff match yes | revert time 27.285 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-27 15:27:09 CT trial 10 | dns_and_proxy | PASS | diff match yes | revert time 26.694 s | OOB ok yes | Wi-Fi ssh back yes

2026-10-27 23:24:31 CT probe-window trial 1 | dns_and_proxy | NOT_RUN_native_helper_compile_failed | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
2026-10-27 23:24:31 CT probe-window trial 2 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
2026-10-27 23:24:31 CT probe-window trial 3 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
Window note: no physical trial number consumed; no retry. Zero Uplink watchdog jobs after verified staging cleanup. Prior trial outcomes remain unchanged.

2026-10-28 00:04:54 CT third-release trial 1 | dns_and_proxy | NOT_RUN_controller_offline_cache_missing | diff match n/a (no injection) | revert time n/a s | OOB ok not checked | Wi-Fi ssh back n/a (no injection)
2026-10-28 00:04:54 CT third-release trial 2 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok not checked | Wi-Fi ssh back n/a (no injection)
2026-10-28 00:04:54 CT third-release trial 3 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok not checked | Wi-Fi ssh back n/a (no injection)
Third-release note: no physical trial number consumed; no lab-wifi contact or retry; remote staging never created and this run loaded zero watchdog jobs.

2026-10-28 00:31:07 CT conditional-window trial 1 | dns_and_proxy | NOT_RUN_local_model_freshness_refused | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
2026-10-28 00:31:07 CT conditional-window trial 2 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
2026-10-28 00:31:07 CT conditional-window trial 3 | dns_and_proxy | NOT_RUN_batch_stopped | diff match n/a (no injection) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no injection)
Conditional-window note: no physical trial number consumed. No retry; timestamp-alignment step was omitted by the standalone preflight. Staging removed and zero Uplink watchdog jobs verified.
2026-10-28 01:29:37 CT trial 11 | dns_and_proxy | PASS | diff match yes | revert time 36.613 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-28 01:32:42 CT trial 12 | dns_and_proxy | PASS | diff match yes | revert time 34.742 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-28 01:35:48 CT trial 13 | dns_and_proxy | PASS | diff match yes | revert time 34.280 s | OOB ok yes | Wi-Fi ssh back yes

Fourth-release hand-back, 2026-10-28 01:39:05 CDT:
Three consecutive probe-enabled primary Wi-Fi DNS/proxy recoveries verified on
10228ee7fce02d02029ca058438305e2e9dde6fd. Window trials 1/2/3 correspond to journal
trials 11/12/13, run IDs a95a8ff88ace / bb80b459dbc6 / 9cfc8b5dd427. All eight
getters match each snapshot, protected services are unchanged, all six independent
oracle checks pass, OOB remains available and Wi-Fi SSH returns. Restorations:
36.613 / 34.742 / 34.280 s. One runner invocation, no retry.
Fresh root launchd readback: zero Uplink watchdog jobs,
2026-10-28T01:37:08.881640-05:00. Owned staging removed after verification of all 102
source files, the built helper and private config. Controller config restored
byte-for-byte at mode 0600. Root snapshots/audits retained; base server unchanged.
Receipt: artifacts/uplink/fourth-release-20261028/handback.json
SHA-256: eaf7f72e6c1dd7e2711b9158b48cfce585da6f793c67840ec425b38643be8ecf
2026-10-28 04:39:42 CT refusal b83e8f2a9e8a | dns_and_proxy | refused_no_fault | diff match n/a (no change) | revert time n/a s | OOB ok yes | Wi-Fi ssh back n/a (no change) | reason probe_positive_control_not_returned

## V3 preflight refusal, 2026-10-28

Source `3da086e`; run `b83e8f2a9e8a`. Three trials were requested, but zero
trials started. The fixed positive control was refused before root prepare:
`probe_sandbox_child_refused`, exit 1, zero returned rows. Python site
initialization failed with a sandbox permission error under the existing
virtualenv. The controller retained `probe_positive_control_not_returned` and
stopped without retry. No snapshot, watchdog, injection or inference request ran.

| Trial slot | Result | Snapshot/revert diff | Returned non-read_state probes |
| --- | --- | --- | ---: |
| 1 | Not run: preflight refused | N/A, no prepare | 0 |
| 2 | Not run: batch stopped | N/A | 0 |
| 3 | Not run: batch stopped | N/A | 0 |

The positive control's before/after settings hashes matched and its Wi-Fi route
was unchanged. Root readback confirmed no run directory and zero Uplink watchdog
jobs. All owned native/staging directories and the metrics sampler are removed
or stopped; controller config restored byte-for-byte. The existing server PID
and start time were unchanged. Authorized /props attestation/build reads occurred;
no inference or server lifecycle operation occurred. No VM contact.

Sampled maxima at 2-second intervals: active/wired/compressed host memory
19.197 GiB; model RSS 16.051 GiB; load (1/5/15 min) 2.602/2.557/2.437.
Swap stayed at 333.25 MiB. These are sampled maxima, not kernel high-water marks.
Receipt: `artifacts/uplink/v3-window-20261028/handback.json`.
SHA-256: `42a310453c80164ac4d3bdbc17d11f993f70cbfd3847d5952002a174aefcf32d`.
2026-10-28 05:44:43 CT trial 14 | dns_and_proxy | RECOVERED_WITHOUT_PROBES | diff match yes | revert time 34.640 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-28 06:39:40 CT trial 15 | dns_and_proxy | PASS | diff match yes | revert time 38.266 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-28 06:42:45 CT trial 16 | dns_and_proxy | PASS | diff match yes | revert time 33.328 s | OOB ok yes | Wi-Fi ssh back yes
2026-10-28 06:45:51 CT trial 17 | dns_and_proxy | PASS | diff match yes | revert time 34.215 s | OOB ok yes | Wi-Fi ssh back yes
