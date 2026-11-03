"""Frozen F10: Stale IPv4 lease after a router/subnet change. Plan-only by default."""

from offline_agent.uplink.top10_cli import fault_main

if __name__ == "__main__":
    fault_main("F10")
