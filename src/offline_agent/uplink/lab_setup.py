"""CLI for the run-owned Linux fixture. Dry-run unless --execute is explicit."""

from __future__ import annotations

import argparse
import json
import uuid

if __package__:
    from .lab_protocol import FAULTS, request
    from .lab_worker import dispatch
else:
    from lab_protocol import FAULTS, request  # type: ignore[no-redef,import-not-found]
    from lab_worker import dispatch  # type: ignore[no-redef,import-not-found]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "up",
            "check",
            "inject",
            "undo",
            "down",
            "smoke",
            "inject-wrong-dns",
            "restore-dns",
        ),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--fault", choices=FAULTS, default="wrong_dns")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or uuid.uuid4().hex[:12]
    if args.execute and args.run_id is None:
        parser.error("--run-id is required for execution")
    operation = {"inject-wrong-dns": "inject", "restore-dns": "undo"}.get(
        args.operation, args.operation
    )
    calls = (
        [request(operation, run_id, {"fault": args.fault} if operation == "inject" else {})]
        if operation != "smoke"
        else [
            request("up", run_id),
            request("check", run_id),
            request("arm", run_id, {"seconds": 20}),
            request("inject", run_id, {"fault": args.fault}),
            request("check", run_id),
            request("undo", run_id),
            request("check", run_id),
            request("down", run_id),
        ]
    )
    if operation == "inject":
        calls.insert(0, request("arm", run_id, {"seconds": 20}))
    for index, value in enumerate(calls):
        result = dispatch(value, execute=args.execute)
        print(json.dumps({"request": value, "result": result}), flush=True)
        if args.execute and operation == "smoke" and value["operation"] == "check":
            expected = index != 4 or args.fault in (
                "healthy",
                "misleading_status",
                "site_only_outage",
            )
            if result.get("healthy") is not expected:
                raise SystemExit("fault smoke verdict mismatch; stop")


if __name__ == "__main__":
    main()
