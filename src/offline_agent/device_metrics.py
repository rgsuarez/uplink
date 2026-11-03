"""Pure, fixture-testable spike measurements. Importing this module never loads a model."""

import math
from dataclasses import dataclass

from offline_agent.contracts import Json, object_json, parse_action


class SpikeRefused(ValueError):
    pass


def require_device_opt_in(allow_model_load: bool) -> None:
    if allow_model_load is not True:
        raise SpikeRefused("device_spike_disabled_requires_explicit_allow_model_load")


def _positive(value: Json) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise SpikeRefused("missing_or_invalid_native_timing")
    return float(value)


@dataclass(frozen=True)
class NativeTiming:
    prompt_tokens: int
    generated_tokens: int
    prompt_tokens_per_s: float
    generated_tokens_per_s: float
    valid_action: bool

    def as_json(self) -> dict[str, Json]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "generated_tokens": self.generated_tokens,
            "prompt_tokens_per_s": self.prompt_tokens_per_s,
            "generated_tokens_per_s": self.generated_tokens_per_s,
            "valid_action": self.valid_action,
        }


def parse_native_timing(response: dict[str, Json]) -> NativeTiming:
    content = response.get("content")
    if not isinstance(content, str):
        raise SpikeRefused("native_completion_missing")
    parse_action(content)
    timing = object_json(response.get("timings"))
    prompt = _positive(timing.get("prompt_n"))
    generated = _positive(timing.get("predicted_n"))
    prompt_ms = _positive(timing.get("prompt_ms"))
    generated_ms = _positive(timing.get("predicted_ms"))
    if not prompt.is_integer() or not generated.is_integer():
        raise SpikeRefused("nonintegral_token_count")
    prompt_rate = _positive(timing.get("prompt_per_second"))
    generated_rate = _positive(timing.get("predicted_per_second"))
    if not math.isclose(prompt_rate, prompt * 1000 / prompt_ms, rel_tol=0.05) or not math.isclose(
        generated_rate, generated * 1000 / generated_ms, rel_tol=0.05
    ):
        raise SpikeRefused("inconsistent_native_timing")
    if (
        response.get("stop") is not True
        or response.get("truncated") is True
        or response.get("stopped_limit") is True
        or response.get("stop_type") in ("limit", "none")
    ):
        raise SpikeRefused("incomplete_spike_completion")
    return NativeTiming(int(prompt), int(generated), prompt_rate, generated_rate, True)


def check_growth(baseline: int, current: int, max_growth_bytes: int) -> int:
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (baseline, current, max_growth_bytes)
    ):
        raise SpikeRefused("invalid_swap_monitor_sample")
    growth = max(0, current - baseline)
    if growth > max_growth_bytes:
        raise SpikeRefused("swap_growth_during_model_run")
    return growth


def rss_from_ps(output: str) -> int:
    text = output.strip()
    if not text.isdigit() or int(text) <= 0:
        raise SpikeRefused("unparsed_server_rss")
    return int(text) * 1024
