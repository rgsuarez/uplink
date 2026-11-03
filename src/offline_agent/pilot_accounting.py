"""Durable, value-blind per-call accounting for one bounded teacher pilot."""

import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.providers import ProviderError, SpendLedger


def usage_count(value: Json) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderError("pilot_usage_invalid")
    return value


@dataclass
class CallJournal:
    path: Path
    ledger: SpendLedger
    attempt: int = 0
    task_id: str = ""
    rows: list[dict[str, Json]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_usd: Decimal = Decimal("0")
    incomplete_usage_calls: int = 0

    def append(self, row: dict[str, Json]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.rows.append(row)

    def before_send(self, input_bound: int, output_bound: int, request_hash: str) -> float:
        self.append(
            {
                "event": "reserved_before_send",
                "call": self.ledger.calls,
                "attempt": self.attempt,
                "task_id": self.task_id,
                "request_sha256": request_hash,
                "input_token_bound": input_bound,
                "output_token_bound": output_bound,
                "reserved_spend_usd": str(self.ledger.reserved_usd),
                "spend_cap_usd": str(self.ledger.cap_usd),
            }
        )
        return time.monotonic()

    def after_response(
        self,
        response: dict[str, Json],
        start: float,
        input_bound: int,
        output_bound: int,
    ) -> None:
        row: dict[str, Json] = {
            "event": "response_received",
            "call": self.ledger.calls,
            "attempt": self.attempt,
            "task_id": self.task_id,
            "latency_seconds": time.monotonic() - start,
        }
        try:
            usage = object_json(response.get("usage"))
            inputs = usage_count(usage.get("prompt_tokens"))
            outputs = usage_count(usage.get("completion_tokens"))
            input_details = object_json(usage.get("prompt_tokens_details", {}))
            output_details = object_json(usage.get("completion_tokens_details", {}))
            cached = usage_count(input_details.get("cached_tokens", 0))
            reasoning = usage_count(output_details.get("reasoning_tokens", 0))
            if cached > inputs or reasoning > outputs or inputs == 0:
                raise ProviderError("pilot_usage_invalid")
        except (ValueError, ProviderError) as exc:
            self.incomplete_usage_calls += 1
            row.update({"usage_complete": False, "reason": "pilot_usage_invalid"})
            self.append(row)
            raise ProviderError("pilot_usage_invalid_reservation_retained") from exc
        cost = (
            inputs * self.ledger.input_usd_per_million
            + outputs * self.ledger.output_usd_per_million
        ) / Decimal(1_000_000)
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.cached_tokens += cached
        self.reasoning_tokens += reasoning
        self.estimated_usd += cost
        row.update(
            {
                "usage_complete": True,
                "input_tokens": inputs,
                "output_tokens": outputs,
                "cached_input_tokens": cached,
                "reasoning_tokens": reasoning,
                "estimated_cost_usd_no_cache_discount": str(cost),
                "cumulative_estimated_usd": str(self.estimated_usd),
            }
        )
        if (
            inputs > input_bound
            or outputs > output_bound
            or self.estimated_usd > self.ledger.cap_usd
        ):
            row["reason"] = "pilot_usage_exceeded_reserved_bounds"
            self.append(row)
            raise ProviderError("pilot_usage_exceeded_reserved_bounds")
        self.append(row)

    def failed(self, start: float, reason: str, diagnostic: dict[str, Json] | None = None) -> None:
        self.incomplete_usage_calls += 1
        self.append(
            {
                "event": "request_failed_reservation_retained",
                "call": self.ledger.calls,
                "attempt": self.attempt,
                "task_id": self.task_id,
                "reason": reason,
                "latency_seconds": time.monotonic() - start,
                "reserved_spend_usd": str(self.ledger.reserved_usd),
                "usage_complete": False,
                "diagnostic": diagnostic or {},
            }
        )

    def summary(self) -> dict[str, Json]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_cost_usd_no_cache_discount": str(self.estimated_usd),
            "usage_incomplete_calls": self.incomplete_usage_calls,
            "actual_provider_billed_usd": None,
            "reserved_spend_usd": str(self.ledger.reserved_usd),
            "spend_cap_usd": str(self.ledger.cap_usd),
            "price_input_usd_per_million": str(self.ledger.input_usd_per_million),
            "price_output_usd_per_million": str(self.ledger.output_usd_per_million),
        }
