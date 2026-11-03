"""Frozen Muse template plus eager GBNF on llama.cpp's raw completion route."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import math
import platform
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from offline_agent.contracts import Json, object_json, parse_action
from offline_agent.grammar import gbnf
from offline_agent.memory import SwapGuard
from offline_agent.providers import (
    Generation,
    HttpTransport,
    Message,
    ProviderError,
    ProviderTimeout,
    Transport,
)
from offline_agent.native_assets import template_assets

EOT = "<|eot|>"
CONTENT_PREFIX = "<|start|>assistant to=user<|message|>"


def native_prompt(
    root: Path, messages: Sequence[Message], *, reasoning_strength: str = "low"
) -> str:
    """Prefill the native recipient/header so the unchanged grammar starts at JSON."""
    if not messages or messages[-1].role not in {"user", "tool"}:
        raise ProviderError("native_prompt_requires_observation")
    source, bos = template_assets(root)
    template = ImmutableSandboxedEnvironment(undefined=StrictUndefined).from_string(source)
    rendered = template.render(
        messages=[m.as_json() for m in messages] + [Message("assistant", "").as_json()],
        bos_token=bos,
        tools=[],
        reasoning_strength=reasoning_strength,
        add_generation_prompt=False,
    )
    if not rendered.endswith(CONTENT_PREFIX + EOT):
        raise ProviderError("native_content_prefix_mismatch")
    return rendered.removesuffix(EOT)


def server_owned_prompt(
    root: Path, messages: Sequence[Message], *, reasoning_strength: str = "low"
) -> str:
    """The raw completion route adds BOS. Remove only the template-owned BOS."""
    rendered = native_prompt(root, messages, reasoning_strength=reasoning_strength)
    _, bos = template_assets(root)
    if not rendered.startswith(bos) or rendered.count(bos) != 1:
        raise ProviderError("native_prompt_requires_exactly_one_template_bos")
    return rendered.removeprefix(bos)


def native_action(content: str) -> str:
    # Permit exactly the observed terminal token, never arbitrary envelope/prose removal.
    text = content.removesuffix(EOT)
    try:
        parse_action(text)
    except ValueError as exc:
        raise ProviderError("invalid_native_action") from exc
    return text


def _token_count(value: Json) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderError("invalid_native_usage")
    return value


def parse_native_completion(response: dict[str, Json]) -> Generation:
    if (
        response.get("stop") is not True
        or response.get("stop_type") not in ("eos", "word")
        or response.get("truncated") is not False
        or response.get("stopped_limit") is True
    ):
        raise ProviderError("incomplete_native_completion")
    if response.get("stop_type") == "word" and response.get("stopping_word") != EOT:
        raise ProviderError("unexpected_native_stop_word")
    content = response.get("content")
    if not isinstance(content, str) or not content:
        raise ProviderError("missing_native_completion")
    try:
        timing = object_json(response.get("timings"))
        inputs = _token_count(response.get("tokens_evaluated"))
        outputs = _token_count(response.get("tokens_predicted"))
        if outputs != _token_count(timing.get("predicted_n")):
            raise ProviderError("inconsistent_native_usage")
    except ValueError as exc:
        raise ProviderError("invalid_native_response") from exc
    telemetry: dict[str, Json] = {}
    # b10353 timings.cache_n counts reused prompt tokens. tokens_cached includes
    # generated tokens in this build, so never use it as the reuse numerator.
    for key in ("cache_n", "prompt_n", "prompt_ms", "predicted_n", "predicted_ms"):
        value = timing.get(key)
        if value is not None:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ProviderError("invalid_native_timing")
            telemetry[key] = value
    return Generation(native_action(content), inputs, outputs, "provider_reported", telemetry)


def native_request(
    root: Path, messages: Sequence[Message], max_output_tokens: int
) -> dict[str, Json]:
    """One BOS owner, one serial KV slot, no implicit history compaction."""
    return {
        "prompt": server_owned_prompt(root, messages),
        "grammar": gbnf(),
        "grammar_lazy": False,
        "n_predict": max_output_tokens,
        "temperature": 0,
        "seed": 0,
        "stop": [EOT],
        "stream": False,
        "cache_prompt": True,
        "id_slot": 0,
    }


@dataclass
class NativeLlamaCppProvider:
    root: Path
    allow_remote_worker: bool = False
    endpoint: str = _public_fixture("http://@PUBLIC_IPV4_056@:8080/completion")
    guard: SwapGuard = field(default_factory=SwapGuard)
    transport: Transport = field(default_factory=HttpTransport)
    name: str = "llamacpp"
    allow_bench: bool = False

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "http"
            or url.hostname != _public_fixture("@PUBLIC_IPV4_056@")
            or url.username is not None
            or url.password is not None
            or url.path != "/completion"
            or url.query
            or url.fragment
            or url.port is None
        ):
            raise ProviderError("native_endpoint_refused")

    def generate(
        self, messages: Sequence[Message], max_output_tokens: int, timeout_s: float
    ) -> Generation:
        import sys

        remote = self.allow_remote_worker and sys.platform == "linux"
        bench = (
            self.allow_bench
            and sys.platform == "darwin"
            and platform.node().split(".", 1)[0].lower() == "bench"
        )
        if not (remote or bench):
            raise ProviderError("native_inference_requires_remote_linux_opt_in")
        deadline = time.monotonic() + timeout_s
        self.guard.check()
        body = native_request(self.root, messages, max_output_tokens)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeout()
        return parse_native_completion(self.transport.post(self.endpoint, body, {}, remaining))
