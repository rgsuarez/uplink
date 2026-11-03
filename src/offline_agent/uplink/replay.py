"""Read-only, standard-library terminal replay. No models, tools or network calls."""

import argparse
import json
import math
import re
import shlex
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

from offline_agent.uplink.trace_format import TraceFormatError, TraceJson, mapping, read_trace

COLORS = {
    "diagnose": "36",
    "plan": "34",
    "confirm": "33",
    "apply": "35",
    "verify": "32",
    "revert": "31",
    "report": "37",
    "start": "90",
    "finish": "90",
}


def terminal(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", lambda m: f"\\x{ord(m[0]):02x}", text)


def render(row: dict[str, TraceJson], *, color: bool) -> str:
    event = str(row["event"])
    phase = str(row.get("phase", event))
    action = row.get("action")
    tool = str(mapping(action)["tool"]) if action is not None else ""
    heading = f"{row['ts']} {str(row.get('step', '-')):>3} {phase.upper()} {tool}".rstrip()
    if color:
        heading = f"\x1b[{COLORS[phase]}m{heading}\x1b[0m"
    lines = [heading]
    if "display" in row:
        lines.append("  " + terminal(str(row["display"])))
    if event == "start":
        identity = mapping(row["model"])
        lines.append(
            "  mode: "
            + terminal(str(row["mode"]))
            + "; model: "
            + terminal(str(identity.get("active_model_name", identity["name"]) or "none"))
        )
        if "gguf_sha256" in identity:
            lines.append(
                "  artifact: " + str(identity["name"]) + " / " + str(identity["gguf_filename"])
            )
            lines.append(
                "  sha256: " + str(identity["gguf_sha256"]) + "; " + str(identity["verification"])
            )
        lines.append("  grammar: " + str(row["grammar_sha256"]))
    elif event == "step":
        observation = mapping(row["observation"])
        argv = cast(list[str], observation["argv"])
        if argv:
            lines.append("  $ " + terminal(shlex.join(argv)))
        for key in ("stdout", "stderr"):
            content = str(observation[key])
            if content:
                lines.extend("  " + terminal(line) for line in content.splitlines())
        choice = mapping(row["decision"])
        lines.append(f"  {choice['outcome']}: {terminal(str(choice['reason']))}")
        if row["confirm_card"] is not None:
            lines.append(
                "  ConfirmCard: " + terminal(json.dumps(row["confirm_card"], sort_keys=True))
            )
        if choice["timer"] is not None:
            lines.append("  timer: " + terminal(json.dumps(choice["timer"], sort_keys=True)))
        if row["config_diff"] is not None:
            lines.append(terminal(str(mapping(row["config_diff"])["unified"])))
        if row["oracle"] is not None:
            oracle = mapping(row["oracle"])
            lines.append(f"  oracle: {oracle['verdict']} ({oracle['latency_ms']} ms)")
        if row["model"] is not None:
            lines.append("  model: " + terminal(str(mapping(row["model"])["text"])))
            perf = mapping(row["perf"])
            lines.append(
                f"  timings: prompt={perf['prompt_n']} eval={perf['predicted_n']} "
                f"cached={perf['cached_n']} eval_tok/s={perf['predicted_per_second']}"
            )
    elif event == "finish":
        lines.append(f"  {row['verdict']}; {mapping(row['totals'])['steps']} steps")
    return "\n".join(lines) + "\n"


def replay(
    path: Path,
    *,
    speed: float = 1,
    color: bool = True,
    output: TextIO | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not math.isfinite(speed) or speed < 0:
        raise TraceFormatError("speed_must_be_finite_and_nonnegative")
    records = list(read_trace(path))  # Validate the whole file before rendering untrusted text.
    started = clock()
    initial = cast(float, records[0]["elapsed_ms"])
    stream = output or sys.stdout
    for row in records:
        if speed:
            target = (cast(float, row["elapsed_ms"]) - initial) / 1000 / speed
            remaining = target - (clock() - started)
            if remaining > 0:
                sleep(remaining)
        stream.write(render(row, color=color))
        stream.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("replay")
    command.add_argument("trace", type=Path)
    command.add_argument("--speed", type=float, default=1)
    command.add_argument("--no-color", action="store_true")
    args = parser.parse_args()
    try:
        replay(args.trace, speed=args.speed, color=not args.no_color)
    except TraceFormatError as exc:
        parser.exit(2, f"Trace error: {exc}\n")


if __name__ == "__main__":
    main()
