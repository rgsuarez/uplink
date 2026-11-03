"""Parse-time pseudonyms; raw identifiers are memory-only and never evidence text."""

import hashlib
import hmac
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from offline_agent.contracts import Json

MAC_PATTERN = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2}(?![0-9a-f:])")


@dataclass
class Redactor:
    salt: bytes = field(repr=False)
    _values: set[str] = field(default_factory=set, repr=False)
    _kinds: dict[str, set[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if len(self.salt) != 32:
            raise ValueError("redaction_salt_must_be_32_bytes")

    def handle(self, kind: str, value: str, *, remember: bool = True) -> str:
        if not re.fullmatch(r"[a-z]+", kind):
            raise ValueError("invalid_handle_kind")
        if kind == "host":
            try:
                ipaddress.ip_address(value)
            except ValueError:
                pass
            else:
                # IPs are intentional typed diagnostics, unlike hostnames. A proxy
                # IP can also legitimately appear in a route or DNS observation.
                remember = False
        if remember:
            self._values.add(value)
            self._kinds.setdefault(value, set()).add(kind)
        canonical = (
            ":".join(part.zfill(2).lower() for part in value.split(":"))
            if kind == "mac" and MAC_PATTERN.fullmatch(value)
            else value
        )
        digest = hmac.new(self.salt, (kind + "\0" + canonical).encode(), hashlib.sha256).hexdigest()
        return f"{kind}-{digest[:24]}"

    def scan(self, text: str) -> dict[str, Json]:
        # Value-blind: not even the offending field or neighboring bytes are returned.
        hits = [
            value
            for value in self._values
            if len(value) >= 4
            and (
                re.search(r"(?i)(?<![0-9a-f:])" + re.escape(value) + r"(?![0-9a-f:])", text)
                if MAC_PATTERN.fullmatch(value)
                else value.casefold() in text.casefold()
                if "serial" in self._kinds[value]
                else value in text
            )
        ]
        kinds = sorted({kind for value in hits for kind in self._kinds[value]})
        known = len(hits)
        macs = len(MAC_PATTERN.findall(text))
        return {
            "passed": known == 0 and macs == 0,
            "known_identifier_hits": known,
            "mac_pattern_hits": macs,
            "identifier_kinds": list(kinds),
        }

    @classmethod
    def local(cls, directory: Path) -> "Redactor":
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "salt.key"
        if path.is_symlink():
            raise ValueError("salt_symlink_refused")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.stat().st_mode & 0o077:
                raise ValueError("salt_permissions_refused") from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(os.urandom(32))
        return cls(path.read_bytes())

    def register_lab_identity(self) -> None:
        """Load private identity before any live receipt or trace can be emitted."""
        from offline_agent.uplink.lab_config import load_config

        self.handle("serial", load_config().approved_serial)

    def scrub_serials(self, text: str) -> str:
        # Serial numbers may occur inside argv, JSON keys, or longer identifiers.
        # Token boundaries are inappropriate here: every configured occurrence is private.
        for value, kinds in self._kinds.items():
            if "serial" in kinds:
                marker = self.handle("serial", value, remember=False)
                text = re.sub(re.escape(value), marker, text, flags=re.IGNORECASE)
        return text

    def scrub_serials_json(self, value: Json) -> Json:
        """Preserve typed receipt fields and authorization keys while hiding the serial."""
        if isinstance(value, str):
            return self.scrub_serials(value)
        if isinstance(value, list):
            return [self.scrub_serials_json(v) for v in value]
        if isinstance(value, dict):
            return {self.scrub_serials(k): self.scrub_serials_json(v) for k, v in value.items()}
        return value

    def learn_command(self, argv: tuple[str, ...], text: str) -> None:
        """Register identifiers before raw output can reach the trace boundary."""
        if len(argv) >= 2 and argv[1] == "-listallnetworkservices":
            for line in text.splitlines():
                if line.strip() and not line.startswith("An asterisk"):
                    self.handle("service", line.strip().removeprefix("*"))
        if len(argv) >= 2 and argv[1] == "-listpreferredwirelessnetworks":
            for line in text.splitlines()[1:]:
                if line.strip():
                    self.handle("network", line.strip())
        for value in re.findall(r"Hardware Port: ([^\n,)]+)", text):
            self.handle("port", value.strip())
        for value in re.findall(r"(?m)^\((?:\d+|\*)\) ([^\n]+)", text):
            self.handle("service", value.strip().removeprefix("*"))
        # Hidden identifiers inside DHCP/scutil summaries and proxy output.
        for key, value in re.findall(
            r"(?im)^\s*(SSID|BSSID|host_name|hostname|user(?:name)?|server|url|"
            r"client[_ ]?(?:id|identifier)|chaddr|mac|ethernet address)"
            r"[^:=\n]*[:=][ \t]*([^\n]+)",
            text,
        ):
            if value.strip():
                kind = (
                    "network"
                    if key.lower() == "ssid"
                    else "host"
                    if key.lower() == "server"
                    else "identifier"
                )
                self.handle(kind, value.strip())

    def scrub(self, text: str) -> str:
        """Preserve raw formatting, replacing only private or secret-bearing spans.

        Never attempt to log credentials/keychain payloads. Unknown secret-labelled
        lines and private-key blocks are replaced as a unit. This also protects
        adversarial model output, stderr and configuration diffs.
        """

        text = self.scrub_serials(text)

        def hidden(match: re.Match[str], kind: str) -> str:
            kinds = self._kinds.get(match[0], set())
            canonical = next(
                (
                    k
                    for k in ("network", "service", "user", "host", "domain", "mac", "port")
                    if k in kinds
                ),
                kind,
            )
            return self.handle(canonical, match[0], remember=False)

        value = re.sub(
            r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
            lambda m: hidden(m, "secret"),
            text,
        )
        value = re.sub(
            r"(?im)^.*\b(?:password|passwd|token|access[_ -]?token|api[_ -]?key|secret|cookie|"
            r"authorization|keychain)\b[^\n]*(?:\n|$)",
            lambda m: hidden(m, "secret") + ("\n" if m[0].endswith("\n") else ""),
            value,
        )
        value = re.sub(
            r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]+|hf_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16}|Bearer\s+\S+)",
            lambda m: hidden(m, "secret"),
            value,
        )
        value = re.sub(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", lambda m: hidden(m, "user"), value
        )
        # A URL can carry userinfo, secret query parameters, a hostname or a PAC path.
        value = re.sub(
            r"(?i)\b(?:https?|socks[45]h?)://[^\s\"'<>]+", lambda m: hidden(m, "url"), value
        )
        for identifier in sorted(self._values, key=len, reverse=True):
            if identifier:
                # Handles have their own namespace and are never rewritten recursively.
                boundary = "[0-9a-fA-F:]" if MAC_PATTERN.fullmatch(identifier) else "[A-Za-z0-9_-]"
                pattern = "(?<!" + boundary + ")" + re.escape(identifier) + "(?!" + boundary + ")"
                value = re.sub(pattern, lambda m: hidden(m, "identifier"), value)
        value = MAC_PATTERN.sub(lambda m: hidden(m, "mac"), value)
        value = re.sub(
            r"(?i)\b(?:[a-z][a-z0-9_-]*\.)+[a-z][a-z0-9_-]*\b", lambda m: hidden(m, "host"), value
        )
        value = re.sub(r"/(?:Users|home)/[^/\s]+", lambda m: "/home/" + hidden(m, "user"), value)
        return value

    def scrub_json(self, value: Json) -> Json:
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, list):
            return [self.scrub_json(v) for v in value]
        if isinstance(value, dict):
            return {self.scrub(k): self.scrub_json(v) for k, v in value.items()}
        return value

    def display(self, text: str) -> str:
        from offline_agent.uplink.display import readable

        bindings = {
            self.handle(kind, value, remember=False): value
            for value, kinds in self._kinds.items()
            for kind in kinds
            if kind in {"service", "port"}
        }
        return readable(self.scrub_serials(text), identifiers=self._kinds, bindings=bindings)

    def scan_trace(self, text: str) -> dict[str, Json]:
        import json

        # Display may intentionally retain service labels; machine evidence may not.
        machine: list[Json] = []
        displays: list[str] = []
        try:
            for line in text.splitlines():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("trace_row_required")
                displays.append(str(row.pop("display", "")))
                machine.append(row)
        except ValueError:
            return {
                "passed": False,
                "known_identifier_hits": 0,
                "mac_pattern_hits": 0,
                "identifier_kinds": [],
                "display_validation_failed": True,
            }
        scan = self.scan(json.dumps(machine))
        display_safe = all(self.display(value) == value for value in displays)
        scan["display_validation_failed"] = not display_safe
        scan["passed"] = bool(scan["passed"]) and display_safe
        return scan
