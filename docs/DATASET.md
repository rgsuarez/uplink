# Frozen F1 corpus, version 1

There are 23 tasks: 12 training and 11 held-out. Nineteen contain a verified planted bug.
Four require an unavailable fixture in the immutable oracle directory and should receive
`cannot_fix`. All source is pinned permissive upstream code or newly written synthetic code.

| Split | Source repository or synthetic family | Repair tasks | Impossible tasks |
| --- | --- | ---: | ---: |
| Train | jpvanhal/inflection, MIT | 5 | 1 |
| Train | numeric-v1, original synthetic, CC0-1.0 | 5 | 1 |
| Held-out | okunishinishi/python-stringcase, MIT | 6 | 1 |
| Held-out | text-v1, original synthetic, CC0-1.0 | 3 | 1 |

`data/sources.json` records immutable 40-character upstream revisions, source URLs, license
files, and SHA-256 receipts. Files were fetched directly from the official repositories.
No source code was copied from the prior private prototype. Each task adds a fresh mission,
one uniquely reversible planted mutation, and an immutable three-test unittest oracle.
The reference passes all three tests, the planted source fails one or two tests while preserving at least one
regression, and the reference repair returns all three to green. Clamping and uppercase
mutations each break two assertions; both failures are retained.

The impossible fixtures retain two passing source tests and one failing missing-fixture
test even on the clean reference. The allowed tools cannot create the required file outside
the editable source copy, and the OS sandbox denies attempts to write it. This is a bounded
task label under the cooperative threat model in `docs/SAFETY.md`.

`data/train.sha256` and `eval/heldout.sha256` list the exact manifests, sources, licenses,
and oracle files. Task content hashes additionally bind the mission, mutation, identity,
source hash, and oracle hash. Both freezes are checked before a task is loaded. Missing,
unparsed, duplicate, incomplete, or mismatched freeze evidence refuses execution. Source
repository identities and synthetic families are disjoint across the split. The training
filter must enforce this split again at admission.

```sh
make corpus
```

This reruns reference, planted, and repaired sources in isolated test sandboxes and writes
`artifacts/corpus-verification.json`. It never refreshes the freeze. The original one-time
`scripts/prepare_corpus.py --freeze` refuses once either freeze file exists. Changes require
a new version, never quietly regenerating hashes to make a run pass. The held-out set is
frozen in the step 3 commit before evaluation or training code is implemented.

These small planted tasks validate the pipeline. They are not naturally occurring bugs and
do not yet establish a useful real-world pass rate. The mock policy uses known fixture repairs
explicitly; its results are control-flow checks, not model performance.

# F2 frozen corpus, version 2

V2 has **190 independently verified tasks: 160 solvable and 30 impossible**.
Training: 120 solvable plus 20 impossible (140). Held-out: 40 solvable plus 10 impossible (50).
Source repositories and CC0 synthetic families are disjoint between train and held-out.
F1 data and frozen lists are retained byte-for-byte as history. Runtime commands accept
`--corpus-version 1|2`; F2 corpus/eval commands default to v2, while the F1 demo remains v1.

| Source | Split | License | Pinned revision / source | Solvable |
| --- | --- | --- | --- | ---: |
| jpvanhal/inflection | Train | MIT | `88eefaacf7d0caaa701af7c8ab2d0ab3f17086f1`, `inflection/__init__.py` | 30 |
| mahmoud/boltons | Train | BSD-3-Clause | `d31d821d56c8f7756af9c30a864077f2e5855321`, `boltons/mathutils.py` | 40 |
| numeric-v2 | Train | CC0-1.0 | Original synthetic numeric contracts; content-pinned | 25 |
| sequence-v2 | Train | CC0-1.0 | Original synthetic sequence contracts; content-pinned | 25 |
| okunishinishi/python-stringcase | Held-out | MIT | `57aae96649f5cf7fc98e50b1d62479456d492715`, `stringcase.py` | 20 |
| text-v2 | Held-out | CC0-1.0 | Original synthetic text contracts; content-pinned | 10 |
| records-v2 | Held-out | CC0-1.0 | Original synthetic record contracts; content-pinned | 10 |

`src/offline_agent/corpus_specs.py` contains explicit independent expected values. The generator
never obtains expected answers by executing a candidate. `src/offline_agent/mutations.py`
enumerates arithmetic substitutions, comparison boundaries, integer boundaries, builtin swaps,
string methods, and return-value corruption in stable source order. Function interleaving and
fixed source quotas avoid selecting only one function's mutations. Whole-function context makes
each patch uniquely reversible; mutated source hashes exclude duplicate repairs. The operator
recipe and function for each retained mutation are listed in `data/v2/recipes.json`.

Each retained solvable task is run on three fresh states: upstream/synthetic reference (green),
plant (red with at least one passing regression), then repaired source (green). Verbose unittest
case identities bind FAIL_TO_PASS and PASS_TO_PASS evidence; missing/unparsed verdicts refuse
admission. Equivalent or nonfailing candidates are rejected with recorded reasons rather than
counted. All 190 tasks are reverified after the final freeze. `docs/CORPUS-V2.json` records the
aggregate and evidence hash; full per-case results are under ignored artifacts.

Impossible tasks retain passing source behavior but require a named missing dependency in the
immutable oracle directory. Their clean source still fails that one dependency case; this is
the deliberate exception to reference-green/repair-green, not a solvable mutation. The verifier
requires exactly that missing-fixture failure and the same passing source cases on both runs.
There is no legitimate repair with the allowed tools. Give-ups do not enter test-passing SFT.

`data/v2/train.sha256` and `eval/v2/heldout.sha256` freeze all executable task inputs, manifests,
upstream sources/licenses and source receipts. Task hashes bind mission, source, oracle, and
mutation. The informational generation recipes are not execution inputs. The one-time generator
refuses if either v2 freeze exists. Never regenerate a freeze to bypass a failed run.

```sh
make corpus                         # reverify all 190 frozen v2 tasks
make eval ARM=base CORPUS_VERSION=2  # explicitly mock, no model loads
```

These remain planted, cooperative tasks, with multiple mutations per source function. They
validate scale and pipeline integrity; counts are not a claim of 160 independent real-world bugs.
