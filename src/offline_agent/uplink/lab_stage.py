"""Content-verified stdlib worker transfer through Lima's existing copy transport."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.lab_config import configured_host

WORKERS = (
    "lab_protocol.py",
    "lab_worker.py",
    "lab_servers.py",
    "lab_setup.py",
    "probe_protocol.py",
    "probe_execution.py",
    "faults_v1_linux.py",
)
SEALED_WORKERS = ("sealed_registry.py", "sealed_linux.py", "sealed_qualify.py")
SEALED_FILES = tuple(
    code + ".json"
    for code in ("P04", "D09", "X02", "G02", "I04", "I02", "X03", "X04", "C04", "P07", "F05", "V03")
)
VM_PREFIX = ("limactl", "shell", "--workdir", "/", "uplink-harness")


def staging_failure(stderr: str) -> str:
    """Carry a known value-blind reason across the transport; never echo raw stderr."""
    known = {
        "bundle_directory_refused",
        "bundle_owner_or_permissions",
        "unexpected_bundle_files",
        "bundle_content_mismatch",
        "bundle_publish_collision",
        "unknown_stage_operation",
    }
    matches = re.findall(r"(?m)^ValueError: ([a-z_]+)$", stderr)
    return matches[-1] if matches and matches[-1] in known else "unclassified_staging_failure"


# No root here. An existing destination is reusable only when every byte matches.
STAGE_CODE = r"""
import hashlib,json,os,pathlib,sys,tempfile
value=json.loads(sys.argv[1]); expected=value['hashes']; target=pathlib.Path(value['directory'])
def verify(directory):
 if directory.is_symlink() or not directory.is_dir():
  raise ValueError('bundle_directory_refused')
 if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
  raise ValueError('bundle_owner_or_permissions')
 if set(p.name for p in directory.iterdir()) != set(expected):
  raise ValueError('unexpected_bundle_files')
 for name,digest in expected.items():
  p=directory/name
  if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
   raise ValueError('bundle_content_mismatch')
if value['operation']=='prepare':
 if target.exists():
  verify(target); print(json.dumps({'ready':True,'directory':str(target)}))
 else:
  stage=tempfile.mkdtemp(prefix=target.name+'-stage-',dir='/tmp')
  print(json.dumps({'ready':False,'directory':stage}))
elif value['operation']=='publish':
 source=pathlib.Path(value['staging']); verify(source)
 for p in source.iterdir(): p.chmod(0o600)
 if target.exists():
  raise ValueError('bundle_publish_collision')
 os.rename(source,target); verify(target)
 print(json.dumps({'ready':True,'directory':str(target)}))
else:
 raise ValueError('unknown_stage_operation')
"""

# Execute only after the source manifest is rechecked inside the VM, under the
# same privilege and interpreter that will execute the fixed worker entrypoint.
EXEC_VERIFIED = r"""
import hashlib,json,os,pathlib,sys
v=json.loads(sys.argv[1]); root=pathlib.Path(v['directory'])
if root.is_symlink() or not root.is_dir(): raise ValueError('bundle_directory_refused')
if set(p.name for p in root.iterdir()) != set(v['hashes']):
 raise ValueError('unexpected_bundle_files')
for name,digest in v['hashes'].items():
 p=root/name
 if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
  raise ValueError('worker_bundle_changed')
