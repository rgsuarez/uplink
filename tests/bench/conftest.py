"""All reduced-package tests run behind the existing OS no-network fence."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from offline_agent.uplink import bench_contract as b
from offline_agent.uplink import top10_contract as c
from offline_agent.uplink.bench_package import registration

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fenced() -> None:
    if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        pytest.skip("run through scripts/top10_local_sandbox.py")


@pytest.fixture
def manifest() -> dict[str, object]:
    row = c.read(ROOT / b.MANIFEST_PATH)
    row["oracles"] = b.ORACLES
    return b.validate_registration(row)


def bundle(manifest: dict[str, object]) -> tuple[float, dict[str, object], str, str]:
    now = time.time()

    def stamp(value: float) -> str:
        return datetime.fromtimestamp(value, UTC).isoformat()

    binds = b.binding(manifest, "a" * 64, "b" * 64)
    shared = {
        "start": stamp(now - 10),
        "end": stamp(now + 1790),
        "approval_nonce": "e" * 32,
        "limits": b.LIMITS,
        "privileged_write_set": b.WRITE_SET,
    }
    owner = json.dumps(
        binds
        | {
            "kind": "BENCH-REDUCED-OWNER-v1",
            "approved_by": "Kaylin",
            "released": True,
            "released_at": stamp(now - 60),
            "server_pid": 23456,
            "server_birth": "Sun Nov  1 12:00:00 2026",
        }
    )
    window = (
        binds
        | shared
        | {
            "kind": "KAYLIN-OK-BENCH-REDUCED-v1",
            "authorized": True,
            "approved_by": "Kaylin",
            "arm": "BASE",
            "server_pid": 23456,
            "server_birth": "Sun Nov  1 12:00:00 2026",
            "server_context": 4096,
            "latest_start": stamp(now + 140),
            "owner_handback_sha256": hashlib.sha256(owner.encode()).hexdigest(),
            "richard_approval_sha256": None,
            "private_config_sha256": "c" * 64,
            "oob_interface": c.OOB_INTERFACE,
            "oob_server_ipv4": c.OOB_SERVER_IPV4,
            "oob_client_ipv4": _public_fixture("@PUBLIC_IPV4_067@"),
            "manual_recovery_confirmed": True,
        }
    )
    richard = json.dumps(
        binds
        | shared
        | {
            "kind": "RICHARD-OK-BENCH-REDUCED-v1",
            "approved_by": "Richard",
            "authorized": True,
            "window_binding_sha256": c.digest(
                {k: v for k, v in window.items() if k != "richard_approval_sha256"}
            ),
        }
    )
    window["richard_approval_sha256"] = hashlib.sha256(richard.encode()).hexdigest()
    return now, window, owner, richard


@pytest.fixture
def authority_bundle(manifest: dict[str, object]) -> tuple[float, dict[str, object], str, str]:
    return bundle(manifest)
