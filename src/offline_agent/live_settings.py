"""Shared explicit launch flags, bounded cost forecasts, and staged-asset identity checks."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from offline_agent.contracts import Json, object_json, read_json
from offline_agent.native_assets import MODEL, REVISION


class LaunchRefused(ValueError):
    pass


@dataclass(frozen=True)
class LaunchBudget:
    allow_remote: bool = False
    allow_model_load: bool = False
    spend_cap_usd: Decimal = Decimal("0")
    hourly_usd: Decimal = Decimal("0")
    max_seconds: int = 600

    def forecast(self) -> Decimal:
        if not self.hourly_usd.is_finite() or self.hourly_usd < 0:
            raise LaunchRefused("invalid_hourly_price_bound")
        if isinstance(self.max_seconds, bool) or not 1 <= self.max_seconds <= 3600:
            raise LaunchRefused("invalid_remote_time_limit")
        return self.hourly_usd * Decimal(self.max_seconds) / Decimal(3600)

    def require(self) -> None:
        if not self.allow_remote or not self.allow_model_load:
            raise LaunchRefused("explicit_remote_and_model_load_flags_required")
        if (
            not self.spend_cap_usd.is_finite()
            or self.spend_cap_usd <= 0
            or not self.hourly_usd.is_finite()
            or self.hourly_usd <= 0
        ):
            raise LaunchRefused("positive_spend_cap_and_hourly_bound_required")
        if self.forecast() > self.spend_cap_usd:
            raise LaunchRefused("remote_cost_bound_exceeds_cap")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def is_new_run_output(output: Path, runs_root: Path) -> bool:
    """Check containment against the canonical root of an existing asset mount."""
    return (
        runs_root.parent.is_dir()
        and output.resolve().is_relative_to(runs_root.resolve())
        and not output.exists()
    )


def staged_model(directory: Path, expected_revision: str = REVISION) -> dict[str, Json]:
    """No download fallback: an operator stages the exact same approved model in advance."""
    receipt = directory / "asset-manifest.json"
    if not receipt.is_file() or receipt.is_symlink():
        raise LaunchRefused("staged_model_identity_receipt_missing")
    row = object_json(read_json(receipt.read_text()))
    if row.get("model") != MODEL or row.get("revision") != expected_revision:
        raise LaunchRefused("staged_model_identity_mismatch")
    assets = object_json(row.get("assets"))
    if not assets:
        raise LaunchRefused("empty_staged_asset_manifest")
    for name, digest in assets.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not isinstance(digest, str):
            raise LaunchRefused("invalid_staged_asset_path")
        path = directory / relative
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise LaunchRefused("staged_asset_symlink_refused")
        if not path.is_file() or file_digest(path) != digest:
            raise LaunchRefused("staged_asset_hash_mismatch")
    return row
