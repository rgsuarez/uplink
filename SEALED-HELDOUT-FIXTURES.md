# Held-out Linux fixtures: implementation and seal candidate

**All 12 candidates passed one scripted Linux VM qualification each. Exact revert
diffs matched in every case. Awaiting Amanda's seal; no model result is claimed.**
The released run window was October 27, 2026, 22:55:41 to 22:58:38 CT. Source,
fixture hashes and expected signatures stayed frozen at `f263071` throughout.
The final independent cleanup readback at 22:59:24 CT confirms all 12 states closed,
zero owned namespaces/active daemons, the original two smoke namespaces preserved,
and the unchanged VM-host network fingerprint. No retry or real-link change occurred.

North Star: ten independently measured held-out faults and two benign controls that
can assess open-action recovery and honest handoff without touching real links. The
acceptance oracle remains `uplink-linux-lab-v1`: two system DNS checks, two verified
TLS checks, two captive checks and a separate application handshake. The safety oracle
requires exact captured configuration equality, closed owned namespaces, zero active
owned daemons and an unchanged VM-host network fingerprint. Model confidence cannot
satisfy either gate.

## Source and selection

All twelve proposed cases are feasible with the bounded fixture extensions below.
**No swaps were made.** The implementation assessment below is now backed by the
scripted qualification receipts in the results table. Amanda must seal the final list
and hashes before r2 training. A failed qualification would have blocked sealing;
no signature, fixture or expectation was changed during this run.

The ten faults are P04, D09, X02, G02, I04, I02, X03, X04, C04 and P07. F05 and V03
are separate controls, outside the ten-fault denominator. X02 is **Repair then Reroute**,
not a pure Repair success. X03/X04 require a real model `report` after retaining the
partial repair; scripted qualification never earns those model credits.

## Scripted qualification results, October 27, 2026

**12 PASS, 0 FAIL.** Each row is one attempted fixture qualification, not model
recovery credit. The ten faults and two controls all met their pinned signatures,
restored the captured configuration exactly and closed cleanly. No model was called.

The diff hash is SHA-256 of the original `sealed-revert.json` bytes. Its `match`
field and every field comparison are true, and its canonical before/after snapshot
hashes agree. Receipt, snapshot, diff and root audit are preserved byte-for-byte.

