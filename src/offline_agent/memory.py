"""Fail-closed growth-based swap admission, independent of absolute swap use."""

import math
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class SwapRefused(RuntimeError):
    pass


def swap_used() -> int:
    if sys.platform == "linux":
        try:
            fields = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith(("SwapTotal:", "SwapFree:")):
                    key, value, unit = line.split()
                    if unit != "kB":
                        raise SwapRefused("swap_unit_unparsed")
                    fields[key.rstrip(":")] = int(value) * 1024
            return fields["SwapTotal"] - fields["SwapFree"]
        except (OSError, ValueError, KeyError) as exc:
            raise SwapRefused("swap_sampler_failed") from exc
    if sys.platform != "darwin":
        raise SwapRefused("swap_sampler_unavailable")
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "vm.swapusage"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SwapRefused("swap_sampler_failed") from exc
    match = re.search(r"used\s*=\s*([0-9.]+)([MKG])", result.stdout)
    if match is None:
        raise SwapRefused("swap_sample_unparsed")
    scale = {"K": 1024, "M": 1024**2, "G": 1024**3}[match[2]]
    return int(float(match[1]) * scale)


@dataclass(frozen=True)
class SwapVerdict:
    before_bytes: int
    after_bytes: int
    growth_bytes: int
    elapsed_s: float


@dataclass
class SwapGuard:
    sample: Callable[[], int] = swap_used
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    interval_s: float = 1.0
    max_growth_bytes: int = 64 * 1024**2
    max_growth_bytes_per_s: int = 32 * 1024**2

    def check(self) -> SwapVerdict:
        if (
            not math.isfinite(self.interval_s)
            or self.interval_s <= 0
            or self.max_growth_bytes < 0
            or self.max_growth_bytes_per_s < 0
        ):
            raise SwapRefused("invalid_swap_window")
        start = self.clock()
        before = self.sample()
        self.sleep(self.interval_s)
        after = self.sample()
        elapsed = self.clock() - start
        if (
            isinstance(before, bool)
            or not isinstance(before, int)
            or isinstance(after, bool)
            or not isinstance(after, int)
            or before < 0
            or after < 0
            or not math.isfinite(elapsed)
            or elapsed < self.interval_s * 0.9
        ):
            raise SwapRefused("invalid_swap_sample")
        growth = max(0, after - before)
        if growth > self.max_growth_bytes or growth / elapsed > self.max_growth_bytes_per_s:
            raise SwapRefused("swap_growth_exceeded")
        return SwapVerdict(before, after, growth, elapsed)

    def load(self, loader: Callable[[], object]) -> object:
        """Any future in-process loader must pass this gate first."""
        self.check()
        return loader()
