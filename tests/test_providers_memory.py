
from offline_agent.public_fixtures import expand_fixture_text as _public_fixture
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from offline_agent.contracts import Json, Tool, action_text
from offline_agent.memory import SwapGuard, SwapRefused
from offline_agent.providers import (
    LlamaCppProvider,
    Message,
    MetaProvider,
    ProviderError,
    SpendLedger,
    parse_completion,
)


def stable_guard(samples: Sequence[int] = (15 * 1024**3, 15 * 1024**3)) -> SwapGuard:
    values = iter(samples)
    times = iter((0.0, 1.0))
    return SwapGuard(sample=lambda: next(values), clock=lambda: next(times), sleep=lambda _: None)


@dataclass
class FakeTransport:
    bodies: list[dict[str, Json]] = field(default_factory=list)

    def post(
        self, url: str, body: dict[str, Json], headers: dict[str, str], timeout_s: float
    ) -> dict[str, Json]:
        self.bodies.append(body)
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": action_text(Tool.RUN_TESTS)}}
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15},
        }


def test_stable_high_swap_allowed_without_model_load() -> None:
    assert stable_guard().check().growth_bytes == 0


def test_swap_growth_blocks_loader() -> None:
    loaded: list[bool] = []
    with pytest.raises(SwapRefused, match="growth"):
        stable_guard((0, 128 * 1024**2)).load(lambda: loaded.append(True))
    assert loaded == []


def test_unknown_swap_fails_closed() -> None:
    with pytest.raises(SwapRefused):
        stable_guard((-1, -1)).check()


@pytest.mark.parametrize("constraint", ["json_schema", "gbnf"])
def test_local_constraints_sent_only_after_opt_in(constraint: str) -> None:
    transport = FakeTransport()
    provider = LlamaCppProvider(transport=transport, constraint=constraint)
    with pytest.raises(ProviderError, match="opt_in"):
        provider.generate([], 100, 1)
    assert not transport.bodies
    provider.allow_local = True
    provider.guard = stable_guard()
    assert provider.generate([Message("user", "repair")], 100, 1).output_tokens == 15
    key = "grammar" if constraint == "gbnf" else "response_format"
    assert key in transport.bodies[0]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/v1/chat/completions",
        "http://localhost:80/v1/chat/completions",
        _public_fixture("http://@PUBLIC_IPV4_056@:80/elsewhere"),
    ],
)
def test_nonlocal_or_ambiguous_endpoint_refused(endpoint: str) -> None:
    with pytest.raises(ProviderError):
        LlamaCppProvider(endpoint=endpoint)


def test_zero_spend_and_no_opt_in_refuse_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-test-only")
    transport = FakeTransport()
    provider = MetaProvider(transport=transport)
    with pytest.raises(ProviderError, match="opt_in"):
        provider.generate([], 100, 1)
    provider.allow_live = True
    with pytest.raises(ProviderError, match="spend_cap"):
        provider.generate([], 100, 1)
    assert not transport.bodies


def test_spend_reserved_before_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-test-only")
    transport = FakeTransport()
    ledger = SpendLedger(Decimal("0.001"), Decimal("1"), Decimal("1"))
    provider = MetaProvider(True, ledger, transport)
    provider.generate([], 100, 1)
    assert ledger.calls == 1
    assert ledger.reserved_usd == Decimal("0.000164")
    with pytest.raises(ProviderError, match="exceeded"):
        provider.generate([], 1000, 1)
    assert len(transport.bodies) == 1


def test_missing_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="key_missing"):
        MetaProvider(allow_live=True).generate([], 100, 1)


@pytest.mark.parametrize(
    "response", [{}, {"choices": []}, {"choices": [{"finish_reason": "length"}]}]
)
def test_invalid_or_degraded_provider_response(response: dict[str, Json]) -> None:
    with pytest.raises(ProviderError):
        parse_completion(response)
