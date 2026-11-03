# Uplink Linux fault harness

North Star: reproducible, independently graded Repair/Reroute/Report trials on a real
Linux kernel, with the same versioned model-facing actions as macOS. The worker, paired
runner and twelve catalog scenarios are implemented. Dry runs are the default; live
namespace creation, fault injection and inference require separate authorization.

The target is the existing Lima VM `uplink-harness` on bench, with Linux hostname
`lima-uplink-harness`. The model stays on the bench host, outside the faulted client
namespace. No VM, package, host mount, firewall rule or public egress is provisioned by
this implementation.

## Dry rehearsal and source staging

From a source checkout, these commands have no network or namespace effects:

```sh
uv run --frozen --offline python -m offline_agent.uplink.lab_runner --fault all --arm both
sh harness/linux/setup.sh smoke --run-id 0123456789ab
```

The matrix covers all twelve faults for Uplink and the scripted baseline. Its Uplink
policy is deterministic synthetic plumbing, explicitly labeled MOCK. It is not model
inference, recovery evidence, or footage suitable for a real-agent demo.

The VM has no shared source mount. The host uses `limactl copy` to transfer four stdlib
worker files plus `setup.sh` into a private, content-addressed VM directory. Every file's
SHA-256 is verified before publishing the directory and before each privileged worker
invocation. Existing mismatched directories are refused, never replaced. Staging does
not install packages, change VM mounts, create root files, start daemons, or inject faults.

On bench, after staging this checkout under `${LAB_BENCH_ROOT}/uplink-u1`:

```sh
cd ${LAB_BENCH_ROOT}/uplink-u1
PYTHONPATH=src ${LAB_BENCH_ROOT}/offline-agent/.venv/bin/python -B -m offline_agent.uplink.lab_stage --stage
```

This returns the verified VM directory and a successful no-effect worker probe.
`setup.sh` works from a source checkout or beside the staged workers. It supports `up`,
`check`, `inject`, `inject-wrong-dns`, `restore-dns`, `undo`, `down` and `smoke`. Actual
execution requires `--execute` and an explicit 12-hex run ID, inside the named VM as root.
Tests cannot enable that execution path.

## Topology and oracle

Each run owns two namespaces, `ul-<run-id>-c` and `ul-<run-id>-i`, connected by two veth
pairs. The client has a primary DHCP route and an alternate route. The isolated internet
fixture uses a virtual server address; it has no public upstream or host forwarding.
Address blocks are documentation ranges `ADDRESS_REMOVED/24`, `ADDRESS_REMOVED/24` and `ADDRESS_REMOVED/24`.
The earlier `uh-client`/`uh-inet` smoke fixture is never reused or deleted.

The worker embeds existing components: iproute2 namespaces/routes, dnsmasq DNS/DHCP,
busybox DHCP acquisition, tinyproxy HTTP CONNECT, OpenSSL certificate fixtures, and tc
packet matching. A small standard-library HTTP/TLS server provides deterministic portal
and application responses. No replacement DNS, DHCP, proxy or process supervisor is built.

Tinyproxy receives its run-owned configuration through the existing namespace-specific
`/etc` bind mount, at `/etc/tinyproxy/tinyproxy.conf` inside that namespace. This honors
the stock AppArmor rule for the default path. Foreground output uses the inherited
run log; the proxy opens no shared log or PID file. Cleanup removes only that run's
fixed configuration file and directory. A worker refusal preserves its bounded reason
code in the evaluation receipt, including the daemon name when startup fails.

The independent `uplink-linux-lab-v1` oracle resolves two names through the client's system
resolver, validates TLS with a fixture-specific CA through the application's actual proxy
path, and checks both captive responses. The fixture CA is private to the run and is never
installed into a host trust store. This oracle profile cannot certify a real Mac as online.
An application handshake is additionally required for accepted end-to-end success.

The controller proves both primary and alternate connectivity before presenting a route
as previously successful. The model sees observed state, not the injected fault label.
Model requests use the closed action schema and its generated GBNF; they never contain a
shell or arbitrary argv tool. The scripted baseline uses the same diagnostics and actions.

## Faults and guarded execution

