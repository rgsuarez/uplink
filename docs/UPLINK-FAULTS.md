# Uplink fault catalog v1 (draft)

North Star: regain a verified application connection through Repair, Reroute or Report. U1 supplies read-only evidence and inert plans. This catalog is a design, not evidence of successful recovery. All fault injection and repair execution are deferred to a separately approved U2 environment.

## Acceptance and scope

- Wednesday, October 28, 2026 at 9 PM CT: three consecutive real Mac recoveries of DNS and proxy faults (including the compound case), with rollback, plus a running Linux harness. U1 does not satisfy that recovery gate.
- ONLINE requires two independent system-resolver DNS successes, two certificate-validated TLS/content checks over the effective app proxy path, and two passing portal checks. PAC/WPAD or scoped proxy uncertainty fails closed. A healthy independent oracle does not prove an app-specific request resumed; that separate handshake is the final product success metric.
- The expected verdicts below assume a single affected route, cold/unusable DNS for complete route outages, healthy test endpoints otherwise, and no unexpected fallback. A warm cache or unaffected fallback can produce DEGRADED instead of OFFLINE. The frozen U2 harness must record those preconditions and the actual evidence.
- Use a dedicated, reversible laboratory setting. No unknown networks, password/keychain reads, VPN/firewall/MDM changes, profile deletion, global network resets, or automatic portal login. No U1 setting changes or injections.
- JSON action values are synthetic scope handles. Bind them to discovered services, prior-evidence candidates, an actual pre-change snapshot and actual evidence before planning. They are never executable shell templates.

## Fault matrix

| Fault | Family | Wed gate | Expected oracle | Correct move | Fixability | Typed actions |
| --- | --- | --- | --- | --- | --- | --- |
| wrong_dns | dns | yes | OFFLINE | Repair | repairable | set_dns(policy=snapshot) |
| stale_proxy | proxy | yes | DEGRADED | Repair | repairable | restore_proxy(policy=snapshot) |
| dns_plus_proxy | compound | yes | OFFLINE | Repair | repairable | set_dns(policy=snapshot), restore_proxy(policy=snapshot) |
| lease_loss | dhcp | no | OFFLINE | Repair | conditional | renew_lease |
| bad_default_route | route | no | OFFLINE | Report | report_only | report |
| interface_down | link | no | OFFLINE | Repair | conditional | cycle_interface |
| captive_portal | portal | no | CAPTIVE | Report | report_only | report |
| isp_down_saved_alternate | upstream | no | OFFLINE | Reroute | conditional | reroute |
| healthy | control | no | ONLINE | no-op | no_change | none |
| site_only_outage | application | no | ONLINE | no-op/report | report_only | report |
| mtu_black_hole | transport | no | DEGRADED | Report | held_out_report_only | report |
| misleading_status | observation | no | ONLINE | no-op | no_change | none |

## Injection and undo contracts

### wrong_dns

Mac: Set a dedicated test service DNS to an unreachable documentation address, after saving exact DNS mode and servers.

Linux: Point the client resolver at the unreachable namespace address 192.0.2.254.

Undo: Restore exact pre-injection DNS mode and server list.

### stale_proxy

Mac: Enable both web and secure web proxies on a closed loopback port, after saving hosts, ports, state and exclusions.

Linux: Set both app proxy variables to the namespace proxy port with the proxy stopped.

Undo: Restore both prior proxy configurations, including enabled states; never change VPN/MDM or authentication.

### dns_plus_proxy

Mac: Combine the two approved DNS and proxy fixtures on a dedicated test service.

Linux: Combine wrong_dns and stale_proxy in the same client namespace.

Undo: Restore both independent pre-injection snapshots. Re-probe between plans; never call a partial repair online.

### lease_loss

Mac: Future isolated test adapter with an expired lease or isolated DHCP source; no command run in U1.

Linux: Stop the namespace DHCP responder, expire the test lease, and remove only the client namespace address.

Undo: Restore the exact address/route/resolver checkpoint or recreate the disposable namespace. Lease renewal is not exactly invertible; U1 plan remains blocked.

