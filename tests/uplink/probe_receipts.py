"""Synthetic returned probe bytes, independent of the receipt validators."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

from typing import cast

from offline_agent.contracts import Json


def returned_probe() -> dict[str, Json]:
    program: dict[str, Json] = {
        "version": 1,
        "probes": [{"op": "read_state", "timeout_ms": 1000}],
    }
    return {
        "version": 1,
        "profile": "lab_wifi",
        "status": "completed",
        "uid": 501,
        "requested": program,
        "validated": program,
        "started_at": 1.0,
        "finished_at": 1.001,
        "elapsed_ms": 1.0,
        "rows": [
            {
                "index": 0,
                "probe": {"op": "read_state", "timeout_ms": 1000},
                "status": "completed",
                "ok": True,
                "started_at": 1.0,
                "finished_at": 1.001,
                "elapsed_ms": 1.0,
                "state": {
                    "dns_configured": True,
                    "proxy_configured": False,
                    "wifi_default_route": True,
                    "lease_present": True,
                },
                "stdout": "synthetic observed state",
            }
        ],
        "budget": {"attempted": 1, "reserved_ms": 1000, "elapsed_ms": 1.0},
        "settings_unchanged": True,
        "wifi_route_unchanged": True,
        "before_sha256": "a" * 64,
        "after_sha256": "a" * 64,
    }


def returned_measurement(probe: dict[str, Json] | None = None) -> dict[str, Json]:
    """A completed network primitive, distinct from the preflight state echo."""
    probe = probe or {
        "op": "dns_query",
        "name": "one",
        "resolver": "configured",
        "timeout_ms": 1000,
    }
    execution = returned_probe()
    program: dict[str, Json] = {"version": 1, "probes": [probe]}
    execution.update(requested=program, validated=program)
    row = cast(list[dict[str, Json]], execution["rows"])[0]
    row.pop("state")
    row.update(probe=probe, stdout="synthetic network measurement", answers=[_public_fixture("@PUBLIC_IPV4_066@")])
    return execution
