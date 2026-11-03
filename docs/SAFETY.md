# Safety contract

The only model outputs accepted are schema-valid calls to six tools. No shell tool, arbitrary
test command, package installation, or test-file edit is exposed. Literal patches must match
exactly once in an original source module. A successful patch invalidates prior test success.
`fixed` requires a nonempty, successful test run on the exact current source hash.

Each task runs on a temporary source copy. Its immutable unittest oracle is stored outside the
editable tree. On macOS, sandbox-exec denies network and file writes except the copied source,
temporary home, and bounded output file. Python starts isolated, without site initialization,
and receives a minimal environment without inherited credentials. Wall/CPU/output/file limits
apply; the whole process group is killed after each run, including successful parent exits.
Linux now selects a bubblewrap backend with the same fail-closed verdict. Its real container
execution remains unverified; see `docs/LINUX-SANDBOX.md`. Other hosts fail closed.

An outer, distinct sandbox-exec policy can prevent macOS from applying the task policy.
That condition fails closed, with the child return code and output saved in verification
failure evidence. The demo's offline contract is also tested with socket, DNS, and inference
transport guards in the parent process; sandboxed task processes independently deny network.

This is containment for cooperative coding tasks, not proof against malicious Python cheating
through monkey-patching. The held-out corpus and trajectory replay independently rerun the
immutable oracle. Model self-grading and recorded green claims cannot admit training data.

Mock is the default and performs no inference network calls. Real local inference requires
an explicit opt-in and two swap samples demonstrating bounded growth. Missing or invalid
samples refuse admission. Existing high, stable swap is not itself permission or a refusal.
F1 never starts an inference server or loads model weights. A future loader must use the same
growth-based guard. The existing GGUF may be linked under ignored `models/`; no other model
download is part of this milestone.

Traces contain actions, bounded observations, budgets, safe failure reasons, and final source.
They never include request headers or environment values. Keep artifacts private and ignored.

F3 source audit: accepted-action validation is distinct from constrained native sampling.
The pinned Muse chat handler has schema/grammar framing risks; source-only conclusions and
exact harness behavior are in `docs/GRAMMAR-THINKING.md`. Live compatibility remains unverified.
