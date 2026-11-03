"""Trusted interpreter for read-only probe data, never for generated Python or shell.

Linux runs in the owned netns and a private mount namespace. Its child is chrooted
into a run-owned directory with read-only runtime mounts, then switched to nobody
with no-new-privileges. macOS uses the existing unprivileged user and sandbox-exec.
Only the parent writes receipts; the child receives a fixed, validated context.
"""

from __future__ import annotations

import argparse
import fcntl
import http.client
import ipaddress
import json
import os
import platform
import resource
import signal
import socket
import ssl
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

if __package__:
    from .probe_protocol import MAX_EPISODE_MS, Probe, ProbeBudget, ProbeRefused, validate_program
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from probe_protocol import (  # type: ignore[import-not-found,no-redef]
        MAX_EPISODE_MS,
        Probe,
        ProbeBudget,
        ProbeRefused,
        validate_program,
    )

OUTPUT_LIMIT = 8192


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ProbeRefused("probe_object_required")
    return cast(dict[str, object], value)


def proxy_address(
    context: dict[str, object], index: int = 0, *, portal: bool = False
) -> tuple[str, int] | None:
    routes = context.get("proxy_routes")
    raw = (
        routes[index + (2 if portal else 0)]
        if isinstance(routes, list) and len(routes) == 4
        else context.get("proxy")
    )
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ProbeRefused("probe_proxy_shape")
    value = urlsplit(raw)
    if (
        value.scheme != "http"
        or not value.hostname
        or value.username
        or value.password
        or value.path not in {"", "/"}
        or value.query
        or value.fragment
    ):
        raise ProbeRefused("probe_proxy_refused")
    port = value.port or 80
    if not 1 <= port <= 65535:
        raise ProbeRefused("probe_proxy_refused")
    return value.hostname, port


def source(context: dict[str, object], path: str = "current") -> tuple[str, int] | None:
    value = obj(context.get("sources", {})).get(path)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProbeRefused("probe_source_refused")
    return str(ipaddress.IPv4Address(value)), 0


def command(argv: tuple[str, ...], timeout: float) -> dict[str, object]:
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={"PATH": os.defpath},
    )
    return {
        "argv": list(argv),
        "stdout": result.stdout[:OUTPUT_LIMIT],
        "stderr": result.stderr[:OUTPUT_LIMIT],
        "exit_code": result.returncode,
        "ok": result.returncode == 0,
    }


def connect_socket(
    context: dict[str, object],
    address: tuple[str, int],
    timeout: float,
    source_address: tuple[str, int] | None = None,
    *,
    all_errors: bool = False,
) -> socket.socket:
    """Bind actual TCP/TLS traffic to the approved source and macOS Wi-Fi interface."""
    del all_errors
    targets = socket.getaddrinfo(address[0], address[1], socket.AF_INET, socket.SOCK_STREAM)
    last: OSError | None = None
    for family, kind, protocol, _canonical, target in targets:
        stream = socket.socket(family, kind, protocol)
        try:
            stream.settimeout(timeout)
            interface = context.get("interface")
            if platform.system() == "Darwin":
                if (
                    not isinstance(interface, str)
                    or not interface.startswith("en")
                    or interface == "en7"
                ):
                    raise ProbeRefused("probe_wifi_binding_required")
                stream.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(interface))
            if source_address is not None:
                stream.bind(source_address)
            stream.connect(target)
            return stream
        except (OSError, ProbeRefused) as exc:
            stream.close()
            if isinstance(exc, ProbeRefused):
                raise
            last = exc
    raise last or OSError("probe_endpoint_unresolved")


