# DIAG-GLIMMER

Glimmer selected `cycle_interface` in all three inspected Linux development failures even though the recorded link, lease and default route were healthy. The action schema validated syntax and scope, but did not require a diagnosed failing layer before allowing a network change. These records support fixing the input/action structure; they do not establish a model capability limit.

## North Star and evidence boundary

The objective is verified connectivity with the smallest authorized change, followed by an independent oracle and exact rollback. This diagnosis uses only the isolated Linux fixture and does not establish real Internet recovery or interrupted-cloud-task resumption. The acceptance oracle remains `uplink-linux-lab-v1`. No oracle or scoring rule was changed for this inspection.

All three captured replies below are exact `model.text` values with `model.redacted:false`. The historical HTTP request body was not journaled. The full prompts below are reconstructed from commit `4f52db4e93081c2c24c49e14d154e107314125bc`, recorded scoped handles and oracle evidence, and the immutable synthetic topology. They are **not original captured requests**. `oracle.checked_at` is reconstructed to the recorded millisecond; its original fractional remainder is unavailable. Synthetic proxy URLs are recovered from the pinned injector because the traces store their redacted handles. Known-good facts follow setup/restore plus the recorded healthy qualification, not a saved original baseline dump.

Each reconstructed grammar matches the SHA-256 in its original run-start record. No private network identifier, live probe, inference rerun or application import was needed for this inspection.

## What the model saw and chose

| Development case | Link / lease / route | DNS oracle | TLS and captive path | Configuration drift | Choice | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| wrong DNS | Up / present / primary | Both fail | All four pass through configured proxy | DNS differs from snapshot | `cycle_interface` | No oracle improvement, reverted |
| stale proxy | Up / present / primary | Both pass | All four fail | Proxy differs from snapshot | `cycle_interface` | No oracle improvement, reverted |
| DNS plus proxy | Up / present / primary | Both fail | All four fail | DNS and proxy differ from snapshot | `cycle_interface` | No oracle improvement, reverted |

No fault label appeared in the model input. The raw replies cite the failing evidence IDs correctly: DNS only, TLS/captive only, and all six respectively. The bad choice therefore occurred after the failing observations were available. The records do not expose a diagnosis or rationale beyond the closed `why:"snapshot_drift"` enum.

## Action order, grammar and missing structure

The system tool guide was serialized with `sort_keys=True`, placing the tools in this displayed order:

```text
cycle_interface
declare_online
diff_snapshot
flush_dns_cache
give_up
list_route_candidates
probe_online
read_dns
read_proxy
read_routes
read_vitals
renew_lease
report
reroute
restore_proxy
set_dns
set_service_order
```

The schema and GBNF choices instead followed enum order:

```text
read_vitals
probe_online
read_dns
read_proxy
read_routes
list_route_candidates
diff_snapshot
set_dns
restore_proxy
renew_lease
flush_dns_cache
cycle_interface
reroute
set_service_order
report
declare_online
give_up
```

`cycle_interface` was first in the displayed guide and twelfth in the grammar. That mismatch is a plausible positional bias, not a proven causal explanation. The larger verified defect is that a link-up observation did not remove interface cycling from the allowed action set. There was no explicit action cost, no short DNS/proxy examples, no layer diagnosis, no gateway ping, no configured-versus-alternate DNS comparison, and no direct-versus-proxy HTTPS comparison. All tools shared one action grammar.

The native wrapper added `Reasoning strength: low.` and prefilled `<|start|>assistant to=user<|message|>` before an eager JSON grammar. It requested an action immediately; no separate diagnostic output was captured. Requests used temperature 0, seed 0, `n_predict:256`, `grammar_lazy:false`, `cache_prompt:true`, `id_slot:0`, a 15-second HTTP timeout, and stop token `<|eot|>`. These are source facts, not a claim that changing reasoning strength alone would fix selection.

The three requests each reported zero cached prompt tokens and about 8.2-8.5 seconds of prompt evaluation plus 3.6-4.0 seconds of generation. A two-step diagnosis must keep both prompts compact so the bounded rollback deadline remains meaningful.

## Required corrective slice

Use a first grammar for `{layer:link|ip|dns|proxy|captive|upstream,evidence:[...]}` and a second grammar restricted to actions for that diagnosed layer. Independently reject unsupported diagnoses and require actual link-failure evidence before `cycle_interface` becomes admissible. Place that disruptive action last and state its cost. Supply fixed, read-only per-layer observations plus two or three short examples. Direct or alternate-path diagnostics must never substitute for the acceptance oracle.

