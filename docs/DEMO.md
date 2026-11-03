# Offline demo script

Run `make demo-watch` after `make setup`. It runs three frozen v2 training fixtures in order:
two repairs from different source families, then one impossible task. Each repair visibly
runs red tests, shows a replacement-block diff, reruns green tests, and declares fixed.
The give-up panel includes the immutable failing test, its missing dependency and the reason
the writable source boundary cannot restore it. Every frame is visibly MOCK.

`offline-agent watch artifacts/<run>/<trace>.jsonl --delay 0.15` replays an existing trace.
Add `--follow --timeout 60` to tail a trace being flushed by a running agent. Partial lines
wait in follow mode; malformed lines, replacement/truncation, forged actions, unsafe fixed
claims and timeout refuse. An incomplete replay never becomes a successful run.

The network badge is sampled from the current host's IPv4 and IPv6 routing tables. OFFLINE
means no default route. It does not infer Wi-Fi state from a fixture, alter interfaces,
probe a server, or prove absence of a reachable local network. A route on Ethernet or VPN
keeps the badge online even if Wi-Fi is off. Unreadable tables display NETWORK UNKNOWN.
The badge describes the viewing host now, not historical network state of a saved run.

Budget counters use byte bounds for mock runs and reported tokens for live providers.
Model generation speed and model peak RSS remain UNMEASURED until a real measurement exists.
The measured Python harness peak RSS is labeled separately. A live trace can additionally
show reported output tokens per provider wall second, which includes transport overhead.
Grammar-constrained model sampling is not proven by mock schema validation.

Each demo writes three traces plus final viewer text/SVG captures into its ignored
`artifacts/demo-watch/<run>/` directory. Nothing calls a model or publishes an artifact.

Real network footage is separate from this mock coding demo. For trials 11-13,
use the corrected evidence labels (private source artifact omitted): three real
restorations in about 35 seconds, forced repair selection, and all six generated
probes refused. The model did not choose the repairs or correctly identify the
proxy layer during the proxy-repair steps.