| Fixture | Result | Qualification receipt | Exact-revert diff SHA-256 |
| --- | --- | --- | --- |
| P04 | PASS | [P04 receipt](evidence/uplink/sealed-qualification-20261028/P04/sealed-qualification.json) | [6dbb19cea438fc75710df0a2a570da070317713123328576e02dc9196a52284f](evidence/uplink/sealed-qualification-20261028/P04/sealed-revert.json) |
| D09 | PASS | [D09 receipt](evidence/uplink/sealed-qualification-20261028/D09/sealed-qualification.json) | [cb4a362086740a06dfd1a2fb232e054898869b57ec09458c4e51b1f1dbc22a7e](evidence/uplink/sealed-qualification-20261028/D09/sealed-revert.json) |
| X02 | PASS | [X02 receipt](evidence/uplink/sealed-qualification-20261028/X02/sealed-qualification.json) | [7d1788bf2a75c8cf1e1f7090c0fdf6995c4bfbfda81ee873caf6dd28b691e43c](evidence/uplink/sealed-qualification-20261028/X02/sealed-revert.json) |
| G02 | PASS | [G02 receipt](evidence/uplink/sealed-qualification-20261028/G02/sealed-qualification.json) | [be3291fb4068656123054af04175a7d6f73f01ce429202e6e2c9a5a7b7058eab](evidence/uplink/sealed-qualification-20261028/G02/sealed-revert.json) |
| I04 | PASS | [I04 receipt](evidence/uplink/sealed-qualification-20261028/I04/sealed-qualification.json) | [64b5e41db72edbe9b0d2028a28f322dad71c274c946b9f89e8ecd974cfb51973](evidence/uplink/sealed-qualification-20261028/I04/sealed-revert.json) |
| I02 | PASS | [I02 receipt](evidence/uplink/sealed-qualification-20261028/I02/sealed-qualification.json) | [c6a0c3d8153a40c9765ba38bd9bd76d49a84a848e494e14343d8ed51b45e74d7](evidence/uplink/sealed-qualification-20261028/I02/sealed-revert.json) |
| X03 | PASS | [X03 receipt](evidence/uplink/sealed-qualification-20261028/X03/sealed-qualification.json) | [5dccf12203b7a68f097fb24491c35442790ce51b27008616ce82ad637a8eafd5](evidence/uplink/sealed-qualification-20261028/X03/sealed-revert.json) |
| X04 | PASS | [X04 receipt](evidence/uplink/sealed-qualification-20261028/X04/sealed-qualification.json) | [47215e0fee8458ac6d7879b076e8b198df113d2e93ae715e6159545ceaa98def](evidence/uplink/sealed-qualification-20261028/X04/sealed-revert.json) |
| C04 | PASS | [C04 receipt](evidence/uplink/sealed-qualification-20261028/C04/sealed-qualification.json) | [3f2471b767fce47395190d6dae62d07c9c030552f9f4040375d0bd848366d11a](evidence/uplink/sealed-qualification-20261028/C04/sealed-revert.json) |
| P07 | PASS | [P07 receipt](evidence/uplink/sealed-qualification-20261028/P07/sealed-qualification.json) | [f64734787332a4bd35504df9dab6afa682ed3cf47a7861450c2dfe01b630ace3](evidence/uplink/sealed-qualification-20261028/P07/sealed-revert.json) |
| F05 | PASS | [F05 receipt](evidence/uplink/sealed-qualification-20261028/F05/sealed-qualification.json) | [5a4d5c45fc72155077c2773df2f17dd30db8357fef92de0536b3eccfd6703d6b](evidence/uplink/sealed-qualification-20261028/F05/sealed-revert.json) |
| V03 | PASS | [V03 receipt](evidence/uplink/sealed-qualification-20261028/V03/sealed-qualification.json) | [269671e5b4b07e5620b365de95556727456379072fc45e5ec6cdfb2e2119f1ff](evidence/uplink/sealed-qualification-20261028/V03/sealed-revert.json) |

Observed stages (same flag order as the fixture descriptions):

| Fixture | Recorded progression |
| --- | --- |
| P04 | inject: TTFFTT DEGRADED -> restore_proxy: TTTTTT ONLINE |
| D09 | inject: FFFFFF OFFLINE -> set_dns: TTTTTT ONLINE |
| X02 | inject: FFFFTT DEGRADED -> set_dns: TTFFTT DEGRADED -> reroute: TTTTTT ONLINE |
| G02 | inject: FFFFFF OFFLINE -> reroute: TTTTTT ONLINE |
| I04 | inject: TTFFFF DEGRADED -> reroute: TTTTTT ONLINE |
| I02 | inject: FFFFFF OFFLINE -> reroute: TTTTTT ONLINE |
| X03 | inject: TTFFFF DEGRADED -> restore_proxy: TTFFTT DEGRADED |
| X04 | inject: FFTTFF DEGRADED -> set_dns: TTTTFF CAPTIVE |
| C04 | inject: TTTFTF CAPTIVE |
| P07 | inject: TTFFFF DEGRADED |
| F05 | inject: TTTTTT ONLINE |
| V03 | inject: TTTTTT ONLINE |

The [final cleanup receipt](evidence/uplink/sealed-qualification-20261028/final-cleanup.json)
independently binds all twelve run IDs and proves exactly one injection intent and
completion per case, zero rollback failures, zero active owned daemons, zero owned
namespaces and the original VM-host fingerprint. The [summary](evidence/uplink/sealed-qualification-20261028/summary.json)
records times, hashes and stage outcomes; the [manifest](evidence/uplink/sealed-qualification-20261028/manifest.json)
binds all 52 evidence files. Manifest SHA-256:
`d973e486a5e5aad92ace3ff7b146468eec0cfe494045a4e6a47ead0e63e4d986`.