Journal both full redacted prompts, both raw replies, grammar hashes, provider timings and the diagnosis/action decision before a later guard can reject the response. Re-run only the three named development cases, preserve all attempts, and compare against the scripted baseline without relabeling these cases held out. Mac trials remain dependent on the separately required safety checks.

## wrong_dns

Trace: uplink-fc17396814d6440586a3622316d05591.jsonl (private source artifact omitted). Observation at `2026-10-27T12:53:05.547-05:00`; action recorded at `2026-10-27T12:53:17.963-05:00`.

Reconstructed grammar SHA-256: `997fa6794d28e2f9922c62c9e76c66d1e0ff3025475cfa7d33d0dcf82dc5110c`; matches original start record. Full grammar: wrong_dns.gbnf (private source artifact omitted).

Full reconstructed prompt (wire-envelope form; `checked_at` precision limitation above):

```text
<|start|>system<|message|>Restore verified connectivity with the smallest safe change. Emit only one JSON object: {"version":1,"tool":"name","args":{...}}. Use only the listed tools and arguments; all listed arguments are required. Enum entries beginning with $ refer to the corresponding scope list in the user message: choose an exact supplied value, never emit the placeholder. An empty scope list forbids that argument. Do not infer a fault label. A known-good snapshot is available: propose restoring observed configuration drift from it. Prefer snapshot policies over arbitrary replacement values. If captive or unrecoverable, report one human action. {"tools":{"cycle_interface":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"declare_online":{},"diff_snapshot":{"snapshot_id":{"enum":["$snapshots"]}},"flush_dns_cache":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"give_up":{"reason":{"enum":["insufficient_evidence","needs_human","budget"]}},"list_route_candidates":{},"probe_online":{},"read_dns":{},"read_proxy":{},"read_routes":{},"read_vitals":{},"renew_lease":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"report":{"component":{"enum":["dns","proxy","lease","interface","upstream","portal","site","unknown"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"physical_step":{"enum":["check_cable","enable_saved_hotspot","connect_phone_usb","open_portal","wait"]}},"reroute":{"candidate_id":{"enum":["$candidates"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"restore_proxy":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["off","snapshot"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_dns":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["dhcp","snapshot","cloudflare","google"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_service_order":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_ids":{"items":{"enum":["$services"]},"maxItems":32,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}}},"version":1}

Reasoning strength: low.

# Valid recipients: "self", "user".<|eot|><|start|>user<|message|>{"scope":{"services":["service-d663dd92c858787c227b9c90","service-b0d89e52c4a3902be3d0676a"],"candidates":["candidate-7cee8d872b7299d07d0e6cc6"],"snapshots":["checkpoint-fe58bc83cbe940a2a96e40c14f617ff8"],"evidence":["dns-1","dns-2","tls-apple","tls-google","portal-apple","portal-google"]},"state":{"current":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:8888","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"known_good_snapshot":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:8888","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"prior_alternate_online":true},"oracle":{"version":"uplink-linux-lab-v1","verdict":"DEGRADED","checked_at":1793123582.814,"evidence":[{"id":"dns-1","kind":"dns","ok":false,"reason":"system_resolution_failed","path_id":null,"latency_ms":1077.066736001143,"target_id":"host-6c869c3a5bbd48ce6e92eb0b","status":null,"proxy_used":null},{"id":"dns-2","kind":"dns","ok":false,"reason":"system_resolution_failed","path_id":null,"latency_ms":1073.40732099874,"target_id":"host-40df933f4a5b35d93a2e44f9","status":null,"proxy_used":null},{"id":"tls-apple","kind":"tls","ok":true,"reason":"verified_lab_fixture","path_id":"path-d677dbad2c7b75fedb6adab2","latency_ms":9.272204999433598,"target_id":"host-6c869c3a5bbd48ce6e92eb0b","status":204,"proxy_used":"proxy-03e9bf989391e2495d8c19f8"},{"id":"tls-google","kind":"tls","ok":true,"reason":"verified_lab_fixture","path_id":"path-d677dbad2c7b75fedb6adab2","latency_ms":9.39545400069619,"target_id":"host-40df933f4a5b35d93a2e44f9","status":204,"proxy_used":"proxy-03e9bf989391e2495d8c19f8"},{"id":"portal-apple","kind":"portal","ok":true,"reason":"verified_lab_fixture","path_id":"path-d677dbad2c7b75fedb6adab2","latency_ms":6.796413999836659,"target_id":"host-6c869c3a5bbd48ce6e92eb0b","status":204,"proxy_used":"proxy-03e9bf989391e2495d8c19f8"},{"id":"portal-google","kind":"portal","ok":true,"reason":"verified_lab_fixture","path_id":"path-d677dbad2c7b75fedb6adab2","latency_ms":6.22803900114377,"target_id":"host-40df933f4a5b35d93a2e44f9","status":204,"proxy_used":"proxy-03e9bf989391e2495d8c19f8"}]}}<|eot|><|start|>assistant to=user<|message|>
```