def primitive(probe: Probe, context: dict[str, object], timeout: float) -> dict[str, object]:
    values = probe.values
    if probe.op == "read_state":
        return {"ok": True, "state": context["state"], "stdout": json.dumps(context["state"])}
    if probe.op == "ping_gateway":
        path = str(values["path"])
        gateway = obj(context["gateways"]).get(path)
        if not isinstance(gateway, str):
            return {"ok": False, "available": False, "reason": "gateway_unavailable"}
        gateway = str(ipaddress.IPv4Address(gateway))
        count, interval = int(values["count"]), int(values["interval_ms"]) / 1000
        args: tuple[str, ...] = (
            "/sbin/ping" if platform.system() == "Darwin" else "/usr/bin/ping",
            "-n",
            "-c",
            str(count),
            "-i",
            str(interval),
        )
        binding = source(context, path)
        if binding is not None:
            args += ("-S", binding[0]) if platform.system() == "Darwin" else ("-I", binding[0])
        if platform.system() == "Darwin":
            interface = context.get("interface")
            if not isinstance(interface, str) or interface == "en7":
                raise ProbeRefused("probe_wifi_binding_required")
            args += ("-b", interface)
        return command((*args, gateway), timeout)
    names = context.get("names")
    if not isinstance(names, list) or len(names) != 2 or not all(isinstance(n, str) for n in names):
        raise ProbeRefused("probe_names_refused")
    index = 0 if values.get("name", values.get("endpoint")) == "one" else 1
    portal = probe.op == "captive_check"
    if portal:
        names = context.get("portal_names", names)
        if not isinstance(names, list) or len(names) != 2:
            raise ProbeRefused("probe_portal_names_refused")
    host = str(names[index])
    if probe.op == "dns_query":
        resolver = obj(context["resolvers"]).get(str(values["resolver"]))
        if not isinstance(resolver, str):
            return {"ok": False, "available": False, "reason": "resolver_unavailable"}
        resolver_ip = ipaddress.ip_address(resolver)
        if resolver_ip.version == 6 and context.get("profile") != "linux_lab":
            raise ProbeRefused("ipv6_resolver_probe_requires_linux_namespace")
        resolver = str(resolver_ip)
        args = ("/usr/bin/dig", "@" + resolver, host, "A", "+short", "+tries=1", "+time=1")
        binding = source(context, "alternate" if values["resolver"] == "alternate" else "current")
        if binding is not None and resolver_ip.version == 4:
            args += ("-b", binding[0])
        result = command(args, timeout)
        answers = []
        for line in str(result["stdout"]).splitlines():
            with suppress(ipaddress.AddressValueError):
                answers.append(str(ipaddress.IPv4Address(line.strip())))
        result["ok"] = result["exit_code"] == 0 and bool(answers)
        result["answers"] = answers
        return result
    proxy = proxy_address(context, index, portal=portal)
    binding = source(context)
    if probe.op == "tcp_connect":
        if values["endpoint"] == "proxy":
            if proxy is None:
                return {"ok": False, "available": False, "reason": "no_configured_proxy"}
            target = proxy
        else:
            target = proxy or (host, 443)
        with connect_socket(context, target, timeout, source_address=binding):
            return {
                "ok": True,
                "stdout": "tcp_connected",
                "endpoint": values["endpoint"],
                "proxy_used": proxy is not None,
            }
    if probe.op not in {"https_head", "captive_check"}:
        raise ProbeRefused("probe_primitive_refused")
    tls = probe.op == "https_head"
    paths = context["https_paths" if tls else "portal_paths"]
    if not isinstance(paths, list) or len(paths) != 2:
        raise ProbeRefused("probe_endpoint_shape")
    request_path = str(paths[index])
    if not request_path.startswith("/") or any(c in request_path for c in "\r\n"):
        raise ProbeRefused("probe_endpoint_shape")
    port = 443 if tls else 80
    connection: http.client.HTTPConnection
    if tls:
        ca = context.get("ca_pem")
        if isinstance(ca, str):
            # The fixture CA is private to this run. Match its existing curl trust
            # contract while retaining required chain and hostname verification.
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.load_verify_locations(cadata=ca)
        else:
            ssl_context = ssl.create_default_context()
        address = proxy or (host, port)
        connection = http.client.HTTPSConnection(
            address[0],
            address[1],
            context=ssl_context,
            timeout=timeout,
            source_address=binding,
        )
        if proxy is not None:
            connection.set_tunnel(host, port)
    else:
        address = proxy or (host, port)
        connection = http.client.HTTPConnection(
            address[0],
            address[1],
            timeout=timeout,
            source_address=binding,
        )
        if proxy is not None:
            request_path = "http://" + host + request_path

    def connect(
        address: tuple[str, int],
        timeout: float,
        source_address: tuple[str, int] | None = None,
        *,
        all_errors: bool = False,
    ) -> socket.socket:
        return connect_socket(context, address, timeout, source_address, all_errors=all_errors)

    # http.client keeps this factory per connection, including CONNECT-proxy TLS.
    connection._create_connection = connect  # type: ignore[attr-defined]
    try:
        connection.request("HEAD" if tls else "GET", request_path, headers={"Host": host})
        response = connection.getresponse()
        body = b"" if tls else response.read(1024)
        expected = context["https_status" if tls else "portal_status"]
        if not isinstance(expected, list) or len(expected) != 2:
            raise ProbeRefused("probe_status_shape")
        matches = True
        if not tls:
            bodies = context["portal_bodies"]
            if not isinstance(bodies, list) or len(bodies) != 2:
                raise ProbeRefused("probe_portal_shape")
            matches = body.strip() == str(bodies[index]).encode().strip()
        return {
            "ok": response.status == expected[index] and matches,
            "http_status": response.status,
            "tls_verified": tls,
            "body_matches": matches,
            "proxy_used": proxy is not None,
            "stdout": f"HTTP status {response.status}; body_match={matches}; tls={tls}",
        }
    finally:
        connection.close()


