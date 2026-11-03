# Reduced Bench F04 package

This package registers one F04 DNS-fault observation using unadapted Q4 BASE and
`snapshot-restore-descriptions-v1`. It is an offline review artifact. It does not
authorize contact, staging, root installation, fault injection or inference.

The prior 21/21 result is MODEL_IN_LOOP_SIM, tuned after F06/F07 inspection, not
held-out and not live Wi-Fi. This package has no measured live recovery result.

## Binding and activation

`FROZEN.json` contains a new run ID, registration nonce, explicit file inventory,
profile/model/prompt hashes, one-slot limits and privileged write set. The tar
hash binds that document and its exact contents. `bench_package --verify` checks
archive membership, types, modes and bytes without extracting or contacting hosts.
The archive contains no model weights, private configuration or actual approvals.

Fresh owner handback, Richard approval and KAYLIN-OK are separate private JSON
documents. Templates deliberately have `authorized: false` or `released: false`,
null live coordinates and null hashes. They always refuse execution. Do not turn
the templates into an approval on an operator's behalf.

Richard's `window_binding_sha256` is the canonical JSON digest of the complete
window excluding only `richard_approval_sha256`. Populate the owner hash and all
target/window coordinates first, then bind Richard's document, then place its raw
file hash in the window. This binds every target coordinate without a hash cycle.

Review the exact model PID/start time, one idle server slot, context (4096 to 8192),
empty adapter set, BASE file hash/size/build, physical host identity, private-config
hash, owner release, thirty-minute window and physical-console/manual-recovery
plan. The existing server is never restarted or reconfigured. Each decision may
make one read-only `/tokenize` request before its one completion; both share the
eight-second deadline. A prompt that does not fit with 192 output tokens refuses
before completion. There are zero warmup or other unscored completions.

Staging remains a separately authorized operator action. Preserve the original tar
beside the extracted `bench-reduced-<run_id>` directory under the approved bench
directory. Supply the existing approved private `lab.local.json` at the package's
`src/offline_agent/uplink/lab.local.json` path with mode 0600. Never alter the tar,
source files, frozen dependencies, model, credentials or management settings.
The client and root reservation verify the archive hash and extracted inventory.

After a fresh exact KAYLIN-OK, the entry point is
`python -m offline_agent.uplink.bench_run --execute --package <exact-tar>
--authority <private-kaylin-json> --owner <private-owner-json>
--richard <private-richard-json> --output <new-private-receipt-directory>`.
Without `--execute` the entry point only inspects the local registration.

## Execution and durable state

The client consumes a canonical run-ID marker before its first Relay probe.
Relay load and identity precede each target probe. Three consecutive pinned OOB
successes are required, at most five attempts and one transport failure, eight
seconds per probe and sixty seconds total. Scope/window-bound evidence expires
after ninety seconds. Failed or indeterminate control responses never reconnect.

Root consumes the registration/approval nonce before preflight. The root ledger,
payload copies, authoritative snapshot and audit events live only under
`/private/var/db/uplink-bench-reduced`. An incomplete install or preflight remains
consumed. Each completion intent is durably consumed before inference; a missing
result leaves it pending and refuses retries. No TOP10 run ID, approval or output
directory can be substituted.

The exact frozen Engine, MacKernel, native binary, snapshot primitives, full
13-tool grammar, parser, ONLINE oracle and scorer are reused. Root permits only
F04 injection. The model can choose all thirteen actions; three held restore
actions still refuse. Non-held IPv4/DHCP, DNS, gateway, proxy and MTU restorations
use only captured state. No shell/argv is accepted from the model.

One root-owned `com.uplink.bench-reduced.<run_id>` launchd watchdog must be proven
running before injection. The unchanged watchdog begins restoration at 45 seconds
or on controller cleanup request, with the shared 30-second restore budget.
Its root payload is byte-checked against the approved inventory. Cleanup does not
depend on source-tree availability, fresh owner/OOB evidence, the model server or
the active window. Failed restoration is terminal and requires manual recovery.
Retirement requires a successful launchd listing proving this job absent.

The controller allows at most 110 seconds of trial work and at least 40 seconds
cooldown. Preflight has 600 seconds; handback and reserve retain 600 and 300
seconds. Remaining-window checks can refuse earlier. No retries or replacement
faults extend these ceilings.

## Receipts and evidence limits

The final `reduced-result.json` has kind `uplink-bench-reduced-v1`, planned_trials=1,
recorded_trials at most one and top10_complete=false. It retains the one trial's
prompt/grammar/raw output, parsed action, negative control, independent pre-cleanup
scoring proof, resource/OOB checks, exact restore/close/retire evidence and every
failure. Model recovery is never credited to the watchdog or controller cleanup.
An honest report remains a non-recovery. Raw snapshots and device coordinates stay
private; no evidence or source is deleted.

The new controller is a narrow copy of the frozen trial controller because the
parent hardcodes a historical PID. Its differences are the actual approved PID,
reduced metadata and stricter repeated-action accounting. Scoring functions and
acceptance/edge oracles remain unchanged. The new explicit inventory avoids the
historical manifest validator's dynamic membership rule when new modules exist.

Offline tests use the existing OS fence that denies network and host-setting
executables. Fake-kernel watchdog and synthetic-policy results prove controller
safety only. No offline result is a BASE model run or live Wi-Fi observation.
