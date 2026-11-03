"""Value-blind publication scan; prints locations and rule names, never matched values."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from offline_agent.contracts import object_json
from offline_agent.release import release_files, scan_files
from offline_agent.artifact_files import json_file
from offline_agent.uplink.lab_config import load_config

# Exact legacy wire/test lines, retained without admitting new host bindings.
COMPATIBILITY_LINES = {
    "tests/test_f9.py": (
        "7928bb01e07a298eff00e804064e1ee06a4b45baeab60305be80b9893c84cb3b",
        "frozen_provider_test_fixture",
    ),
    "src/offline_agent/f10_results.py": (
        "08924dfe515dc120e374bd043a502215d1ae11525f10ea53a6ccaf195280df38",
        "historical_receipt_compatibility",
    ),
}


def scan(
    root: Path, private_config: Path | None = None, *, files: list[Path] | None = None
) -> dict[str, object]:
    explicit_files = files is not None
    files = release_files(root) if files is None else files
    if not files:
        raise ValueError("empty_scan_scope")
    if len(set(files)) != len(files) or any(
        path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in files
    ):
        raise ValueError("invalid_scan_scope")
    patterns = {
        "personal_path": re.compile(r"(?:/Users/|/home/)[a-zA-Z0-9_.-]+/"),
        "named_approval_flag": re.compile(r"--approved-by\s+[a-zA-Z]+"),
    }
    if private_config is not None:
        config = load_config(private_config)
        patterns.update(
            {
                "lab_identity": re.compile(
                    r"(?<![a-zA-Z0-9_])(?:"
                    + "|".join(
                        re.escape(v) for v in {config.node, config.oob_host, config.wifi_host}
                    )
                    + r")(?![a-zA-Z0-9_])",
                    re.IGNORECASE,
                ),
                "device_serial": re.compile(re.escape(config.approved_serial)),
                "private_lab_path": re.compile(re.escape(config.bench_relative)),
            }
        )
    registration = root / "configs/f10-ondevice.json"
    registration_digest = "02bd368bd332a613dcedfe719add19ede11e7efe192ad27035756234e6dc5459"
    if hashlib.sha256(registration.read_bytes()).hexdigest() != registration_digest:
        raise ValueError("frozen_registration_mismatch")
    frozen = object_json(json_file(registration)["protected_sha256"])
    # Only unchanged frozen bytes may explain a legacy label, never a credential.
    frozen_paths = {}
    for name, digest in frozen.items():
        if not isinstance(digest, str):
            raise ValueError("frozen_digest_required")
        relocated = "bench/lab/" + Path(name).name if name.startswith("bench/") else name
        frozen_paths[relocated] = digest
    frozen_paths["configs/f10-ondevice.json"] = registration_digest
    findings: list[dict[str, object]] = [
        {"path": f.path, "rule": f.rule} for f in scan_files(root, files)
    ]
    exceptions: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for path in files:
        name = str(path.relative_to(root))
        content = path.read_bytes()
        unchanged_frozen = frozen_paths.get(name) == hashlib.sha256(content).hexdigest()
        for number, line in enumerate(content.decode("utf-8").splitlines(), 1):
            for rule, pattern in patterns.items():
                if not pattern.search(line):
                    continue
                counts[rule] += 1
                location = {"path": name, "line": number, "rule": rule}
                if rule == "lab_identity" and unchanged_frozen:
                    exceptions.append({**location, "reason": "unchanged_frozen_f10_input"})
                elif (
                    rule == "lab_identity"
                    and name in COMPATIBILITY_LINES
                    and hashlib.sha256(line.strip().encode()).hexdigest()
                    == COMPATIBILITY_LINES[name][0]
                ):
                    exceptions.append({**location, "reason": COMPATIBILITY_LINES[name][1]})
                elif (
                    rule == "personal_path"
                    and name == "tests/uplink/test_trace.py"
                    and "/home/" + "synthetic-person/" in line
                ):
                    exceptions.append({**location, "reason": "synthetic_privacy_regression"})
                else:
                    findings.append(location)
    return {
        "scan_version": 1,
        "scope": "explicit_export_files"
        if explicit_files
        else "tracked_and_unignored_candidate_files",
        "files_scanned": len(files),
        "private_coordinates_checked": private_config is not None,
        "matches": {rule: counts[rule] for rule in patterns},
        "findings": findings,
        "exceptions": exceptions,
        "passed": not findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-config", type=Path)
    args = parser.parse_args()
    result = scan(Path(__file__).resolve().parents[1], args.private_config)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
