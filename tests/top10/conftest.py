"""Process tests require the OS sandbox; no exception to Uplink's unit-test fence."""

import os

import pytest


@pytest.fixture(autouse=True)
def require_desk_sandbox() -> None:
    if os.environ.get("UPLINK_TOP10_SANDBOX") != "1":
        pytest.skip("run through scripts/top10_local_sandbox.py")
