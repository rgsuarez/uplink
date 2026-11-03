"""Pinned native serving template and tokenizer metadata for base Muse Glimmer."""

from pathlib import Path

from offline_agent.contracts import object_json, read_json
from offline_agent.corpus import local_file, sha256

MODEL = "meta-models/Muse-Glimmer-30B"
REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
TEMPLATE_HASH = "cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678"


class AdmissionError(ValueError):
    pass


def template_assets(root: Path) -> tuple[str, str]:
    manifest = object_json(read_json(local_file(root, "templates/manifest.json").read_text()))
    if manifest.get("model") != MODEL or manifest.get("revision") != REVISION:
        raise AdmissionError("native_template_identity_mismatch")
    assets = object_json(manifest.get("assets"))
    for name, value in assets.items():
        record = object_json(value)
        if record.get("sha256") != sha256(local_file(root, name)):
            raise AdmissionError("native_asset_hash_mismatch")
    if sha256(local_file(root, "templates/glimmer_native.jinja")) != TEMPLATE_HASH:
        raise AdmissionError("native_template_hash_mismatch")
    config = object_json(read_json(local_file(root, "templates/tokenizer_config.json").read_text()))
    bos = config.get("bos_token")
    if not isinstance(bos, str) or not bos:
        raise AdmissionError("native_bos_missing")
    return local_file(root, "templates/glimmer_native.jinja").read_text(), bos
