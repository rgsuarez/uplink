"""Linux VM/netns injector skeleton. Dry-run only in U1, including on Linux as root.

No subprocess, namespace, network, daemon or host-file operation occurs in this module.
Commands below are plan data. U2 must implement isolation, watchdogs and cleanup first.
"""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import json
import os
import platform
from dataclasses import dataclass
from typing import NoReturn

from offline_agent.contracts import ContractError, Json, object_json, read_json
from offline_agent.uplink.contracts import Action, Scope, parse_action

FAULTS = (
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
)


@dataclass(frozen=True)
class Step:
    purpose: str
    argv: tuple[str, ...]
    undo_argv: tuple[str, ...]

    def as_json(self) -> dict[str, Json]:
        return {"purpose": self.purpose, "argv": list(self.argv), "undo_argv": list(self.undo_argv)}


@dataclass(frozen=True)
class FilePlan:
    path: str
    content: str
    undo: str

    def as_json(self) -> dict[str, Json]:
        return {"path": self.path, "content": self.content, "undo": self.undo}


@dataclass(frozen=True)
class InjectionPlan:
    fault: str
    seed: int
    namespaces: tuple[str, ...]
    setup: tuple[Step, ...]
    services: tuple[Step, ...]
    injection: tuple[Step, ...]
    files: tuple[FilePlan, ...]
    blockers: tuple[str, ...]

    def as_json(self) -> dict[str, Json]:
        return {
            "version": 1,
            "contract_version": 1,
            "mode": "dry_run",
            "fault": self.fault,
            "seed": self.seed,
            "namespaces": list(self.namespaces),
            "setup": [s.as_json() for s in self.setup],
            "services": [s.as_json() for s in self.services],
            "injection": [s.as_json() for s in self.injection],
            "files": [f.as_json() for f in self.files],
            "blockers": list(self.blockers),
            "executable": False,
            "cleanup": [list(("ip", "netns", "delete", n)) for n in reversed(self.namespaces)],
        }