os.environ['PYTHONDONTWRITEBYTECODE']='1'
args=[sys.executable,'-B',str(root/'lab_worker.py')]
args+=['--execute'] if v['execute'] else []
os.execv(sys.executable,args+['--request',json.dumps(v['request'])])
"""


@dataclass(frozen=True)
class WorkerBundle:
    source: Path
    hashes: dict[str, str]

    @classmethod
    def read(cls, source: Path, *, sealed: bool = False) -> "WorkerBundle":
        source = source.resolve()
        files = {name: source / name for name in WORKERS}
        files["setup.sh"] = source.parents[2] / "harness/linux/setup.sh"
        if sealed:
            files.update({name: source / name for name in SEALED_WORKERS})
            for name in SEALED_FILES:
                files[name] = source.parents[2] / "harness/linux/sealed" / name
        hashes = {}
        for name, path in files.items():
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
                raise ContractError("bounded_worker_bundle_required")
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(source, hashes)

    @property
    def directory(self) -> str:
        digest = hashlib.sha256(json.dumps(self.hashes, sort_keys=True).encode()).hexdigest()
        return "/tmp/uplink-worker-" + digest[:24]

    @property
    def sources(self) -> tuple[str, ...]:
        sealed = "sealed_registry.py" in self.hashes
        return (
            *(str(self.source / name) for name in WORKERS),
            str(self.source.parents[2] / "harness/linux/setup.sh"),
            *(str(self.source / name) for name in SEALED_WORKERS if sealed),
            *(
                str(self.source.parents[2] / "harness/linux/sealed" / name)
                for name in SEALED_FILES
                if sealed
            ),
        )

    def command(self, request: dict[str, object], *, execute: bool) -> tuple[str, ...]:
        payload = {
            "directory": self.directory,
            "hashes": self.hashes,
            "execute": execute,
            "request": request,
        }
        return (
            *VM_PREFIX,
            *(("sudo", "-n") if execute else ()),
            "python3",
            "-B",
            "-c",
            EXEC_VERIFIED,
            json.dumps(payload, sort_keys=True),
        )


def stage_worker(source: Path, *, authorize: bool = False, sealed: bool = False) -> WorkerBundle:
    if (
        not authorize
        or platform.system() != "Darwin"
        or not configured_host()
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        raise ContractError("worker_transfer_requires_explicit_lab_staging")
    bundle = WorkerBundle.read(source, sealed=sealed)

    def invoke(operation: str, staging: str | None = None) -> dict[str, Json]:
        payload = {"operation": operation, "directory": bundle.directory, "hashes": bundle.hashes}
        if staging is not None:
            payload["staging"] = staging
        result = subprocess.run(
            (*VM_PREFIX, "python3", "-c", STAGE_CODE, json.dumps(payload)),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            raise ContractError("vm_bundle_staging_refused__" + staging_failure(result.stderr))
        return object_json(read_json(result.stdout, max_bytes=4096))

    prepared = invoke("prepare")
    if prepared.get("ready") is True:
        if prepared.get("directory") != bundle.directory:
            raise ContractError("vm_bundle_location_mismatch")
        return bundle
    staging = prepared.get("directory")
    if not isinstance(staging, str) or not re.fullmatch(
        re.escape(bundle.directory) + r"-stage-[a-zA-Z0-9_]+", staging
    ):
        raise ContractError("vm_staging_directory_refused")
    result = subprocess.run(
        ("limactl", "copy", "--backend=scp", *bundle.sources, "uplink-harness:" + staging + "/"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ContractError("vm_bundle_copy_failed_no_retry")
    verified = invoke("publish", staging)
    if verified.get("ready") is not True or verified.get("directory") != bundle.directory:
        raise ContractError("vm_bundle_verification_failed")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--sealed", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    bundle = (
        stage_worker(source, authorize=True, sealed=args.sealed)
        if args.stage
        else WorkerBundle.read(source, sealed=args.sealed)
    )
    result: dict[str, Json] = {
        "mode": "staged" if args.stage else "plan",
        "directory": bundle.directory,
        "hashes": dict(bundle.hashes),
        "root_effects": False,
    }
    if args.stage:
        from offline_agent.uplink.lab_protocol import request

        checked = subprocess.run(
            bundle.command(request("observe", "0123456789ab"), execute=False),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        response = object_json(read_json(checked.stdout)) if checked.returncode == 0 else {}
        result["dry_worker_ok"] = checked.returncode == 0 and response.get("effects") is False
        if result["dry_worker_ok"] is not True:
            print(json.dumps(result))
            raise SystemExit("staged_worker_dry_probe_failed")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
