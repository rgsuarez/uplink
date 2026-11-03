"""Scan fixtures pin detections, value-blind output and refusal of an empty scope."""

import json
import runpy
import shutil
from pathlib import Path
from typing import Any

import pytest

from offline_agent.uplink import lab_config

ROOT = Path(__file__).resolve().parents[2]


def scanner(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    module = runpy.run_path(str(ROOT / "scripts/scrub_check.py"))
    (tmp_path / "configs").mkdir()
    shutil.copyfile(ROOT / "configs/f10-ondevice.json", tmp_path / "configs/f10-ondevice.json")
    target = tmp_path / "sample.txt"
    module["scan"].__globals__["release_files"] = lambda _: [target]
    return module, target


def test_clean_public_text_passes(tmp_path: Path) -> None:
    module, target = scanner(tmp_path)
    target.write_text("A bounded offline experiment.\n")
    assert module["scan"](tmp_path, lab_config.CONFIG_PATH)["passed"] is True


def test_private_values_and_credentials_fail_without_echoing_them(tmp_path: Path) -> None:
    module, target = scanner(tmp_path)
    config = lab_config.load_config()
    secret = "sk-" + "x" * 30
    target.write_text(config.oob_host + "\n" + config.approved_serial + "\n" + secret)
    result = module["scan"](tmp_path, lab_config.CONFIG_PATH)
    assert result["passed"] is False
    rules = {row["rule"] for row in result["findings"]}
    assert {"lab_identity", "device_serial", "secret-pattern-3"} <= rules
    serialized = json.dumps(result)
    assert all(
        value not in serialized for value in (secret, config.oob_host, config.approved_serial)
    )


def test_empty_scan_is_not_a_pass(tmp_path: Path) -> None:
    module, _ = scanner(tmp_path)
    module["scan"].__globals__["release_files"] = lambda _: []
    with pytest.raises(ValueError, match="empty_scan_scope"):
        module["scan"](tmp_path)


def test_explicit_export_scope_never_reads_the_git_index(tmp_path: Path) -> None:
    module, target = scanner(tmp_path)
    target.write_text("A bounded offline experiment.\n")

    def no_git(_: Path) -> list[Path]:
        raise AssertionError("a history-free export has no index")

    module["scan"].__globals__["release_files"] = no_git
    result = module["scan"](tmp_path, lab_config.CONFIG_PATH, files=[target])
    assert result["passed"] is True and result["scope"] == "explicit_export_files"
    with pytest.raises(ValueError, match="empty_scan_scope"):
        module["scan"](tmp_path, files=[])


def test_explicit_export_refuses_duplicates_and_outside_paths(tmp_path: Path) -> None:
    module, target = scanner(tmp_path)
    target.write_text("safe\n")
    for files in ([target, target], [tmp_path.parent / "outside"]):
        with pytest.raises(ValueError, match="invalid_scan_scope"):
            module["scan"](tmp_path, files=files)
