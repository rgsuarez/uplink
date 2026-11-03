# EVIDENCE-F04-FIX-NOW-12-RECORDED

F04 bench trial PASSED with a complete terminal recording (Amanda, 2026-11-03 11:27 CST).

- Run: 1ec5c6c66f0a, launched 2026-11-03 11:12:45 CT in tmux f04-fixnow12 on bench, limits 24.5 GiB entry / 25.5 GiB runtime (Richard approval 10:55 AM CT), all other rules unchanged.
- Outcome: recovered. Real BASE model, 1 scored request (restore_dns), false_online 0, verified fault-to-recovery 17.555 s, restore 4.4 s, cleanup and handback ONLINE, OOB verified. 3 unscored warm-ups.
- Recording: complete (CAPTURE.json capture_complete=true, exit 0); all 34 events replayed exactly. Terminal recording, not desktop screen capture.
- Launcher exit 2 at native_index_export was an exporter cross-machine clock comparison defect (bench clock ~0.245 s ahead); fixed on branch fix/f04-recording-clock-domain (2f87f19) and re-exported separately. Original run files unchanged.
- Strict reader (unchanged): VALIDATED, eligible_for_submission=true.

Paths (Mac):
- Original run: ~/uplink-f04-runs/f04-fix-now-12-1ec5c6c66f0a
- Export: PRIVATE_HOME/uplink-f04-runs/f04-fix-now-12-1ec5c6c66f0a-export
- Recording: PRIVATE_HOME/uplink-f04-runs/f04-fix-now-12-1ec5c6c66f0a-export/terminal.typescript (sha256 7d115fbe777b8bc5122e494c55670286965f324ec7ff5e545f1d5a76823224d0, identical to original)
- STRICT-RESULT.json sha256 5d404d58d6c6ce3d24a09a04646faa645c0c0d9bd89166c4f7a93067988e4186
- OWNER-HANDOFF.json sha256 449f11360cd725150286f831670dfcdbc87c213159fc2d12fc99bc7c6225c411
- Export index sha256 bd9f72875913e19507ad851996418affd0c2193883072a898ad0c0e3b3294109
- Details: RESULT-F04-FIX-NOW-12-RECORDING-RECONCILE.md

Not pushed. independent_owner_attestation remains false pending Richard.
