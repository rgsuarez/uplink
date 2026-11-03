# Linux container test backend

Status: **code and command-contract tests pass locally; real Linux container execution is
unverified**. No container or remote resource was launched in F2.

`Workspace.run_tests` selects macOS sandbox-exec on macOS and bubblewrap on Linux. Missing
backends, unsupported platforms, unavailable namespaces, or a failed sandbox process cannot
fall back to an uncontained Python command and cannot produce green tests. Linux uses the
same immutable oracle, source hashes, test-count check, CPU/wall/output limits, environment
scrubbing and process-group cleanup as macOS.

The Linux plan uses bubblewrap namespaces (`--unshare-all`), a separate PID/session, dropped
capabilities, disabled nested user namespaces, and a cleared environment. It mounts only
runtime directories read-only, the single oracle file read-only, and the disposable work
copy writable. Home and temporary space are empty. Host root, project source, credentials,
model mounts and sockets are not visible. The network namespace has no external interface.

The backend embeds the existing bubblewrap isolation primitive. Namespace/runtime support
inside the selected GPU container is a launch gate, not an assumption. Source options were
checked against upstream `containers/bubblewrap` v0.11.0. Build pinning is part of the remote
toolchain. The recorded fixture tests inspect the exact argument vector and refusal paths;
they do not substitute for a real runtime proof.

After separate authorization to run a Linux container, execute inside that container:

```sh
python scripts/linux_sandbox_smoke.py
```

It requires a real red-to-green repair, rejects stale green, denies network, denies an oracle
write, hides credentials, and enforces a timeout. On success it writes
`artifacts/linux-sandbox-acceptance.json`. Namespace refusal is a blocking diagnostic. Do not
enable remote evaluation or teacher replay until this real acceptance script passes there.

Containment targets cooperative coding tasks. Python source can still try to monkey-patch
unittest; this backend is not a proof against malicious oracle gaming.
