# Recovery transcripts for the video

These are rendered historical receipts, not new runs. Trials 15-17 show completed
negative DNS measurements, failed HTTPS probes, forced DNS/proxy repairs and final
ONLINE checks over the restored effective route. They do not prove independent
diagnosis, a successful generated HTTPS probe, or a model improvement.

| Journal trial | Run | Transcript |
| --- | --- | --- |
| 15 | `0f3194dee834` | [trial-15.md](trial-15.md) |
| 16 | `75429ca1ec21` | [trial-16.md](trial-16.md) |
| 17 | `6c04d80f1c00` | [trial-17.md](trial-17.md) |

The journal numbers and run mapping come from
`evidence/uplink/probe-v5-handback-20261028.json`, SHA-256
`32dfaf50cb6b68451e8e653b246cc2ae5a06cb53b3f6982419970cf2ecf7de3f`.
Each transcript carries the original receipt and trace hashes and field references.
Expand its provenance section when auditing; keep it collapsed for the video.
The failed HTTPS attempt is not a returned negative measurement. The final HTTPS
checks record a direct route, not successful traffic through a proxy.

## Reproduce a transcript locally

From the recovery worktree, using its existing environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python scripts/demo_receipt.py \
  --receipt artifacts/uplink/real-probe-trials-v5-20261028/0f3194dee834-receipt.json \
  --sha256 56f80b7f9ec34f8bb518451d27e595882aa1996c64f1d7433a8db15d91a5608c
```

Omit `--output` to print Markdown; use `--output NEW.md` to create a new file.
Existing files are never overwritten. Reproduction needs the original local
receipt and its `traces/` sibling, which remain private and are not exported here.
The script verifies the supplied receipt SHA, the receipt's trace SHA and the
existing strict trace contract. It reads choices/probes from the trace, not copied
summary counters. It opens no process, socket, model or VM and does not read the
private lab config. Unknown/missing evidence is shown as unknown or refused;
hash mismatch, malformed input, unsupported labels and symlinks refuse.

No raw prompts, model text, hostnames, IPs, device serials, service IDs, proxy URLs,
credentials, stderr or authorization records are copied into a transcript.
Only fixed labels, booleans, validated measurements, public check names and hashes
are displayed. This is a local video artifact, not publication authorization.

## Future FAULTS-V1 receipts

The open model controller now writes `receipt.json` with kind
`uplink-newfault-model-trial`, plus a SHA-indexed set of measured state, independent
oracle results, raw bounded emissions, root verification, timing and handback
artifacts. Pass that receipt and its exact SHA to the same renderer. The view checks
every artifact hash and the action against its recorded open grammar, labels
accepted versus rejected choices, and keeps give_up/safety rollback separate from
verified model recovery. `MOCK` evidence stays marked `MOCK`. No live FAULTS-V1
output has been generated. The model is BASE only.

For older worker-only captures, the renderer also accepts a private **manifest of saved
artifacts**, with `version: 1`, `kind: "uplink-faults-v1-demo-input"`, and a `files`
object. Every entry is `{ "path": "relative-file.json", "sha256": "ACTUAL_SHA256" }`.
Use hashes of the exact retrieved bytes, not rewritten copies. Paths must stay
under the manifest directory; symlinks and traversal are refused.

| Entry | Exact producer / required content |
| --- | --- |
| `state` (required) | Retrieved root `state.json`: admitted spec/run, snapshot ID, lifecycle, reason and boot ID |
| `inject` | Saved `inject` worker response: `fault_effective`, `target_online` |
| `apply_request` | Exact submitted request with matching `spec`, `operation: apply`, and `payload.action` |
| `verify` | Saved `verify` worker response: `verified`, `target_online`, `exact_diff` with fields and before/after hashes |
| `oracle` | Exact independent `OracleResult.as_json()` supplied to verification |
| `timing` (optional) | Root receipt's measured `run_id`, `boot_id`, `clock_basis: posix-monotonic-v1`, `timing_unproven: false`, `injected_mono`, `restored_mono` |

Example manifest structure only, not evidence:

```json
{
  "version": 1,
  "kind": "uplink-faults-v1-demo-input",
  "files": {
    "state": {"path": "state.json", "sha256": "ACTUAL_STATE_SHA256"},
    "inject": {"path": "inject-response.json", "sha256": "ACTUAL_INJECT_SHA256"},
    "apply_request": {"path": "apply-request.json", "sha256": "ACTUAL_REQUEST_SHA256"},
    "verify": {"path": "verify-response.json", "sha256": "ACTUAL_VERIFY_SHA256"},
    "oracle": {"path": "verify-oracle.json", "sha256": "ACTUAL_ORACLE_SHA256"}
  }
}
```

Only include files actually recorded for the same run. The manifest must be pinned
with `--sha256`, just like V5. It creates no measurements and grants no recovery
authority. A request is labeled requested, not executed; verification is displayed
separately. These legacy worker-only manifests are always labeled forced
single-choice. The new model controller uses its own complete receipt contract;
adding an `open` label to this legacy manifest earns no open-choice credit.

Timing is optional for older artifacts that did not record injection-to-restore
elapsed time. The new root worker records both endpoints on the same boot and
monotonic clock, and the controller requires at most 60 seconds for success. The
renderer checks the boot against root state. It never
subtracts wall-clock from monotonic time, treats the watchdog deadline as elapsed
time, or fills a missing measurement with zero. Without timing evidence the screen
reads UNKNOWN. Target failure/recovery remains separate from general ONLINE.
