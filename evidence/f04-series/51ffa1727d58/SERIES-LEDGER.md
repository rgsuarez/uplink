# F04 SERIES-LEDGER 51ffa1727d58

State: COMPLETED. Passes / model-reached: 7/7.

Preserve series 54b5899ccb66 and 075540cb0307 as STOPPED/ABORT with all six prior rows visible. Run seven new model-reached DNS trials with fresh IDs under relay-load-policy-v2. Report the original v1 segment (1/1), the first v2 continuation (2/2), and this v2 segment separately, plus a clearly labeled mixed-policy aggregate toward ten model-reached trials. Count each confirmed model-reached trial once, including failures and aborts; retain pre-model refusals and aborts without counting them. Unknown reach remains unknown. No reopened output, reused allocated ID, dropped row or rerun-to-replace; stop on any ABORT. The aggregate measures progress toward the owner objective, not one unchanged-policy experiment.

| Cohort | Policy | Passes / model-reached |
| --- | --- | --- |
| Original, STOPPED / ABORT | relay-load-policy-v1 | 1/1 |
| Previous continuation, STOPPED / ABORT | relay-load-policy-v2 | 2/2 |
| Continuation | relay-load-policy-v2 | 7/7 |
| Mixed-policy progress toward ten | v1 + v2 | 10/10 |

The mixed-policy aggregate is not one unchanged-policy experiment.

Prior segment 54b5899ccb66 (relay-load-policy-v1), preserved STOPPED / ABORT.

| Index | Run ID | Outcome | Model requests | Action chosen | Recovery s | False ONLINE | Strict reader | Recording path | Recording SHA-256 | Result SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | d2c83ffa97ec | PASS | 1 | restore_dns | 17.541 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-54b5899ccb66/attempts/01-d2c83ffa97ec/terminal.typescript | 98f3506acd43e652c2ca4256a2e6ca88c5bb61d793cb6c862a03a54e6e2ec2fc | c723ce90f288fb4b067375058738cee3d4aaf6137effcfb86d93961f935d7cc6 |
| 2 | c81f7ae72ef1 | REFUSED-BEFORE-MODEL | 0 |  | unknown | 0 | REFUSED | PRIVATE_HOME/uplink-f04-runs/f04-series-54b5899ccb66/attempts/02-c81f7ae72ef1/terminal.typescript | a923f64e5d8008a05aaf33f6336e1fa9fa9232e7bdfa70f31d6845be45394ce4 | 5d4929cafc2ee03a8d0794b618af8f6ab0655e9f63ed8afd61fda5ba39d844dd |
| 3 | 8d35661dd4a7 | ABORT | 0 |  | unknown | 0 | REFUSED | PRIVATE_HOME/uplink-f04-runs/f04-series-54b5899ccb66/attempts/03-8d35661dd4a7/terminal.typescript | 964955709eedd8d81c1e79be9bbb8fbf90c1d9635333d2037ccfd22a385ce93c | unknown |

Prior segment 075540cb0307 (relay-load-policy-v2), preserved STOPPED / ABORT.

| Index | Run ID | Outcome | Model requests | Action chosen | Recovery s | False ONLINE | Strict reader | Recording path | Recording SHA-256 | Result SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | edc3c02c085b | PASS | 1 | restore_dns | 17.695 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/01-edc3c02c085b/terminal.typescript | ce4f75673705683aed124fcc963437cb2a3f91f073051b77ccab2b1afecfb2b8 | 9ddd71aaa9b1c5b5b19dee6dcbeae0fd3d632b7711ac2d1d8ee2cd3dcd4d7c33 |
| 2 | a435d119fad8 | PASS | 1 | restore_dns | 17.692 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/02-a435d119fad8/terminal.typescript | 8b00c9e549ffbd2fe2ecec35b677528ffadd1854d1fbf0878b6e7f5540ce6716 | e1330870eb5549955df7bfedb11d3ba5f429b511e39e51a89a5a4b3d96539a90 |
| 3 | 38eb55e41fd4 | ABORT | 0 |  | unknown | 0 | REFUSED | PRIVATE_HOME/uplink-f04-runs/f04-series-075540cb0307/attempts/03-38eb55e41fd4/terminal.typescript | 75472c0e86b4967e2e68b4601fb671fdacef6b97f8f072af32d400395176e468 | unknown |

Current segment only. Prior rows above do not enter this segment's denominator.

| Index | Run ID | Outcome | Model requests | Action chosen | Recovery s | False ONLINE | Strict reader | Recording path | Recording SHA-256 | Result SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 9b7b453f9bf1 | PASS | 1 | restore_dns | 15.233 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/01-9b7b453f9bf1/terminal.typescript | 29943480a4aeb4441a7d298d3d86635bc41d010196da6b2d1c7e9582e9d5857e | 8ca87c17dd94df98ebeedf5397b6ccdfffa9c897ffd8dcd366fce16a36567088 |
| 2 | 16b413d9be22 | PASS | 1 | restore_dns | 15.371 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/02-16b413d9be22/terminal.typescript | be6e43a146b3f0c1a22b78a30294e11ab9968dc5be198bdfb66835e8e90fe654 | 3ce5b3860932ec8abf9e9899148fc0ed7b7d5b8633048d6a41a1ceae244e0026 |
| 3 | b8c0a4e053a7 | PASS | 1 | restore_dns | 15.236 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/03-b8c0a4e053a7/terminal.typescript | 1526ccca94a3066fd77f4d5f75769c834386bbd1055d90a1193592fc918062bd | 250796ec27e89d9199fc45005391b983a77d54a4ff69a1f53c1255c4157b1583 |
| 4 | d3c545d1e1fa | PASS | 1 | restore_dns | 15.125 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/04-d3c545d1e1fa/terminal.typescript | 978ecdb28a966364b4de6f6fe1b7dcb95633259638a4b4388750744ecee223a5 | a35cc1233d2b104437835147d4ad15247a79ee81e4937ac81ae2cc1bef6b040b |
| 5 | 063ada5c624e | PASS | 1 | restore_dns | 16.425 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/05-063ada5c624e/terminal.typescript | fc9b02b2c8a12d91fd0e2fe647b3b9d6b6962867fef7e782fcc767ea1efb39bb | 9dbc1287ff443ebbab10d9134d00aadaba77cd5c230c15ff5d00602625bebb89 |
| 6 | 17ce0712a888 | PASS | 1 | restore_dns | 15.267 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/06-17ce0712a888/terminal.typescript | 2298221c8f32ea57f01b7f66534701740e9ab00582cd30560d34ecbf065c2d92 | 663ac08cb02650f6f6d29934cb904f54cacacf4af165f4dc0a09a1b7b18ac1de |
| 7 | 069f1d061acb | REFUSED-BEFORE-MODEL | 0 |  | unknown | 0 | REFUSED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/07-069f1d061acb/terminal.typescript | e21698fb73b8486082572c1d3507f5e830461701e04bc455b6dd68ec6252ecd9 | unknown |
| 8 | 1d9cba41a474 | PASS | 1 | restore_dns | 15.137 | 0 | VALIDATED | PRIVATE_HOME/uplink-f04-runs/f04-series-51ffa1727d58/attempts/08-1d9cba41a474/terminal.typescript | 78d87713d23b78a83a5cb22e9783e525779b3466184f19c590762b3bff8ab23d | fc243a90b3f354fa07037a61edf432714c17ebce5315f9ed7bf54b16db76ca81 |

Median recovery (PASS rows): 15.236 seconds.
False ONLINE total: 0; complete: True.
Denominator complete: True. Refusals are retained and excluded from the denominator.