def child(value: dict[str, object]) -> dict[str, object]:
    context = obj(value["context"])
    profile = str(context["profile"])
    if os.geteuid() == 0:
        raise ProbeRefused("probe_child_must_be_unprivileged")
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise ProbeRefused("live_probe_child_forbidden_in_tests")
    probes = validate_program(value["program"], profile, alternate=context.get("alternate") is True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (48, 48))
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))

    def deadline(_signum: int, _frame: object) -> None:
        raise TimeoutError("probe_timeout")

    signal.signal(signal.SIGALRM, deadline)
    rows = []
    begun = time.monotonic()
    for index, probe in enumerate(probes):
        start = time.monotonic()
        if (start - begun) * 1000 >= MAX_EPISODE_MS:
            rows.append(
                {
                    "index": index,
                    "probe": probe.as_json(),
                    "status": "refused",
                    "ok": False,
                    "reason": "probe_episode_budget_exceeded",
                    "elapsed_ms": 0.0,
                }
            )
            break
        row: dict[str, object] = {
            "index": index,
            "probe": probe.as_json(),
            "started_at": time.time(),
        }
        try:
            signal.setitimer(signal.ITIMER_REAL, probe.timeout_ms / 1000)
            result = primitive(probe, context, max(0.1, probe.timeout_ms / 1000 - 0.05))
            row.update(result)
            row["status"] = "completed"
        except (TimeoutError, subprocess.TimeoutExpired):
            row.update(status="timeout", ok=False, reason="probe_timeout")
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
            row.update(
                status="unavailable" if isinstance(exc, FileNotFoundError) else "failed",
                ok=False,
                reason=type(exc).__name__,
                stderr=str(exc)[:1024],
            )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        row.update(elapsed_ms=(time.monotonic() - start) * 1000, finished_at=time.time())
        rows.append(row)
    return {
        "version": 1,
        "uid": os.geteuid(),
        "rows": rows,
        "elapsed_ms": (time.monotonic() - begun) * 1000,
    }


def checked(argv: tuple[str, ...]) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
    if result.returncode:
        raise ProbeRefused("probe_sandbox_setup_refused")


