# Real-host probe acceptance

Probe-enabled means at least one model-generated probe other than `read_state` ran
in the configured device's sandbox and returned a result recorded in the recovery
trace. A `read_state`-only trial proves no network measurement. A requested flag, successful
restoration, preflight control or independent ONLINE check cannot establish this.
The historical trials 11-13 correction (private source artifact omitted) remains in force.

## Admission and positive control

`macos_sandbox` uses the private configuration's host identity, the same admission
authority as the live bridge. A missing or invalid private config, an unnamed host,
root execution or a non-macOS host is refused.

The macOS probe child uses the resolved base interpreter with `-I -S -B`.
It imports only the standard library and the pinned probe protocol, so Python
site initialization is unnecessary. Disabling site prevents virtualenv metadata
reads outside the sandbox; it does not broaden filesystem permissions. The
virtualenv-layout regression fails against the pre-fix implementation and passes
against the repaired command. Real-host startup remains part of live preflight.

For `--probe-scripts`, the unified preflight adds `probe_positive_control` after
staging readback and before any root prepare. It executes one fixed `read_state`
probe through the actual Wi-Fi capture and unprivileged sandbox. It requires a
completed row, a non-root child, populated state, unchanged settings hashes and an
unchanged Wi-Fi route. Refused, empty, timed-out or changed-state results persist a
typed refusal and stop the trial before snapshot, watchdog or injection.

The control has its own ledger under the owned preflight directory. It cannot
consume or reset the recovery episode's eight-probe budget or 55-second watchdog.
No model request is made by the control. Its remote call has a 30-second timeout.

## Receipt and report contract

The controller reads its completed, validated trace before issuing a trial receipt.
Version 2 `probe_execution` records requested, attempted, returned and
`non_read_state_returned` counts for each
`generated_probe_episode` observation and binds the evidence with `trace_sha256`.
A completed negative measurement from a non-`read_state` primitive qualifies;
an empty refusal, timed-out operation or `read_state` alone does not.
Malformed or missing execution evidence makes accounting incomplete and refuses
probe-enabled credit, even when another row returned successfully.

`probe_scripts` records requested configuration. `probe_enabled` comes from the
trace and requires at least one returned non-`read_state` row with complete accounting
and unchanged
state. `recovered` remains the independently verified restoration result.
`trial_passed` additionally requires probe-enabled credit when probes were requested.
A recovered trial with no returned non-`read_state` probes stops the batch, exits
unsuccessfully,
and is journaled as `RECOVERED_WITHOUT_PROBES`. Summary and overlay output preserve
this distinction. The top-level trial receipt and overlay also expose
`non_read_state_returned`. A `read_state`-only trial carries the typed reason
`no_returned_non_read_state_probe_rows`. The overlay reads episodes from the trace,
not optimistic receipt
flags or copied episode lists.

## Artifact privacy

The configured device serial is registered before live evidence is serialized.
Authorization metadata, nested records, embedded JSON, command strings and display
text replace the serial with a pseudonym, including occurrences inside longer
identifiers. The scanner detects case variants without reporting the value.
Runtime authorization and root-owned restoration snapshots remain private control
inputs; redacted receipts are not used as runtime authorization.

Historical raw artifacts and Git history are not rewritten. A new live run still
requires a new window, exact source and native staging proof, current clearance,
model attestation and all existing restoration, OOB and protected-service gates.
Passing desk tests does not establish a live positive control or three new recoveries.
