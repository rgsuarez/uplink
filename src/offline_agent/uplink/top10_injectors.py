"""Ten frozen primitive plans; restore actions never receive model-authored argv."""

from __future__ import annotations

if __package__:
    from . import top10_contract as c
    from .top10_kernel import Kernel
else:
    import top10_contract as c  # type: ignore[no-redef,import-not-found]
    from top10_kernel import Kernel  # type: ignore[no-redef,import-not-found]

INJECT = {
    "F01": (("native", "radio_off"),),
    "F02": (("ifconfig", "down"),),
    "F03": (("ipconfig", "linklocal_manual"),),
    "F04": (("native", "bad_dns"),),
    "F05": (("native", "wrong_gateway"),),
    "F06": (("native", "stale_proxy"),),
    "F07": (("native", "portal_proxy"),),
    "F08": (("native", "forget_current_profile"),),
    "F09": (("ifconfig", "mtu_1280"),),
    "F10": (("native", "stale_ipv4"),),
}
REPAIR = {
    "restore_radio": (("native", "restore_radio"),),
    "restore_interface": (("ifconfig", "snapshot_up"),),
    "restore_ipv4": (("native", "restore_ipv4"), ("ipconfig", "dhcp")),
    "renew_lease": (("native", "restore_ipv4"), ("ipconfig", "dhcp")),
    "restore_dns": (("native", "restore_dns"),),
    "restore_gateway": (("native", "restore_ipv4"), ("ipconfig", "dhcp")),
    "restore_proxy": (("native", "restore_proxy"),),
    "restore_saved_network": (("native", "restore_saved_network"),),
    "restore_mtu": (("ifconfig", "snapshot_mtu"),),
}


def inject(kernel: Kernel, fault_id: str, snapshot: dict[str, object]) -> None:
    if fault_id not in INJECT:
        raise c.Refused("top10_unknown_fault")
    for kind, operation in INJECT[fault_id]:
        kernel.primitive(kind, operation, snapshot)


def repair(kernel: Kernel, action: str, snapshot: dict[str, object]) -> None:
    if action not in REPAIR:
        raise c.Refused("top10_snapshot_action_required")
    for kind, operation in REPAIR[action]:
        kernel.primitive(kind, operation, snapshot)


def restore(kernel: Kernel, snapshot: dict[str, object]) -> dict[str, bool]:
    """Restore only observed drift. An independent measurement, not this call, grades it."""
    facts = kernel.facts(snapshot)
    if not facts["protected_services_unchanged"]:
        raise c.Refused("top10_protected_configuration_changed")
    if not facts["radio_on"]:
        repair(kernel, "restore_radio", snapshot)
    if not facts["interface_up"]:
        repair(kernel, "restore_interface", snapshot)
    if not facts["mtu_matches_snapshot"]:
        repair(kernel, "restore_mtu", snapshot)
    if not facts["dns_matches_snapshot"]:
        repair(kernel, "restore_dns", snapshot)
    if not facts["proxy_matches_snapshot"]:
        repair(kernel, "restore_proxy", snapshot)
    if not facts["saved_network_matches_snapshot"] or not facts["associated_with_snapshot"]:
        repair(kernel, "restore_saved_network", snapshot)
    if (
        not facts["ipv4_usable"]
        or not facts["dhcp_lease_present"]
        or not facts["gateway_matches_snapshot"]
    ):
        repair(kernel, "restore_ipv4", snapshot)
    kernel.primitive("native", "verify_profiles", snapshot)
    return kernel.facts(snapshot)
