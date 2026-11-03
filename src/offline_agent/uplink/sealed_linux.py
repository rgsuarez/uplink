"""Closed Linux-only fixture effects over lab_worker ownership and rollback.

Expectations live in separate evaluator descriptors. These functions never emit a
model answer or infer a successful probe from the selected fixture's identity.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import hashlib
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

if __package__:
    from . import lab_worker as worker
    from .lab_protocol import (
        ALTERNATE_GW,
        DEAD_DNS,
        DNS_NAMES,
        LAB_VERSION,
        PRIMARY_GW,
        PRIMARY_IP,
        PROXY_URL,
        SERVER_IP,
        LabSpec,
    )
    from .sealed_registry import load
else:
    import lab_worker as worker  # type: ignore[no-redef,import-not-found]
    from lab_protocol import (  # type: ignore[no-redef,import-not-found]
        ALTERNATE_GW,
        DEAD_DNS,
        DNS_NAMES,
        LAB_VERSION,
        PRIMARY_GW,
        PRIMARY_IP,
        PROXY_URL,
        SERVER_IP,
        LabSpec,
    )
    from sealed_registry import load  # type: ignore[no-redef,import-not-found]

TABLE = "uplink_sealed"
DEAD_PROXY = f"http://{SERVER_IP}:9"
DEAD_ROUTER = _public_fixture("@PUBLIC_IPV4_084@")
V6_RESOLVER = _public_fixture("@PUBLIC_IPV6_102@")
V6_CLIENT = _public_fixture("@PUBLIC_IPV6_099@/64")
BAD_MAC = _public_fixture("@PUBLIC_MAC_114@")


def descriptor(state: dict[str, object]) -> dict[str, object]:
    return load(state.get("sealed_selection"))


def baseline(state: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], descriptor(state)["baseline"])


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def nft(spec: LabSpec, *argv: str, internet: bool = False) -> None:
    worker.run(spec.namespaced("nft", *argv, internet=internet))


def has_table(spec: LabSpec, *, internet: bool) -> bool:
    raw = json.loads(
        worker.run(spec.namespaced("nft", "-j", "list", "tables", internet=internet)).stdout
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("nftables"), list):
        raise ValueError("sealed_nft_readback_invalid")
    return any(
        isinstance(row, dict)
        and isinstance(row.get("table"), dict)
        and row["table"].get("family") == "inet"
        and row["table"].get("name") == TABLE
        for row in raw["nftables"]
    )


def firewall(spec: LabSpec, hook: str, *rule: str, internet: bool = False) -> None:
    if not has_table(spec, internet=internet):
        nft(spec, "add", "table", "inet", TABLE, internet=internet)
    nft(
        spec,
        "add",
        "chain",
        "inet",
        TABLE,
        hook,
        "{",
        "type",
        "filter",
        "hook",
        hook,
        "priority",
        "0",
        ";",
        "policy",
        "accept",
        ";",
        "}",
        internet=internet,
    )
    nft(spec, "add", "rule", "inet", TABLE, hook, *rule, internet=internet)


def signal_dns(spec: LabSpec) -> None:
    rows = [json.loads(line) for line in (spec.state / "processes.jsonl").read_text().splitlines()]
    rows = [r for r in rows if isinstance(r, dict) and r.get("name") == "dns"]
    if len(rows) != 1:
        raise ValueError("sealed_dns_owner_required")
    row = rows[0]
    if type(row.get("pid")) is not int or not isinstance(row.get("start_ticks"), str):
        raise ValueError("sealed_dns_owner_invalid")
    pid = row["pid"]
    if Path(f"/proc/{pid}/stat").read_text().split()[21] != row["start_ticks"]:
        raise ValueError("sealed_dns_pid_reused")
    os.kill(pid, signal.SIGHUP)


def lease_hook(spec: LabSpec) -> str:
    c0 = spec.interfaces[0]
    return f'''#!/bin/sh
set -eu
[ "$interface" = "{c0}" ] || exit 2
case "$1" in
  bound|renew)
    [ "$ip" = "{PRIMARY_IP}" ] || exit 2
    case "$router" in {PRIMARY_GW}|{DEAD_ROUTER}) ;; *) exit 2 ;; esac
    ip address replace {PRIMARY_IP}/24 dev {c0}
    ip route replace default via "$router" dev {c0}
    ;;
  deconfig) : ;;
  *) exit 2 ;;
esac
'''


def prepare_pki(spec: LabSpec, state: dict[str, object]) -> None:
    if descriptor(state)["code"] != "X02":
        return
    # A large *valid* leaf makes the TLS flight exceed 1400 bytes. The baseline
    # must pass TLS before the size-selective network fault is injected.
    pki = spec.state / "pki"
    names = [*DNS_NAMES, *(f"padding-{i:02d}.uplink.test" for i in range(64))]
    (pki / "ext.cnf").write_text(
        "subjectAltName=" + ",".join("DNS:" + n for n in names) + "\nextendedKeyUsage=serverAuth\n"
    )
    worker.run(
        (
            "openssl",
            "x509",
            "-req",
            "-in",
            str(pki / "server.csr"),
            "-CA",
            str(pki / "ca.pem"),
            "-CAkey",
            str(pki / "ca.key"),
            "-CAserial",
            str(pki / "ca.srl"),
            "-days",
            "2",
            "-extfile",
            str(pki / "ext.cnf"),
            "-out",
            str(pki / "server.pem"),
        ),
        timeout=20,
    )


def configure(spec: LabSpec, state: dict[str, object]) -> None:
    b = baseline(state)
    c0, _, c1, i1 = spec.interfaces
    # Namespace-local baseline, captured before injection. No host sysctl changes.
    for name in ("all", "default", "lo", c0, c1):
        worker.run(spec.namespaced("sysctl", "-q", "-w", f"net.ipv6.conf.{name}.disable_ipv6=1"))
    if b["ipv6"] == "static_primary":
        worker.run(("ip", "-n", spec.client, "link", "set", "dev", c0, "addrgenmode", "none"))
        worker.run(spec.namespaced("sysctl", "-q", "-w", f"net.ipv6.conf.{c0}.disable_ipv6=0"))
        worker.run(("ip", "-n", spec.client, "-6", "address", "add", V6_CLIENT, "dev", c0, "nodad"))
    if b["alternate"] is False:
        worker.run(("ip", "-n", spec.client, "link", "set", c1, "down"))
        worker.run(("ip", "-n", spec.internet, "link", "set", i1, "down"))
    state.update(
        proxy=PROXY_URL if b["https_proxy"] == "configured" else "",
        http_proxy=PROXY_URL if b["http_proxy"] == "configured" else "",
        process_proxy_override=None,
        sealed_injection_started=False,
        sealed_restored=False,
    )
    saved = capture(spec, state)
    if any(cast(list[bool], saved["owned_firewalls"])) or saved["permanent_neighbors"]:
        raise ValueError("sealed_baseline_resources_not_empty")
    worker.atomic_json(spec.state / "sealed-snapshot.json", saved)
    state["sealed_snapshot_sha256"] = digest(saved)
    worker.audit(spec, "complete", "sealed_snapshot_persisted")


def capture(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    try:
        return capture_fields(spec, state)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("sealed_configuration_readback_invalid") from exc


def capture_fields(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    networks: dict[str, object] = {}
    for ns in (spec.client, spec.internet):
        links = json.loads(worker.run(("ip", "-j", "-n", ns, "address")).stdout)
        addresses = []
        for link in links:
            addresses.append(
                {
                    "ifname": link["ifname"],
                    "up": "UP" in link["flags"],
                    "mtu": link["mtu"],
                    "addresses": sorted(
                        [
                            {k: a[k] for k in ("family", "local", "prefixlen", "scope") if k in a}
                            for a in link.get("addr_info", [])
                        ],
                        key=lambda a: json.dumps(a, sort_keys=True),
                    ),
                }
            )
        routes = []
        for family in ("-4", "-6"):
            data = json.loads(
                worker.run(("ip", "-j", "-n", ns, family, "route", "show", "table", "all")).stdout
            )
            for row in data:
                row.pop("expires", None)
            routes.append(sorted(data, key=lambda row: json.dumps(row, sort_keys=True)))
        networks[ns] = {
            "links": sorted(addresses, key=lambda a: str(a["ifname"])),
            "routes": routes,
        }
    neighbors = json.loads(
        worker.run(("ip", "-j", "-n", spec.client, "neigh", "show", "nud", "permanent")).stdout
    )
    return {
        "network": networks,
        "resolver": (spec.resolver_directory / "resolv.conf").read_text(),
        "dhcp_options": (spec.state / "dhcp-options").read_text(),
        "proxy": state["proxy"],
        "http_proxy": state.get("http_proxy"),
        "process_proxy_override": state.get("process_proxy_override"),
        "stub": state["stub"],
        "ipv6_disabled": {
            name: worker.run(
                spec.namespaced("sysctl", "-n", f"net.ipv6.conf.{name}.disable_ipv6")
            ).stdout.strip()
            for name in ("all", "default", "lo", spec.interfaces[0], spec.interfaces[2])
        },
        "permanent_neighbors": neighbors,
        "owned_firewalls": [has_table(spec, internet=False), has_table(spec, internet=True)],
    }


def snapshot(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    path = spec.state / "sealed-snapshot.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
        raise ValueError("sealed_snapshot_required")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or digest(value) != state.get("sealed_snapshot_sha256"):
        raise ValueError("sealed_snapshot_changed")
    return cast(dict[str, object], value)


def inject(spec: LabSpec, state: dict[str, object]) -> None:
    if state.get("sealed_injection_started") is not False:
        raise ValueError("sealed_injection_never_retried")
    saved = snapshot(spec, state)
    if capture(spec, state) != saved:
        raise ValueError("sealed_before_state_mismatch")
    code = str(descriptor(state)["code"])
    state["sealed_injection_started"] = True
    state["sealed_restored"] = False
    worker.atomic_json(spec.state / "state.json", state)
    c0, i0, _, _ = spec.interfaces
    if code in {"X02", "X04"}:
        worker.write_resolver(spec, DEAD_DNS)
    if code == "P04":
        state["proxy"] = DEAD_PROXY
    elif code == "D09":
        (spec.resolver_directory / "resolv.conf").write_text(
            f"nameserver {V6_RESOLVER}\noptions timeout:1 attempts:1\n"
        )
    elif code == "X02":
        firewall(
            spec,
            "output",
            "oifname",
            i0,
            "ip",
            "length",
            ">",
            "1400",
            "tcp",
            "sport",
            "{",
            "443",
            ",",
            "8888",
            "}",
            "counter",
            "drop",
            internet=True,
        )
    elif code == "G02":
        worker.run(
            (
                "ip",
                "-n",
                spec.client,
                "neigh",
                "replace",
                PRIMARY_GW,
                "lladdr",
                BAD_MAC,
                "nud",
                "permanent",
                "dev",
                c0,
            )
        )
    elif code == "I04":
        (spec.state / "dhcp-options").write_text(f"3,{DEAD_ROUTER}\n")
        signal_dns(spec)
        worker.renew(spec)
    elif code == "I02":
        firewall(
            spec, "input", "iifname", i0, "udp", "dport", "67", "counter", "drop", internet=True
        )
        worker.run(("ip", "-n", spec.client, "address", "del", PRIMARY_IP + "/24", "dev", c0))
    elif code == "X03":
        state.update(proxy=DEAD_PROXY, http_proxy=DEAD_PROXY)
        firewall(spec, "input", "tcp", "dport", "443", "counter", "drop", internet=True)
        firewall(spec, "output", "tcp", "dport", "443", "counter", "drop", internet=True)
    elif code == "X04":
        state["stub"] = "captive"
    elif code == "C04":
        state["stub"] = "walled_second"
    elif code == "P07":
        state["process_proxy_override"] = DEAD_PROXY
    elif code == "F05":
        firewall(
            spec,
            "output",
            "ip",
            "daddr",
            ALTERNATE_GW,
            "meta",
            "l4proto",
            "{",
            "tcp",
            ",",
            "udp",
            "}",
            "th",
            "dport",
            "53",
            "counter",
            "drop",
        )
    elif code == "V03":
        worker.run(
            (
                "ip",
                "-n",
                spec.client,
                "-6",
                "route",
                "add",
                "default",
                "via",
                _public_fixture("@PUBLIC_IPV6_112@"),
                "dev",
                c0,
                "onlink",
            )
        )
    state["status"] = "faulted"


def before_restore(spec: LabSpec, state: dict[str, object]) -> None:
    saved = snapshot(spec, state)
    code = str(descriptor(state)["code"])
    for internet in (False, True):
        if has_table(spec, internet=internet):
            nft(spec, "delete", "table", "inet", TABLE, internet=internet)
    if code == "G02":
        neighbors = json.loads(
            worker.run(("ip", "-j", "-n", spec.client, "neigh", "show", "nud", "permanent")).stdout
        )
        if neighbors:
            if (
                len(neighbors) != 1
                or neighbors[0].get("dst") != PRIMARY_GW
                or neighbors[0].get("lladdr") != BAD_MAC
            ):
                raise ValueError("sealed_neighbor_ownership_changed")
            worker.run(
                ("ip", "-n", spec.client, "neigh", "del", PRIMARY_GW, "dev", spec.interfaces[0])
            )
    if code == "I04":
        (spec.state / "dhcp-options").write_text(str(saved["dhcp_options"]))
        signal_dns(spec)
    if code == "V03":
        routes = json.loads(
            worker.run(("ip", "-j", "-n", spec.client, "-6", "route", "show", "default")).stdout
        )
        if routes:
            if (
                len(routes) != 1
                or routes[0].get("gateway") != _public_fixture("@PUBLIC_IPV6_112@")
                or routes[0].get("dev") != spec.interfaces[0]
            ):
                raise ValueError("sealed_ipv6_route_ownership_changed")
            worker.run(
                (
                    "ip",
                    "-n",
                    spec.client,
                    "-6",
                    "route",
                    "del",
                    "default",
                    "via",
                    _public_fixture("@PUBLIC_IPV6_112@"),
                    "dev",
                    spec.interfaces[0],
                )
            )


def after_restore(spec: LabSpec, state: dict[str, object]) -> None:
    saved = snapshot(spec, state)
    (spec.resolver_directory / "resolv.conf").write_text(str(saved["resolver"]))
    for key in ("proxy", "http_proxy", "process_proxy_override", "stub"):
        state[key] = saved[key]
    networks = cast(dict[str, dict[str, object]], saved["network"])
    for ns, network in networks.items():
        for link in cast(list[dict[str, object]], network["links"]):
            if link["ifname"] in spec.interfaces:
                worker.run(
                    (
                        "ip",
                        "-n",
                        ns,
                        "link",
                        "set",
                        str(link["ifname"]),
                        "up" if link["up"] else "down",
                    )
                )
    current = capture(spec, state)
    fields = {key: current.get(key) == value for key, value in saved.items()}
    result: dict[str, object] = {
        "match": all(fields.values()),
        "fields": fields,
        "before_sha256": digest(saved),
        "after_sha256": digest(current),
    }
    worker.atomic_json(spec.state / "sealed-revert.json", result)
    if not result["match"]:
        raise ValueError("sealed_exact_revert_mismatch")
    state["sealed_restored"] = True


def apply_snapshot(spec: LabSpec, state: dict[str, object], tool: str, policy: str) -> bool:
    if descriptor(state)["role"] == "control" and state.get("sealed_injection_started") is True:
        raise ValueError("sealed_control_mutation_refused")
    if tool == "reroute" and baseline(state)["alternate"] is not True:
        raise ValueError("sealed_no_alternate")
    if tool not in {"set_dns", "restore_proxy"}:
        return False
    if policy != "snapshot":
        raise ValueError("sealed_snapshot_policy_required")
    saved = snapshot(spec, state)
    if tool == "set_dns":
        (spec.resolver_directory / "resolv.conf").write_text(str(saved["resolver"]))
    else:
        state["proxy"], state["http_proxy"] = saved["proxy"], saved["http_proxy"]
    # The process environment is deliberately outside the system-proxy repair.
    return True


def effective_proxy(state: dict[str, object], *, portal: bool) -> str:
    override = state.get("process_proxy_override")
    value = (
        override if override is not None else state.get("http_proxy") if portal else state["proxy"]
    )
    if value not in ("", PROXY_URL, DEAD_PROXY):
        raise ValueError("sealed_proxy_not_allowlisted")
    return cast(str, value)


def signature(rows: list[dict[str, object]]) -> dict[str, object]:
    if len(rows) != 7:
        raise ValueError("sealed_complete_oracle_required")
    checks = [row.get("ok") is True for row in rows[:2]]
    checks += [
        row.get("ok") is True
        and row.get("status") == 204
        and row.get("body_matches") is True
        and (index in (2, 3) or row.get("tls_verified") is True)
        for index, row in enumerate(rows[2:])
    ]
    flags = "".join("T" if ok else "F" for ok in checks[:6])
    captive = any(row.get("status") in (200, 301, 302, 303, 307, 308) for row in rows[4:6])
    verdict = (
        "CAPTIVE"
        if captive
        else "ONLINE"
        if flags == "TTTTTT"
        else "OFFLINE"
        if flags == "FFFFFF"
        else "DEGRADED"
    )
    return {"flags": flags, "verdict": verdict, "app": checks[6], "checks": rows}


def measure(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    # Same six independent network checks and separate application handshake as
    # the existing oracle. No expected flag, catalog label or model is consulted.
    started = time.time()
    with ThreadPoolExecutor(max_workers=7) as pool:
        dns = [pool.submit(worker.resolve, spec, i) for i in range(2)]
        web = [pool.submit(worker.fetch, spec, state, i) for i in range(5)]
        rows = [f.result() for f in [*dns, *web]]
    result = signature(rows)
    try:
        alternate_dns = worker.diagnostic_dns(spec, 0, ALTERNATE_GW, "alternate")
    except worker.LabCommandFailed as exc:
        if exc.details.get("timeout_seconds") != 1:
            raise
        alternate_dns = {"ok": False, "reason": "probe_timeout"}
    try:
        tcp = worker.run(
            spec.namespaced(
                sys.executable,
                "-I",
                "-c",
                "import socket,sys;"
                "s=socket.create_connection((sys.argv[1],443),0.6,(sys.argv[2],0));s.close()",
                SERVER_IP,
                PRIMARY_IP,
            ),
            timeout=1,
            check=False,
        )
        tcp_ok = tcp.returncode == 0
    except worker.LabCommandFailed as exc:
        if exc.details.get("timeout_seconds") != 1:
            raise
        tcp_ok = False
    dropped = 0
    for internet in (False, True):
        if has_table(spec, internet=internet):
            table = json.loads(
                worker.run(
                    spec.namespaced("nft", "-j", "list", "table", "inet", TABLE, internet=internet)
                ).stdout
            )
            if not isinstance(table, dict) or not isinstance(table.get("nftables"), list):
                raise ValueError("sealed_filter_readback_invalid")
            for item in table["nftables"]:
                if not isinstance(item, dict):
                    raise ValueError("sealed_filter_readback_invalid")
                rule = item.get("rule", {})
                if not isinstance(rule, dict) or not isinstance(rule.get("expr", []), list):
                    raise ValueError("sealed_filter_readback_invalid")
                for expression in rule.get("expr", []):
                    if not isinstance(expression, dict):
                        raise ValueError("sealed_filter_readback_invalid")
                    counter = expression.get("counter")
                    if isinstance(counter, dict) and type(counter.get("packets")) is int:
                        dropped += counter["packets"]
    return {
        **result,
        "profile": LAB_VERSION,
        "checked_at": started,
        "configuration": capture(spec, state),
        "alternate_dns_ok": alternate_dns["ok"],
        "alternate_dns": alternate_dns,
        "primary_tcp_443_ok": tcp_ok,
        "filtered_packets": dropped,
    }
