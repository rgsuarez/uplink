"""Parse saved/fresh survey facts without changing or inventing a target address."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import cast

from offline_agent.uplink.faults_v1 import IPV4_FAULTS, FaultSpec, obj


def read_text(reads: object, name: str) -> str:
    row = obj(obj(reads).get(name))
    if (
        type(row.get("exit_code")) is not int
        or row.get("exit_code") != 0
        or not isinstance(row.get("stdout"), str)
    ):
        raise ValueError("inventory_read_missing_or_failed")
    return str(row["stdout"])


def tailscale_addresses(reads: object) -> tuple[str, ...]:
    addresses: set[str] = set()
    for name in ("tailscale_netcheck", "tailscale_status"):
        text = read_text(reads, name)
        if not text.strip():
            raise ValueError("tailscale_read_empty")
        # Verify structured output before examining endpoint/address tokens.
        if not isinstance(json.loads(text), dict):
            raise ValueError("tailscale_inventory_object_required")
        for token in re.findall(r"[0-9a-fA-F:.]+", text):
            endpoint = re.fullmatch(r"([0-9.]+):[0-9]+", token)
            if endpoint:
                token = endpoint[1]
            try:
                addresses.add(str(ipaddress.ip_address(token)))
            except ValueError:
                continue
    if not addresses or len(addresses) > 1024:
        raise ValueError("tailscale_inventory_empty_or_unbounded")
    return tuple(sorted(addresses))


def protected_networks(raw: object) -> tuple[str, ...]:
    survey = obj(raw)
    reads = survey.get("reads")
    interfaces = read_text(reads, "interfaces")
    networks: set[str] = set()
    for line in interfaces.splitlines():
        fields = line.strip().split()
        if not fields or fields[0] not in {"inet", "inet6"}:
            continue
        try:
            if fields[0] == "inet":
                address = ipaddress.IPv4Address(fields[1])
                mask = fields[fields.index("netmask") + 1]
                if mask.startswith("0x"):
                    mask = str(ipaddress.IPv4Address(int(mask, 16)))
                network = ipaddress.ip_network(str(address) + "/" + mask, strict=False)
            else:
                address6 = ipaddress.IPv6Address(fields[1].split("%", 1)[0])
                prefix = fields[fields.index("prefixlen") + 1]
                network = ipaddress.ip_network(str(address6) + "/" + prefix, strict=False)
            networks.add(str(network))
        except (ValueError, IndexError) as exc:
            raise ValueError("interface_network_unparsed") from exc
    resolver = read_text(reads, "resolver")
    nameservers = re.findall(r"nameserver\[[0-9]+\]\s*:\s*(\S+)", resolver)
    if not networks or not nameservers:
        raise ValueError("local_network_and_resolver_inventory_required")
    for value in nameservers:
        resolver_address = ipaddress.ip_address(value.split("%", 1)[0])
        networks.add(str(ipaddress.ip_network(resolver_address)))
    connection = survey.get("ssh_connection")
    if not isinstance(connection, str) or len(connection.split()) != 4:
        raise ValueError("inventory_ssh_endpoints_required")
    for i in (0, 2):
        networks.add(str(ipaddress.ip_network(ipaddress.ip_address(connection.split()[i]))))
    if len(networks) > 128:
        raise ValueError("protected_network_inventory_unbounded")
    return tuple(sorted(networks))


def ipv4_targets(raw: object) -> tuple[str, ...]:
    values = obj(raw).get("target_addresses")
    if (
        not isinstance(values, list)
        or len(values) > 128
        or any(not isinstance(v, str) for v in values)
    ):
        raise ValueError("target_addresses_unproven")
    addresses = [ipaddress.ip_address(v) for v in cast(list[str], values)]
    result = sorted({str(a) for a in addresses if a.version == 4 and a.is_global})
    if not result:
        raise ValueError("public_ipv4_target_missing")
    return tuple(result)


def pin_ipv4(
    raw: object, run_ids: tuple[str, str], extra: tuple[str, ...] = ()
) -> tuple[FaultSpec, ...]:
    survey = obj(raw)
    hardware = read_text(survey.get("reads"), "hardware")
    interfaces = re.findall(r"Hardware Port: Wi-Fi\s+Device: (en[0-9]+)", hardware)
    if len(interfaces) != 1:
        raise ValueError("wifi_interface_unproven")
    networks = tuple(sorted(set(protected_networks(survey)) | set(extra)))
    management = tailscale_addresses(survey.get("reads"))
    # Deterministic selection; refuse the selected target if an exclusion overlaps.
    # Never cycle through DNS answers to evade a denied candidate.
    target = ipv4_targets(survey)[0]
    return tuple(
        FaultSpec(run_id, fault, target, "", interfaces[0], networks, management)
        for run_id, fault in zip(run_ids, IPV4_FAULTS, strict=True)
    )


READ_CODE = r"""
import hashlib,ipaddress,json,os,platform,shutil,socket,subprocess
commands={
 "hardware":["/usr/sbin/networksetup","-listallhardwareports"],
 "service_order":["/usr/sbin/networksetup","-listnetworkserviceorder"],
 "wifi_info":["/usr/sbin/networksetup","-getinfo","Wi-Fi"],
 "wifi_v6_routes":["/usr/sbin/networksetup","-getv6additionalroutes","Wi-Fi"],
 "interfaces":["/sbin/ifconfig","-a"],
 "ipv4_route_table":["/usr/sbin/netstat","-rn","-f","inet"],
 "ipv6_route_table":["/usr/sbin/netstat","-rn","-f","inet6"],
 "default_ipv4":["/sbin/route","-n","get","-inet","default"],
 "default_ipv6":["/sbin/route","-n","get","-inet6","default"],
 "resolver":["/usr/sbin/scutil","--dns"],
 "proxy":["/usr/sbin/scutil","--proxy"],
 "vpn_status":["/usr/sbin/scutil","--nc","list"],
 "pf_info":["/usr/bin/sudo","-n","/sbin/pfctl","-s","info"],
 "pf_main_rules":["/usr/bin/sudo","-n","/sbin/pfctl","-sr"],
 "pf_states":["/usr/bin/sudo","-n","/sbin/pfctl","-ss"],
}
for getter in ("-getdnsservers","-getsearchdomains","-getwebproxy","-getsecurewebproxy",
               "-getautoproxyurl","-getproxybypassdomains","-getsocksfirewallproxy","-getproxyautodiscovery"):
 commands[getter]=["/usr/sbin/networksetup",getter,"Wi-Fi"]
tailscale=shutil.which("tailscale")
if tailscale:
 commands["tailscale_netcheck"]=[tailscale,"netcheck","--format=json"]
 commands["tailscale_status"]=[tailscale,"status","--json"]
rows={}
for label,argv in commands.items():
 try:
  bound=20 if label.startswith("tailscale") else 3
  r=subprocess.run(argv,capture_output=True,text=True,timeout=bound)
  if len(r.stdout.encode())+len(r.stderr.encode())>1048576:raise ValueError("bounded_read_required")
  rows[label]={"exit_code":r.returncode,"stdout":r.stdout,"stderr":r.stderr}
 except (OSError,ValueError,subprocess.SubprocessError) as exc:
  rows[label]={"exit_code":None,"stdout":"","stderr":"read_failed_"+type(exc).__name__}
addresses=sorted({row[4][0] for row in socket.getaddrinfo("www.gstatic.com",443)})
print(json.dumps({"kind":"faults-v1-read-only","node":platform.node().split(".")[0],
 "ssh_connection":os.environ.get("SSH_CONNECTION",""),"target_addresses":addresses,
 "tailscale_installed":bool(tailscale),"reads":rows}))
"""
