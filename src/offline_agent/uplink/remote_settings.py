"""Configurable SSH aliases; changing the transport never changes the serial allowlist."""

import os
import re
from dataclasses import dataclass

from offline_agent.contracts import ContractError
from offline_agent.uplink import lab_config

SSH_OPTIONS = (
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=3",
    "-o",
    "ServerAliveInterval=2",
    "-o",
    "ServerAliveCountMax=1",
)


@dataclass(frozen=True)
class RemoteTargets:
    oob_host: str
    wifi_host: str

    def __post_init__(self) -> None:
        if any(
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,253}", value)
            for value in (self.oob_host, self.wifi_host)
        ):
            raise ContractError("ssh_alias_refused")
        if self.oob_host == self.wifi_host:
            raise ContractError("distinct_oob_and_wifi_routes_required")

    @classmethod
    def from_environment(cls, oob: str | None = None, wifi: str | None = None) -> "RemoteTargets":
        oob_value = oob if oob is not None else os.environ.get("UPLINK_OOB_HOST")
        wifi_value = wifi if wifi is not None else os.environ.get("UPLINK_WIFI_HOST")
        config = lab_config.load_config() if lab_config.CONFIG_PATH.exists() else None
        return cls(
            oob_value if oob_value is not None else (config.oob_host if config else "lab-oob"),
            wifi_value if wifi_value is not None else (config.wifi_host if config else "lab-wifi"),
        )

    @property
    def oob_ssh(self) -> tuple[str, ...]:
        return (*SSH_OPTIONS, self.oob_host)

    @property
    def wifi_ssh(self) -> tuple[str, ...]:
        return (*SSH_OPTIONS, "-o", "ControlMaster=no", "-o", "ControlPath=none", self.wifi_host)
