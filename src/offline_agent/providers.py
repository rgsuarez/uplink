"""Mock by default. Every real provider requires an explicit admission flag."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from offline_agent.contracts import Json, Tool, action_text, object_json, read_json, tool_schema
from offline_agent.grammar import gbnf
from offline_agent.memory import SwapGuard


class ProviderError(RuntimeError):
    """Only safe reason codes cross this boundary."""

    def __init__(self, reason: str, *, diagnostic: dict[str, Json] | None = None) -> None:
        super().__init__(reason)
        self.diagnostic = diagnostic or {}


class ProviderTimeout(ProviderError):
    """A timed-out transport, distinct from HTTP and response failures."""

    def __init__(self) -> None:
        super().__init__("http_timeout")


def response_error_diagnostic(raw: bytes, headers: dict[str, str]) -> dict[str, Json]:
    """Allowlisted error fields only; credentials and complete headers are never retained."""
    try:
        error = object_json(object_json(read_json(raw.decode())).get("error"))
    except (ValueError, UnicodeError):
        return {"error_body": "unparsed"}
    diagnostic: dict[str, Json] = {}
    private = [
        part
        for value in headers.values()
        for part in (value, value.removeprefix("Bearer "))
        if part
    ]
    for name in ("type", "code", "param", "message"):
        value = error.get(name)
        if isinstance(value, str):
            for secret in private:
                value = value.replace(secret, "[REDACTED]")
            diagnostic[name] = "".join(char if char.isprintable() else " " for char in value)[:1200]
    return diagnostic


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def as_json(self) -> dict[str, Json]:
        return {"role": self.role, "content": self.content}


def prompt_bound(messages: Sequence[Message]) -> int:
    # One UTF-8 byte per token plus framing is deliberately conservative.
    return sum(len(m.content.encode()) + 64 for m in messages) + 64


@dataclass(frozen=True)
class Generation:
    text: str
    input_tokens: int
    output_tokens: int
    accounting: str
    telemetry: dict[str, Json] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def generate(
        self, messages: Sequence[Message], max_output_tokens: int, timeout_s: float
    ) -> Generation: ...


@dataclass
class MockProvider:
    actions: tuple[str, ...] = ()
    name: str = "mock"
    _index: int = 0

    def generate(
        self, messages: Sequence[Message], max_output_tokens: int, timeout_s: float
    ) -> Generation:
        if self._index < len(self.actions):
            action = self.actions[self._index]
            self._index += 1
        else:
            action = action_text(
                Tool.DECLARE_DONE, status="cannot_fix", reason="Mock has no further scripted action"
            )
        return Generation(action, prompt_bound(messages), len(action.encode()), "mock_byte_bound")


class Transport(Protocol):
    def post(
        self, url: str, body: dict[str, Json], headers: dict[str, str], timeout_s: float
    ) -> dict[str, Json]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise ProviderError("redirect_refused")


class HttpTransport:
    def post(
        self, url: str, body: dict[str, Json], headers: dict[str, str], timeout_s: float
    ) -> dict[str, Json]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout_s) as response:
                data = response.read(100_001)
            return object_json(read_json(data.decode()))
        except urllib.error.HTTPError as exc:
            # Retain useful status without logging headers, request bodies or response text.
            try:
                diagnostic = response_error_diagnostic(exc.read(16_001), headers)
            except OSError:
                diagnostic = {"error_body": "unavailable"}
            raise ProviderError(f"http_status_{exc.code}", diagnostic=diagnostic) from exc
        except TimeoutError as exc:
            raise ProviderTimeout() from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout() from exc
            raise ProviderError("http_or_response_failure") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProviderError("http_or_response_failure") from exc


def _usage_int(value: Json) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderError("invalid_usage")
    return value


def parse_completion(response: dict[str, Json]) -> Generation:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError("invalid_choices")
        choice = object_json(choices[0])
        if choice.get("finish_reason") != "stop":
            raise ProviderError("incomplete_completion")
        message = object_json(choice["message"])
        content = message["content"]
        if not isinstance(content, str) or not content:
            raise ProviderError("missing_completion")
        usage = object_json(response["usage"])
        inputs = _usage_int(usage["prompt_tokens"])
        outputs = _usage_int(usage["completion_tokens"])
        if inputs == 0 or outputs == 0:
            raise ProviderError("empty_usage")
        return Generation(content, inputs, outputs, "provider_reported")
    except (KeyError, ValueError) as exc:
        raise ProviderError("invalid_response") from exc


@dataclass
class SpendLedger:
    cap_usd: Decimal = Decimal("0")
    input_usd_per_million: Decimal = Decimal("0")
    output_usd_per_million: Decimal = Decimal("0")
    reserved_usd: Decimal = Decimal("0")
    calls: int = 0

    def reserve(self, input_bound: int, output_max: int) -> None:
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (input_bound, output_max)
            )
            or output_max == 0
        ):
            raise ProviderError("invalid_reservation_token_bounds")
        values = (self.cap_usd, self.input_usd_per_million, self.output_usd_per_million)
        if any(not v.is_finite() or v <= 0 for v in values):
            raise ProviderError("positive_spend_cap_and_rates_required")
        cost = (
            input_bound * self.input_usd_per_million + output_max * self.output_usd_per_million
        ) / Decimal(1_000_000)
        if self.reserved_usd + cost > self.cap_usd:
            raise ProviderError("spend_cap_exceeded")
        # Network failures retain the reservation because server-side charging is indeterminate.
        self.reserved_usd += cost
        self.calls += 1


@dataclass
class LlamaCppProvider:
    allow_local: bool = False
    endpoint: str = _public_fixture("http://@PUBLIC_IPV4_056@:8080/v1/chat/completions")
    model: str = "muse-glimmer-30B"
    constraint: str = "json_schema"
    guard: SwapGuard = field(default_factory=SwapGuard)
    transport: Transport = field(default_factory=HttpTransport)
    name: str = "llamacpp"

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        if (
            url.scheme != "http"
            or url.hostname not in {_public_fixture("@PUBLIC_IPV4_056@"), _public_fixture("@PUBLIC_IPV6_108@")}
            or url.username is not None
            or url.password is not None
            or url.path != "/v1/chat/completions"
            or url.query
            or url.fragment
        ):
            raise ProviderError("local_endpoint_refused")
        if self.constraint not in {"json_schema", "gbnf"}:
            raise ProviderError("unknown_constraint")

    def generate(
        self, messages: Sequence[Message], max_output_tokens: int, timeout_s: float
    ) -> Generation:
        if not self.allow_local:
            raise ProviderError("local_inference_requires_opt_in")
        deadline = time.monotonic() + timeout_s
        self.guard.check()
        body: dict[str, Json] = {
            "model": self.model,
            "messages": [m.as_json() for m in messages],
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "seed": 0,
        }
        if self.constraint == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "action", "strict": True, "schema": tool_schema()},
            }
        else:
            body["grammar"] = gbnf()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTimeout()
        return parse_completion(self.transport.post(self.endpoint, body, {}, remaining))


@dataclass
class MetaProvider:
    allow_live: bool = False
    ledger: SpendLedger = field(default_factory=SpendLedger)
    transport: Transport = field(default_factory=HttpTransport)
    model: str = "muse-spark-1.3"
    name: str = "meta"

    def generate(
        self, messages: Sequence[Message], max_output_tokens: int, timeout_s: float
    ) -> Generation:
        if not self.allow_live:
            raise ProviderError("live_inference_requires_opt_in")
        if self.model != "muse-spark-1.3":
            raise ProviderError("teacher_model_refused")
        key = os.environ.get("MODEL_API_KEY")
        if not key:
            raise ProviderError("model_api_key_missing")
        self.ledger.reserve(prompt_bound(messages), max_output_tokens)
        body: dict[str, Json] = {
            "model": self.model,
            "messages": [m.as_json() for m in messages],
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "action", "strict": True, "schema": tool_schema()},
            },
        }
        return parse_completion(
            self.transport.post(
                "https://api.meta.ai/v1/chat/completions",
                body,
                {"Authorization": f"Bearer {key}"},
                timeout_s,
            )
        )