def injection_plan(fault: str, seed: int = 0) -> InjectionPlan:
    if fault not in FAULTS or type(seed) is not int or not 0 <= seed <= 9999:
        raise ContractError("invalid_injection_selection")
    client, router, alternate = (f"u1-{seed}-{role}" for role in ("c", "r", "a"))
    namespaces = (client, router, alternate)
    setup = [
        Step("create owned namespace", ("ip", "netns", "add", n), ("ip", "netns", "delete", n))
        for n in namespaces
    ]
    for index, destination in enumerate((router, alternate)):
        left, right = f"u1c{seed}{index}", f"u1r{seed}{index}"
        subnet = "192.0.2" if index == 0 else "198.51.100"
        setup += [
            Step(
                "create veth pair",
                ("ip", "link", "add", left, "type", "veth", "peer", "name", right),
                ("ip", "link", "delete", left),
            ),
            Step(
                "move client endpoint",
                ("ip", "link", "set", left, "netns", client),
                ("ip", "-n", client, "link", "delete", left),
            ),
            Step(
                "move router endpoint",
                ("ip", "link", "set", right, "netns", destination),
                ("ip", "-n", destination, "link", "delete", right),
            ),
            Step(
                "set isolated gateway address",
                ("ip", "-n", destination, "address", "add", f"{subnet}.1/24", "dev", right),
                ("ip", "-n", destination, "address", "delete", f"{subnet}.1/24", "dev", right),
            ),
            Step(
                "enable router endpoint",
                ("ip", "-n", destination, "link", "set", right, "up"),
                ("ip", "-n", destination, "link", "set", right, "down"),
            ),
            Step(
                "enable client endpoint",
                ("ip", "-n", client, "link", "set", left, "up"),
                ("ip", "-n", client, "link", "set", left, "down"),
            ),
        ]
    for n in namespaces:
        setup.append(
            Step(
                "enable isolated loopback",
                ("ip", "-n", n, "link", "set", "lo", "up"),
                ("ip", "-n", n, "link", "set", "lo", "down"),
            )
        )
    prefix = ("ip", "netns", "exec", router)
    runtime = f"/run/uplink-u1-{seed}"
    services = (
        Step(
            "DNS and DHCP responder",
            (
                *prefix,
                "dnsmasq",
                "--keep-in-foreground",
                "--bind-interfaces",
                f"--interface=u1r{seed}0",
                "--no-resolv",
                _public_fixture("--dhcp-range=@PUBLIC_IPV4_068@,@PUBLIC_IPV4_070@,@PUBLIC_IPV4_092@,1h"),
                _public_fixture("--server=@PUBLIC_IPV4_003@"),
            ),
            (),
        ),
        Step(
            "explicit HTTP CONNECT proxy",
            (*prefix, "tinyproxy", "-d", "-c", f"{runtime}/proxy.conf"),
            (),
        ),
        Step(
            "captive stub (server wiring deferred)",
            (*prefix, "python3", "-m", "offline_agent.uplink.portal"),
            (),
        ),
    )
    file_plans = [
        FilePlan(
            f"/etc/netns/{client}/resolv.conf",
            _public_fixture("nameserver @PUBLIC_IPV4_066@\n"),
            "restore exact prior file or unlink owned new file",
        ),
        FilePlan(
            f"{runtime}/proxy.conf",
            _public_fixture("Port 8888\nListen @PUBLIC_IPV4_066@\nAllow @PUBLIC_IPV4_065@/24\n"),
            "unlink owned generated file",
        ),
        FilePlan(
            f"{runtime}/app-env.json",
            json.dumps(
                {"http_proxy": _public_fixture("http://@PUBLIC_IPV4_066@:8888"), "https_proxy": _public_fixture("http://@PUBLIC_IPV4_066@:8888")}
            ),
            "restore exact prior app environment",
        ),
    ]
    injection: list[Step] = []
    if fault in {"wrong_dns", "dns_plus_proxy"}:
        file_plans[0] = FilePlan(
            file_plans[0].path, _public_fixture("nameserver @PUBLIC_IPV4_073@\n"), _public_fixture("restore nameserver @PUBLIC_IPV4_066@")
        )
    if fault in {"stale_proxy", "dns_plus_proxy"}:
        file_plans[2] = FilePlan(
            file_plans[2].path,
            json.dumps({"http_proxy": _public_fixture("http://@PUBLIC_IPV4_066@:9"), "https_proxy": _public_fixture("http://@PUBLIC_IPV4_066@:9")}),
            "restore both prior app proxy variables",
        )
    if fault == "lease_loss":
        injection.append(
            Step(
                "remove only test lease address",
                ("ip", "-n", client, "address", "flush", "dev", f"u1c{seed}0"),
                (),
            )
        )
    if fault == "bad_default_route":
        injection.append(
            Step(
                "poison isolated default gateway",
                ("ip", "-n", client, "route", "replace", "default", "via", _public_fixture("@PUBLIC_IPV4_073@")),
                ("ip", "-n", client, "route", "replace", "default", "via", _public_fixture("@PUBLIC_IPV4_066@")),
            )
        )
    if fault in {"interface_down", "isp_down_saved_alternate"}:
        injection.append(
            Step(
                "disable primary test link",
                ("ip", "-n", client, "link", "set", f"u1c{seed}0", "down"),
                ("ip", "-n", client, "link", "set", f"u1c{seed}0", "up"),
            )
        )
    if fault == "mtu_black_hole":
        injection += [
            Step(
                "lower isolated MTU",
                ("ip", "-n", client, "link", "set", f"u1c{seed}0", "mtu", "1200"),
                ("ip", "-n", client, "link", "set", f"u1c{seed}0", "mtu", "1500"),
            ),
            Step(
                "loss approximation, not a true PMTU oracle",
                (
                    "ip",
                    "netns",
                    "exec",
                    client,
                    "tc",
                    "qdisc",
                    "add",
                    "dev",
                    f"u1c{seed}0",
                    "root",
                    "netem",
                    "loss",
                    "20%",
                ),
                ("ip", "netns", "exec", client, "tc", "qdisc", "del", "dev", f"u1c{seed}0", "root"),
            ),
        ]
    if fault in {"captive_portal", "site_only_outage", "misleading_status"}:
        file_plans.append(
            FilePlan(
                f"{runtime}/observation-mode.json",
                json.dumps({"scenario": fault}),
                "restore healthy stub mode",
            )
        )
    blockers = (
        "u1_execution_disabled",
        "real_linux_vm_required",
        "upstream_attachment_unimplemented",
        "namespace_dhcp_client_unimplemented",
        "portal_listener_and_redirect_unimplemented",
        "private_ca_or_live_endpoint_strategy_required",
        "watchdog_and_daemon_cleanup_required",
        "ownership_collision_and_host_invariance_checks_required",
    )
    return InjectionPlan(
        fault,
        seed,
        namespaces,
        tuple(setup),
        services,
        tuple(injection),
        tuple(file_plans),
        blockers,
    )


