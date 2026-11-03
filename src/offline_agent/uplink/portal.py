"""Pure captive/application response stub. U1 never opens a listening socket."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StubResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


def response(mode: str) -> StubResponse:
    if mode == "captive_portal":
        return StubResponse(
            200,
            (("Content-Type", "text/html"),),
            b"<html><body>Laboratory portal: human action required.</body></html>",
        )
    if mode == "site_only_outage":
        return StubResponse(503, (("Content-Type", "text/plain"),), b"Laboratory app unavailable")
    if mode == "healthy":
        return StubResponse(204, (), b"")
    raise ValueError("unknown_stub_mode")


if __name__ == "__main__":
    raise SystemExit("U1 stub is response-only; namespace listener execution is disabled.")
