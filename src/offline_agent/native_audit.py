"""Hash-bound source inspection only. This module cannot load a runtime library or model."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.release import scan_bytes
from offline_agent.artifact_files import json_file, string

MAX_SOURCE = 2_000_000


def source_excerpt(content: bytes, pin: dict[str, Json]) -> bytes:
    ranges = pin.get("selected_lines")
    if ranges is None:
        return content
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("nonempty_source_ranges_required")
    lines = content.splitlines(keepends=True)
    chunks: list[bytes] = []
    previous = 0
    for span in ranges:
        if not isinstance(span, list) or len(span) != 2:
            raise ValueError("invalid_source_line_range")
        first, last = span
        if (
            isinstance(first, bool)
            or not isinstance(first, int)
            or isinstance(last, bool)
            or not isinstance(last, int)
            or not previous < first <= last <= len(lines)
        ):
            raise ValueError("invalid_source_line_range")
        chunks.append(
            f"// Original source lines {first}-{last}\n".encode()
            + b"".join(lines[first - 1 : last])
        )
        previous = last
    return b"\n".join(chunks)


def verify_source(path: Path, pin: dict[str, Json]) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("pinned_source_missing:" + path.name)
    if path.stat().st_size > MAX_SOURCE:
        raise ValueError("static_source_size_limit")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != pin.get("sha256") or len(content) != pin.get("size"):
        raise ValueError("pinned_source_hash_mismatch:" + path.name)
    if scan_bytes(path.name, content):
        raise ValueError("source_cache_content_refused:" + path.name)
    text = content.decode()
    markers = pin.get("required_markers")
    if (
        not isinstance(markers, list)
        or not markers
        or any(not isinstance(marker, str) or marker not in text for marker in markers)
    ):
        raise ValueError("pinned_source_marker_missing:" + path.name)
    return text


def inspect_sources(root: Path, source_dir: Path, *, fetch: bool = False) -> dict[str, Json]:
    if not source_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError("project_local_source_cache_required")
    audit = json_file(root / "configs/native-source-audit.json")
    revision = string(audit.get("revision"))
    toolchain = json_file(root / "configs/remote-toolchain.json")
    sources = object_json(toolchain.get("native_sources"))
    runtime = object_json(sources.get("ggml-org/llama.cpp"))
    if audit.get("version") != 1 or runtime.get("revision") != revision:
        raise ValueError("source_audit_runtime_pin_mismatch")
    pins = audit.get("files")
    if not isinstance(pins, list) or not pins:
        raise ValueError("nonempty_static_source_pins_required")
    texts: dict[str, str] = {}
    verified: list[Json] = []
    for value in pins:
        pin = object_json(value)
        name = string(pin.get("file"))
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in texts:
            raise ValueError("invalid_or_duplicate_source_filename")
        url = string(pin.get("url"))
        if not url.startswith(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{revision}/"):
            raise ValueError("only_pinned_primary_source_urls_allowed")
        target = source_dir / name
        if fetch and not target.exists():
            source_dir.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(url, timeout=20) as response:
                content = response.read(MAX_SOURCE + 1)
            upstream_hash = pin.get("upstream_sha256", pin.get("sha256"))
            if len(content) > MAX_SOURCE or hashlib.sha256(content).hexdigest() != upstream_hash:
                raise ValueError("downloaded_source_hash_mismatch:" + name)
            content = source_excerpt(content, pin)
            if hashlib.sha256(content).hexdigest() != pin.get("sha256") or scan_bytes(
                name, content
            ):
                raise ValueError("downloaded_source_excerpt_refused:" + name)
            target.write_bytes(content)
        texts[name] = verify_source(target, pin)
        verified.append(
            {
                "file": name,
                "cache_sha256": pin["sha256"],
                "url": url,
                "upstream_sha256": pin.get("upstream_sha256", pin["sha256"]),
                "selected_lines": pin.get("selected_lines"),
            }
        )
    chat = texts.get("chat.cpp", "")
    start = chat.find("static common_chat_params common_chat_params_init_muse_glimmer(")
    end = chat.find("static json common_chat_extra_context()", start)
    if start < 0 or end < 0:
        raise ValueError("muse_specialized_handler_not_found")
    muse = chat[start:end]
    if "auto include_grammar = has_tools" not in muse:
        raise ValueError("muse_grammar_gate_changed")
    if "inputs.json_schema" in muse or "inputs.grammar" in muse:
        raise ValueError("muse_source_conclusion_changed_requires_new_audit")
    return {
        "version": 1,
        "status": "STATIC ONLY; UNVERIFIED UNTIL DEVICE SPIKE",
        "source_revision": revision,
        "sources_verified": verified,
        "model_weights_loaded": False,
        "native_library_loaded": False,
        "device_runtime_verified": False,
        "grammar_syntax_checked": False,
        "inferences": {
            "native_nonlazy_grammar_covers_entire_generated_output": True,
            "lazy_grammar_does_not_constrain_before_trigger": True,
            "response_format_is_not_a_universal_thinking_constraint": True,
            "muse_specialized_grammar_requires_tools": True,
            "muse_specialized_handler_consumes_json_schema": False,
            "current_json_action_chat_route_has_schema_enforcement_risk": True,
            "explicit_gbnf_chat_route_has_native_framing_risk": True,
        },
        "harness": {
            "executes_reasoning_content": False,
            "executes_only_schema_valid_actions": True,
            "empty_final_content": "provider_error:missing_completion",
            "non_json_or_thinking_in_content": "invalid action; never executed",
            "non_stop_completion": "provider_error:incomplete_completion",
            "unsafe_fixed_claim": "fixed_requires_current_green_tests",
            "strips_thinking_or_retries_unconstrained": False,
            "live_compatibility": "UNVERIFIED; device spike must resolve both route risks",
        },
        "documentation": "docs/GRAMMAR-THINKING.md",
    }


def write_audit(root: Path, source_dir: Path, *, fetch: bool = False) -> Path:
    target = root / "artifacts/native-thinking-check.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = inspect_sources(root, source_dir, fetch=fetch)
    except (OSError, ValueError) as exc:
        failure: dict[str, Json] = {
            "status": "REFUSED",
            "model_weights_loaded": False,
            "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
        }
        (target.parent / "native-thinking-failure.json").write_text(
            json.dumps(failure, indent=2) + "\n"
        )
        target.write_text(json.dumps(failure, indent=2) + "\n")
        raise
    target.write_text(json.dumps(report, indent=2) + "\n")
    return target
