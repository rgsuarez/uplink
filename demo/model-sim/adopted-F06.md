MODEL_IN_LOOP_SIM | recorded BASE Muse Glimmer Q4 | post-hoc development evidence
Adopted descriptions rewritten after F06/F07 failures; not held-out and not live Wi-Fi.
Replay reads saved files only: zero new model calls or host probes.


## F06: stale proxy | adopted | trial 1

Source result SHA-256: `a3074b9f0c8ddc5e190f50b1e6f0bfbe8d98487c1ac6ec7e7a6af1164d8b1ed0`

Recorded run ID: `8dee03fad5ca`

Fault engine: FAKE_KERNEL. Real model completion was recorded by the simulation wrapper.



### Decision 1

Observed: dns_matches_snapshot=true, proxy_matches_snapshot=false, ipv4_path_ok=true, protected_services_unchanged=true, persistent_baseline_equal=false

Before: dns-1=FAIL, dns-2=FAIL, tls-apple=FAIL, tls-google=FAIL, portal-apple=FAIL, portal-google=FAIL

Model selected `restore_proxy`; reason code `snapshot_repair`.

All 13 tools remained in the grammar. Recorded model-call time: 4.891 s.

After: dns-1=PASS, dns-2=PASS, tls-apple=PASS, tls-google=PASS, portal-apple=PASS, portal-google=PASS

Verifier: RECOVERED in the recorded simulation, before cleanup.



Recorded outcome: `recovered`; false ONLINE: 0.

Cleanup: recorded verified; never counted as model recovery.

This is saved evidence replay. No network state changed and no model ran during replay.