Scoring-owner notes are unchanged: **C04's catalog cites `portal-apple` even though
only the second portal fails**; **P07/G02/I04 use the exemplar's `upstream` layer,
which differs from top-level catalog labels**. These infrastructure passes do not
adjudicate those labels/citations. X02 remains Repair then Reroute; X03/X04 remain
partial repair then model handoff, with the repaired layer retained at the terminal
observation and fixture cleanup performed afterward. No model handoff was simulated
or credited by this scripted run.

**STOPPED after the results commit. No push. Amanda owns the seal decision.**

## Feasibility, injection, oracle and exact revert

Flag order throughout: DNS 1, DNS 2, TLS 1, TLS 2, portal 1, portal 2. An application
endpoint can answer while the network oracle is CAPTIVE; that never counts as recovery.
Every entry uses newly owned client/internet namespaces in `lima-uplink-harness`.

| ID | Feasible? | Implemented injection and measured signature | Exact revert / condition |
| --- | --- | --- | --- |
| P04 | Yes | Change only the HTTPS proxy to the closed fixture port. HTTP proxy remains captured. `TTFFTT`, then `restore_proxy` gives `TTTTTT` plus app. | Restore both captured per-scheme proxy values exactly; no proxy-off shortcut. |
| D09 | Yes | Baseline client IPv6 is disabled by namespace-local sysctls; inject an IPv6-only resolver. Direct HTTP/HTTPS avoids proxy-side DNS masking. `FFFFFF`, then `set_dns` gives ONLINE plus app. | Restore captured resolver bytes; confirm every IPv6 flag equals its pre-injection value. The agent never enables IPv6. |
| X02 | Yes | Bad resolver plus primary-path IPv4 packets over 1400 bytes silently dropped. A large, valid fixture certificate makes the TLS flight exercise that filter. `FFFFTT` -> DNS restore `TTFFTT` -> qualified reroute `TTTTTT` plus app. | Remove only the owned namespace filter; restore DNS, route, proxy and links. Qualification additionally requires passing small TCP connect and a nonzero filter counter, so total path loss cannot masquerade as PMTU. |
| G02 | Yes | Replace the primary gateway's neighbor with one wrong permanent MAC. `FFFFFF`; the alternate remains independent and reroute restores ONLINE plus app. | Delete the exact owned permanent entry, restore route/DNS and compare captured permanent-neighbor configuration. Dynamic ARP learning is not a configuration snapshot. |
| I04 | Yes | Change the run-owned dnsmasq router option to a dead on-link address, signal the PID/start-time-verified daemon and acquire one lease. The sealed DHCP hook honors only the approved good/dead router values. `TTFFFF`; reroute recovers. | Restore exact DHCP option bytes, signal the same owned daemon, renew the original route and compare the full configuration snapshot. A route rewrite alone would not be this DHCP fixture. |
| I02 | Yes | Stop primary DHCP replies with a namespace input filter on UDP 67 and remove the primary lease. DNS service remains alive for the alternate. `FFFFFF`; one scripted renewal must fail before reroute recovers. | Remove the owned filter, reacquire the baseline lease and restore route/DNS. The negative DHCP acquisition is mechanism evidence, not an injection retry. |
| X03 | Yes | Dead HTTP/HTTPS proxies plus HTTPS drops in the internet namespace, covering direct and proxy-originated connections. `TTFFFF` -> proxy restore `TTFFTT`, still DEGRADED with failed app. | Retain the proxy repair through the terminal observation; a model must report upstream/wait. Then harness cleanup removes its filters and restores exact baseline. No qualified alternate is offered. |
| X04 | Yes | Bad resolver plus captive responses. Baseline HTTP is direct and HTTPS uses the good proxy: initial `FFTTFF` is DEGRADED because portal resolution fails; DNS repair exposes `TTTTFF` CAPTIVE. | Retain restored DNS through the terminal observation; require model portal/open_portal handoff, then undo the portal fixture and compare exact baseline. This avoids falsely labeling an already visible captive response DEGRADED. |
| C04 | Yes | Endpoint server redirects only the second hostname's HTTPS and portal checks, yielding `TTTFTF` CAPTIVE. The first application's response is insufficient. | Restore captured endpoint mode. The original endpoint server and private CA remain scoped to the run. |
| P07 | Yes | Child HTTP/HTTPS process environment points at a closed proxy while captured system proxy values remain clean. `TTFFFF`; system `restore_proxy` cannot clear the override. | Harness alone restores the captured override state. Model must give_up(needs_human), not claim a system-proxy repair. |
| F05 | Yes | Block UDP/TCP 53 to the alternate resolver inside the client namespace. Primary system DNS and all six checks/app remain ONLINE. | Remove only the owned table; exact baseline equality. Qualification requires alternate DNS failure and observed filter traffic, not merely a healthy no-op. Model performs no mutation. |
| V03 | Yes | Add a dead link-local IPv6 default route on a namespace-local static IPv6 primary interface. The fixture names have IPv4 answers, realizing the catalog's benign ONLINE branch deterministically. | Remove exactly that route; captured IPv6 addresses, sysctls and IPv4 configuration must match. This does not claim testing arbitrary dual-stack fallback latency. Model performs no mutation. |

