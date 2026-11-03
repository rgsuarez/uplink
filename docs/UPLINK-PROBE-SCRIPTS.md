# Evidence-led probe programs

Real-host admission, positive-control requirements and trace-derived trial credit
are specified in [Real-host probe acceptance](UPLINK-PROBE-ACCEPTANCE.md).

`--probe-scripts` adds a two-request diagnosis path to the existing Glimmer runners.
The first request emits a hypothesis and a small read-only probe program. The trusted
interpreter validates and executes that data without root privileges. The second
request revises the hypothesis and emits an existing typed repair. Every repair still
passes confirmation, the original independent oracle and rollback checks.

The program can read normalized state, ping a supplied gateway, query a fixed name
against a supplied resolver, connect to a fixed endpoint, issue HTTPS HEAD through
the effective proxy, or check a fixed captive endpoint. It cannot supply code, shell,
paths, URLs, arbitrary addresses, listeners or settings commands. The shared episode
ledger admits at most eight probes and 30 seconds of reserved probe time. Every probe
has a wall timeout of at most five seconds; ping bursts allow at most 20 packets with
at least 200 ms spacing. Refusals and timeouts are retained as observations.

Linux execution requires the existing `uplink-harness` VM, the run's owned network
namespace, a private mount namespace, a read-only runtime in a run-owned chroot,
UID/GID 65534, no-new-privileges and dropped inherited/ambient capabilities. macOS
execution requires the approved bench identity, an unprivileged process and a
deny-by-default sandbox. TCP/TLS sockets bind to Wi-Fi itself. Neither child can
write settings or the parent ledger. Only the parent writes inside the run's
artifact directory. No package or service installation is needed.

The optional profile uses a 55-second initial Linux watchdog and a 55-second
detached Mac watchdog. It never extends an active deadline. Default profiles retain
their original 20-second Linux and 40-second Mac limits. The probe timeout also
reserves time for the typed action and verification. Live Mac faults still require
separate clearance and all existing serial, OOB, exact-settings, spacing and stop gates.

Hypothesis labels are descriptive. Observed snapshot drift permits the corresponding
snapshot repair. A previously independently qualified alternate also needs fresh
successful alternate gateway and DNS probes plus an unchanged-settings proof. That
probe grammar requires both alternate checks when the primary fails without DNS/proxy drift.
The reversible route must be attempted before report becomes admissible. A measured down
link may permit cycling only after the route option is exhausted. A failed attempt is
verified, rolled back and handed off without recovery credit. Probes never grant ONLINE.

The receipt and trace retain the verbatim generated program, validated program,
privacy-filtered output, both hypotheses, typed action, citations and decision timing.
`python -m offline_agent.uplink.probe_overlay RECEIPT --trace TRACE` produces a compact
probe, hypothesis, action and independent-check view from those original files.
`python -m offline_agent.uplink.replay replay TRACE --speed 1` replays the original trace.
Linux fixture application handshakes are separate from real cloud-task resumption.


After a verified route change, the Linux probe controller can admit one extra
snapshot DNS or proxy restore when that setting has fresh drift. It requires a
prior attempt before the route, an actual alternate-gateway readback and independent
oracle improvement. Each restore still has a maximum of two attempts per episode;
other tools retain one attempt. The existing deadline and probe budget never reset.
A failed restore still rolls back and ends the episode.

When a primary link is down, or unchanged DNS fails on both independent names, a
qualified alternate precedes a proxy restore. Both fresh alternate probes are still
required. A working resolver intentionally selected by a verified route is retained
while the remaining proxy fault is repaired. The recorded 72-cell acceptance matrix
predates this follow-up; deterministic regression cases cover the new transitions.


The retry identity is `(action, target_path, state_sha256)`. `target_path` is the
canonical JSON array `[owned namespace, active default-route device, gateway]`.
The hash is SHA-256 of sorted compact JSON from `configuration_facts`: DNS server,
proxy URL, primary-link state, lease presence and default gateway. It excludes
clocks, counters and model text. The controller reads those facts again after model
selection and before admitting the write. An already-used key is refused.

The current hard cap is eight mutations per episode, at most one reroute, and at
most two attempts for each snapshot restore. The second restore additionally needs
a different path from its first attempt, a verified intervening route transition,
and fresh snapshot drift. `repair_budget.py` is the shared admission predicate;
the model receives only the controller's eligible retry tools. Typed actions,
confirmation, independent checks, snapshot restoration and the original deadline
still govern every accepted retry. The receipt retains both keys and the transition
index so a generator or evaluator can mirror the exact rule.
