# Lab Wi-Fi recovery runner

`uplink-real` rehearses DNS, proxy, combined DNS-plus-proxy, and Wi-Fi power failures on
lab. It defaults to a pure synthetic run. The live path is built and separately gated;
no dry result counts toward the three-consecutive-recovery gate. The current live
authorization permits DNS and web/secure proxy faults only. Wi-Fi power stays excluded.

Measured limitation: trials 11-13 (private source artifact omitted) restored three
effective DNS-plus-proxy faults, but all generated probes were refused. Their
requested probe flag did not prove execution, and repair decisions were forced.
Private-config sandbox admission, the real-probe preflight control and trace-derived
trial credit are implemented and desk-verified. Probe-enabled credit requires at
least one model-generated probe other than `read_state` to return results; receipts
report that count as `non_read_state_returned`. The real-host positive control and
three new qualifying recoveries still require separately authorized live acceptance.
See [Real-host probe acceptance](UPLINK-PROBE-ACCEPTANCE.md).

The acceptance oracle is `uplink-online-v1`: both system DNS lookups, both certificate-
validated HTTPS requests through the effective Wi-Fi proxy path, and both captive checks
must pass. HTTP requests bind explicitly to the Wi-Fi interface. The default route must
match that interface before and after the probe. A successful OOB connection is never
connectivity evidence for the Wi-Fi trial. Cached DNS or a fault that does not actually
break this oracle causes an aborted trial, not a recovery credit. DNS and combined
trials require both DNS checks to fail after injection; a proxy-only outage cannot earn
a combined-fault recovery. Diagnostics remain available to the model under the same
fixed deadline, and prior typed actions are carried forward in its next prompt.

## Rehearsal

From the source checkout:

```sh
uv run --frozen --offline uplink-real --fault dns_and_proxy --trials 3
```

The four accepted fault names are `wrong_dns`, `stale_proxy`, `dns_and_proxy`, and
`wifi_power_off`. Rehearsals use a synthetic policy, never SSH, a model, root, or a network
write. Their receipts say `MOCK`, preserve the planned real command in each confirm card,
and always set `three_consecutive_dns_proxy_recoveries` to false.

Every trial writes a version-4 JSONL trace, a private receipt, and model provenance under
`artifacts/uplink/real/`. The summary links those receipts. Replay a trace with:

```sh
uv run --frozen --offline python -m offline_agent.uplink.replay replay artifacts/uplink/real/traces/<run_id>.jsonl --speed 0
```

The trace distinguishes intent, acknowledged apply, independent verification, and abort.
A transport loss after an apply request records an indeterminate write; it does not infer
that the command failed or retry it.

## Lab build and prerequisites

Copy `configs/lab.example.json` to `src/offline_agent/uplink/lab.local.json`, fill in
the operator-approved host aliases, home-relative runtime paths, device serial and
clearance metadata, and restrict the file to mode 0600. The empty example cannot
authorize execution. Keep the same private config beside the staged helpers on the
bench host. It is ignored by Git and excluded from release packages. Missing, malformed,
symlinked or publicly readable configuration refuses live execution.

The config's `clearance_file` names the separately supplied fleet clearance document.
The controller still requires that document, explicit `--confirm-live`, and
`--confirm-restores`. Configuring a target grants no live trial permission.

The controller runs on the workstation. All live operations use the configured OOB SSH
alias, including model calls (config `oob_host`, overridable with `--oob-host`).
The normal Wi-Fi SSH check uses config `wifi_host`, overridable with `--wifi-host`.
The OOB alias must reach bench through the USB Ethernet path
and resolve without the Wi-Fi DNS being tested. No fallback to the ordinary bench route
is implemented.

Before reserving a live window, verify the exact controller entry locally:

```sh
uv run --frozen --offline uplink-real --help
```

