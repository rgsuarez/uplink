"""Development-only analogues in run-owned namespaces, outside frozen fault catalogs."""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
from typing import TYPE_CHECKING

if __package__:
    from .lab_protocol import SERVER_IP, LabSpec
else:
    from lab_protocol import SERVER_IP, LabSpec  # type: ignore[no-redef,import-not-found]

if TYPE_CHECKING:
    from types import ModuleType

FAULTS = ("test_host_blackhole", "wifi_ipv6_off", "owned_pf_outbound")
TOOLS = ("restore_test_host_route", "restore_wifi_ipv6", "restore_owned_pf_anchor")
V6_CLIENT = _public_fixture("@PUBLIC_IPV6_101@")
V6_SERVER = _public_fixture("@PUBLIC_IPV6_100@")


def table(spec: LabSpec) -> str:
    return "uplink_v1_" + spec.run_id


def filter_exists(spec: LabSpec, worker: ModuleType) -> bool:
    result = worker.run(spec.namespaced("nft", "list", "table", "inet", table(spec)), check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1 and "No such file or directory" in result.stderr:
        return False
    raise ValueError("fault_filter_state_unavailable")


def capture(spec: LabSpec, worker: ModuleType) -> dict[str, object]:
    c0 = spec.interfaces[0]

    def values(*args: str) -> object:
        rows = json.loads(worker.run(("ip", "-j", "-n", spec.client, *args)).stdout)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("fault_kernel_snapshot_unparsed")
        # Kernel lifetime counters are observations, not restorable configuration.
        for row in rows:
            row.pop("expires", None)
            addresses = row.get("addr_info", [])
            if not isinstance(addresses, list) or any(not isinstance(a, dict) for a in addresses):
                raise ValueError("fault_address_snapshot_unparsed")
            for address in addresses:
                address.pop("valid_life_time", None)
                address.pop("preferred_life_time", None)
        return rows

    return {
        "ipv4_routes": values("route"),
        "ipv6_routes": values("-6", "route"),
        "addresses": values("address"),
        "ipv6_disabled": worker.run(
            spec.namespaced("sysctl", "-n", f"net.ipv6.conf.{c0}.disable_ipv6")
        ).stdout,
        "filter": filter_exists(spec, worker),
        "resolver": (spec.resolver_directory / "resolv.conf").read_text(),
        "proxy": worker.load(spec)["proxy"],
    }


def configure(spec: LabSpec, state: dict[str, object], worker: ModuleType) -> None:
    fault = state.get("fault_package")
    if fault not in FAULTS:
        raise ValueError("unknown_fault_package")
    c0, i0, _, _ = spec.interfaces
    if filter_exists(spec, worker):
        raise ValueError("fault_filter_collision")
    if fault == "wifi_ipv6_off":
        for ns, iface, addr in ((spec.client, c0, V6_CLIENT), (spec.internet, i0, V6_SERVER)):
            # Static-only fixture: no regenerated link-local address or DAD race.
            worker.run(
                (
                    "ip",
                    "netns",
                    "exec",
                    ns,
                    "sysctl",
                    "-q",
                    "-w",
                    f"net.ipv6.conf.{iface}.disable_ipv6=1",
                )
            )
            worker.run(
                (
                    "ip",
                    "netns",
                    "exec",
                    ns,
                    "sysctl",
                    "-q",
                    "-w",
                    f"net.ipv6.conf.{iface}.addr_gen_mode=1",
                )
            )
            worker.run(
                (
                    "ip",
                    "netns",
                    "exec",
                    ns,
                    "sysctl",
                    "-q",
                    "-w",
                    f"net.ipv6.conf.{iface}.disable_ipv6=0",
                )
            )
            worker.run(
                ("ip", "-n", ns, "-6", "address", "add", addr + "/64", "dev", iface, "nodad")
            )
        worker.spawn(
            spec,
            "v6-http",
            spec.namespaced(
                worker.sys.executable,
                str(worker.Path(__file__).with_name("lab_servers.py")),
                "--state",
                str(spec.state),
                "--port",
                "443",
                "--ipv6",
                internet=True,
            ),
        )
    state["fault_package_snapshot"] = capture(spec, worker)
    state["fault_package_injected"] = False


def inject(spec: LabSpec, state: dict[str, object], worker: ModuleType) -> None:
    fault = state.get("fault_package")
    if state.get("fault_package_injected") is not False:
        raise ValueError("fault_package_one_attempt_only")
    if capture(spec, worker) != state.get("fault_package_snapshot"):
        raise ValueError("fault_package_preinject_drift")
    # Persist intent before a partial command can fail; the watchdog then knows what to undo.
    state["fault_package_injected"] = True
    worker.atomic_json(spec.state / "state.json", state)
    if fault == "test_host_blackhole":
        worker.run(("ip", "-n", spec.client, "route", "add", "blackhole", SERVER_IP + "/32"))
    elif fault == "wifi_ipv6_off":
        worker.run(
            spec.namespaced(
                "sysctl", "-q", "-w", f"net.ipv6.conf.{spec.interfaces[0]}.disable_ipv6=1"
            )
        )
    elif fault == "owned_pf_outbound":
        # nft is the Linux analogue. This never changes the VM's host firewall.
        worker.run(spec.namespaced("nft", "add", "table", "inet", table(spec)))
        worker.run(
            spec.namespaced(
                "nft",
                "add",
                "chain",
                "inet",
                table(spec),
                "output",
                "{ type filter hook output priority -10; policy accept; }",
            )
        )
        worker.run(
            spec.namespaced(
                "nft",
                "add",
                "rule",
                "inet",
                table(spec),
                "output",
                "oifname",
                spec.interfaces[0],
                "ip",
                "daddr",
                SERVER_IP,
                "tcp",
                "dport",
                "{ 80, 443, 8888 }",
                "drop",
            )
        )
    else:
        raise ValueError("unknown_fault_package")
    state["status"] = "faulted"


def restore(spec: LabSpec, state: dict[str, object], worker: ModuleType) -> None:
    fault = state.get("fault_package")
    saved = state.get("fault_package_snapshot")
    if not isinstance(saved, dict):
        raise ValueError("fault_package_snapshot_missing")
    if state.get("fault_package_injected") is True:
        if fault == "test_host_blackhole":
            current = capture(spec, worker)
            if current["ipv4_routes"] != saved["ipv4_routes"]:
                worker.run(
                    ("ip", "-n", spec.client, "route", "del", "blackhole", SERVER_IP + "/32")
                )
        elif fault == "wifi_ipv6_off":
            c0 = spec.interfaces[0]
            worker.run(spec.namespaced("sysctl", "-q", "-w", f"net.ipv6.conf.{c0}.disable_ipv6=0"))
            worker.run(
                (
                    "ip",
                    "-n",
                    spec.client,
                    "-6",
                    "address",
                    "replace",
                    V6_CLIENT + "/64",
                    "dev",
                    c0,
                    "nodad",
                )
            )
        elif fault == "owned_pf_outbound":
            if filter_exists(spec, worker):
                worker.run(spec.namespaced("nft", "delete", "table", "inet", table(spec)))
    current = capture(spec, worker)
    worker.atomic_json(
        spec.state / "fault-package-diff.json",
        {"match": current == saved, "before": saved, "after": current},
    )
    if current != saved:
        raise ValueError("fault_package_exact_restore_mismatch")
    state["status"] = "ready"


def target(spec: LabSpec, state: dict[str, object], worker: ModuleType) -> dict[str, object]:
    v6 = state.get("fault_package") == "wifi_ipv6_off"
    address = "[" + V6_SERVER + "]" if v6 else SERVER_IP
    result = worker.run(
        spec.namespaced(
            "curl",
            "--disable",
            "--silent",
            "--noproxy",
            "*",
            "--ipv6" if v6 else "--ipv4",
            "--interface",
            spec.interfaces[0],
            "--resolve",
            "one.uplink.test:443:" + address,
            "--cacert",
            str(spec.state / "pki/ca.pem"),
            "--max-time",
            "3",
            "--connect-timeout",
            "2",
            "--write-out",
            "\n%{http_code}",
            "https://one.uplink.test/ok",
        ),
        timeout=4,
        check=False,
    )
    return {"ok": result.returncode == 0 and result.stdout == "\n204", "family": 6 if v6 else 4}
