"""Memory-only raw process receipts. Only the trace redactor may serialize them."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawObservation:
    argv: tuple[str, ...] = field(repr=False)
    stdout: str = field(default="", repr=False)
    stderr: str = field(default="", repr=False)
    exit_code: int | None = None
    duration_ms: float = 0.0
    source: str = "command"
