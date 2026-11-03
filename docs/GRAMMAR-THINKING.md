# Grammar and thinking: static audit, runtime unverified

**UNVERIFIED UNTIL DEVICE SPIKE. Source inspection is not evidence of a working model path.**
This audit uses only llama.cpp b10353 source/docs at commit
`f8def7fe168bab245fbf15d3f18b26dbb1ef73c8`. Eleven source files (724,501 bytes) are hash-pinned
in `configs/native-source-audit.json`. No model, vocabulary, tokenizer, native library or
inference process is loaded by this check.
The cache retains selected source regions where appropriate (490,931 bytes total). Full upstream
hashes are checked in memory before extraction; cached excerpt hashes and original line spans
are also pinned. Unrelated template branches and sample credential text are not retained.

```sh
make grammar-thinking-check
# On a fresh clone, fetch only the small pinned source/docs once:
uv run --frozen --offline python scripts/check_native_grammar.py --source-only --fetch-source
```

The receipt is `artifacts/native-thinking-check.json`. Missing, changed or stale source pins
refuse and persist a failure reason. The older `--library <existing-library>` syntax check
remains separate: it parses GBNF with a null vocabulary, but proves nothing about thinking.
`--source-only` refuses `--library`, so the static route cannot accidentally enter that check.

## What the pinned source establishes

| Route or case | Static conclusion | Harness behavior |
| --- | --- | --- |
| Native `/completion`, explicit non-lazy GBNF | The grammar starts at its root and applies to generated token pieces. Ordinary thinking text outside the JSON action is not admitted by the action grammar. This does not inspect hidden model activations. | The device spike supplies a native content prefix plus GBNF and parses the entire returned content as one action. Invalid/truncated content refuses. Model fit, speed and native-token compatibility remain unverified. |
| Chat endpoint, explicit `grammar` | The request's user grammar can reach the eager sampler when a template-generated grammar does not replace it. It is not a promise to constrain only the final answer. | CUDA evaluation requests GBNF. The Muse parser expects native recipient/message framing while our grammar starts with a JSON action. Empty parsed content or a framing error is a compatibility risk, not permission to remove grammar. |
| Chat `response_format` in general | The server parses JSON schema into chat-template inputs. Some handlers build a grammar that allows reasoning before a schema-constrained final message. Reasoning is not universally constrained by the final schema. | The local provider's schema mode requests the six-action schema. Returned content still must pass the independent action parser before dispatch. |
| Current specialized Muse handler, no OpenAI tools | **Concrete source risk:** its grammar is built only when tools are supplied; the handler does not consume `inputs.json_schema` or `inputs.grammar`. The response-format wrapper is parsed earlier, but no conversion to a sampling grammar is evident on this route without tools. | This harness sends JSON actions in content and no OpenAI tool declarations. Treat response-format sampling enforcement as unverified and potentially absent. Valid-looking outputs alone cannot clear this gate. |
| Lazy tool grammar | The low-level grammar returns without filtering while awaiting its trigger. The common sampler can also suspend a lazy grammar during active reasoning. Generated template grammars may allow free reasoning structurally. | This harness does not request lazy OpenAI tool calls. Do not adopt that route as an undocumented workaround; it would need a deliberate action/trace/SFT adapter. |
| Separate `message.reasoning_content` plus valid content | Reasoning extraction changes response formatting, not a universal sampler constraint. | Only `message.content` is parsed as an action. Reasoning is ignored for execution and is not separately persisted by this adapter. Provider-reported completion usage is retained; its reasoning accounting is unverified. |
| Only reasoning, empty content, prose in content or non-stop completion | These are not successful action completions. | Empty content produces `missing_completion`; non-stop output produces `incomplete_completion`; leaked thought/prose fails the tool schema. Nothing strips thought tags, executes reasoning, or retries without a constraint. A fixed claim still requires current green tests. |

These are source-derived inferences for the pinned version. They do not establish which branch
the actual staged template/runtime takes, how special tokens interact with the grammar, or
whether the native parser returns content correctly. High/low native reasoning behavior is
also unverified. The server docs say generic `reasoning_effort` values other than `none` have
no effect; the worker instead supplies the native `reasoning_strength` template kwarg.

## Primary source anchors

- [Server response-format parsing and chat-template handoff](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/tools/server/server-common.cpp#L933): parsed schema/grammar enter template inputs; generated grammar/triggers are copied into sampling parameters.
- [Server sampling schema](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/tools/server/server-schema.cpp#L251): direct schema conversion, user/tool grammar classification and lazy settings.
- [Muse specialized handler](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/common/chat.cpp#L3096): native framing, reasoning extraction, tools-only grammar gate and trigger; nearby handler selection starts at line 3262.
- [Template grammar permitting analysis before schema output](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/common/chat.cpp#L1373): an example of why response format is not a universal thinking constraint, not a claim that this harness uses that template.
- [Common sampler](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/common/sampling.cpp#L264): eager versus lazy grammar initialization; `grammar_should_apply` at line 452 handles lazy reasoning suppression.
- [Low-level grammar](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/src/llama-grammar.cpp#L1353): no filtering while awaiting a trigger.
- [Grammar documentation](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/grammars/README.md#L72) and [server reasoning documentation](https://github.com/ggml-org/llama.cpp/blob/f8def7fe168bab245fbf15d3f18b26dbb1ef73c8/tools/server/README.md#L1372): explicit thinking-token grammars, whole-output root, extracted reasoning and parsing controls.

## Required device-spike matrix (not run)

After explicit load approval and a passed swap-growth guard, retain the existing direct-native
spike. Additionally inspect the actual deployed chat/template branch and sampler grammar for
both JSON-schema and GBNF requests at low and high native reasoning. Use valid-action and
invalid-action controls, reasoning-only/empty/truncated responses, and record final content,
reasoning presence, native framing, effective sampler parameters, tokenizer usage and timings.
The existing F2 spike measures direct `/completion` only and cannot by itself clear chat risks.

Stop if schema enforcement is absent, framing breaks content extraction, or constraints only
appear to work through voluntary model compliance. Choose and implement a compatible constrained
route before live evaluation/teacher collection; do not silently strip thinking, drop grammar,
add an unconstrained fallback, or claim a four-arm device result. Any training-template or tool
protocol change must preserve replay admission and held-out isolation.