Captured raw model reply:

```json
{"version":1,"tool":"cycle_interface","args":{"snapshot_id":"checkpoint-fe58bc83cbe940a2a96e40c14f617ff8","why":"snapshot_drift","evidence_ids":["dns-1","dns-2"],"service_id":"service-d663dd92c858787c227b9c90"}}
```

Captured native timings:

```json
{
  "cache_n": 0,
  "prompt_n": 1639,
  "prompt_ms": 8482.885,
  "prompt_per_second": 193.21256860136617,
  "predicted_n": 72,
  "predicted_ms": 3622.835,
  "predicted_per_second": 19.87393850396168
}
```

## stale_proxy

Trace: uplink-31482cc3e1ca4d8bb38afd21bd831495.jsonl (private source artifact omitted). Observation at `2026-10-27T12:54:35.507-05:00`; action recorded at `2026-10-27T12:54:48.091-05:00`.

Reconstructed grammar SHA-256: `99bce70b78d181443ed6966cffb482216ddd44b8ba89017b7dce31da1691fb79`; matches original start record. Full grammar: stale_proxy.gbnf (private source artifact omitted).

Full reconstructed prompt (wire-envelope form; `checked_at` precision limitation above):

```text
<|start|>system<|message|>Restore verified connectivity with the smallest safe change. Emit only one JSON object: {"version":1,"tool":"name","args":{...}}. Use only the listed tools and arguments; all listed arguments are required. Enum entries beginning with $ refer to the corresponding scope list in the user message: choose an exact supplied value, never emit the placeholder. An empty scope list forbids that argument. Do not infer a fault label. A known-good snapshot is available: propose restoring observed configuration drift from it. Prefer snapshot policies over arbitrary replacement values. If captive or unrecoverable, report one human action. {"tools":{"cycle_interface":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"declare_online":{},"diff_snapshot":{"snapshot_id":{"enum":["$snapshots"]}},"flush_dns_cache":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"give_up":{"reason":{"enum":["insufficient_evidence","needs_human","budget"]}},"list_route_candidates":{},"probe_online":{},"read_dns":{},"read_proxy":{},"read_routes":{},"read_vitals":{},"renew_lease":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"report":{"component":{"enum":["dns","proxy","lease","interface","upstream","portal","site","unknown"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"physical_step":{"enum":["check_cable","enable_saved_hotspot","connect_phone_usb","open_portal","wait"]}},"reroute":{"candidate_id":{"enum":["$candidates"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"restore_proxy":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["off","snapshot"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_dns":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["dhcp","snapshot","cloudflare","google"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_service_order":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_ids":{"items":{"enum":["$services"]},"maxItems":32,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}}},"version":1}

Reasoning strength: low.

# Valid recipients: "self", "user".<|eot|><|start|>user<|message|>{"scope":{"services":["service-5c3e00170eb7f58067335d9f","service-180c62f082174d0705e10780"],"candidates":["candidate-5b293c124bfcfc1d3ae8d896"],"snapshots":["checkpoint-fcb4c59d7472402c9d659487da7d4899"],"evidence":["dns-1","dns-2","tls-apple","tls-google","portal-apple","portal-google"]},"state":{"current":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:9","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"known_good_snapshot":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:8888","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"prior_alternate_online":true},"oracle":{"version":"uplink-linux-lab-v1","verdict":"DEGRADED","checked_at":1793123675.0,"evidence":[{"id":"dns-1","kind":"dns","ok":true,"reason":"system_resolved","path_id":null,"latency_ms":10.189370999796665,"target_id":"host-5210b3f1f50a03260abad29c","status":null,"proxy_used":null},{"id":"dns-2","kind":"dns","ok":true,"reason":"system_resolved","path_id":null,"latency_ms":9.90770499993232,"target_id":"host-367827be1482615c6960ada1","status":null,"proxy_used":null},{"id":"tls-apple","kind":"tls","ok":false,"reason":"probe_failed","path_id":"path-d62eb59bbf39c8389634dece","latency_ms":3.2879979989957064,"target_id":"host-5210b3f1f50a03260abad29c","status":0,"proxy_used":"proxy-ffcbeb67163e42ddf96fbbe1"},{"id":"tls-google","kind":"tls","ok":false,"reason":"probe_failed","path_id":"path-d62eb59bbf39c8389634dece","latency_ms":3.720165999766323,"target_id":"host-367827be1482615c6960ada1","status":0,"proxy_used":"proxy-ffcbeb67163e42ddf96fbbe1"},{"id":"portal-apple","kind":"portal","ok":false,"reason":"probe_failed","path_id":"path-d62eb59bbf39c8389634dece","latency_ms":3.8549150012840983,"target_id":"host-5210b3f1f50a03260abad29c","status":0,"proxy_used":"proxy-ffcbeb67163e42ddf96fbbe1"},{"id":"portal-google","kind":"portal","ok":false,"reason":"probe_failed","path_id":"path-d62eb59bbf39c8389634dece","latency_ms":3.2377489987993613,"target_id":"host-367827be1482615c6960ada1","status":0,"proxy_used":"proxy-ffcbeb67163e42ddf96fbbe1"}]}}<|eot|><|start|>assistant to=user<|message|>
```

