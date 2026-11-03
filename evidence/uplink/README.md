# Verifiable Uplink receipts

These bundles contain the original per-case result JSON and redacted trace bytes,
their SHA-256 manifest, trace validation receipts, and recomputable summaries.
They contain no model weights, salt keys, private lab configuration or credentials.

| Bundle | Source | Cells | Status |
| --- | --- | ---: | --- |
| [Current four-arm comparison](comparison-20261027/summary.json) | `b784277` | 48 | Complete development matrix |
| [Historical forced policy](forced-20261027/summary.json) | `2f88b59` | 24 | Complete two-arm development matrix |
| [Initial open-history attempt](open-history-stop-20261027/summary.json) | `fc3beb4` | 10 of 48 | Stopped; all failed cells retained |
| [Strong-baseline guard attempt](strong-guard-stop-20261027/summary.json) | `e8ad257` | 27 of 48 | Stopped; failed baseline case retained |

From a clone, use Python 3.12. Verification needs no model, provider, socket, or
third-party Python package:

```sh
PYTHONPATH=src python3.12 scripts/uplink_receipts.py --verify evidence/uplink/comparison-20261027
PYTHONPATH=src python3.12 scripts/uplink_receipts.py --verify evidence/uplink/forced-20261027
PYTHONPATH=src python3.12 scripts/uplink_receipts.py --verify evidence/uplink/open-history-stop-20261027
PYTHONPATH=src python3.12 scripts/uplink_receipts.py --verify evidence/uplink/strong-guard-stop-20261027
```

Verification checks inventory, every file hash, trace schema and replay, receipt
binding, fresh six-check ONLINE evidence, independent final cleanup, recovery/Repair/Reroute totals, first-decision hypothesis
accuracy and the declared schedule. Missing, changed or extra files fail. A partial
attempt cannot be relabeled complete by changing its manifest.

The legacy arm names `uplink` and `baseline` map to `glimmer-forced` and `scripted`
in summaries; original bytes are unchanged. Healthy-control outcomes are normalized
to `healthy_noop`, with their raw original value retained separately. A successful
failover is Reroute, not root-cause repair. The first-decision label metric and its
coarse-label limitations are defined in [the comparison contract](../../docs/UPLINK-ACTION-MODES.md).

Hashes establish byte integrity and reproducible rollups, not third-party attestation
of the physical experiment. All scenarios here were development-exposed. None of
these bundles is held-out evidence or supports a generalization claim.
