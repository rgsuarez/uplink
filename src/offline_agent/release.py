"""Local release gates with value-blind findings and an offline local-clone rehearsal."""

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.artifact_files import run_directory
from offline_agent.artifact_files import json_file

MAX_RELEASE_FILE = 1_048_576
RESTRICTED = tuple(
    bytes.fromhex(value).lower()
    for value in (
        "4c6971756964",
        "4c464d",
        "4c5148",
        "456c656574",
        "4765617578",
    )
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?:hf_|sk-)[A-Za-z0-9_-]{24,}"),
    re.compile(rb"AKIA[A-Z0-9]{16}"),
    re.compile(
        rb"(?i)(?:model_api_key|api_key|access_token|password)\s*[=:]\s*[\"'][^\"'\s]{16,}[\"']"
    ),
    re.compile(rb"https?://[^\s/:@]+:[^\s/@]+@"),
)
PERMISSIVE = {
    "Apache-2.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "PSF-2.0",
    "Apache-2.0 OR BSD-2-Clause",
}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=30
    ).stdout


def release_files(root: Path) -> list[Path]:
    """Inspect every tracked and non-ignored candidate; never hide a file by its name."""
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    files: list[Path] = []
    for name in sorted(set(names) - {""}):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("release_symlink_or_escape_refused")
        if not path.is_file():
            raise ValueError("release_tracked_file_missing")
        files.append(path)
    return files


def scan_bytes(name: str, content: bytes) -> list[Finding]:
    findings: list[Finding] = []
    for index, needle in enumerate(RESTRICTED, 1):
        if needle in content.lower() or needle in name.lower().encode():
            findings.append(Finding(name, f"restricted-name-{index}"))
    for index, pattern in enumerate(SECRET_PATTERNS, 1):
        if pattern.search(content):
            findings.append(Finding(name, f"secret-pattern-{index}"))
    return findings


