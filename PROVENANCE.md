Third-party inputs: Meta open weights (Muse-Glimmer-30B), the local GGUF runtime, Python open-source libraries and the pinned upstream repos below.

All project implementation is written fresh during the building period. Upstream assets,
licenses, revisions, and substantial verbatim inclusions are listed below.

| Path | Source | License / purpose |
| --- | --- | --- |
| `LICENSE` | https://www.apache.org/licenses/LICENSE-2.0.txt | Standard Apache-2.0 license text |
| `uv.lock` | PyPI packages and their recorded hashes | Open-source development and runtime dependencies |

Model weights are not part of this repository. No new model download is required for F1.

Pinned upstream task inputs, fetched directly during F1:

| Path | Source path and revision | License / inclusion |
| --- | --- | --- |
| `data/upstream/inflection/module.py` | https://github.com/jpvanhal/inflection/blob/88eefaacf7d0caaa701af7c8ab2d0ab3f17086f1/inflection/__init__.py | MIT, verbatim upstream source for historical task fixtures |
| `data/upstream/inflection/LICENSE` | Same repository/revision, `LICENSE` | MIT, verbatim attribution retained |
| `data/upstream/stringcase/module.py` | https://github.com/okunishinishi/python-stringcase/blob/57aae96649f5cf7fc98e50b1d62479456d492715/stringcase.py | MIT, verbatim upstream source for held-out tasks |
| `data/upstream/stringcase/LICENSE` | Same repository/revision, `LICENSE` | MIT, verbatim attribution retained |
| `data/synthetic/LICENSE` | https://github.com/spdx/license-list-data/blob/v3.26.0/text/CC0-1.0.txt | Standard CC0 dedication text |

The upstream code and licenses were fetched directly from the primary URLs listed above.
Richard Suarez dedicates the newly authored `data/synthetic/*.py` fixtures to CC0-1.0.
Project runtime, task oracles, and task-generation code remain under Apache-2.0.

Pinned Meta metadata used for native serving-template rendering (no weights):

| Path | Source | Why / inclusion |
| --- | --- | --- |
| `templates/glimmer_native.jinja` | `meta-models/Muse-Glimmer-30B`, revision `cec94fcfd6512f2cfe708138c32533b346ea4f69`, `chat_template.jinja` | Verbatim native template, SHA-256 pinned; preserves the native serving protocol |
| `templates/tokenizer_config.json` | Same model/revision, `tokenizer_config.json` | Verbatim tokenizer metadata for native special tokens; not the tokenizer or model weights |
| `templates/MODEL-LICENSE` | Same model/revision, `LICENSE` | Verbatim Meta model license governing these upstream assets |

Primary download URLs, sizes, and content hashes are in `templates/manifest.json`. The native
template was fetched directly from the pinned Meta repository. The standard upstream
license texts and the pinned upstream source/metadata files listed above are the only
substantial verbatim inclusions.

F2 additions:

| Path | Source | License / inclusion |
| --- | --- | --- |
| `data/v2/upstream/boltons/module.py` | https://github.com/mahmoud/boltons/blob/d31d821d56c8f7756af9c30a864077f2e5855321/boltons/mathutils.py | BSD-3-Clause, verbatim pinned source for historical task mutations |
| `data/v2/upstream/boltons/LICENSE` | Same repository and revision, `LICENSE` | BSD-3-Clause, verbatim attribution retained |
| `data/v2/synthetic/*.py` | Newly authored numeric, sequence, text, and record families | Dedicated by Richard Suarez to CC0-1.0, standard license retained at `data/synthetic/LICENSE` |

V2 reuses this repository's F1 inflection/stringcase source assets with the same attribution.
All generation logic, independent behavior cases, oracles and later worker code are newly
authored here. `data/v2/sources.json` lists source URLs, exact commits, licenses and asset
hashes. F1 frozen history remains unchanged.


The submission uses Meta Muse Glimmer 30B: base, unmodified open weights. The native tokenizer metadata
is preserved byte for byte, including its upstream special-token vocabulary.
Original runtime, safety checks, watchdog, oracle and evidence tooling were authored
during the hackathon. Source-specific hashes in the export manifest distinguish
measured private inputs from sanitized public derivatives.
