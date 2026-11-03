# F2 source restaging for C3 v10

Status: desk-prepared, no host or VM operation performed. This packet restores the
exact sealed worker bytes required by C3 v10. It runs no collection, model, fault,
watchdog or training job. Execution still needs an exclusive fleet window covering
the existing VM, source transfer and the named cleanup. Infrastructure preparation
does not wait for another review cycle.

The 03:56 staging ruling says the stopped VM's `/tmp` was tmpfs, so the old bundle
and root-owned bytecode cache are gone. Do not mount a disk image to recover them.
Verify that state after an authorized restart. An unexpected surviving cache is a
named exception below, never a reason to ignore extra files or weaken the gate.

| Binding | Value |
| --- | --- |
| Sealed source | `90fef73c1169950a7490ba3dabe0607bc1b93c8f` |
| Plan SHA-256 | `54996e420d3557e152c0c7bf12ff7aeae17d7e857a5a59129c99496fc0d60496` |
| Collection payload SHA-256, separate from this source archive | `e32ba3af6261c9bef839080167d5d780f32662abbf4525b837e39e06c9cde237` |
| Guest bundle | `/tmp/uplink-worker-5b4c78804f0c12499b567792` |
| Worker files / bytes | 22 / 133,152 |
| Host source files / bytes, including five stager dependencies | 27 / 151,488 |
| Source archive bytes | 184,320 |
| Source archive SHA-256 | `488eb41561a71e51d4632fd7f55bae2ac16888fd79dab52d75953375548b7178` |
| Source manifest SHA-256 | `d189e9ec9556cf98fe59e9d6849dbfa613e7f55d374c6496a9977cf95313ed80` |

The prepared inputs are under `artifacts/uplink/f2-restage-v10-20261028/`:
`source.tar`, `manifest.json`, `host-proof.py`, `guest-proof.py`, and `packet.json`.
The archive contains sealed descriptors and stays private on the evaluation side.
The committed hash-only record is
`evidence/uplink/f2-restage-v10-packet-20261028.json`. All 22 worker hashes were
checked locally against both the fixed source commit and the v10 plan. The host
and guest proofs below remain pending execution; local matching is not live proof.

## Resource and scheduling request

F2 only: estimate 2-5 minutes, allow 8 minutes including an existing-VM boot.
File hashing/staging should use less than 256 MiB of incremental process memory;
this is an estimate, not a measurement. Reserve 32 MiB host and 32 MiB guest disk
for source copies and receipts. If unexpected cache preservation is needed, its
measured size is additional and must be included before the move. VM residency
is its existing configured allocation; inspect it before start, do not resize it.

The subsequent C3 collection is separate: 19 serial jobs, 43 captures, 180 seconds
per job, 5-19 minute forecast, 45-minute whole-window cap, 1 GiB incremental-memory
budget and 1 GiB shared disk reservation from v10. No GPU or model calls, no paid
spend. Do not silently fit collection into an F2-only window. The fixed plan and
submitter retain their exact execution-envelope validation; the standing infra
direction does not permit fabricating its window or proof fields.

## Exact execution sequence, only inside the released window

Run controller commands from this repository root. Use a new host directory for
each authorized attempt. The following commands do not change any model server.
Capture command failures and stop; never continue after a partial transfer.

```sh
set -e
umask 077
F2_PACKET="$PWD/artifacts/uplink/f2-restage-v10-20261028"
F2_HOST="/private/tmp/uplink-f2-v10-${CODEX_THREAD_ID}"
F2_VM="uplink-harness"
F2_OOB=$(python3 -B -c 'import json;from pathlib import Path;print(json.loads(Path("src/offline_agent/uplink/lab.local.json").read_text())["oob_host"])')
test -n "$CODEX_THREAD_ID"
test -f "$F2_PACKET/source.tar"
```

Claim the host path with the direct lane authority before creating it. Require
zero conflicting claims and a nonempty thread ID. The VM bundle is a separate
shared target; claim it too before transfer. Claims do not replace the window.

