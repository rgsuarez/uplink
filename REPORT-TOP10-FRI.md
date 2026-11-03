# Friday TOP10 result: no run

Staging stopped at 2026-10-30T14:30:06.422689-05:00 with top10_relay_load_too_high. The staging attempt is consumed and the ABORT marker is terminal. No retry, trial, fault injection, model call, or temporary sudoers installation occurred.

The scheduled Relay checkpoint passed at 2026-10-30T14:15:00.536580-05:00: load1 9.1396484375, load5 11.50537109375, 14 cores. Kaylin's real 14:11:26 CT verification validated against all ten unchanged hashes. The separate staging qualification subsequently failed; the checkpoint does not override that gate.

| Fault | Disposition | Trial IDs |
| --- | --- | --- |
| F01 | HELD, unchanged scope | 5fd474e289dd, 8de868f36211, 0d439d54e108 |
| F02 | HELD, unchanged scope | fd42f9fbd800, 4cd205ac6f99, 23dd9d2645d6 |
| F03 | UNATTEMPTED, terminal staging gate | ea52dbf8ed63, 42099f77954a, 253269ab8dac |
| F04 | UNATTEMPTED, terminal staging gate | 2390dc3e5d49, a6493887bd2d, e3c33ef17e24 |
| F05 | UNATTEMPTED, terminal staging gate | 875511edd180, 75ed49866b19, 8ddad55e24a0 |
| F06 | UNATTEMPTED, terminal staging gate | 2eaf59e74744, 12770d49042e, 9a581cfc1443 |
| F07 | UNATTEMPTED, terminal staging gate; LOCAL PORTAL SIMULATION | b9f13a5f7a7a, f3c376c827a9, 301a842ddcbb |
| F08 | HELD, unchanged scope | 8678a103214b, 44f86f43de6f, a399d147e5a1 |
| F09 | UNATTEMPTED, terminal staging gate | 80adbe422044, 7e0e299c361f, 5f1ed7b1eba4 |
| F10 | UNATTEMPTED, terminal staging gate | a1d32a297e68, f19fe8f4edc0, 25b2a5f024fb |

Outcome: 0 of 21 selected trials attempted; all nine held slots remain held. Zero BASE calls and zero false-ONLINE events were observed because no trial ran. There is no performance score or recovery success claim. Restoration was not required because no fault was injected. No manual recovery occurred.

Final sudo -n -l readback at 2026-10-30T14:30:39.463457-05:00 succeeded and is byte-for-byte unchanged from the preflight readback. No temporary TOP10 command markers appear in either output. PID 19904 birth/command identity matched before and after. The temporary NOPASSWD rule was never installed, so there was no owned rule to remove. Existing unrelated policy remains unchanged.

| Evidence | Exact path | Raw SHA-256 |
| --- | --- | --- |
| checkpoint | artifacts/uplink/top10-live-20261030-1430/relay-1415.json | 526eafc2ab7ba80997bac903930f6245ced7c8bb062603469655b6ce72e993a1 |
| stage_receipt | artifacts/uplink/top10-live-20261030-1430/runtime-stage.private.json | 4ed356ba1a4f9e8c932d28ce2ba822c3de09ee3d5db765b24def1014755b2b24 |
| abort | artifacts/uplink/top10-live-20261030-1430/ABORT.json | 67be508bd7a7d0c8309f237284e3c773dbdd1d14fb8b95805f51dd5dfdd53ecc |
| sudo_before | artifacts/uplink/top10-live-20261030-1430/operator-phases/sudo-before-receipt.private.json | 41266c87dd5c904a97e45cd1885db21d434da80c3bd4cccbdb6ad0abc1395cad |
| sudo_after | artifacts/uplink/top10-live-20261030-1430/operator-phases/sudo-after-receipt.private.json | bddcd73fe1861a578949ff4fadbe9f8837feb2f0e58c0e4fbda39c87a6edde16 |

Machine-readable result: evidence/uplink/top10-friday-live-20261030.json

Rejected staging sample records top10_relay_load_too_high but does not retain its numeric load values. A separately authorized future change should retain those values; current frozen runtime remains unchanged.

North Star Check: the frozen runtime, manifest, original 30 IDs, selected 21 BASE IDs, independent oracles, load limits, and automatic-restore policy remain unchanged. No push. Kaylin's receipt remains untracked and unmodified.
