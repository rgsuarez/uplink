"""Readable command display with literal privacy markers, separate from HMAC evidence."""

import ipaddress
import re
import shlex
from collections.abc import Mapping

MARKER = "«redacted»"
HANDLE = re.compile(r"\b[a-z]+-[0-9a-f]{24}\b")
MAC = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2}(?![0-9a-f:])")
HOST = re.compile(r"(?i)\b(?:[a-z][a-z0-9_-]*\.)+[a-z][a-z0-9_-]*\b")
IP_TOKEN = re.compile(
    r"(?<![\w:])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])|(?<![\w:])[0-9a-fA-F]*:[0-9a-fA-F:]+(?:%[a-z0-9]+)?"
)


def readable(text: str, *, identifiers: Mapping[str, set[str]], bindings: Mapping[str, str]) -> str:
    value = HANDLE.sub(lambda m: bindings.get(m[0], MARKER), text)
    value = re.sub(
        r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
        MARKER,
        value,
    )
    # Redact values, preserving the option/key that explains the command.
    value = re.sub(
        r"(?i)(\b(?:password|passwd|token|secret|api[_-]?key|authorization|cookie|keychain)\b\s*(?:=|:|\s)\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)",
        lambda m: m[1] + MARKER,
        value,
    )
    value = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9]+|hf_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16}|Bearer\s+\S+)",
        MARKER,
        value,
    )
    value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", lambda m: m[1] + MARKER + "@", value)
    for identifier, kinds in sorted(identifiers.items(), key=lambda row: len(row[0]), reverse=True):
        if not kinds.intersection(
            {"network", "mac", "host", "user", "domain", "identifier", "secret", "url"}
        ):
            continue
        # Private IPs are useful args; identifying hostnames are not.
        try:
            if not ipaddress.ip_address(identifier).is_global:
                continue
        except ValueError:
            pass
        if identifier:
            value = re.sub(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", MARKER, value)
    value = MAC.sub(MARKER, value)
    value = HOST.sub(MARKER, value)
    value = re.sub(r"/(?:Users|home)/[^/\s]+", "/home/" + MARKER, value)
    value = re.sub(r"\b[^\s@]+@[^\s@]+\b", MARKER, value)

    def ip(match: re.Match[str]) -> str:
        try:
            parsed = ipaddress.ip_address(match[0].split("%", 1)[0])
        except ValueError:
            return match[0]
        return MARKER if parsed.is_global else match[0]

    return IP_TOKEN.sub(ip, value)


def command(argv: tuple[str, ...]) -> str:
    return shlex.join(argv)