Require exit 0 from this worktree's environment. Native typecheck or staging
success alone does not prove the controller can start. If its offline cache is
incomplete, stop before contacting the lab. With separate download authorization,
populate the existing pinned cache using `uv sync --frozen --no-python-downloads`,
then repeat the offline preflight. Preserve `uv.lock` and `pyproject.toml` unchanged;
do not substitute another environment or dependency version. This check neither
starts a trial nor grants permission to reuse an aborted window.

The only pre-trial entrypoint is `real_preflight.run_preflight`, called by
`uplink-real` before every trial. After local authorization admission (required by
the transport guard), its order is OOB `true`, pinned `LabBackend.identity()` with
its bounded clock wait, serving llama.cpp build readback, native typecheck,
optimized helper build, zero-read/zero-write self-test, clearance reload, and
staging source/helper/config readback. With `--probe-scripts`, one fixed `read_state`
positive control then proves sandbox execution before any root prepare. This control
does not earn trial credit or consume the recovery probe budget. The original
300-second model freshness
limit and one-second maximum clock wait are unchanged. Freshness is checked again
at the end; expiration refuses without a second attestation.

Rehearse that exact entrypoint locally before asking for a window:

```sh
uv run --frozen --offline uplink-real --rehearse-boundary --probe-scripts \
  --output artifacts/uplink/new-boundary-rehearsal
```

This installs a process/socket audit barrier and stops before creating the first
`ssh lab-oob true` process. It writes `boundary-result.json`; it cannot contact
the bench, run native tools, or start a trial. Fake-transport end-to-end coverage is
in `tests/uplink/test_real_preflight.py`. Do not use standalone preflight wrappers
or repeat individual gates after a refusal.

Live preflight requires the separately authorized, newly owned staging payload
described below, including its private config. It does not provision staging or
grant a window. `uplink-real --execute` runs the chain automatically; do not run
another preflight ahead of it. `--preflight-only` is available only for a separately
authorized live preflight without trial preparation and requires the same explicit
execution confirmations. It is not a desk rehearsal.

Each run reserves a unique `<run-id>-preflight.json`, records the active gate
before effects, and preserves the refusal reason. A pre-trial refusal reaches the
existing abort path before any root prepare, snapshot, watchdog or injection;
there is no network state to restore. Later failures keep the existing single
restore attempt and OOB-loss rules. `ABORT-U1.md` appends instead of replacing
earlier refusals. Native build state and diagnostics remain private under the
owned staging directory; the previous binary remains until the new helper passes
self-test. No preflight failure automatically cleans up or retries a remote step.

Before any trial staging transfer, obtain a successful native typecheck receipt for
the exact source. In an approved normal-user temporary checkout on the build host,
containing only `scripts/check_macos_swift.py` and all of `harness/macos/`, run once:

```sh
python3 -B scripts/check_macos_swift.py --typecheck --output native-typecheck.json
```

This runs `xcrun swiftc -typecheck` on every Swift file, with a temporary module
cache, and removes its temporary inputs. It does not execute the helper or use root.
Do not run the compiler on the controller. Use the approved OOB route, checking
`ssh lab-oob true` before the temporary check. Return the receipt and remove the
normal-user temporary checkout. Only these typecheck inputs may be copied before
the gate; no trial staging directory, private config or compiled helper is involved.

On the controller, the required preflight verifies the receipt without compiling:

```sh
make macos-staging-payload \
  NATIVE_TYPECHECK_RECEIPT=artifacts/uplink/native-typecheck.json \
  MACOS_STAGING_REVISION=HEAD \
  MACOS_STAGING_PAYLOAD=artifacts/uplink/macos-staging.tar
```