Captured raw model reply:

```json
{"version":1,"tool":"cycle_interface","args":{"snapshot_id":"checkpoint-fcb4c59d7472402c9d659487da7d4899","why":"snapshot_drift","evidence_ids":["tls-apple","tls-google","portal-apple","portal-google"],"service_id":"service-5c3e00170eb7f58067335d9f"}}
```

Captured native timings:

```json
{
  "cache_n": 0,
  "prompt_n": 1588,
  "prompt_ms": 8251.611,
  "prompt_per_second": 192.44726878181726,
  "predicted_n": 79,
  "predicted_ms": 3993.138,
  "predicted_per_second": 19.78393934795141
}
```

## dns_plus_proxy

Trace: uplink-8d70518e1b0f4a1db6753d15e51f3ed0.jsonl (private source artifact omitted). Observation at `2026-10-27T12:55:21.614-05:00`; action recorded at `2026-10-27T12:55:34.136-05:00`.

Reconstructed grammar SHA-256: `fceda2f4c82f43300cbad81fa44f69fdaf5e080645c745bc7b6a2ad0a1f1b5dd`; matches original start record. Full grammar: dns_plus_proxy.gbnf (private source artifact omitted).

Full reconstructed prompt (wire-envelope form; `checked_at` precision limitation above):