def linux_sandbox(context: dict[str, object], directory: Path) -> tuple[str, ...]:
    if (
        platform.system() != "Linux"
        or os.geteuid() != 0
        or platform.node() != "lima-uplink-harness"
    ):
        raise ProbeRefused("owned_linux_probe_namespace_required")
    run_id = context.get("run_id")
    if (
        not isinstance(run_id, str)
        or len(run_id) != 12
        or any(c not in "0123456789abcdef" for c in run_id)
    ):
        raise ProbeRefused("probe_run_id_refused")
    target = Path("/var/run/netns") / ("ul-" + run_id + "-c")
    if not target.exists() or os.stat(target).st_ino != os.stat("/proc/self/ns/net").st_ino:
        raise ProbeRefused("owned_linux_probe_namespace_required")
    if os.stat("/proc/self/ns/mnt").st_ino == os.stat("/proc/1/ns/mnt").st_ino:
        raise ProbeRefused("private_probe_mount_namespace_required")
    jail = directory / "jail"
    jail.mkdir(mode=0o755)
    for name in ("usr", "lib", "lib64", "bin", "sbin"):
        source_path = Path("/") / name
        destination = jail / name
        if source_path.is_symlink():
            destination.symlink_to(os.readlink(source_path))
        elif source_path.is_dir():
            destination.mkdir(mode=0o755)
            checked(("/usr/bin/mount", "--bind", str(source_path), str(destination)))
            checked(("/usr/bin/mount", "-o", "remount,bind,ro", str(destination)))
    for name in ("etc", "dev", "app"):
        (jail / name).mkdir(mode=0o755)
    for name in ("resolv.conf", "nsswitch.conf", "hosts"):
        (jail / "etc" / name).write_bytes((Path("/etc") / name).read_bytes())
        (jail / "etc" / name).chmod(0o444)
    for name in ("null", "urandom"):
        destination = jail / "dev" / name
        destination.touch(mode=0o444)
        checked(("/usr/bin/mount", "--bind", "/dev/" + name, str(destination)))
    for name in ("probe_execution.py", "probe_protocol.py"):
        destination = jail / "app" / name
        destination.write_bytes(Path(__file__).with_name(name).read_bytes())
        destination.chmod(0o444)
    return (
        "/usr/bin/chroot",
        "--userspec=65534:65534",
        "--groups=65534",
        str(jail),
        "/usr/bin/setpriv",
        "--no-new-privs",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        "/app/probe_execution.py",
        "--child",
    )


def macos_sandbox(directory: Path) -> tuple[str, ...]:
    # Resolve admission from the same private authority as the live bridge. Public
    # naming changes must never replace a real device identity with a placeholder.
    if __package__:
        from .lab_config import configured_host
    else:
        from lab_config import configured_host  # type: ignore[import-not-found,no-redef]

    if platform.system() != "Darwin" or os.geteuid() == 0 or not configured_host():
        raise ProbeRefused("unprivileged_lab_probe_required")
    # The child is stdlib-only. A virtualenv's site initialization otherwise reads
    # pyvenv.cfg outside the sandbox, before our probe code can run. Use the real
    # interpreter and disable site, as the Linux sandbox already does.
    interpreter = Path(sys.executable).resolve()
    runtime = str(interpreter.parent.parent)
    reads = (
        "/System",
        "/usr",
        "/bin",
        "/sbin",
        "/opt/homebrew",
        "/Library/Apple",
        "/Library/Developer",
        "/Library/Preferences/SystemConfiguration",
        "/private/etc",
        str(Path(__file__).resolve().parent),
        runtime,
        str(directory),
    )
    profile = (
        "(version 1)(deny default)(allow process-exec)(allow process-fork)"
        "(allow sysctl-read)(allow mach-lookup)(allow network-outbound)(allow file-read-metadata)"
        "(allow file-read* " + " ".join("(subpath " + json.dumps(p) + ")" for p in reads) + ")"
        '(allow file-read* (literal "/") (literal "/dev/null") (literal "/dev/urandom"))'
        '(allow file-write* (literal "/dev/null"))'
    )
    return (
        "/usr/bin/sandbox-exec",
        "-p",
        profile,
        str(interpreter),
        "-I",
        "-S",
        "-B",
        str(Path(__file__).resolve()),
        "--child",
    )