def scan_files(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    forbidden_suffixes = {
        ".gguf",
        ".safetensors",
        ".onnx",
        ".pt",
        ".pth",
        ".bin",
        ".mp4",
        ".mov",
        ".wav",
    }
    for path in files:
        name = str(path.relative_to(root))
        if path.stat().st_size > MAX_RELEASE_FILE:
            findings.append(Finding(name, "large-file"))
            continue
        if (
            path.suffix.lower() in forbidden_suffixes
            or path.name.startswith(".env")
            or path.suffix == ".key"
            or path.name.endswith(".local.json")
        ):
            findings.append(Finding(name, "forbidden-release-file"))
        findings += scan_bytes(name, path.read_bytes())
    return findings


def scan_history(root: Path) -> list[Finding]:
    """Scan unique committed blobs; never print a matching byte or neighboring context."""
    findings: list[Finding] = []
    for line in git(root, "rev-list", "--objects", "--all").splitlines():
        oid, _, name = line.partition(" ")
        if git(root, "cat-file", "-t", oid).strip() != "blob":
            continue
        size = int(git(root, "cat-file", "-s", oid))
        if size > MAX_RELEASE_FILE:
            findings.append(Finding(f"history:{oid[:12]}:{name}", "large-file"))
            continue
        content = subprocess.run(
            ["git", "cat-file", "blob", oid], cwd=root, capture_output=True, check=True, timeout=10
        ).stdout
        findings += scan_bytes(f"history:{oid[:12]}:{name}", content)
    return findings


def check_licenses(root: Path, files: list[Path]) -> list[Finding]:
    manifest = json_file(root / "licenses/manifest.json")
    if manifest.get("version") != 1:
        raise ValueError("license_manifest_version_required")
    rules = manifest.get("project_rules")
    third_party = manifest.get("third_party_files")
    dependencies = manifest.get("dependencies")
    if (
        not isinstance(rules, list)
        or not isinstance(third_party, list)
        or not isinstance(dependencies, list)
    ):
        raise ValueError("complete_license_manifest_required")
    findings: list[Finding] = []
    asset_rules = {str(object_json(value).get("path")): object_json(value) for value in third_party}
    names = {str(path.relative_to(root)) for path in files}
    for name, rule in asset_rules.items():
        path = root / name
        if name not in names or not path.is_file():
            findings.append(Finding(name, "licensed-asset-missing"))
        elif hashlib.sha256(path.read_bytes()).hexdigest() != rule.get("sha256"):
            findings.append(Finding(name, "licensed-asset-hash-mismatch"))
        license_id = rule.get("license")
        if license_id not in PERMISSIVE and license_id != "Meta-Muse-Glimmer-30B":
            findings.append(Finding(name, "unapproved-asset-license"))
        for required in ("source", "revision", "license_file"):
            if not isinstance(rule.get(required), str) or not rule[required]:
                findings.append(Finding(name, "asset-provenance-incomplete"))
        license_file = root / str(rule.get("license_file"))
        if str(license_file.relative_to(root)) not in names:
            findings.append(Finding(name, "asset-license-text-missing"))
    for name in names - set(asset_rules):
        matching = [
            object_json(value)
            for value in rules
            if fnmatch.fnmatchcase(name, str(object_json(value).get("pattern")))
        ]
        if len(matching) != 1 or matching[0].get("license") not in PERMISSIVE:
            findings.append(Finding(name, "missing-or-ambiguous-file-license"))
    lock = tomllib.loads((root / "uv.lock").read_text())
    locked = {
        (str(row["name"]), str(row["version"]))
        for row in lock["package"]
        if row["name"] != "offline-agent"
    }
    declared: set[tuple[str, str]] = set()
    for value in dependencies:
        row = object_json(value)
        identity = (str(row.get("name")), str(row.get("version")))
        if identity in declared or row.get("license") not in PERMISSIVE or not row.get("source"):
            findings.append(Finding(identity[0], "invalid-dependency-license"))
        declared.add(identity)
    if declared != locked:
        findings.append(Finding("uv.lock", "dependency-license-manifest-drift"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    build = manifest.get("build_dependencies")
    if not isinstance(build, list):
        raise ValueError("pinned_build_license_manifest_required")
    declared_build = {
        f"{object_json(value).get('name')}=={object_json(value).get('version')}" for value in build
    }
    if declared_build != set(pyproject["build-system"]["requires"]):
        findings.append(Finding("pyproject.toml", "build-license-manifest-drift"))
    for value in build:
        if object_json(value).get("license") not in PERMISSIVE:
            findings.append(Finding("pyproject.toml", "unapproved-build-license"))
    return findings


def clean_clone(root: Path, output: Path) -> dict[str, Json]:
    """All clone, install, caches and temporary test sources stay inside this repository."""
    clone = output / "clone"
    head = git(root, "rev-parse", "HEAD").strip()
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(root), str(clone)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    git(clone, "remote", "remove", "origin")
    clone_head = git(clone, "rev-parse", "HEAD").strip()
    if clone_head != head or git(clone, "status", "--porcelain").strip():
        raise ValueError("local_clone_head_or_cleanliness_mismatch")
    scratch = output / "tmp"
    scratch.mkdir()
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("existing_uv_required_no_installer")
    environment = {
        "PATH": os.defpath,
        "UV_CACHE_DIR": str(root / ".cache/uv"),
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "XDG_CACHE_HOME": str(root / ".cache"),
    }
    commands = [
        [uv, "sync", "--frozen", "--offline", "--python", str(Path(sys.executable).resolve())],
        [
            str(clone / ".venv/bin/python"),
            "-c",
            "from pathlib import Path; import offline_agent; "
            "assert Path(offline_agent.__file__).is_relative_to(Path.cwd()/'src')",
        ],
        [uv, "run", "--frozen", "--offline", "offline-agent", "--help"],
        [uv, "run", "--frozen", "--offline", "offline-agent", "demo"],
        [uv, "run", "--frozen", "--offline", "offline-agent", "demo-watch", "--delay", "0"],
    ]
    for index, command in enumerate(commands, 1):
        result = subprocess.run(
            command, cwd=clone, env=environment, capture_output=True, timeout=120
        )
        (output / f"clone-{index}.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError(f"clean_clone_check_{index}_failed_see_persisted_log")
    if git(clone, "status", "--porcelain").strip() or git(clone, "remote").strip():
        raise ValueError("clone_changed_tracked_state_or_has_remote")
    return {
        "head": clone_head,
        "install": "frozen/offline",
        "import_from_clone": True,
        "mock_demo": True,
        "mock_demo_watch": True,
        "remotes": 0,
    }


def release_check(root: Path) -> Path:
    files = release_files(root)
    findings = scan_files(root, files) + scan_history(root) + check_licenses(root, files)
    output = run_directory(root, "release-check")
    report: dict[str, Json] = {
        "version": 1,
        "published": False,
        "files_checked": len(files),
        "head": git(root, "rev-parse", "HEAD").strip(),
        "findings": [object_json(asdict(finding)) for finding in findings],
        "passed": False,
    }
    target = output / "receipt.json"
    try:
        if findings:
            raise ValueError("release_scans_failed_see_value_blind_receipt")
        if git(root, "status", "--porcelain").strip():
            raise ValueError("commit_authorized_milestone_before_clean_clone_check")
        report["clean_clone"] = clean_clone(root, output)
        report["passed"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["reason"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        target.write_text(json.dumps(report, indent=2) + "\n")
        raise ValueError(f"release_check_refused_receipt:{target.relative_to(root)}") from exc
    target.write_text(json.dumps(report, indent=2) + "\n")
    return target