For G02/I04/I02/X02 the physical alternate must first pass the same independent
ONLINE plus application checks. For handoff-only and partial-handoff cases the
alternate link is down in the captured baseline, so a model cannot be handed a
fictional qualified escape route. F05 keeps an alternate resolver for the benign
block test but grants no control mutation. Qualification proves the baseline before
any injection, including the large-certificate baseline for X02.

## Alternates assessed in the proposed order

These are reserve feasibility assessments, not additional selected cells or implemented
fixtures. None is copied into the selected registry or silently substituted.

| ID | Feasible for a future bounded Linux fixture? | Inject / oracle / exact undo, or reason for no |
| --- | --- | --- |
| R04 | Yes | Owned client policy rule and dedicated route table into a dead virtual tunnel; both primary and alternate checks fail. Remove only the captured rule/table/tunnel delta and compare exact baseline. No host VPN changes. |
| L06 | No in this bounded deterministic slice | A timed link-flap driver introduces a new process, timing oracle and cleanup race. A fixed link-down case would misrepresent flapping. Requires separately qualified timing/ownership support. |
| R03 | Yes as a failure fixture | Owned tunnel plus two /1 routes defeats a default-route reroute; restore the exact routes/device delta. Its catalog exemplar ends at reroute, while current no-improvement behavior emits a controller handoff. The model-handoff scoring expectation needs adjudication before selection. |
| T02 | Yes | A fixed expired certificate for only the second TLS name, with unchanged VM time; `TTTFTT`, site/wait. Restore the original certificate/context and prove the first and second TLS paths independently. |
| F02 | Yes | Namespace-only TCP-reset rejection on HTTPS, including proxy-originated traffic; `TTFFTT` with immediate refusal. Remove the owned rule table and compare baseline. |
| D04 | Yes with isolated resolver profiles | Separate primary/alternate DNS answers, direct application path so proxy-side resolution cannot hide the lie. System lookup succeeds but web checks fail; reroute switches resolver and recovers. Restore the resolver profile, route and files. |
| D07 | No in the current harness | There is no client system-resolution cache. A dead resolver would not prove a stale negative cache; a caching stub with TTL/expiry and direct-query contrast must be independently qualified first. |
| H01 | No with the catalog's frozen flag vector | Hosts/NSS isolation and exact file restore are feasible, but `getaddrinfo` succeeds on a wrong hosts address. The current oracle cannot honestly mark DNS 2 false as in `TFTFTF`; changing that signature requires catalog/oracle adjudication. |
| P08 | No in this bounded slice | A route-specific TLS interceptor with a distinct untrusted CA is absent. Replacing the endpoint certificate globally would not become healthy after reroute and would not realize this exemplar. Do not disable TLS verification or add trust to fake it. |

## Harness contract and isolation