def execute_sandbox(value: dict[str, object], directory: Path, timeout: float) -> dict[str, object]:
    context = obj(value["context"])
    profile = str(context["profile"])
    started = time.monotonic()
    argv = linux_sandbox(context, directory) if profile == "linux_lab" else macos_sandbox(directory)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        return {
            "status": "timeout",
            "reason": "probe_sandbox_setup_timeout",
            "rows": [],
            "elapsed_ms": (time.monotonic() - started) * 1000,
        }
    with subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    ) as process:
        try:
            stdout, stderr = process.communicate(json.dumps(value), timeout=remaining)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return {
                "status": "timeout",
                "reason": "probe_script_timeout",
                "rows": [],
                "elapsed_ms": (time.monotonic() - started) * 1000,
            }
        if process.returncode:
            return {
                "status": "refused",
                "reason": "probe_sandbox_child_refused",
                "rows": [],
                "exit_code": process.returncode,
                "stderr": stderr[:OUTPUT_LIMIT],
                "elapsed_ms": (time.monotonic() - started) * 1000,
            }
    result = obj(json.loads(stdout))
    if type(result.get("uid")) is not int or result["uid"] == 0:
        raise ProbeRefused("probe_privilege_drop_unproven")
    result["status"] = "completed"
    return result


def run_program(
    program: object,
    context: dict[str, object],
    budget: ProbeBudget,
    directory: Path,
    *,
    limit_ms: int = MAX_EPISODE_MS,
    on_admit: Callable[[], None] | None = None,
) -> dict[str, object]:
    started, wall = time.monotonic(), time.time()
    result: dict[str, object] = {
        "version": 1,
        "requested": program,
        "started_at": wall,
        "rows": [],
        "status": "refused",
        "profile": context["profile"],
    }
    try:
        probes = validate_program(
            program, str(context["profile"]), alternate=context.get("alternate") is True
        )
        budget.admit(probes)
        if on_admit is not None:
            on_admit()
        result["validated"] = {"version": 1, "probes": [p.as_json() for p in probes]}
        remaining = min(MAX_EPISODE_MS - budget.elapsed_ms, limit_ms)
        if remaining < sum(p.timeout_ms for p in probes):
            raise ProbeRefused("probe_episode_time_budget_exceeded")
        result.update(
            execute_sandbox(
                {"program": result["validated"], "context": context},
                directory,
                max(0.1, (remaining - 250) / 1000),
            )
        )
    except (ProbeRefused, OSError, ValueError) as exc:
        result.update(
            status="refused",
            reason=str(exc) if isinstance(exc, ProbeRefused) else type(exc).__name__,
            stderr=str(exc)[:OUTPUT_LIMIT],
        )
    elapsed = (time.monotonic() - started) * 1000
    budget.elapsed_ms += elapsed
    result.update(elapsed_ms=elapsed, finished_at=time.time(), budget=budget.as_json())
    return result


def write_receipt(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(".new")
    with temporary.open("x") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def episode(
    program: object,
    context: dict[str, object],
    directory: Path,
    *,
    limit_ms: int,
) -> dict[str, object]:
    """The parent reserves a durable budget before starting the sandbox child.

    An interrupted attempt consumes its reservation. Children cannot access the
    ledger. Full output is returned to the caller for privacy filtering and trace.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = directory / "budget.json"
        budget = (
            ProbeBudget.read(json.loads(ledger.read_text())) if ledger.exists() else ProbeBudget()
        )
        run = directory / ("script-" + uuid.uuid4().hex)
        run.mkdir(mode=0o700)
        write_receipt(run / "intent.json", {"program": program, "budget_before": budget.as_json()})
        result = run_program(
            program,
            context,
            budget,
            run,
            limit_ms=limit_ms,
            on_admit=lambda: write_receipt(ledger, budget.as_json()),
        )
        write_receipt(ledger, budget.as_json())
        write_receipt(
            run / "terminal.json",
            {
                "status": result["status"],
                "budget": budget.as_json(),
                "reason": result.get("reason"),
            },
        )
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--linux-launch", action="store_true")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    value = obj(json.loads(sys.stdin.read(131073)))
    if args.child:
        print(json.dumps(child(value)))
        return
    if not args.linux_launch or args.directory is None:
        raise ProbeRefused("explicit_probe_launcher_required")
    print(
        json.dumps(
            episode(
                value["program"],
                obj(value["context"]),
                args.directory,
                limit_ms=int(str(value["limit_ms"])),
            )
        )
    )


if __name__ == "__main__":
    main()
