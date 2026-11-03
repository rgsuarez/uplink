"""Explicit local portal simulation using the standard-library HTTP server.

Loopback only, no forwarding, TLS interception, authentication or request logging.
The fixture is owned by the independent watchdog and dies with that process.
"""

from __future__ import annotations

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

from http.server import BaseHTTPRequestHandler, HTTPServer

BODY = b"UPLINK LOCAL PORTAL SIMULATION: sign-in required; no credentials accepted."


class Handler(BaseHTTPRequestHandler):
    timeout = 0.2
    server_version = "UplinkLocalFixture"
    sys_version = ""

    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "http://portal.invalid/login")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(BODY)
        self.close_connection = True

    def do_CONNECT(self) -> None:
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        # URLs and proxy headers can contain credentials. Never record them.
        return


def listener() -> HTTPServer:
    server = HTTPServer((_public_fixture("@PUBLIC_IPV4_056@"), 0), Handler)
    server.timeout = 0.05
    return server
