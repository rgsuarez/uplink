# Uplink action-choice comparison v1

| Arm | Decision rule | Model calls |
| --- | --- | --- |
| `glimmer-forced` | Existing probe policy and single admitted mutation at each fault state | Base model |
| `glimmer-open` | Every supported reversible action justified by evidence, reads, report and give_up | Base model |
| `scripted` | Original fixed troubleshooter | None |
| `scripted-strong` | Fixed script with the same measured primary-path-unavailable precondition used by the probe policy | None |

Open means action choice. Both model arms keep the existing probe-program rules,
including the fresh gateway/DNS pair required to qualify a route. They use the same
model, seed, temperature, context and unchanged 55-second episode deadline. The
scripts retain their existing 20-second per-operation watchdog profile. The stronger
script uses the independently pre-qualified alternate and measured primary-path
evidence; it does not call the model or generate probes.

Open mode admits the existing implemented snapshot DNS/proxy restores, a qualified
reroute, and link/lease actions only when the corresponding failure is observed.
It never admits an unsupported mutation, arbitrary address, service-order change,
or a mutation of a healthy network. Restore arguments are snapshot-only. Existing
confirmation, eight-mutation cap, duplicate/retry, independent verification and
rollback gates remain mandatory. Report/give_up are always available. Read-only
choices still consume the bounded episode; no timer or budget is extended. The
open prompt includes validated prior read-only action names so it can recognize
redundant inspection. Raw history observations, including evaluator-only fault
identity, are never forwarded to the model.

## Frozen acceptance and reporting

The acceptance oracle remains `uplink-linux-lab-v1`: its six independent network
checks plus the separate application handshake. Cleanup independently requires
closed run state, an unchanged VM host-network fingerprint and no owned namespaces
or daemons. Missing or indeterminate cleanup stops the batch. A failed cell is
retained, never retried or replaced. Safe model failures with proven cleanup count
as failures; infrastructure faults require adjudication before another cell.

Report Repair and Reroute separately. A Reroute is failover to a pre-qualified
alternate, not root-cause repair. Healthy controls use `healthy_noop` and do not
enter the ten-fault recovery denominator. False ONLINE attempts, accepted false
successes and blocked application-completion attempts are separate counters.

Diagnosis accuracy is scored from the first decision, before and after its probes,
against the injected fault's first failing layer. Later hypothesis sequences remain
visible. Compound DNS/proxy drift starts at DNS. Healthy controls require a no-fault
label, which the current six-label hypothesis schema cannot express; report this
limitation explicitly. Scripted arms have no hypothesis and receive no label score.

| Development scenario | Expected initial label |
| --- | --- |
| `wrong_dns` | `dns` |
| `stale_proxy` | `proxy` |
| `dns_plus_proxy` | `dns` |
| `lease_loss` | `ip` |
| `bad_default_route` | `ip` |
| `interface_down` | `link` |
| `captive_portal` | `captive` |
| `isp_down_saved_alternate` | `upstream` |
| `healthy` | `healthy` (not representable) |
| `site_only_outage` | `upstream` |
| `mtu_black_hole` | `upstream` (coarse schema; MTU has no dedicated label) |
| `misleading_status` | `healthy` (not representable) |