def validate_plan(text: str) -> InjectionPlan:
    value = object_json(read_json(text, max_bytes=100000))
    fault, seed = value.get("fault"), value.get("seed")
    if not isinstance(fault, str) or type(seed) is not int:
        raise ContractError("invalid_injection_plan")
    expected = injection_plan(fault, seed)
    if value != expected.as_json():
        raise ContractError("injection_plan_drift_or_arbitrary_command")
    return expected


@dataclass(frozen=True)
class LinuxAdapter:
    """Same action parser/schema as the Mac adapter; effects remain typed plan data."""

    scope: Scope
    seed: int = 0

    def plan(self, text: str) -> dict[str, Json]:
        action: Action = parse_action(text, self.scope)
        namespace = injection_plan("healthy", self.seed).namespaces[0]
        return {"namespace": namespace, "action": action.as_json(), "executable": False}


def require_execution(*, execute: bool, system: str, uid: int, under_test: bool) -> None:
    if under_test:
        raise ContractError("injector_never_executes_in_tests")
    if not execute:
        raise ContractError("explicit_execute_required")
    if system != "Linux":
        raise ContractError("real_linux_required")
    if uid != 0:
        raise ContractError("root_required_in_disposable_vm")


def execute_plan(plan: InjectionPlan, *, execute: bool = False) -> NoReturn:
    validate_plan(json.dumps(plan.as_json()))
    require_execution(
        execute=execute,
        system=platform.system(),
        uid=os.geteuid(),
        under_test="PYTEST_CURRENT_TEST" in os.environ,
    )
    raise ContractError("u1_skeleton_has_no_executor")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=FAULTS, default="healthy")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--validate", help="Validate a JSON plan file; never executes its argv")
    args = parser.parse_args()
    from pathlib import Path

    from offline_agent.uplink.contracts import Tool
    from offline_agent.uplink.privacy import Redactor
    from offline_agent.uplink.records import RawObservation
    from offline_agent.uplink.trace import RunTrace, decision

    directory = Path(__file__).resolve().parents[3] / "artifacts/uplink"
    redactor = Redactor.local(directory)
    with RunTrace(redactor, mode="dry_run") as trace:
        try:
            plan = (
                validate_plan(Path(args.validate).read_text())
                if args.validate
                else injection_plan(args.fault, args.seed)
            )
            if args.execute:
                execute_plan(plan, execute=True)
            text = json.dumps(plan.as_json(), indent=2)
            trace.step(
                "report",
                Action(Tool.GIVE_UP, {"reason": "needs_human"}),
                observation=RawObservation((), text, source="injection_plan_data"),
                choice=decision("dry_run_plan_emitted_execution_disabled", move="Report"),
            )
            print(text)
        except (ContractError, OSError):
            trace.step(
                "report",
                Action(Tool.GIVE_UP, {"reason": "needs_human"}),
                choice=decision("injection_refused", accepted=False, move="Report"),
            )
            parser.exit(
                2,
                "Injection refused: U1 is dry-run only; plan or execution preconditions failed.\n",
            )


if __name__ == "__main__":
    main()
