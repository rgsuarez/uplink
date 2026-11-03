# Recorded simulation views

These are sanitized views of saved **MODEL_IN_LOOP_SIM** evidence: real unadapted
BASE Muse Glimmer Q4 inference on Modal A10G, with a fake fault kernel. The adopted
descriptions were tuned after F06/F07 failures. None is held-out or live Wi-Fi evidence.
Opening a view does not measure this computer's connection or run a model.

| View | Recorded observation |
| --- | --- |
| [Adopted F04, trial 1](adopted-F04.md) | DNS snapshot drift, model-selected `restore_dns`, independent recovery before cleanup |
| [Adopted F06, trial 1](adopted-F06.md) | Proxy snapshot drift, model-selected `restore_proxy`, independent recovery before cleanup |
| [Baseline F06, trial 1](baseline-F06.md) | Valid `report`, no model recovery credit; cleanup remains separate |
| [Four-run results table](results-table.md) | Retained 15/21 baseline, both rejected 6/21 variants and adopted 21/21 simulation result |

Each trajectory carries its original result-file SHA-256 and recorded run ID. The viewer
checks that whole-file hash, the trial's individual artifact hashes, the frozen parser
and historical recovery proof before emitting text. It prints only allowlisted labels,
booleans, counts, hashes and recorded call times. Raw prompts, device coordinates,
private config and approval documents are excluded.

The saved views can be opened directly without running a model or contacting a host.
Original private receipts are not bundled; regeneration requires those hash-pinned
originals and refuses when they are missing.

Live F04 DNS series evidence is now committed separately in
[EVIDENCE-F04-SERIES.md](../../EVIDENCE-F04-SERIES.md). Simulation views do not
earn live recovery credit. Nothing here authorizes a new trial, upload or publication.
