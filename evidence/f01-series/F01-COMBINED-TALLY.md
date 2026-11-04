# F01 Wi-Fi radio off: public evidence tally

BLUF: **1/5 model-reached, 1 strict recovery 11.213 s, false ONLINE 0** in the specified three-series continuation: one PASS, one FAIL and three ABORTs. Including the additional earlier model-reached ABORT gives **1/6 across all listed series**. Model: BASE Meta Muse Glimmer 30B, unmodified open weights.

Small n: radio-off is the hardest measured live repair segment. The continuation spans versioned series, not one homogeneous reliability experiment. All model-reached ABORTs remain in their denominators. Four pre-model refusals/aborts are excluded and listed below. Zero observed false ONLINE is not a reliability guarantee.

| Population | Series | Model-reached result | Treatment |
| --- | --- | --- | --- |
| Specified continuation | b6cec6aa1c24, 943ca53b4167, 1c2228ebcf54 | 1/5: PASS 1, FAIL 1, ABORT 3 | Primary scoped result |
| Additional prior model-reached trial | 8ff8fb7d3935 | 0/1: ABORT | Included in all-listed 1/6; not relabeled as a pre-model stop |
| Pre-model stops | 0e706a845a37 and 55786933090e | 3 REFUSED-BEFORE-MODEL and 1 ABORT; zero requests | Excluded from model-reached denominators |

| Series | Run | Population | Model reached / requests | Recorded outcome | Strict recovery seconds | False ONLINE | Ledger reason |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| b6cec6aa1c24 | 723a4f9ec014 | specified_continuation | true / 1 | FAIL | n/a | 0 | top10_completion_failed_or_unparsed |
| b6cec6aa1c24 | 19276fdcfae2 | specified_continuation | true / 1 | ABORT | n/a | 0 | safety_or_terminal_evidence_unproven |
| 943ca53b4167 | 694aac7fb3c2 | specified_continuation | true / 1 | ABORT | n/a | 0 | safety_or_terminal_evidence_unproven |
| 1c2228ebcf54 | 445b799cf7b0 | specified_continuation | true / 1 | PASS | 11.213 | 0 | strict_recovered |
| 1c2228ebcf54 | 35e22e746aa8 | specified_continuation | true / 1 | ABORT | n/a | 0 | safety_or_terminal_evidence_unproven |
| 0e706a845a37 | 0e97caca67ec | additional_prior | false / 0 | REFUSED-BEFORE-MODEL | n/a | 0 | entry_headroom_refused |
| 0e706a845a37 | ed5f6bfe95b0 | additional_prior | false / 0 | REFUSED-BEFORE-MODEL | n/a | 0 | entry_headroom_refused |
| 0e706a845a37 | 3e4028ff45dd | additional_prior | false / 0 | REFUSED-BEFORE-MODEL | n/a | 0 | entry_headroom_refused |
| 8ff8fb7d3935 | 9d52a77c17d7 | additional_prior | true / 2 | ABORT | n/a | 0 | safety_or_terminal_evidence_unproven |
| 55786933090e | 268242b741d6 | additional_prior | false / 0 | ABORT | n/a | 0 | export_validation_failed |

The only strict recovery is run 445b799cf7b0, 11.213 s. This is one passing trial's recovery time, not a five-trial median. Trial 19276fdcfae2 has a raw recovery claim but the frozen strict reader refused it; its ABORT is retained. Later handback does not rewrite original failures or cleanup uncertainty.

The excluded pre-model runs are 0e97caca67ec, ed5f6bfe95b0, 3e4028ff45dd and 268242b741d6. The first three refused for entry headroom; the last stopped before any model request. Details remain in the machine-readable [public ledger](SERIES-LEDGER.json).

Source commit: 3ba3d74fd79ed615f367199d14e8546d59c97b36. Original source ledgers, tally and copy-manifest hashes are recorded in [the public evidence manifest](F01-EVIDENCE-MANIFEST.json). Original recordings, approvals, device coordinates, runtime documents and media paths are omitted. Source hashes preserve provenance; they do not validate the changed public bytes or authorize execution. No new trial, model call, capture, cleanup or media verification is claimed by this projection.
