"""Frozen F03: DHCP failure / self-assigned 169.254 address. Plan-only by default."""

from offline_agent.uplink.top10_cli import fault_main

if __name__ == "__main__":
    fault_main("F03")
