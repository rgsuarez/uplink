"""Private, exact-request cache. Never infer provider-side discounts or reuse partial replies."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from offline_agent.contracts import Json, object_json, read_json
from offline_agent.providers import Generation, ProviderError


def request_key(model: str, body: dict[str, Json]) -> str:
    canonical = json.dumps(
        {"version": 1, "model": model, "request": body}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class PromptCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Generation | None:
        self._validate_key(key)
        if self.directory.is_symlink():
            raise ProviderError("prompt_cache_symlink_refused")
        path = self.directory / f"{key}.json"
        if not path.exists():
            self.misses += 1
            return None
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ProviderError("unsafe_prompt_cache_permissions")
        row = object_json(read_json(path.read_text()))
        if row.get("version") != 1 or row.get("key") != key:
            raise ProviderError("prompt_cache_identity_mismatch")
        text = row.get("text")
        input_tokens, output_tokens = row.get("input_tokens"), row.get("output_tokens")
        if (
            not isinstance(text, str)
            or not text
            or isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens <= 0
            or isinstance(output_tokens, bool)
            or not isinstance(output_tokens, int)
            or output_tokens <= 0
        ):
            raise ProviderError("invalid_prompt_cache_record")
        expected = hashlib.sha256(
            json.dumps(
                {"text": text, "input_tokens": input_tokens, "output_tokens": output_tokens},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if row.get("content_sha256") != expected:
            raise ProviderError("prompt_cache_content_hash_mismatch")
        self.hits += 1
        return Generation(text, input_tokens, output_tokens, "cached_provider_reported")

    def put(self, key: str, generation: Generation) -> None:
        self._validate_key(key)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise ProviderError("prompt_cache_symlink_refused")
        data: dict[str, Json] = {
            "text": generation.text,
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
        }
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        data.update({"version": 1, "key": key, "content_sha256": digest})
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(data, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / f"{key}.json")
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _validate_key(key: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ProviderError("invalid_prompt_cache_key")