```sh
lane list --json
lane claim uplink-f2-host-v10 --repo offline-agent-live \
  --target "$F2_HOST" --instance "codex-${CODEX_THREAD_ID}"
lane claim uplink-f2-guest-v10 --repo offline-agent-live \
  --target /tmp/uplink-worker-5b4c78804f0c12499b567792 \
  --instance "codex-${CODEX_THREAD_ID}"
ssh -o BatchMode=yes -o ConnectTimeout=3 "$F2_OOB" true
ssh "$F2_OOB" 'limactl list --json' > "$F2_PACKET/vm-before.private.json"
```

If the named VM is stopped, start that existing VM only when the released window
explicitly includes its start. Never create a replacement or mount its disk.
Record whether this window started it, for the handback decision.

```sh
ssh "$F2_OOB" 'limactl start uplink-harness'
```

Create the normal-user host directory exclusively, transfer only the listed files,
and prove the host archive and all 22 worker hashes before touching the guest.
`host-proof.py` checks the pinned archive/manifest digests and refuses reuse.

```sh
ssh "$F2_OOB" "umask 077; mkdir '$F2_HOST'"
rsync -a "$F2_PACKET/source.tar" "$F2_PACKET/manifest.json" \
  "$F2_PACKET/host-proof.py" "$F2_PACKET/guest-proof.py" \
  "$F2_OOB:$F2_HOST/"
ssh "$F2_OOB" "python3 -B - '$F2_HOST'" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
expected = {
    'host-proof.py': 'f93778f323183c18c2ac2aa2d68d5ad8d34e0844fe627e21d79e57071bbc1908',
    'guest-proof.py': '5f4b3beaf30864c58b06100fb804ce68f3c1cdf6717e9d8b507852951815b37c',
}
assert all(hashlib.sha256((root / p).read_bytes()).hexdigest() == h for p, h in expected.items())
print('helper hashes match')
PY
ssh "$F2_OOB" "python3 -B '$F2_HOST/host-proof.py' '$F2_HOST'" \
  > "$F2_PACKET/host-proof.json"
```

The inline check proves the transferred helper hashes before either executes.
Never dump the private config.

### Read-only VM `/tmp` listing and idle proof

This runs only stdlib metadata/hash checks plus `ip -j netns list` and `findmnt`.
It requires zero owned namespaces and zero non-closed state directories. It lists
only `uplink-*` names, entry types, ownership and modes; no fixture contents.

```sh
ssh "$F2_OOB" "limactl shell --workdir / '$F2_VM' sudo -n env \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -c \"\$(cat '$F2_HOST/guest-proof.py')\" inspect \
  < '$F2_HOST/manifest.json'" > "$F2_PACKET/guest-before.json"
```

Expected: bundle absent, `/tmp` on tmpfs, zero namespaces/active runs. If a clean
22-file bundle already exists, verify it and report reuse; do not overwrite it.
If unexpected entries exist, stop for the bounded cache disposition below.

### Source restaging and guest proof

Copy the existing private config only into the host-side stager's source directory,
mode 0600. It is not part of the guest worker manifest and must never be copied to
the guest. The host stager uses the existing Python environment named by that config.
No install, model load, fixture qualification or collection is part of staging.

```sh
rsync -a src/offline_agent/uplink/lab.local.json \
  "$F2_OOB:$F2_HOST/source/src/offline_agent/uplink/lab.local.json"
ssh "$F2_OOB" "python3 -B - '$F2_HOST'" > "$F2_PACKET/stage-receipt.json" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]); source = root / 'source'
config = source / 'src/offline_agent/uplink/lab.local.json'
assert config.stat().st_mode & 0o777 == 0o600
private = json.loads(config.read_text())
python = Path.home() / private['python_relative']
env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(source / 'src')}
result = subprocess.run([str(python), '-B', '-m', 'offline_agent.uplink.lab_stage',
                         '--sealed', '--stage'], cwd=source, env=env,
                        capture_output=True, text=True, timeout=90)
(root / 'stage.stderr.private').write_text(result.stderr)
if result.returncode: raise SystemExit('source_stage_failed_no_retry')
receipt = json.loads(result.stdout)
manifest = json.loads((root / 'manifest.json').read_text())
assert receipt['directory'] == manifest['worker_bundle_directory']
assert receipt['hashes'] == manifest['worker_hashes'] and receipt['dry_worker_ok'] is True
print(json.dumps(receipt, sort_keys=True))
PY
ssh "$F2_OOB" "limactl shell --workdir / '$F2_VM' sudo -n env \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -c \"\$(cat '$F2_HOST/guest-proof.py')\" verify \
  < '$F2_HOST/manifest.json'" > "$F2_PACKET/guest-proof.json"
```

