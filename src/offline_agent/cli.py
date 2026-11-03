"""Base Muse Glimmer Uplink commands."""

import argparse
from pathlib import Path

from offline_agent.uplink.cli import add_parser, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser(subparsers)
    args = parser.parse_args()
    try:
        run(args, Path(__file__).resolve().parents[2])
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Refused: {exc}\n")


if __name__ == "__main__":
    main()
