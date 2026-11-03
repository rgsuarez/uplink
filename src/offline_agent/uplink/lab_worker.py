"""Root VM worker with fixed operations, owned namespaces and append-before-mutate journal.

Invoked as a script with standard-library-only sibling imports. It never changes the
host route, resolver, firewall, model server or existing uh-client smoke namespace.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import TextIO, cast

if __package__:
    from .lab_protocol import (
        ALTERNATE_GW,
        ALTERNATE_IP,
        APP_URL,
        DEAD_DNS,
        DNS_NAMES,
        HTTPS_URLS,
        LAB_VERSION,
        PORTAL_URLS,
        PRIMARY_GW,
        PRIMARY_IP,
        PROXY_URL,
        SERVER_IP,
        VM_NAME,
        LabSpec,
        request,
    )
else:
    from lab_protocol import (  # type: ignore[no-redef,import-not-found]
        ALTERNATE_GW,
        ALTERNATE_IP,
        APP_URL,
        DEAD_DNS,
        DNS_NAMES,
        HTTPS_URLS,
        LAB_VERSION,
        PORTAL_URLS,
        PRIMARY_GW,
        PRIMARY_IP,
        PROXY_URL,
        SERVER_IP,
        VM_NAME,
        LabSpec,
        request,
    )


def require_vm(execute: bool) -> None:
    if (
        not execute
        or platform.system() != "Linux"
        or platform.node() != VM_NAME
        or os.geteuid() != 0
    ):
        raise ValueError("explicit_root_execution_in_target_vm_required")
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise ValueError("lab_effects_forbidden_in_tests")


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LabCommandFailed(ValueError):
    def __init__(self, argv: tuple[str, ...], result: subprocess.CompletedProcess[str]) -> None:
        super().__init__("lab_command_failed")
        self.details: dict[str, object] = {
            "argv": list(argv),
            "exit_code": result.returncode,
            "stdout": result.stdout[-8192:],
            "stderr": result.stderr[-8192:],
        }


def run(
    argv: tuple[str, ...], *, timeout: float = 8, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else exc.stdout or ""
        )
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else exc.stderr or ""
        )
        error = LabCommandFailed(argv, subprocess.CompletedProcess(argv, -1, stdout, stderr))
        error.details["timeout_seconds"] = timeout
        raise error from exc
    if check and result.returncode:
        raise LabCommandFailed(argv, result)
    return result


def host_fingerprint() -> str:
    rows = []
    for command in (("ip", "-j", "address"), ("ip", "-j", "route"), ("ip", "-j", "-6", "route")):
        values = json.loads(run(command).stdout)
        stable = []
        for row in values:
            row.pop("expires", None)
            for addr in row.get("addr_info", []):
                addr.pop("valid_life_time", None)
                addr.pop("preferred_life_time", None)
            stable.append(row)
        rows.append(json.dumps(stable, sort_keys=True))
    rows.append(Path("/etc/resolv.conf").read_text())
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def write_resolver(spec: LabSpec, server: str) -> None:
    if server not in {PRIMARY_GW, ALTERNATE_GW, DEAD_DNS}:
        raise ValueError("unapproved_lab_resolver")
    (spec.resolver_directory / "resolv.conf").write_text(
        f"nameserver {server}\noptions timeout:1 attempts:1\n"
    )


def audit(spec: LabSpec, event: str, operation: str) -> None:
    with (spec.state / "audit.jsonl").open("a") as stream:
        stream.write(json.dumps({"ts": time.time(), "event": event, "operation": operation}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load(spec: LabSpec) -> dict[str, object]:
    if (
        spec.state.is_symlink()
        or spec.state.stat().st_uid != 0
        or spec.state.stat().st_mode & 0o077
    ):
        raise ValueError("lab_state_owner_or_permissions")
    value: object = json.loads((spec.state / "state.json").read_text())
    if (
        not isinstance(value, dict)
        or value.get("version") != LAB_VERSION
        or value.get("run_id") != spec.run_id
    ):
        raise ValueError("lab_state_identity_mismatch")
    return cast(dict[str, object], value)


def spawn(spec: LabSpec, name: str, argv: tuple[str, ...]) -> None:
    with (spec.state / f"{name}.log").open("xb") as output:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True
        )
    # Persist ownership before waiting for readiness so partial setup can clean up.
    # PID plus Linux process start ticks prevents PID-reuse cleanup mistakes.
    start = Path(f"/proc/{process.pid}/stat").read_text().split()[21]
    with (spec.state / "processes.jsonl").open("a") as stream:
        stream.write(json.dumps({"pid": process.pid, "start_ticks": start, "name": name}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    time.sleep(0.15)
    if process.poll() is not None:
        raise ValueError("lab_daemon_exited_" + name)


def renew(spec: LabSpec) -> None:
    client, _, _, _ = spec.interfaces
    # The fixed hook writes only this interface's address and route inside the namespace.
    # An observed initial DHCP offer arrived after 3.2 seconds.
    # Allow that check and another discover within one bounded acquisition.
    run(
        spec.namespaced(
            "busybox",
            "udhcpc",
            "-f",
            "-n",
            "-q",
            "-t",
            "3",
            "-T",
            "2",
            "-i",
            client,
            "-s",
            str(spec.state / "lease-hook.sh"),
        ),
        timeout=9,
    )


def sealed_module() -> ModuleType:
    if __package__:
        from . import sealed_linux
    else:
        import sealed_linux  # type: ignore[no-redef,import-not-found]
    return sealed_linux


def fault_package_module() -> ModuleType:
    if __package__:
        from . import faults_v1_linux
    else:
        import faults_v1_linux  # type: ignore[no-redef,import-not-found]
    return faults_v1_linux


def setup(spec: LabSpec, selection: dict[str, object] | None = None) -> dict[str, object]:
    owner = uuid.uuid4().hex
    try:
        return (
            setup_owned(spec, owner) if selection is None else setup_owned(spec, owner, selection)
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # An existing collision is never ours. Only this invocation's persisted nonce
        # authorizes cleanup after a partially completed namespace/bootstrap operation.
        if (spec.state / "state.json").is_file():
            state = load(spec)
            if state.get("setup_owner") == owner:
                state["setup_failure"] = (
                    str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                )
                if isinstance(exc, LabCommandFailed):
                    try:
                        exc.details["dns_log"] = (spec.state / "dns.log").read_text(
                            errors="replace"
                        )[-8192:]
                    except OSError as log_error:
                        exc.details["dns_log_failure"] = type(log_error).__name__
                    state["setup_failure_details"] = exc.details
                    atomic_json(spec.state / "setup-failure.json", exc.details)
                    audit(spec, "failed", "setup_command")
                try:
                    down(spec, state, partial=True)
                except (ValueError, OSError, subprocess.SubprocessError) as cleanup_error:
                    state["status"] = "failed"
                    state["cleanup_failure"] = "partial_setup_cleanup_failed_no_retry"
                    atomic_json(spec.state / "state.json", state)
                    audit(spec, "failed", "setup_cleanup")
                    raise ValueError("partial_setup_cleanup_failed_no_retry") from cleanup_error
        raise


def setup_owned(
    spec: LabSpec, owner: str, selection: dict[str, object] | None = None
) -> dict[str, object]:
    package = selection is not None and set(selection) == {"fault_package"}
    if package:
        assert selection is not None
        if not shutil.which("nft"):
            raise ValueError("fault_package_tool_missing_nft")
        package_fault = selection["fault_package"]
        selection = None
    if selection is not None:
        sealed_module().load(selection)
        if not shutil.which("nft"):
            raise ValueError("sealed_tool_missing_nft")
    for tool in ("ip", "dnsmasq", "curl", "openssl", "tc", "tinyproxy", "busybox"):
        if not shutil.which(tool):
            raise ValueError("lab_tool_missing_" + tool)
    if "udhcpc" not in run(("busybox", "--list")).stdout.split():
        raise ValueError("dhcp_client_missing")
    if not Path("/etc/tinyproxy").is_dir():
        raise ValueError("proxy_config_mount_target_missing")
    existing = {
        line.split()[0] for line in run(("ip", "netns", "list")).stdout.splitlines() if line.split()
    }
    existing_links = {row["ifname"] for row in json.loads(run(("ip", "-j", "link", "show")).stdout)}
    if (
        any(name in existing_links for name in spec.interfaces)
        or (Path("/etc/netns") / spec.internet).exists()
        or spec.client in existing
        or spec.internet in existing
        or spec.state.exists()
        or spec.resolver_directory.exists()
    ):
        raise ValueError("lab_resource_collision")
    before = host_fingerprint()
    spec.state.mkdir(parents=True, mode=0o700)
    state: dict[str, object] = {
        "version": LAB_VERSION,
        "run_id": spec.run_id,
        "status": "creating",
        "setup_owner": owner,
        "host_fingerprint": before,
        "proxy": PROXY_URL,
        "stub": "healthy",
        "prior_alternate_online": False,
    }
    if selection is not None:
        state["sealed_selection"] = selection
    if package:
        state["fault_package"] = package_fault
    atomic_json(spec.state / "state.json", state)
    audit(spec, "intent", "up")
    for ns in (spec.client, spec.internet):
        run(("ip", "netns", "add", ns))
        run(("ip", "-n", ns, "link", "set", "lo", "up"))
    # Enable only datagram ICMP for the dropped probe UID in this owned netns.
    # No capability or setuid execution is granted to generated probe programs.
    run(spec.namespaced("sysctl", "-q", "-w", "net.ipv4.ping_group_range=65534 65534"))
    c0, i0, c1, i1 = spec.interfaces
    for c, i, gateway in ((c0, i0, PRIMARY_GW), (c1, i1, ALTERNATE_GW)):
        run(("ip", "link", "add", c, "type", "veth", "peer", "name", i))
        run(("ip", "link", "set", c, "netns", spec.client))
        run(("ip", "link", "set", i, "netns", spec.internet))
        run(("ip", "-n", spec.internet, "address", "add", gateway + "/24", "dev", i))
        run(("ip", "-n", spec.internet, "link", "set", i, "up"))
        run(("ip", "-n", spec.client, "link", "set", c, "up"))
    run(("ip", "-n", spec.client, "address", "add", ALTERNATE_IP + "/24", "dev", c1))
    run(("ip", "-n", spec.internet, "address", "add", SERVER_IP + "/32", "dev", "lo"))
    spec.resolver_directory.mkdir(mode=0o700)
    write_resolver(spec, PRIMARY_GW)
    (spec.resolver_directory / "nsswitch.conf").write_text(
        "passwd: files\ngroup: files\nhosts: dns\n"
    )
    (spec.resolver_directory / "hosts").write_text(_public_fixture("@PUBLIC_IPV4_056@ localhost\n"))
    internet_files = Path("/etc/netns") / spec.internet
    internet_files.mkdir(mode=0o700)
    (internet_files / "hosts").write_text(
        _public_fixture("@PUBLIC_IPV4_056@ localhost\n") + SERVER_IP + " " + " ".join(DNS_NAMES) + "\n"
    )
    (internet_files / "nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
    hook = spec.state / "lease-hook.sh"
    hook.write_text(f'''#!/bin/sh
set -eu
[ "$interface" = "{c0}" ] || exit 2
case "$1" in
  bound|renew)
    [ "$ip" = "{PRIMARY_IP}" ] || exit 2
    ip address replace {PRIMARY_IP}/24 dev {c0}
    ip route replace default via {PRIMARY_GW} dev {c0}
    ;;
  deconfig) : ;;
  *) exit 2 ;;
esac
''')
    hook.chmod(0o700)
    if selection is not None:
        hook.write_text(sealed_module().lease_hook(spec))
        (spec.state / "dhcp-options").write_text(f"3,{PRIMARY_GW}\n")
    spawn(
        spec,
        "dns",
        spec.namespaced(
            "dnsmasq",
            "--keep-in-foreground",
            "--log-facility=-",
            "--log-dhcp",
            "--no-resolv",
            "--no-hosts",
            "--bind-interfaces",
            f"--interface={i0}",
            f"--interface={i1}",
            "--except-interface=lo",
            f"--address=/{DNS_NAMES[0]}/{SERVER_IP}",
            f"--address=/{DNS_NAMES[1]}/{SERVER_IP}",
            _public_fixture(f"--dhcp-range={PRIMARY_IP},{PRIMARY_IP},@PUBLIC_IPV4_092@,1h"),
            f"--dhcp-option=3,{PRIMARY_GW}"
            if selection is None
            else f"--dhcp-optsfile={spec.state / 'dhcp-options'}",
            f"--dhcp-leasefile={spec.state / 'leases'}",
            "--user=root",
            internet=True,
        ),
    )
    renew(spec)
    pki = spec.state / "pki"
    pki.mkdir(mode=0o700)
    run(
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=Uplink Lab CA",
            "-keyout",
            str(pki / "ca.key"),
            "-out",
            str(pki / "ca.pem"),
        ),
        timeout=20,
    )
    run(
        (
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=Uplink Lab",
            "-keyout",
            str(pki / "server.key"),
            "-out",
            str(pki / "server.csr"),
        ),
        timeout=20,
    )
    ext = pki / "ext.cnf"
    ext.write_text(
        "subjectAltName="
        + ",".join("DNS:" + n for n in DNS_NAMES)
        + "\nextendedKeyUsage=serverAuth\n"
    )
    run(
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
            "-CAcreateserial",
            "-days",
            "2",
            "-extfile",
            str(ext),
            "-out",
            str(pki / "server.pem"),
        ),
        timeout=20,
    )
    if selection is not None:
        sealed_module().prepare_pki(spec, state)
    for port in (80, 443):
        spawn(
            spec,
            f"http{port}",
            spec.namespaced(
                sys.executable,
                str(Path(__file__).with_name("lab_servers.py")),
                "--state",
                str(spec.state),
                "--port",
                str(port),
                internet=True,
            ),
        )
    start_proxy(spec, internet_files)
    if selection is not None:
        sealed_module().configure(spec, state)
    if package:
        fault_package_module().configure(spec, state, sys.modules[__name__])
    state["status"] = "ready"
    atomic_json(spec.state / "state.json", state)
    if host_fingerprint() != before:
        raise ValueError("host_network_changed")
    audit(spec, "complete", "up")
    return {"ready": True, "version": LAB_VERSION}


def start_proxy(spec: LabSpec, internet_files: Path) -> None:
    # ip netns exec bind-mounts this directory over /etc/tinyproxy only inside
    # the process's private mount namespace. The stock AppArmor path stays valid.
    proxy_directory = internet_files / "tinyproxy"
    proxy_directory.mkdir(mode=0o700)
    (proxy_directory / "tinyproxy.conf").write_text(
        _public_fixture(f"Port 8888\nListen {SERVER_IP}\nTimeout 4\n"
        "Allow @PUBLIC_IPV4_080@/24\nAllow @PUBLIC_IPV4_065@/24\nConnectPort 443\n")
    )
    # Foreground logging uses inherited stdout; no shared log or PID file is opened.
    spawn(
        spec,
        "proxy",
        spec.namespaced("tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf", internet=True),
    )


def remove_proxy_config(internet_files: Path) -> None:
    if internet_files.is_symlink():
        raise ValueError("proxy_config_parent_symlink_refused")
    directory = internet_files / "tinyproxy"
    if directory.is_symlink():
        raise ValueError("proxy_config_symlink_refused")
    if not directory.exists():
        return
    config = directory / "tinyproxy.conf"
    if config.is_symlink() or any(p.name != "tinyproxy.conf" for p in directory.iterdir()):
        raise ValueError("unowned_proxy_config_refused")
    config.unlink(missing_ok=True)
    directory.rmdir()


def mutate(
    spec: LabSpec, state: dict[str, object], operation: str, effect: Callable[[], None]
) -> None:
    rollback = operation in {"undo", "watchdog_revert"}
    if rollback and state.get("status") in {"reverting", "failed"}:
        raise ValueError("rollback_already_started_or_failed_no_retry")
    audit(spec, "intent", operation)
    if rollback:
        state.update(status="reverting", timer={"state": "rollback_started"})
        atomic_json(spec.state / "state.json", state)
    try:
        effect()
        atomic_json(spec.state / "state.json", state)
        if host_fingerprint() != state["host_fingerprint"]:
            raise ValueError("host_network_changed")
    except (ValueError, OSError, subprocess.SubprocessError):
        if rollback:
            state.update(status="failed", timer={"state": "failed", "reason": "rollback_failed"})
            atomic_json(spec.state / "state.json", state)
            audit(spec, "failed_no_retry", operation)
        raise
    audit(spec, "complete", operation)


def restore(spec: LabSpec, state: dict[str, object]) -> None:
    if "fault_package" in state:
        fault_package_module().restore(spec, state, sys.modules[__name__])
        return
    if "sealed_selection" in state:
        sealed_module().before_restore(spec, state)
    c0, i0, c1, i1 = spec.interfaces
    for ns, interface in (
        (spec.client, c0),
        (spec.client, c1),
        (spec.internet, i0),
        (spec.internet, i1),
    ):
        run(("ip", "-n", ns, "link", "set", interface, "up"))
        run(("ip", "-n", ns, "link", "set", interface, "mtu", "1500"))
        run(
            ("ip", "netns", "exec", ns, "tc", "qdisc", "del", "dev", interface, "root"), check=False
        )
    renew(spec)
    write_resolver(spec, PRIMARY_GW)
    state.update(proxy=PROXY_URL, stub="healthy", status="ready", misleading_status=False)
    if "sealed_selection" in state:
        sealed_module().after_restore(spec, state)


def inject(spec: LabSpec, state: dict[str, object], fault: str) -> None:
    if fault in fault_package_module().FAULTS:
        if state.get("fault_package") != fault:
            raise ValueError("fault_package_setup_required")
        fault_package_module().inject(spec, state, sys.modules[__name__])
        return
    if "fault_package" in state:
        raise ValueError("fault_package_cannot_inject_other_catalog")
    c0, i0, _, _ = spec.interfaces
    if fault in {"wrong_dns", "dns_plus_proxy"}:
        write_resolver(spec, DEAD_DNS)
    if fault in {"stale_proxy", "dns_plus_proxy"}:
        state["proxy"] = _public_fixture("http://@PUBLIC_IPV4_086@:9")
    if fault == "lease_loss":
        run(("ip", "-n", spec.client, "address", "del", PRIMARY_IP + "/24", "dev", c0))
    if fault == "bad_default_route":
        run(
            (
                "ip",
                "-n",
                spec.client,
                "route",
                "replace",
                "default",
                "via",
                _public_fixture("@PUBLIC_IPV4_084@"),
                "dev",
                c0,
            )
        )
    if fault == "interface_down":
        run(("ip", "-n", spec.client, "link", "set", c0, "down"))
    if fault == "isp_down_saved_alternate":
        run(("ip", "-n", spec.internet, "link", "set", i0, "down"))
    if fault == "captive_portal":
        state["stub"] = "captive"
    if fault == "site_only_outage":
        state["stub"] = "site_down"
    if fault == "mtu_black_hole":
        run(
            spec.namespaced(
                "tc", "qdisc", "add", "dev", i0, "root", "handle", "1:", "prio", internet=True
            )
        )
        run(
            spec.namespaced(
                "tc",
                "filter",
                "add",
                "dev",
                i0,
                "parent",
                "1:",
                "protocol",
                "ip",
                "basic",
                "match",
                "cmp(u16 at 2 layer 2 gt 1200)",
                "action",
                "drop",
                internet=True,
            )
        )
    state["misleading_status"] = fault == "misleading_status"
    state["status"] = "faulted"


def apply(spec: LabSpec, state: dict[str, object], tool: str, policy: str) -> None:
    if "fault_package" in state:
        module = fault_package_module()
        if (
            tool != module.TOOLS[module.FAULTS.index(state["fault_package"])]
            or policy != "snapshot"
        ):
            raise ValueError("fault_package_action_refused")
        module.restore(spec, state, sys.modules[__name__])
        return
    if "sealed_selection" in state and sealed_module().apply_snapshot(spec, state, tool, policy):
        return
    c0, _, c1, _ = spec.interfaces
    if tool == "set_dns":
        write_resolver(spec, PRIMARY_GW)
    elif tool == "restore_proxy":
        state["proxy"] = "" if policy == "off" else PROXY_URL
    elif tool == "renew_lease":
        renew(spec)
    elif tool == "cycle_interface":
        run(("ip", "-n", spec.client, "link", "set", c0, "up"))
    elif tool == "reroute":
        gateway, interface = (ALTERNATE_GW, c1) if policy == "alternate" else (PRIMARY_GW, c0)
        run(
            (
                "ip",
                "-n",
                spec.client,
                "route",
                "replace",
                "default",
                "via",
                gateway,
                "dev",
                interface,
            )
        )
        write_resolver(spec, gateway)
    else:
        raise ValueError("unsupported_lab_tool")


def observe(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    addresses = json.loads(run(("ip", "-j", "-n", spec.client, "address")).stdout)
    routes = json.loads(run(("ip", "-j", "-n", spec.client, "route")).stdout)
    return {
        "addresses": addresses,
        "routes": routes,
        "resolver": (spec.resolver_directory / "resolv.conf").read_text(),
        "proxy": state["proxy"],
        "prior_alternate_online": state["prior_alternate_online"],
        "timer": state.get("timer"),
        "association_status": "not associated"
        if state.get("misleading_status")
        else "not_applicable",
        "profile": LAB_VERSION,
    }


def resolve(spec: LabSpec, target: int) -> dict[str, object]:
    started = time.monotonic()
    response = run(
        spec.namespaced(
            sys.executable,
            "-I",
            "-c",
            "import socket,sys;print(bool(socket.getaddrinfo(sys.argv[1],443)))",
            DNS_NAMES[target],
        ),
        timeout=4,
        check=False,
    )
    return {
        "ok": response.returncode == 0 and response.stdout.strip() == "True",
        "latency_ms": (time.monotonic() - started) * 1000,
    }


def fetch(spec: LabSpec, state: dict[str, object], target: int) -> dict[str, object]:
    urls = (*HTTPS_URLS, *PORTAL_URLS, APP_URL)
    url = urls[target]
    proxy = state["proxy"]
    environment: tuple[str, ...] = ()
    proxy_args: tuple[str, ...] = ("--proxy", str(proxy))
    if "sealed_selection" in state:
        proxy = sealed_module().effective_proxy(state, portal=target in (2, 3))
        proxy_args = ("--proxy", str(proxy))
        if state.get("process_proxy_override") is not None:
            environment = (
                "env",
                "http_proxy=" + str(proxy),
                "https_proxy=" + str(proxy),
                "ALL_PROXY=" + str(proxy),
                "NO_PROXY=",
                "no_proxy=",
            )
            proxy_args = ()
    started = time.monotonic()
    response = run(
        spec.namespaced(
            *environment,
            "curl",
            "--disable",
            "--silent",
            "--max-time",
            "4",
            "--connect-timeout",
            "2",
            "--cacert",
            str(spec.state / "pki/ca.pem"),
            *proxy_args,
            "--noproxy",
            "",
            "--write-out",
            "\nSTATUS:%{http_code}",
            url,
        ),
        timeout=5,
        check=False,
    )
    body, _, status = response.stdout.rpartition("\nSTATUS:")
    code = int(status) if status.isdigit() else 0
    return {
        "ok": response.returncode == 0 and code == 204 and not body.strip(),
        "status": code,
        "tls_verified": response.returncode == 0 and url.startswith("https://"),
        "body_matches": not body.strip(),
        "proxy": proxy,
        "latency_ms": (time.monotonic() - started) * 1000,
        "error": None if response.returncode == 0 else "probe_transport_failed",
    }


def diagnostic_command(
    argv: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[str] | None, float, str | None]:
    """One bounded read. A timeout is failed evidence, never a reason to retry."""
    started = time.monotonic()
    try:
        result = run(argv, timeout=1, check=False)
    except subprocess.TimeoutExpired:
        return None, (time.monotonic() - started) * 1000, "probe_timeout"
    except OSError:
        return None, (time.monotonic() - started) * 1000, "command_unavailable"
    return result, (time.monotonic() - started) * 1000, None


def diagnostic_result(
    result: subprocess.CompletedProcess[str] | None, elapsed: float, error: str | None
) -> dict[str, object]:
    unavailable = error == "command_unavailable" or (
        result is not None and (result.returncode == 127 or "applet not found" in result.stderr)
    )
    return {
        "available": not unavailable,
        "ok": result is not None and result.returncode == 0,
        "latency_ms": elapsed,
        "reason": "command_unavailable"
        if unavailable
        else error
        or ("observed" if result is not None and result.returncode == 0 else "probe_failed"),
    }


def diagnostic_dns(
    spec: LabSpec, target: int, resolver: str | None, path: str
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": f"dns-{path}-{target + 1}",
        "target": DNS_NAMES[target],
        "resolver": resolver,
        "available": False,
        "ok": False,
        "answers": [],
        "latency_ms": 0.0,
        "reason": "resolver_not_allowlisted",
    }
    if resolver not in {PRIMARY_GW, ALTERNATE_GW, DEAD_DNS}:
        return row
    # busybox is an existing fixture dependency. nslookup's server argument does
    # not alter resolv.conf, and the outer subprocess timeout bounds all retries.
    result, elapsed, error = diagnostic_command(
        spec.namespaced("busybox", "nslookup", "-type=A", DNS_NAMES[target], resolver)
    )
    row.update(diagnostic_result(result, elapsed, error))
    answers: list[str] = []
    if result is not None:
        for line in result.stdout.splitlines():
            # Ignore the resolver's header and retain only the fixed fixture IP.
            # A changed or unexpected answer cannot establish a passing DNS check.
            if line.lstrip().startswith("Address"):
                for token in line.split():
                    try:
                        address = str(ipaddress.IPv4Address(token))
                    except ipaddress.AddressValueError:
                        continue
                    if address == SERVER_IP and address not in answers:
                        answers.append(address)
    row["answers"] = answers
    row["ok"] = row["ok"] is True and answers == [SERVER_IP]
    if row["available"] is True and error is None and row["ok"] is not True:
        row["reason"] = "expected_dns_answer_missing"
    return row


def diagnostic_https(
    spec: LabSpec, target: int, proxy: object, *, direct: bool
) -> dict[str, object]:
    path = "direct" if direct else "proxy"
    row: dict[str, object] = {
        "id": f"https-{path}-{target + 1}",
        "target": DNS_NAMES[target],
        "path": "direct_fixture_address" if direct else "configured_proxy",
        "available": False,
        "ok": False,
        "status": 0,
        "tls_verified": False,
        "latency_ms": 0.0,
        "reason": "proxy_not_allowlisted",
    }
    if not direct and (
        not isinstance(proxy, str) or proxy not in {"", PROXY_URL, f"http://{SERVER_IP}:9"}
    ):
        return row
    # Pin the fixture address on the direct diagnostic only, preserving TLS name
    # verification while separating DNS failure from the HTTPS transport layer.
    overrides = ("--resolve", f"{DNS_NAMES[target]}:443:{SERVER_IP}") if direct else ()
    result, elapsed, error = diagnostic_command(
        spec.namespaced(
            "curl",
            "--disable",
            "--silent",
            "--max-time",
            "0.8",
            "--connect-timeout",
            "0.6",
            "--cacert",
            str(spec.state / "pki/ca.pem"),
            "--proxy",
            "" if direct else cast(str, proxy),
            "--noproxy",
            "",
            *overrides,
            "--write-out",
            "\nSTATUS:%{http_code}",
            HTTPS_URLS[target],
        )
    )
    row.update(diagnostic_result(result, elapsed, error))
    if result is not None:
        body, separator, status = result.stdout.rpartition("\nSTATUS:")
        code = int(status) if separator and status.isdigit() else 0
        row.update(status=code, tls_verified=result.returncode == 0)
        row["ok"] = result.returncode == 0 and code == 204 and not body.strip()
        if row["available"] is True and not row["ok"]:
            row["reason"] = "https_probe_failed"
    return row


def layer_probe(spec: LabSpec, state: dict[str, object]) -> dict[str, object]:
    """Fixed read-only differential probes; none contribute to the ONLINE oracle.

    Kernel reads and the nine network probes run in two bounded parallel batches.
    No subprocess may run longer than one second, including a missing dependency.
    The worker's existing lock keeps this snapshot consistent within the run.
    """
    started = time.monotonic()
    c0, _, _, _ = spec.interfaces
    with ThreadPoolExecutor(max_workers=9) as pool:
        address_job = pool.submit(diagnostic_command, ("ip", "-j", "-n", spec.client, "address"))
        route_job = pool.submit(diagnostic_command, ("ip", "-j", "-n", spec.client, "route"))
        address_result, _, _ = address_job.result()
        route_result, _, _ = route_job.result()
        link_up: bool | None = None
        lease_present: bool | None = None
        gateway: str | None = None
        try:
            addresses: object = (
                json.loads(address_result.stdout)
                if address_result is not None and address_result.returncode == 0
                else None
            )
            routes: object = (
                json.loads(route_result.stdout)
                if route_result is not None and route_result.returncode == 0
                else None
            )
        except ValueError:
            addresses, routes = None, None
        if isinstance(addresses, list):
            interface = next(
                (item for item in addresses if isinstance(item, dict) and item.get("ifname") == c0),
                None,
            )
            if isinstance(interface, dict):
                flags, assigned = interface.get("flags"), interface.get("addr_info")
                if isinstance(flags, list) and all(isinstance(flag, str) for flag in flags):
                    link_up = "UP" in flags and interface.get("operstate") != "DOWN"
                if isinstance(assigned, list):
                    lease_present = any(
                        isinstance(item, dict) and item.get("local") == PRIMARY_IP
                        for item in assigned
                    )
        if isinstance(routes, list):
            gateways = [
                item.get("gateway")
                for item in routes
                if isinstance(item, dict) and item.get("dst") == "default"
            ]
            if (
                len(gateways) == 1
                and isinstance(gateways[0], str)
                and gateways[0] in {PRIMARY_GW, ALTERNATE_GW, _public_fixture("@PUBLIC_IPV4_084@")}
            ):
                gateway = str(gateways[0])
        resolver: str | None = None
        try:
            configured = [
                line.split()[1]
                for line in (spec.resolver_directory / "resolv.conf").read_text().splitlines()
                if len(line.split()) == 2 and line.split()[0] == "nameserver"
            ]
            if len(configured) == 1 and configured[0] in {PRIMARY_GW, ALTERNATE_GW, DEAD_DNS}:
                resolver = configured[0]
        except OSError:
            pass
        gateway_job = (
            pool.submit(
                diagnostic_command,
                spec.namespaced("busybox", "ping", "-c", "1", "-W", "1", gateway),
            )
            if gateway is not None
            else None
        )
        configured_jobs = [
            pool.submit(diagnostic_dns, spec, i, resolver, "configured") for i in range(2)
        ]
        alternate_jobs = [
            pool.submit(diagnostic_dns, spec, i, ALTERNATE_GW, "alternate") for i in range(2)
        ]
        direct_jobs = [
            pool.submit(diagnostic_https, spec, i, state.get("proxy"), direct=True)
            for i in range(2)
        ]
        proxy_jobs = [
            pool.submit(diagnostic_https, spec, i, state.get("proxy"), direct=False)
            for i in range(2)
        ]
        gateway_ping = (
            diagnostic_result(*gateway_job.result())
            if gateway_job is not None
            else {
                "available": False,
                "ok": False,
                "latency_ms": 0.0,
                "reason": "gateway_not_allowlisted",
            }
        )
        result: dict[str, object] = {
            "version": 1,
            "kind": "uplink-layer-evidence",
            "link_up": link_up,
            "lease_present": lease_present,
            "default_gateway": gateway,
            "gateway_ping": gateway_ping,
            "dns_configured": [job.result() for job in configured_jobs],
            "dns_alternate": [job.result() for job in alternate_jobs],
            "https_direct": [job.result() for job in direct_jobs],
            "https_proxy": [job.result() for job in proxy_jobs],
        }
    result["elapsed_ms"] = (time.monotonic() - started) * 1000
    return result


def probe_script(spec: LabSpec, state: dict[str, object], program: object) -> dict[str, object]:
    """Build trusted context, then enter the owned netns and a private mount sandbox."""
    before = observe(spec, state)
    fingerprint = host_fingerprint()
    resolver = next(
        (
            line.split()[1]
            for line in str(before["resolver"]).splitlines()
            if len(line.split()) == 2 and line.split()[0] == "nameserver"
        ),
        "",
    )
    routes = before["routes"]
    gateway = (
        next(
            (r.get("gateway") for r in routes if isinstance(r, dict) and r.get("dst") == "default"),
            None,
        )
        if isinstance(routes, list)
        else None
    )
    context = {
        "profile": "linux_lab",
        "run_id": spec.run_id,
        "alternate": True,
        "sources": {
            "current": ALTERNATE_IP if gateway == ALTERNATE_GW else PRIMARY_IP,
            "alternate": ALTERNATE_IP,
        },
        "gateways": {"current": gateway, "alternate": ALTERNATE_GW},
        "resolvers": {"configured": resolver, "alternate": ALTERNATE_GW},
        "names": list(DNS_NAMES),
        "proxy": state["proxy"],
        "https_paths": ["/ok", "/ok"],
        "portal_paths": ["/portal", "/portal"],
        "https_status": [204, 204],
        "portal_status": [204, 204],
        "portal_bodies": ["", ""],
        "ca_pem": (spec.state / "pki/ca.pem").read_text(),
        "state": {k: before[k] for k in ("addresses", "routes", "resolver", "proxy")},
    }
    if "sealed_selection" in state:
        https_proxy = sealed_module().effective_proxy(state, portal=False)
        http_proxy = sealed_module().effective_proxy(state, portal=True)
        context["proxy_routes"] = [https_proxy, https_proxy, http_proxy, http_proxy]
    timer = state.get("timer")
    remaining = (
        float(str(timer["deadline"])) - time.time()
        if isinstance(timer, dict) and timer.get("state") == "armed"
        else 30
    )
    limit_ms = max(0, min(30_000, int((remaining - 10) * 1000)))
    audit(spec, "intent", "read_only_probe_script")
    # ip-netns-exec supplies this run's resolver files. unshare prevents mount effects
    # escaping the chroot setup. Only fixed trusted code executes as root.
    argv = spec.namespaced(
        "/usr/bin/unshare",
        "--mount",
        "--propagation",
        "private",
        sys.executable,
        "-I",
        "-B",
        str(Path(__file__).with_name("probe_execution.py")),
        "--linux-launch",
        "--directory",
        str(spec.state / "probe-traces"),
    )
    response = subprocess.run(
        argv,
        input=json.dumps({"program": program, "context": context, "limit_ms": limit_ms}),
        capture_output=True,
        text=True,
        timeout=max(0.1, min(33, remaining - 5)),
        check=False,
    )
    try:
        result = (
            json.loads(response.stdout)
            if response.returncode == 0
            else {
                "status": "refused",
                "rows": [],
                "reason": "probe_launcher_refused",
                "stderr": response.stderr[-8192:],
                "exit_code": response.returncode,
            }
        )
        if not isinstance(result, dict):
            raise ValueError("probe_receipt_shape")
    except ValueError:
        result = {"status": "refused", "rows": [], "reason": "probe_receipt_unavailable"}
    after = observe(spec, state)
    unchanged = all(before[k] == after[k] for k in ("addresses", "routes", "resolver", "proxy"))
    unchanged = unchanged and host_fingerprint() == fingerprint
    result.update(
        settings_unchanged=unchanged,
        before_sha256=hashlib.sha256(
            json.dumps(
                {k: before[k] for k in ("addresses", "routes", "resolver", "proxy")}, sort_keys=True
            ).encode()
        ).hexdigest(),
        after_sha256=hashlib.sha256(
            json.dumps(
                {k: after[k] for k in ("addresses", "routes", "resolver", "proxy")}, sort_keys=True
            ).encode()
        ).hexdigest(),
    )
    atomic_json(spec.state / ("probe-receipt-" + uuid.uuid4().hex + ".json"), result)
    audit(spec, "complete" if unchanged else "failed", "read_only_probe_script")
    if not unchanged:
        raise ValueError("probe_settings_changed_no_retry")
    return cast(dict[str, object], result)


def down(spec: LabSpec, state: dict[str, object], *, partial: bool = False) -> dict[str, object]:
    if (
        not partial
        and state.get("sealed_injection_started") is True
        and state.get("sealed_restored") is not True
    ):
        raise ValueError("sealed_verified_revert_required_before_down")
    audit(spec, "intent", "down")
    process_file = spec.state / "processes.jsonl"
    if process_file.exists():
        for line in process_file.read_text().splitlines():
            row = json.loads(line)
            pid = int(row["pid"])
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                if stat.read_text().split()[21] != row["start_ticks"]:
                    raise ValueError("daemon_identity_changed")
                with suppress(ProcessLookupError):
                    os.killpg(pid, signal.SIGTERM)
    existing = {
        line.split()[0] for line in run(("ip", "netns", "list")).stdout.splitlines() if line.split()
    }
    for ns in (spec.client, spec.internet):
        if ns in existing or not partial:
            run(("ip", "netns", "delete", ns))
    if partial:
        # Veth creation can fail between creation and moving both ends into their
        # namespaces. These names were proven absent before this owner began setup.
        for name in spec.interfaces:
            if run(("ip", "link", "show", "dev", name), check=False).returncode == 0:
                run(("ip", "link", "delete", "dev", name))
    remove_proxy_config(Path("/etc/netns") / spec.internet)
    for directory, names in (
        (spec.resolver_directory, ("resolv.conf", "nsswitch.conf", "hosts")),
        (Path("/etc/netns") / spec.internet, ("hosts", "nsswitch.conf")),
    ):
        if directory.is_symlink():
            raise ValueError("resolver_symlink_refused")
        if directory.exists() or not partial:
            for name in names:
                (directory / name).unlink(missing_ok=True)
            directory.rmdir()
    state["status"] = "closed"
    atomic_json(spec.state / "state.json", state)
    if host_fingerprint() != state["host_fingerprint"]:
        raise ValueError("host_network_changed")
    audit(spec, "complete", "down")
    return {"closed": True}


def acquire_operation_lock(lock: TextIO) -> None:
    """Wait briefly for watchdog inspection without retrying any operation."""
    deadline = time.monotonic() + 0.5
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("lab_lock_busy_no_operation") from exc
            time.sleep(min(0.01, remaining))


def dispatch(value: dict[str, object], *, execute: bool) -> dict[str, object]:
    operation, run_id = value.get("operation"), value.get("run_id")
    if (
        not isinstance(operation, str)
        or not isinstance(run_id, str)
        or not isinstance(value.get("payload"), dict)
    ):
        raise ValueError("invalid_lab_request")
    canonical = request(operation, run_id, cast(dict[str, object], value["payload"]))
    if canonical != value:
        raise ValueError("lab_request_drift")
    if not execute:
        return {"mode": "dry_run", "request": canonical, "effects": False, "profile": LAB_VERSION}
    require_vm(execute)
    spec = LabSpec(run_id)
    if operation == "up":
        chosen = cast(dict[str, object], value["payload"])
        return setup(spec, chosen) if chosen else setup(spec)
    with (spec.state / "lock").open("a") as lock:
        acquire_operation_lock(lock)
        state = load(spec)
        if state["status"] == "closed":
            raise ValueError("lab_run_closed")
        payload = cast(dict[str, object], value["payload"])
        if operation == "fixture_check":
            if "sealed_selection" not in state:
                raise ValueError("sealed_fixture_required")
            return cast(dict[str, object], sealed_module().measure(spec, state))
        if operation == "fault_package_check":
            if "fault_package" not in state:
                raise ValueError("fault_package_setup_required")
            module = fault_package_module()
            return {
                "target": module.target(spec, state, sys.modules[__name__]),
                "snapshot_match": module.capture(spec, sys.modules[__name__])
                == state["fault_package_snapshot"],
            }
        if operation == "observe":
            return observe(spec, state)
        if operation == "layer_probe":
            return layer_probe(spec, state)
        if operation == "probe_script":
            return probe_script(spec, state, payload["program"])
        if operation == "resolve":
            return resolve(spec, cast(int, payload["target"]))
        if operation == "fetch":
            return fetch(spec, state, cast(int, payload["target"]))
        if operation == "check":
            good = all(resolve(spec, i)["ok"] for i in range(2)) and all(
                fetch(spec, state, i)["ok"] for i in range(4)
            )
            return {"healthy": good}
        if state["status"] in {"reverting", "failed"} and operation in {
            "arm",
            "disarm",
            "undo",
            "apply",
            "inject",
        }:
            raise ValueError("rollback_started_or_failed_no_retry")
        if operation == "arm":
            previous_timer = state.get("timer")
            if isinstance(previous_timer, dict) and previous_timer.get("state") == "armed":
                raise ValueError("rollback_timer_already_armed")
            audit(spec, "intent", "arm")
            timer = {"state": "armed", "deadline": time.time() + int(str(payload["seconds"]))}
            state["timer"] = timer
            atomic_json(spec.state / "state.json", state)
            spawn(
                spec,
                "watchdog-" + str(time.time_ns()),
                (sys.executable, str(Path(__file__).resolve()), "--watchdog", spec.run_id),
            )
            return timer
        observed_timer = state.get("timer")
        if operation in {"disarm", "undo", "apply", "inject"} and isinstance(observed_timer, dict):
            if (
                observed_timer.get("state") == "failed"
                or observed_timer.get("reason") == "rollback_failed"
            ):
                raise ValueError("rollback_failed_no_retry")
            if operation == "undo" and observed_timer.get("reason") == "deadline_reverted":
                return {"ok": True, "operation": "undo", "already_restored": True}
            if operation == "disarm" and (
                observed_timer.get("reason") == "deadline_reverted"
                or observed_timer.get("state") == "armed"
                and float(str(observed_timer["deadline"])) <= time.time()
            ):
                raise ValueError("cannot_disarm_expired_watchdog")
        if operation == "disarm":
            state["timer"] = {"state": "cleared", "reason": "verified"}
            audit(spec, "intent", "disarm")
            atomic_json(spec.state / "state.json", state)
            return {"state": "cleared"}
        if operation in {"apply", "inject"}:
            timer_value = state.get("timer")
            if (
                not isinstance(timer_value, dict)
                or timer_value.get("state") != "armed"
                or float(timer_value["deadline"]) <= time.time()
            ):
                raise ValueError("armed_rollback_timer_required")
        if operation == "inject":
            if "sealed_selection" in state:
                if payload != state["sealed_selection"]:
                    raise ValueError("sealed_selection_mismatch")
                mutate(spec, state, operation, lambda: sealed_module().inject(spec, state))
            else:
                if "fixture" in payload:
                    raise ValueError("sealed_prepare_required")
                mutate(spec, state, operation, lambda: inject(spec, state, str(payload["fault"])))
        elif operation == "undo":
            mutate(spec, state, operation, lambda: restore(spec, state))
            state["timer"] = {"state": "cleared", "reason": "restored"}
            atomic_json(spec.state / "state.json", state)
        elif operation == "apply":
            mutate(
                spec,
                state,
                operation,
                lambda: apply(spec, state, str(payload["tool"]), str(payload["policy"])),
            )
        elif operation == "down":
            return down(spec, state)
        return {"ok": True, "operation": operation}


def watchdog(run_id: str) -> None:
    require_vm(True)
    spec = LabSpec(run_id)
    while True:
        with (spec.state / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = load(spec)
            timer = state.get("timer")
            if not isinstance(timer, dict) or timer.get("state") != "armed":
                return
            remaining = float(timer["deadline"]) - time.time()
            if remaining <= 0:
                try:
                    mutate(spec, state, "watchdog_revert", partial(restore, spec, state))
                    state["timer"] = {"state": "cleared", "reason": "deadline_reverted"}
                except (ValueError, OSError, subprocess.SubprocessError):
                    state["timer"] = {"state": "failed", "reason": "rollback_failed"}
                    state["status"] = "failed"
                atomic_json(spec.state / "state.json", state)
                return
        time.sleep(min(remaining, 0.2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--watchdog")
    parser.add_argument("--request")
    args = parser.parse_args()
    if args.watchdog:
        watchdog(args.watchdog)
        return
    try:
        raw = args.request if args.request is not None else sys.stdin.read(8193)
        if len(raw) > 8192:
            raise ValueError("lab_request_size_limit")
        value: object = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("lab_request_object_required")
        print(json.dumps(dispatch(cast(dict[str, object], value), execute=args.execute)))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": reason,
                    "failure_details": exc.details if isinstance(exc, LabCommandFailed) else None,
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
