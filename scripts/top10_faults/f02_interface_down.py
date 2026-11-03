"""Frozen F02: Wi-Fi interface administratively down. Plan-only by default."""

from offline_agent.uplink.top10_cli import fault_main

if __name__ == "__main__":
    fault_main("F02")
