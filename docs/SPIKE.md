# Device spike

**Not run. No model has been loaded and no device performance measurement is available.**
F2 implemented the rehearsal script and tested pure helpers/recorded response fixtures only.
The device script and `make device-spike` were deliberately not executed.

| Measurement | Status |
| --- | --- |
| Grammar-constrained generation tok/s | Unmeasured |
| Prompt-evaluation tok/s | Unmeasured |
| Sampled peak server RSS | Unmeasured |
| Swap growth during loading/inference | Unmeasured |

The target defaults to refusal. A future separately approved run requires the existing pinned
GGUF and llama.cpp b10353 binary, an explicit `DEVICE_ALLOW_LOAD=1`, and swap-growth admission.
No model download is performed. An unrelated process already occupying the server port blocks
startup; the script does not reuse it.

Future command, **not run in F2**:

```sh
make device-spike DEVICE_ALLOW_LOAD=1
```

The default GGUF path is under ignored `models/`. Reuse the already-downloaded file via a
symlink, or supply its allowed existing path with `DEVICE_GGUF=...`. `LLAMA_SERVER=...` can
select the already-installed pinned binary. All outputs remain in this repository.

Before starting llama.cpp, two swap samples must show bounded growth. Startup and generation
are then monitored for cumulative swap growth (default 64 MiB), sampled process RSS (28 GiB),
and wall time (180 seconds). A refused monitor or unknown sample kills the owned process group
and persists the failure. Existing high swap is never reset or treated as permission to load.

The request uses the pinned native direct-output prefix, seed 0, grammar, and no prompt-cache
reuse. Native prompt and generation timing fields are parsed separately and checked for
positive, finite, consistent values and a complete schema-valid action. Three runs produce
median speed plus sampled peak server RSS. RSS is not total unified-memory accounting.
Successful live execution would replace this file with measurements linked to raw evidence;
a failed authorized attempt writes an explicit refusal and preserves its diagnostics.