`docs/UPLINK-FAULTS.json` is the versioned catalog. The implemented cases are wrong DNS,
stale proxy, DNS plus proxy, lease loss, bad default route, interface down, captive portal,
upstream failure with a saved alternate, healthy control, site-only outage, MTU black hole,
and misleading association text. The held-out MTU fixture drops packets larger than 1200
bytes with an isolated tc ematch filter. Healthy controls require zero changes.

Live evaluation, after the explicit test go and with the pinned model server already
running, uses this command on bench:

```sh
cd ${LAB_BENCH_ROOT}/uplink-u1
PYTHONPATH=src ${LAB_BENCH_ROOT}/offline-agent/.venv/bin/python -B -m offline_agent.uplink.lab_runner --execute --approved-lab --fault all --arm both
```

The command verifies the actual GGUF, llama.cpp build, active server model path and host
chip, then stages the worker. The model runs on localhost on the host, while diagnostics
and changes run only inside the owned client namespace. Every admitted repair has an
exact confirm card, an explicit confirm phase and an independently running 20-second
watchdog. Partial oracle improvement is not ONLINE. A watchdog-completed recovery never
earns agent credit, including a watchdog firing during verification.

The root worker journals intent before mutations. A failed or interrupted rollback is
persisted before another operation can run; neither a controller call nor a watchdog can
retry it or arm a new timer. The matrix stops on refusal, rollback failure, cleanup failure,
provider failure, or unproven control state. It never silently skips a failed case.

## Ownership, cleanup and evidence

Setup refuses collisions with namespace names, host veth names and both resolver
directories. A fresh owner nonce is persisted before resources are created. If setup
fails midway, cleanup is attempted once only when that nonce proves this invocation owns
the fixture. It removes owned namespaces, leftover host veth ends and scoped resolver
files. Existing fixtures are never cleaned up as a side effect of a collision.

Daemon PID/start-time pairs prevent cleanup from targeting reused PIDs. Cleanup preserves
the private audit and compares a value-blind fingerprint of host addresses, routes and
resolver state. Unexpected host changes or failed cleanup stop the matrix without retry.

DHCP acquisition allows three discovery packets two seconds apart, within a nine-second
process timeout. This covers the fixture server's initial address-conflict check; two
packets previously expired as the first offer arrived. A failed acquisition still stops
the cell without retry. dnsmasq writes its DHCP diagnostics into the run-owned `dns.log`,
and setup failures retain the last 8192 characters alongside command stderr and exit code.

Run whole-repository verification from a checkout whose ancestors do not include a
directory named `artifacts`. The existing F9 packager filters absolute path components,
so a frozen source checkout nested beneath `artifacts` produces an incomplete package
and fails its manifest tests. An identical checkout outside that path preserves the
test contract; this restriction does not affect the Linux namespace acceptance runner.

Each trial writes a version-3 JSONL trace and evaluation receipt under ignored
`artifacts/uplink/evals/`. Traces retain primary/alternate qualification, redacted raw
oracle RPC receipts, model output, supplied llama.cpp timings, confirm cards, observed
changes and final outcomes. Raw app handshake receipts are retained alongside the oracle.
Receipts carry the catalog digest, model provenance and a live-versus-MOCK evidence label.
No dry result satisfies the Linux-running gate or the three-real-Mac-recoveries gate.

The earlier `offline_agent.uplink.linux` planner remains an inert API for validating the
original design's JSON plans. Its three-namespace blueprint is not the implemented
runtime topology and its unconditional execution veto remains intact.

References: [namespace-specific resolver mounts](https://man7.org/linux/man-pages/man8/ip-netns.8.html),
[tc extended packet matching](https://man7.org/linux/man-pages/man8/tc-ematch.8.html),
[Lima shell targeting](https://lima-vm.io/docs/reference/limactl_shell/), and the installed
`limactl copy --help` contract. Source placement, partial-setup ownership, raw probe capture
and verification-time watchdog races are pinned by the Uplink regression tests.


Worker commands may wait up to 500 ms to acquire the per-run lock before loading
state or starting an operation. This avoids a spurious refusal when the watchdog
briefly holds the same lock to inspect its deadline. A longer conflict returns
`lab_lock_busy_no_operation`; it does not retry any network action, extend a timer
or bypass the loaded-state/deadline checks. Watchdog rollback behavior is unchanged.
