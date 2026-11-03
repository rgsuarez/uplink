"""The operator-approved lab exception and fleet gates. Standard library only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

if __package__:
    from .lab_config import load_config
else:
    from lab_config import load_config  # type: ignore[import-not-found,no-redef]


def approved_serial() -> str:
    return load_config().approved_serial


APPROVED_FAULTS = ("wrong_dns", "stale_proxy", "dns_and_proxy")
TRIAL_INTERVAL_SECONDS = 120
FLEET_GETTERS = (
    "-getdnsservers",
    "-getsearchdomains",
    "-getwebproxy",
    "-getsecurewebproxy",
    "-getautoproxyurl",
    "-getproxybypassdomains",
    "-getsocksfirewallproxy",
    "-getproxyautodiscovery",
)


def approval() -> dict[str, object]:
    config = load_config()
    return {
        "serial": config.approved_serial,
        "allowed_faults": list(APPROVED_FAULTS),
        "fault_families": ["dns", "proxy"],
        "scope": "primary_wifi_dns_and_web_secure_proxy_only",
        "approved_by": config.approved_by,
        "approved_at": config.approved_at,
        "fleet_clearance_at": config.fleet_clearance_at,
        "minimum_trial_interval_seconds": TRIAL_INTERVAL_SECONDS,
    }


def validate_authorization(value: object) -> dict[str, object]:
    expected_approval = approval()
    if not isinstance(value, dict) or set(value) != {*expected_approval, "clearance_sha256"}:
        raise ValueError("managed_device_refused")
    if any(value.get(key) != expected for key, expected in expected_approval.items()):
        raise ValueError("managed_device_refused")
    digest = value.get("clearance_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("fleet_clearance_digest_required")
    return {**expected_approval, "clearance_sha256": digest}


def require_exception(serial: object, fault: object, authorization: object) -> dict[str, object]:
    if serial != approved_serial() or fault not in APPROVED_FAULTS:
        raise ValueError("managed_device_refused")
    return validate_authorization(authorization)


def load_clearance(root: Path) -> dict[str, object]:
    config = load_config()
    path = root / config.clearance_file
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 16384:
                raise ValueError("fleet_clearance_file_refused")
            content = stream.read(16385)
            if len(content) > 16384:
                raise ValueError("fleet_clearance_file_refused")
    except OSError as exc:
        raise ValueError("fleet_clearance_required_before_fault") from exc
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("fleet_clearance_file_refused") from exc
    if not all(
        marker in text for marker in ("Fleet OK", config.fleet_clearance_at[:10], "primary Wi-Fi")
    ):
        raise ValueError("fleet_clearance_scope_unrecognized")
    return validate_authorization(
        {**approval(), "clearance_sha256": hashlib.sha256(content).hexdigest()}
    )


def settings_digest(settings: dict[str, str]) -> str:
    if set(settings) != set(FLEET_GETTERS) or any(
        not isinstance(v, str) for v in settings.values()
    ):
        raise ValueError("complete_fleet_settings_required")
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
