"""Only the pinned, unmodified base model may cross the serving boundary."""

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink import probe_serving as serving
from offline_agent.uplink import probe_trial_window as runtime
from offline_agent.uplink.model_identity import GGUF_NAME, LLAMA_SERVER_BUILD


def state(monkeypatch, loaded):
    values = {
        "props": {
            "model_path": "/models/" + GGUF_NAME,
            "build_info": LLAMA_SERVER_BUILD,
            "total_slots": 1,
        },
        "lora-adapters": loaded,
    }
    monkeypatch.setattr(serving, "get_json", values.__getitem__)
    monkeypatch.setattr(runtime, "runtime_digest", lambda: "a" * 64)


def test_pinned_unmodified_base_is_accepted(monkeypatch):
    state(monkeypatch, [])
    result = serving.require_serving({"arm": "base", "adapter_path": None})
    assert result["arm"] == "base"
    assert result["adapter_count"] == 0
    assert result["adapter_gguf_sha256"] is None


@pytest.mark.parametrize(
    "value",
    [
        {"arm": "unsupported", "adapter_path": None},
        {"arm": "base", "adapter_path": "/models/modified.gguf"},
    ],
)
def test_other_model_binding_is_denied_before_io(monkeypatch, value):
    def forbidden(_):
        pytest.fail("invalid binding must refuse before contacting a model server")

    monkeypatch.setattr(serving, "get_json", forbidden)
    with pytest.raises(ContractError):
        serving.require_serving(value)


@pytest.mark.parametrize("loaded", [None, {}, [{"path": "/models/modified.gguf"}]])
def test_missing_or_nonempty_modification_evidence_refuses(monkeypatch, loaded):
    state(monkeypatch, loaded)
    with pytest.raises(ContractError, match="probe_base_requires_empty_adapters"):
        serving.require_serving({"arm": "base", "adapter_path": None})


def test_unavailable_server_refuses(monkeypatch):
    def unavailable(_):
        raise OSError("synthetic unavailable server")

    monkeypatch.setattr(serving, "get_json", unavailable)
    with pytest.raises(OSError, match="synthetic unavailable"):
        serving.require_serving({"arm": "base", "adapter_path": None})
