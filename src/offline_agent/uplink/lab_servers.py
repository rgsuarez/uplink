"""Run-owned fixture endpoints, launched only inside the isolated Internet namespace."""

from offline_agent.public_fixtures import expand_fixture_text as _public_fixture

import argparse
import http.server
import json
import socket
import ssl
from pathlib import Path


class Handler(http.server.BaseHTTPRequestHandler):
    state: Path

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        state = json.loads(self.state.read_text())
        mode = state.get("stub", "healthy")
        if (
            mode == "walled_second"
            and "sealed_selection" in state
            and self.headers.get("Host", "").split(":", 1)[0] == "two.uplink-alt.test"
            and self.path in {"/ok", "/portal"}
        ):
            status, body = 302, b"Laboratory portal requires human action"
        elif self.path == "/portal" and mode == "captive":
            status, body = 200, b"Laboratory portal requires human action"
        elif self.path == "/app" and mode == "site_down":
            status, body = 503, b"Laboratory application unavailable"
        else:
            status, body = 204, b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, choices=(80, 443), required=True)
    parser.add_argument("--ipv6", action="store_true")
    args = parser.parse_args()
    Handler.state = args.state / "state.json"

    class IPv6Server(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6

    server_type = IPv6Server if args.ipv6 else http.server.ThreadingHTTPServer
    address = _public_fixture("@PUBLIC_IPV6_100@") if args.ipv6 else _public_fixture("@PUBLIC_IPV4_086@")
    server = server_type((address, args.port), Handler)
    if args.port == 443:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.state / "pki/server.pem", args.state / "pki/server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