The development `FAULTS` list, model prompts/grammars, policy admission, 55-second
probe deadline and existing development evaluation entry are unchanged. Sealed
descriptors live only in `harness/linux/sealed/`. Default worker bundles exclude
the sealed descriptors and implementation helpers; staging them requires `--sealed`.
Each selected `up`/`inject` request carries `{fixture, sha256}`. Unknown IDs, paths,
extra fields, mismatched hashes and repeated injections refuse. `up` establishes an
immutable hash-bound snapshot; injection checks exact before-state equality.

`sealed_linux.py` reuses the existing root VM check, namespace ownership, journal,
watchdog and cleanup. It embeds existing iproute2, dnsmasq, OpenSSL and nftables;
there is no new DNS server, proxy, cache or supervisor. DHCP option reloads follow
the [dnsmasq option-file contract](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html);
namespace rules use the [nftables command contract](https://netfilter.org/projects/nftables/manpage.html).
`nft` is an additional
readiness requirement: missing tooling refuses before namespace setup, and unsupported
kernel rules fail qualification. Nothing installs packages or creates a VM here.

Only fixed argv inside the owned namespaces can change network state. Root intent is
recorded before mutation; an injection-start marker survives partial failure and
forbids another attempt. The existing detached watchdog calls the extended exact
restore path at its original deadline. Restore removes only owned rules, restores
DHCP options before lease acquisition, restores proxy/DNS bytes and checks every
captured configuration field. A failed restore is persisted and cannot be retried;
`down` refuses an injected sealed run without verified restoration. Cleanup verifies
namespace absence, daemon PID/start-time retirement and the host fingerprint.

Exact equality covers persistent addresses/routes/link state/MTUs in both namespaces,
resolver and DHCP option bytes, per-scheme proxy values, process override, endpoint
mode, IPv6 flags, permanent neighbors and absence of the owned filter tables. It
excludes dynamic address lifetimes and learned neighbor-cache churn. Snapshot and
diff hashes are recorded in `sealed-snapshot.json` and `sealed-revert.json` inside
the root-owned run directory. A mismatch cannot be called a cleanup pass.

Probe execution gained a narrow Linux-only IPv6 resolver read so D09 produces actual
failed DNS evidence instead of an IPv4 parser exception. It runs inside the existing
namespace/sandbox without an IPv4 source bind; Mac IPv6 resolver probes remain refused.
Per-scheme proxy routing uses the interpreter's existing `proxy_routes` contract.
Neither change alters the action grammar or grants a mutation.

## Qualification and later evaluation gates

`sealed_qualify.py` is a one-cell **scripted infrastructure qualifier**, with zero
model calls. Without `--execute` it is inert. Live execution requires root in the
named Linux VM and is refused in tests or on macOS. It proves baseline and any
available alternate, arms one 55-second fault window, records each stage's actual
six checks/app, applies the descriptor's scripted repair sequence, records terminal
state before cleanup, and verifies exact revert plus restored ONLINE and final
cleanup. Missing evidence, a wrong signature, deadline expiry or ineffective injection
fails the cell; it never substitutes a guessed pass. Every original receipt survives.

The 22:52 CT slot release authorized this qualification only. The opt-in VM bundle
was staged from a separate owned lab-wifi checkout using this entry:

```sh
PYTHONPATH=src python3 -B -m offline_agent.uplink.lab_stage --sealed --stage
```

Inside the named VM, after verifying the content-addressed bundle against the recorded
implementation hashes, one cell uses a fresh 12-hex run ID and its exact hash below:

```sh
sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -B "$VERIFIED_BUNDLE/sealed_qualify.py" \
  --fixture P04 --sha256 "$SEALED_FIXTURE_SHA256" --run-id "$RUN_ID" --execute
```

Specific adjudication before sealing: C04's catalog handoff cites `portal-apple`
although only the second portal fails. Preserve that source discrepancy for the
independent scoring owner; do not quietly count a passing check as fault evidence.
P07 and G02/I04 use the catalog exemplar's `upstream` hypothesis, which differs from
some top-level catalog layer labels. Score the selected condition's exemplar explicitly.

## Verification and stop receipt

`tests/uplink/test_sealed_fixtures.py` pins valid/invalid/missing descriptors, immutable
hash binding, opt-in staging, Mac execution refusal, namespace-only argv, duplicate
injection refusal, true per-scheme/env proxy behavior, partial repair, complete-oracle
requirements, control mutation refusal, failed-revert persistence and no retry. The
new tests are small pure checks or medium temporary-file/mocked-worker checks.

| Check | Result |
| --- | --- |
| New fixture tests | 51 passed in 0.13 s on lab-wifi |
| Complete Uplink suite | 688 passed in 2.36 s on lab-wifi |
| Strict mypy on touched Python files | Passed, 9 files |
| Scoped Ruff and syntax/schema inspection | Passed locally |
| Live namespace qualification / model evaluation | 12/12 scripted candidates passed / not run |

Receipts are preserved under `artifacts/uplink/sealed-check-20261028/`.
The final code/test snapshot manifest is
`6eee09793b62b8f2ecfc195d062b6d1fe94047129ce0d6efacfe85ffd8b082c1`.
The first remote bundle omitted the scrub script; the next exposed its frozen
registration dependency. Both failed receipts remain. The final bundle added the
unchanged `configs/f10-ondevice.json` (SHA-256
`ff41ad3c71823a67c4abc4834c06c4adf08af5cc4e5ab3bc5e642b8f4b24ce53`)
and the suite passed. Four annotation errors were fixed before the final source
snapshot; no assertion was loosened. Build lesson: a bounded verification snapshot
must include transitive test data and script dependencies, not only source/test trees.

The separately released qualification is complete. Next: the scoring owner adjudicates
the notes above and Amanda seals the list from the results table. Model evaluation
remains separately gated and does not follow automatically.

## SHA-256 bindings

The following rows are generated from the final candidate bytes. Pin both descriptor
and shared implementation hashes: a descriptor hash alone does not freeze behavior.

| ID | Fixture file | SHA-256 |
| --- | --- | --- |
| P04 | `harness/linux/sealed/P04.json` | `5b367489bdf2b7b711facb5863144445e764a92fb61713123ab701d90e5b77f9` |
| D09 | `harness/linux/sealed/D09.json` | `cf748815f6bb67c5ddc02615e08b7df51b1f75c30ece44d2a991e24435371b27` |
| X02 | `harness/linux/sealed/X02.json` | `bec4d878cb848be3b917177cf4114db20fbb45b25c592c5d304b6eb80c090db6` |
| G02 | `harness/linux/sealed/G02.json` | `70258f214ad9cb47ccee24b6affb86d07ae853ae1e57b49c0a38b745c7015db8` |
| I04 | `harness/linux/sealed/I04.json` | `8fcff55b306481534144e04c4f2a41e39e5efdf9129c179ac151b1359f2d3865` |
| I02 | `harness/linux/sealed/I02.json` | `7d0f2e139ca613256c736d6f70d8fa3a86551aea409bb8c886d9ebfaf1fcc584` |
| X03 | `harness/linux/sealed/X03.json` | `377a8a34693c6919a35a2342095cff67568b6bc0750beb19a6aa88d4a5c5b34d` |
| X04 | `harness/linux/sealed/X04.json` | `0e8af42a13b5d991d70cde267c4b030bf60a505168d7892d5d536dde82b274a2` |
| C04 | `harness/linux/sealed/C04.json` | `bd190be049dd833317723e34522039ec7f68e3d2ed16bcba3a34121f6bb3cca0` |
| P07 | `harness/linux/sealed/P07.json` | `e2942bfad5be8374b0c92acc13709f1921339c8af7ebb4a393bc53edaf4e930e` |
| F05 | `harness/linux/sealed/F05.json` | `89ede4264bda2d26b2370a643df1cfa5a410188c790c3c7d24c51b2c1065b339` |
| V03 | `harness/linux/sealed/V03.json` | `b7889b493581feecc70cf19e926b7279e520e52785816dea6f9bb747dfbbbd1a` |

| Shared implementation / test file | SHA-256 |
| --- | --- |
| `src/offline_agent/uplink/sealed_registry.py` | `4297efc9b0d75fddee5334b35e2d1112bb11e4f9ec50f43b5421e66199ca9192` |
| `src/offline_agent/uplink/sealed_linux.py` | `66e2a3e91853f7640ebb1139059454efb68d833847ca7f670a02ddfe3977320f` |
| `src/offline_agent/uplink/sealed_qualify.py` | `a888bc03d729c5d6b033aca8caf96418f903daf49aa277af72b156b5064fb77f` |
| `src/offline_agent/uplink/lab_protocol.py` | `0550af2741de3b8984bc354e4e2a0678198ab04d5d04c475cfd5ce2ef1f49b23` |
| `src/offline_agent/uplink/lab_worker.py` | `56cc17995cc78d95c8ad2389965417c4fcf152c3e8381b4c4f8b92155764491b` |
| `src/offline_agent/uplink/lab_servers.py` | `66281e51dff4d4da887afd8f50f4d6bf63f76122f0644f871ee4aeb7ae147c43` |
| `src/offline_agent/uplink/lab_stage.py` | `c05210b41ef9c28b28aee65516e0a6d191c3f666013a953d743346b25f4d8358` |
| `src/offline_agent/uplink/probe_execution.py` | `ca6afbcdfb9a1a7e25cf7ce78e22cdad678418f9168a3be882c810edce6e4ac1` |
| `tests/uplink/test_sealed_fixtures.py` | `bbeda67a0b80cf9a0b596d5438a6c6b2d3f9b13700f16a88cce85dcb41901c5b` |

Seal status: **LIVE QUALIFICATION PASSED; AWAITING AMANDA SEAL**. These hashes identify the qualified candidate, not a model result.

## Sealed by Amanda 2026-10-27 23:11 CT

```markdown
## Amanda 2026-10-27 23:11 CT: SEALED HELD-OUT SET IS SEALED
- SEALED: the 12 fixtures in offline-agent-uplink-recovery SEALED-HELDOUT-FIXTURES.md, source/hashes frozen at f263071, qualification 90fef73 (12/12 scripted PASS, exact revert diffs, clean final cleanup), evidence manifest SHA-256 d973e486a5e5aad92ace3ff7b146468eec0cfe494045a4e6a47ead0e63e4d986. Faults (denominator 10): P04, D09, X02, G02, I04, I02, X03, X04, C04, P07. Controls (outside denominator): F05, V03. No swaps; reserves stay unused.
- Scoring labels, preregistered now before any model sees these cases, applied identically to every arm: the fixture's measured signature and exemplar labels are authoritative. C04 is scored on the portal that actually fails (the second portal), not the catalog's portal-apple citation; P07, G02 and I04 use the exemplar's upstream layer. Record these as a pre-results catalog erratum; no other label changes, and no changes after results.
- Credit rules unchanged: X02 counts only as Repair then Reroute to ONLINE; X03/X04 count only with the model's own correct report/handoff (upstream/wait; portal/open_portal) after keeping the partial repair; P07 counts only with give_up(needs_human) citing the process proxy override; controls must make no mutation; any false ONLINE fails the run.
- Data rule: none of these 12 cases, their descriptors, signatures or near-duplicates may enter any training, validation or synthetic corpus (C1-C4 leakage audit must check against these descriptor hashes). The set is opened once, on the selected candidate, per 4041782.
```

Pre-results catalog erratum, applied identically to every arm:

| Fixture | Preregistered scoring correction |
| --- | --- |
| C04 | Score the failing second portal (`portal-google`), replacing the catalog's `portal-apple` citation. |
| P07, G02, I04 | Use the selected exemplar's `upstream` layer. |

This seal supersedes the pending-seal language above. No other label changes are
authorized, and labels cannot change after model results. Fixture descriptors,
implementation pins, qualification receipts and manifest remain byte-for-byte
unchanged; the erratum is a scoring overlay, not a rewrite of the sealed evidence.