```text
<|start|>system<|message|>Restore verified connectivity with the smallest safe change. Emit only one JSON object: {"version":1,"tool":"name","args":{...}}. Use only the listed tools and arguments; all listed arguments are required. Enum entries beginning with $ refer to the corresponding scope list in the user message: choose an exact supplied value, never emit the placeholder. An empty scope list forbids that argument. Do not infer a fault label. A known-good snapshot is available: propose restoring observed configuration drift from it. Prefer snapshot policies over arbitrary replacement values. If captive or unrecoverable, report one human action. {"tools":{"cycle_interface":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"declare_online":{},"diff_snapshot":{"snapshot_id":{"enum":["$snapshots"]}},"flush_dns_cache":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"give_up":{"reason":{"enum":["insufficient_evidence","needs_human","budget"]}},"list_route_candidates":{},"probe_online":{},"read_dns":{},"read_proxy":{},"read_routes":{},"read_vitals":{},"renew_lease":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"report":{"component":{"enum":["dns","proxy","lease","interface","upstream","portal","site","unknown"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"physical_step":{"enum":["check_cable","enable_saved_hotspot","connect_phone_usb","open_portal","wait"]}},"reroute":{"candidate_id":{"enum":["$candidates"]},"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"restore_proxy":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["off","snapshot"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_dns":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"policy":{"enum":["dhcp","snapshot","cloudflare","google"]},"service_id":{"enum":["$services"]},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}},"set_service_order":{"evidence_ids":{"items":{"enum":["$evidence"]},"maxItems":8,"minItems":1,"type":"array","uniqueItems":true},"service_ids":{"items":{"enum":["$services"]},"maxItems":32,"minItems":1,"type":"array","uniqueItems":true},"snapshot_id":{"enum":["$snapshots"]},"why":{"enum":["snapshot_drift","dns_failed","proxy_failed","lease_missing","alternate_route"]}}},"version":1}

Reasoning strength: low.

# Valid recipients: "self", "user".<|eot|><|start|>user<|message|>{"scope":{"services":["service-10fecea8ec420102e1050fee","service-496490f246566cadcfc4472b"],"candidates":["candidate-00e04ae2049d7482e073feb4"],"snapshots":["checkpoint-290d9334d4784458add2eb0e83341100"],"evidence":["dns-1","dns-2","tls-apple","tls-google","portal-apple","portal-google"]},"state":{"current":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:9","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"known_good_snapshot":{"dns_server":"ADDRESS_REMOVED","proxy_url":"http://ADDRESS_REMOVED:8888","primary_up":true,"lease_present":true,"default_gateway":"ADDRESS_REMOVED"},"prior_alternate_online":true},"oracle":{"version":"uplink-linux-lab-v1","verdict":"OFFLINE","checked_at":1793123718.891,"evidence":[{"id":"dns-1","kind":"dns","ok":false,"reason":"system_resolution_failed","path_id":null,"latency_ms":1087.3819819989876,"target_id":"host-1b7bd0fbf67bfe364052ef97","status":null,"proxy_used":null},{"id":"dns-2","kind":"dns","ok":false,"reason":"system_resolution_failed","path_id":null,"latency_ms":1066.732907000187,"target_id":"host-e0d0c383c21ad7c510f86129","status":null,"proxy_used":null},{"id":"tls-apple","kind":"tls","ok":false,"reason":"probe_failed","path_id":"path-21b278fbb3d6a8a60048e1da","latency_ms":3.558290000000852,"target_id":"host-1b7bd0fbf67bfe364052ef97","status":0,"proxy_used":"proxy-77fd3ceb616e22ba4f058099"},{"id":"tls-google","kind":"tls","ok":false,"reason":"probe_failed","path_id":"path-21b278fbb3d6a8a60048e1da","latency_ms":3.2547480004723184,"target_id":"host-e0d0c383c21ad7c510f86129","status":0,"proxy_used":"proxy-77fd3ceb616e22ba4f058099"},{"id":"portal-apple","kind":"portal","ok":false,"reason":"probe_failed","path_id":"path-21b278fbb3d6a8a60048e1da","latency_ms":3.3642069993220503,"target_id":"host-1b7bd0fbf67bfe364052ef97","status":0,"proxy_used":"proxy-77fd3ceb616e22ba4f058099"},{"id":"portal-google","kind":"portal","ok":false,"reason":"probe_failed","path_id":"path-21b278fbb3d6a8a60048e1da","latency_ms":3.818206998403184,"target_id":"host-e0d0c383c21ad7c510f86129","status":0,"proxy_used":"proxy-77fd3ceb616e22ba4f058099"}]}}<|eot|><|start|>assistant to=user<|message|>
```

Captured raw model reply:

```json
{"version":1,"tool":"cycle_interface","args":{"snapshot_id":"checkpoint-290d9334d4784458add2eb0e83341100","why":"snapshot_drift","evidence_ids":["dns-1","dns-2","tls-apple","tls-google","portal-apple","portal-google"],"service_id":"service-10fecea8ec420102e1050fee"}}
```

Captured native timings:

```json
{
  "cache_n": 0,
  "prompt_n": 1599,
  "prompt_ms": 8189.525,
  "prompt_per_second": 195.24941922761087,
  "predicted_n": 81,
  "predicted_ms": 4039.915,
  "predicted_per_second": 20.049926793014208
}
```

## Reproducibility

The artifact manifest (private source artifact omitted) records source, trace, receipt, prompt, reply and grammar SHA-256 values. Prompt files are reconstruction artifacts and must not be spliced into the original trace or represented as captured model footage. The source commit, original traces and original replies are unchanged.


## First layer-policy development result

The first two-stage wrong-DNS trial (source f318831, trace
`uplink-ee484ae7072f43a7b9230e74d0cd748b.jsonl`) correctly diagnosed DNS and selected
`set_dns` with the snapshot policy. It repeated `dns-1` and `dns-2` to fill the evidence
array, so the unchanged unique-item validator refused the action before any repair.
Both model outputs and timings were retained. The fixture closed with no namespaces
and an unchanged host-network fingerprint. This is a failed development attempt.

Source bf243c3 narrows the sampled action grammar to one decisive evidence citation.
It does not deduplicate or repair model output after sampling, relax the parser, or
change the independent acceptance oracle. The targeted suite passed 421 tests on lab.