Both proofs must contain exactly the plan's 22 hashes and guest proof must show
zero bytecode entries. The stager's dry worker call has `execute=false`; it does
not run a sealed case. Hand these proofs to the existing C3 submitter before job 1.
Do not invoke collection as part of this packet. V10's own before/after bundle
checks remain mandatory for every job, including job 19.

## CS-2 cache exception and rollback

The default is no cache, per the stopped-tmpfs ruling. If inspection contradicts
that, preserve the inventory and stop staging. With the fleet window covering this
root-file move, the bounded command below verifies the unchanged 22 source hashes,
allows exactly one extra root-owned `__pycache__`, records each cached file's hash,
and atomically moves it outside the bundle on the same `/tmp` filesystem. It never
deletes the evidence or imports it. Any other extra entry or symlink refuses.

```sh
ssh "$F2_OOB" "limactl shell --workdir / '$F2_VM' sudo -n env \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -c \"\$(cat '$F2_HOST/guest-proof.py')\" quarantine-cache \
  < '$F2_HOST/manifest.json'" > "$F2_PACKET/cache-quarantine.json"
```

Quarantine is `/tmp/uplink-cs2-cache-v10-5b4c7880`; capture its root-owned intent
and bytes privately before any VM stop, because tmpfs is not durable. Never restore
the cache into an executable bundle. Do not use `rm -rf`, ignore bytecode, or mask
`tmp.mount`. Re-run `verify` only after the explicit cache reconciliation; this is
not a retry of a failed collection job.

```sh
ssh "$F2_OOB" "limactl shell --workdir / '$F2_VM' sudo -n tar \
  -C /tmp -cf - uplink-cs2-cache-v10-5b4c7880" > "$F2_PACKET/cache-quarantine.private.tar"
```

On any transfer/hash/owner/idle mismatch: stop, retain receipts, no collection and no
automatic retry. F2 changes no network state, so there is no network revert. If
this window newly created the guest bundle and it remains exactly the verified
22 files with no active run, remove only that owned bundle as rollback:

```sh
ssh "$F2_OOB" "limactl shell --workdir / '$F2_VM' sudo -n env \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -c \"\$(cat '$F2_HOST/guest-proof.py')\" \
  remove-owned-bundle --created-in-this-window < '$F2_HOST/manifest.json'" \
  > "$F2_PACKET/guest-rollback.json"
```

Do not apply that command to a reused bundle or one handed to C3. Partial staging
directories require inspection and exact path/owner classification, not a glob.
Retrieve host receipts and any quarantine evidence, remove the newly owned host
directory with its private config, verify absence, and release both lane claims.
If this window started the VM and no immediate C3 handoff is agreed, stop the
existing VM normally with `limactl stop uplink-harness`; never force it and never
stop a VM with an active run. Record host/guest hashes, cleanup, VM disposition,
duration, observed memory and disk, and the next window owner in the handback.

```sh
rsync -a "$F2_OOB:$F2_HOST/stage.stderr.private" "$F2_PACKET/"
ssh "$F2_OOB" "python3 -B - '$F2_HOST'" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
assert root.parent == Path('/private/tmp') and root.name.startswith('uplink-f2-v10-')
assert root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.getuid()
manifest = json.loads((root / 'manifest.json').read_text())
assert all(hashlib.sha256((root / 'source' / p).read_bytes()).hexdigest() == h
           for p, h in manifest['source_hashes'].items())
shutil.rmtree(root)
print(json.dumps({'host_staging_removed': not root.exists()}))
PY
lane release uplink-f2-host-v10 --repo offline-agent-live \
  --instance "codex-${CODEX_THREAD_ID}"
# Release guest ownership only after removal or explicit transfer to the C3 owner.
```