### bad_default_route

Mac: Future isolated test adapter with a wrong gateway; never edit the workstation default route in U1.

Linux: Replace only the client namespace default route with an unreachable gateway.

Undo: Restore namespace route from the harness checkpoint. No model-facing route-add/delete tool exists; a proven alternate may separately reroute.

### interface_down

Mac: Future spare adapter disabled by the operator; never down/up this workstation interface in U1.

Linux: Bring only the client namespace veth down.

Undo: Restore the original link state. cycle_interface is admitted only for an initially-up snapshot; an already-down adapter needs human enablement.

### captive_portal

Mac: Join a separately approved laboratory portal manually; never automate credentials or terms.

Linux: Serve a portal body/redirect for both HTTP connectivity checks in an isolated router namespace.

Undo: Remove the portal stub and recreate the clean namespace. Human portal completion is not an agent repair.

### isp_down_saved_alternate

Mac: Unplug an isolated demo router while a previously verified Ethernet, phone USB or saved hotspot route exists.

Linux: Disable the primary router namespace uplink while retaining a second isolated path with prior online evidence.

Undo: Restore exact service order/route checkpoint. Wi-Fi selection and USB/hotspot prerequisites remain human-gated and unproven on macOS 26.

### healthy

Mac: Do not inject anything; collect fresh evidence only.

Linux: Healthy namespaces and endpoints; no injection steps.

Undo: No mutation, no undo required.

### site_only_outage

Mac: The owned demo cloud endpoint returns a 503 while independent oracle endpoints remain reachable.

Linux: Return 503 from the isolated app endpoint only.

Undo: Restore only the app stub. Do not change laptop networking; generic connectivity is not proof the interrupted request resumed.

### mtu_black_hole

Mac: Held out: external isolated router drops large packets; no workstation MTU edits.

Linux: Set the client veth MTU and use tc netem loss in a disposable namespace; packet-size-selective loss/PMTU suppression needs U2 implementation.

Undo: Restore namespace MTU/qdisc checkpoint or recreate the namespace. No MTU change action is admitted.

### misleading_status

Mac: Replay macOS 26 not-associated text alongside a valid lease, route and successful independent oracle.

Linux: Synthetic parser observation only; keep all namespace connectivity healthy.

Undo: No mutation, no undo required.

## Scripted baseline and evaluation boundaries

Freeze this manifest, endpoint bodies, seeds and split before tuning prompts. Keep held-out MTU and compound variants out of model context. The evaluator owns fault identities; the agent sees only diagnostics. Run a scripted troubleshooter with the same tool budget, confirmation cost, snapshots and oracle: probe; no-op if ONLINE; restore proven DNS drift; restore proven proxy drift; consider a previously verified alternate; otherwise report one human step. Always re-probe and revert unsuccessful changes before another mutation. Do not blindly reset networking.

Compare model, model without field manual, and scripted baseline on identical scenarios. Report recovery k/n with uncertainty, elapsed time including consent, unnecessary changes on healthy and site-only controls, false online declarations, failed rollback, correct report/physical step, and interrupted-request completion. Separate Linux namespace results from Mac hardware trials; namespaces cannot prove radio behavior, hotspot discovery, USB authorization, or macOS privilege semantics. Record ties honestly.

## U1 limitations and U2 requirements

Change execution is unconditionally refused. Cards for cache eviction and DHCP renewal explicitly have no faithful inverse and remain blocked. Configured-service reroute plans change service order only; a saved SSID is a candidate, not permission or proof that a CLI join will work. A saved profile is weak evidence, a lease is stronger, and a prior ONLINE snapshot applies only to its default route. Snapshot files contain HMAC handles, not reversible proxy-host mappings; cross-process exact restoration requires a separately designed private binding mechanism.

Before real repair: bind approval to the exact command hash and pre-change state, take a rollback snapshot even when offline, start an independent watchdog before mutation, check managed-policy restrictions, compare state before reverting so human edits survive, and record rollback failures. Restoring a last-known-good target and undoing the attempted repair are different snapshots. No auto-revert is implemented or advertised as active in U1.