`macos-staging-payload` depends on `macos-staging-preflight` and rechecks it before
creating the archive. Missing, failed, incomplete, parse-only or stale receipts
refuse before any payload is created. Every Swift source hash, the checker hash,
non-root execution, temporary cleanup and every compiler exit code must match.
The pinned Git revision must contain the checked Swift bytes. Any native or checker
edit invalidates the receipt. Only this gated archive may be transferred for trials;
do not substitute an unfiltered checkout copy. It includes `src/` except sealed
fixture helpers, `templates/`, `docs/UPLINK-FAULTS.json` and `harness/macos/`, with a
source manifest and the typecheck receipt. The payload also includes
`scripts/check_macos_swift.py` so the unified preflight can rerun the exact checker.
Private config and binaries are excluded. This checker packaging change invalidates
older checker-hash receipts; obtain new native proof in the next authorized window.

After the separate live release, transfer the gated archive under the private
config's newly owned `source_relative` path and verify its manifest. In the
examples below, `LAB_BENCH_ROOT` is the configured private benchmark directory. The unprivileged bridge reuses
`${LAB_BENCH_ROOT}/offline-agent/.venv/bin/python`; it does not install dependencies or start
llama-server. The unified preflight compiles and self-tests on the bench host, never
on the controller, using `xcrun swiftc -O` and the helper's `self-test` operation.
The runner invokes these gates; do not issue separate manual build commands.

The typecheck pass is required before staging, and does not replace this optimized
build or self-test after release. A parse-only check never substitutes for typechecking.
The native self-test verifies exact disabled/empty proxy round trips, absent configuration,
and stable digests. It performs zero network reads and zero writes. The compiled binary is
ignored. Apple SystemConfiguration supplies the configuration transaction and launchd owns
the detached watchdog; no custom daemon supervisor or authentication layer is introduced.

Live preflight requires:

- `ssh lab-oob true` before the trial and every remote operation, with bounded timeouts.
- A healthy Wi-Fi baseline, one enabled IEEE80211 `Wi-Fi` service, and both protected
  `AX88179B` and `Thunderbolt Bridge` services present. Wi-Fi must not be `en7`.
- No PAC/WPAD, SOCKS, or authentication-bearing Wi-Fi proxy configuration. Managed devices
  remain refused except for the explicit serial-bound DNS/proxy authorization in
  the private config, validated by `real_fleet.py`; that exception permits no MDM or profile change.
- Existing noninteractive sudo access on bench and its system `/usr/bin/python3`.
- An already-running localhost llama.cpp server serving the pinned GGUF. Preflight checks
  the actual file SHA-256, build `10353 (f8def7fe1)`, server model path, and Apple M4 Max chip.
  A missing server produces an error receipt and zero network mutation.

## Detached rollback

Prepare copies only the stdlib worker, its closed protocol, the fleet gates, the private
config and its stdlib reader, and the native bridge into
`/var/db/uplink-real/runs/<12-hex-id>/`, owned by root with directory mode 0700 and private
files. The snapshot contains the exact Wi-Fi DNS and proxy property lists and power state.
It never includes keychain data. Root audit rows contain only timestamps, operations, and
reason codes. A global root lock prevents overlapping trials; an unresolved prior run
blocks a new one.

The root launchd job is `com.uplink.real.<12-hex-id>`. Its deadline is 40 seconds, or
55 seconds with `--probe-scripts`, leaving
margin below the requested 60-second bound for one-second native command limits and restoration.
It does not depend on SSH, the controller, or llama-server. The deadline cannot be extended
or rearmed. Injection requires a ready receipt and a matching live PID in launchd. An
unexpected worker exit restarts against the original deadline, not a new timer.
Arming requires zero prior registered Uplink jobs and verifies exactly one after bootstrap.
Completed or reverted jobs are unloaded only after their terminal state, authorization and
exact settings check are validated. Retirement is audited before `launchctl bootout`,
then verifies that the job is absent. Snapshots and audit files are retained.

Only these changes are possible through the root protocol:

| Operation | Scope | Undo |
| --- | --- | --- |
| Wrong DNS | Existing Wi-Fi DNS entity, documentation address `ADDRESS_REMOVED` | Exact captured DNS entity |
| Stale proxy | Existing Wi-Fi HTTP/HTTPS proxy, localhost port 9 | Exact captured proxy entity |
| DNS plus proxy | Both entities in one preferences transaction | Both captured entities |
| Wi-Fi power off | Synthetic rehearsal only under the current authorization | Captured power state |
| Agent repair | Restore captured DNS or proxy only in live trials | Full captured snapshot |

There is no arbitrary command, arbitrary service, credential, firewall, VPN, profile,
service-order, bridge, or sshd mutation. Protected service configuration digests are
checked before and after changes. The model sees normalized drift facts, emits the
existing typed action language under GBNF, and cannot call the privileged protocol.

Each repair needs a matching confirm card. The explicit `--confirm-restores` policy
approves only restoring the current trial's snapshot. After each repair, the independent
oracle must improve. A partial improvement can retain the same run watchdog while the
next snapshot component is restored. Disarm requires fresh ONLINE evidence and exact
snapshot equality. A watchdog restoration never earns an agent recovery.

On OOB loss, the controller stops immediately and makes no further remote calls. On a
failed or indeterminate rollback, neither the controller nor the watchdog repeats it.
The receipt names the failure. Do not start another trial; inspect the root-owned state
through the recovered control path or physically at lab. This is a hard stop, not an
automatic reconnect loop. After verified restoration and Wi-Fi SSH return, the controller
retires the finished launchd job. Failure to confirm retirement stops further trials.
Root audit/snapshot files remain for inspection.

## Live gate

After the explicit bench test go, the concrete command for the combined gate is:

```sh
uv run --frozen --offline uplink-real --execute --confirm-live --confirm-restores --fault dns_and_proxy --trials 3
```

The flag records explicit live intent; it does not grant authority by itself.
Three successful real receipts are required. The matrix stops on the first failed trial.
Each trial starts at least 120 seconds after the preceding verified restoration and SSH
handback. A proven pre-fault refusal produces an unnumbered `refused_no_fault` log entry,
with settings diff and Wi-Fi recovery marked `n/a (no change)`. It earns no gate credit and
does not reset the quiet interval. Ambiguous injection attempts remain numbered attempts.
Legacy log lines are immutable; corrections are appended separately.
Native compilation, dry tests, and read-only preflight do not prove launchd restoration
under a killed agent or real network recovery. Those remain live acceptance checks.

## References and operating lesson

Apple's SystemConfiguration SDK documents `SCNetworkProtocolSetConfiguration` with a
nullable configuration and `SCPreferencesCommitChanges`/`SCPreferencesApplyChanges` for
transactional persistence. The native bridge uses these APIs to preserve absent and
empty proxy fields that `networksetup -setwebproxy` cannot faithfully reconstruct.
The watchdog follows Apple's [launchd job model](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

Keep the rollback owner outside the agent's process tree, record intent before a write,
and distinguish a healthy route from a successful recovery on the route under test.
These are regression pins in `tests/uplink/test_real.py`, not operator memory requirements.
An exited or disarmed launchd job can remain registered. Treat process completion and job
retirement as separate states; `tests/uplink/test_real_jobs.py` pins their legal transition.


## Cross-process timer clock

Root helpers use `clock_gettime(CLOCK_MONOTONIC)` with a boot-session fingerprint.
The system Python 3.9 on the approved machine gives `time.monotonic()` a different
origin in every process; it must not be used for persisted watchdog deadlines,
injection/restore elapsed time, or cooldown comparisons. The same-process duration
of one restore operation uses `perf_counter` only as local latency telemetry.

Clock basis and boot identity are immutable for each run. Missing or mismatched clock
provenance refuses arm/apply/disarm; a watchdog with unproven timing restores once,
records unknown elapsed time and requires review. It never extends its deadline.
For a verified terminal run from an older clock format, preparation creates a separate
audited barrier and requires a fresh full 120-second quiet interval. Original timestamps
are preserved. Active or failed-rollback runs cannot use that migration path.
