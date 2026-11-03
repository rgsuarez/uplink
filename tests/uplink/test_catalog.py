import json
from pathlib import Path

from offline_agent.contracts import object_json
from offline_agent.uplink.contracts import Scope, parse_action


def test_catalog_pins_all_required_fault_families_and_contract_actions() -> None:
    root = Path(__file__).resolve().parents[2]
    value = object_json(json.loads((root / "docs/UPLINK-FAULTS.json").read_text()))
    faults = value["faults"]
    assert isinstance(faults, list)
    rows = [object_json(x) for x in faults]
    assert {f["id"] for f in rows} == {
        "wrong_dns",
        "stale_proxy",
        "dns_plus_proxy",
        "lease_loss",
        "bad_default_route",
        "interface_down",
        "captive_portal",
        "isp_down_saved_alternate",
        "healthy",
        "site_only_outage",
        "mtu_black_hole",
        "misleading_status",
    }
    scope = Scope(
        ("service-synthetic",),
        ("candidate-synthetic",),
        ("snapshot-synthetic",),
        ("evidence-synthetic",),
    )
    for row in rows:
        actions = row["actions"]
        assert isinstance(actions, list)
        for action in actions:
            parse_action(json.dumps(action), scope)
        assert row["mac_injection"] and row["linux_injection"] and row["undo"]
        if row["id"] in {"healthy", "misleading_status"}:
            assert actions == [] and row["expected_oracle"] == "ONLINE"
    assert {r["id"] for r in rows if r["wed_gate"]} == {
        "wrong_dns",
        "stale_proxy",
        "dns_plus_proxy",
    }
