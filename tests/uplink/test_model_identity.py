import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from offline_agent.contracts import ContractError
from offline_agent.uplink.model_identity import (
    GGUF_NAME,
    GGUF_SHA256,
    LLAMA_BUILD,
    LLAMA_SERVER_BUILD,
    MODEL_KEYS,
    MODEL_NAME,
    attest,
    expected_model,
    validate_identity,
)
from offline_agent.uplink.privacy import Redactor
from offline_agent.uplink.trace import RunTrace
from offline_agent.uplink.trace_format import read_trace, validate_record


def test_every_start_has_complete_pinned_but_truthful_model_block(
    tmp_path: Path, redactor: Redactor
) -> None:
    with RunTrace(redactor, directory=tmp_path) as trace:
        pass
    first = list(read_trace(trace.path))[0]
    identity = first["model"]
    assert isinstance(identity, dict)
    assert set(identity) == MODEL_KEYS
    assert (
        identity["name"] == MODEL_NAME
        and identity["gguf_filename"] == GGUF_NAME
        and identity["gguf_sha256"] == GGUF_SHA256
    )
    assert identity["verification"] == "expected" and identity["model_used"] is False
    assert identity["active_model_name"] is None


@pytest.mark.parametrize("missing", sorted(MODEL_KEYS))
def test_missing_model_field_is_rejected(missing: str) -> None:
    identity = expected_model(chip="Synthetic chip")
    del identity[missing]
    with pytest.raises(ContractError):
        validate_identity(identity)


@pytest.mark.parametrize(
    "field", ("gguf_sha256", "gguf_filename", "source_repo", "llama_cpp_build")
)
def test_drifted_provenance_cannot_be_a_badge(field: str) -> None:
    identity = expected_model(chip="Apple M4 Max")
    identity[field] = "synthetic-mismatch"
    with pytest.raises(ContractError):
        validate_identity(identity)


def test_live_inference_needs_fresh_bound_server_evidence() -> None:
    identity = expected_model(chip="Apple M4 Max")
    with pytest.raises(ContractError):
        validate_identity(identity, live=True)
    identity.update(verification="file_verified", verified_at=time.time())
    with pytest.raises(ContractError):
        validate_identity(identity, live=True)
    identity["verification"] = "server_verified"
    validate_identity(identity, live=True)
    identity["verified_at"] = time.time() - 301
    with pytest.raises(ContractError):
        validate_identity(identity, live=True)


def test_attestation_refuses_workstation_before_hash_or_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("platform.node", lambda: "workstation")
    spy = Mock(side_effect=AssertionError("must not read model"))
    monkeypatch.setattr("offline_agent.uplink.model_identity.file_sha256", spy)
    with pytest.raises(ContractError):
        attest(tmp_path / GGUF_NAME, tmp_path / "llama-server")
    spy.assert_not_called()


def test_attestation_checks_file_and_version_without_loading_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("offline_agent.uplink.model_identity.host_chip", lambda: "Apple M4 Max")
    model = tmp_path / GGUF_NAME
    model.write_bytes(b"synthetic-not-model-weights")
    binary = tmp_path / "llama-server"
    binary.write_text("synthetic")
    monkeypatch.setattr("offline_agent.uplink.model_identity.file_sha256", lambda path: GGUF_SHA256)
    spy = Mock(return_value=subprocess.CompletedProcess([], 0, "version: " + LLAMA_BUILD, ""))
    monkeypatch.setattr("subprocess.run", spy)
    proof = attest(model, binary, require_server=False)
    assert proof["verification"] == "file_verified"
    assert spy.call_args.args[0] == (str(binary), "--version")


def test_trace_validator_rejects_incomplete_model_provenance(
    tmp_path: Path, redactor: Redactor
) -> None:
    with RunTrace(redactor, directory=tmp_path) as trace:
        pass
    row = json.loads(trace.path.read_text().splitlines()[0])
    del row["model"]["host_chip"]
    with pytest.raises(ValueError):
        validate_record(row)


@pytest.mark.parametrize("build", (LLAMA_SERVER_BUILD, "b10352-wrong", None))
def test_live_attestation_binds_serving_build_without_a_second_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, build: str | None
) -> None:
    import io

    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("offline_agent.uplink.model_identity.host_chip", lambda: "Apple M4 Max")
    model = tmp_path / GGUF_NAME
    model.write_bytes(b"synthetic")
    binary = tmp_path / "llama-server"
    binary.write_text("synthetic")
    monkeypatch.setattr("offline_agent.uplink.model_identity.file_sha256", lambda path: GGUF_SHA256)
    process = Mock(side_effect=AssertionError("live attestation must not start another process"))
    monkeypatch.setattr("subprocess.run", process)
    reply = io.BytesIO(json.dumps({"model_path": str(model), "build_info": build}).encode())
    opener = Mock()
    opener.open.return_value = reply
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    if build == LLAMA_SERVER_BUILD:
        proof = attest(model, binary)
        assert proof["verification"] == "server_verified"
    else:
        with pytest.raises(ContractError, match="server_build_binding_unproven"):
            attest(model, binary)
    process.assert_not_called()


def test_file_attestation_preserves_build_probe_timeout_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    monkeypatch.setattr("platform.node", lambda: "lab")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("offline_agent.uplink.model_identity.host_chip", lambda: "Apple M4 Max")
    model = tmp_path / GGUF_NAME
    model.write_bytes(b"synthetic")
    binary = tmp_path / "llama-server"
    binary.write_text("synthetic")
    monkeypatch.setattr("offline_agent.uplink.model_identity.file_sha256", lambda path: GGUF_SHA256)
    cause = subprocess.TimeoutExpired([str(binary), "--version"], 5)
    monkeypatch.setattr("subprocess.run", Mock(side_effect=cause))
    with pytest.raises(ContractError, match="llama_build_probe_failed") as error:
        attest(model, binary, require_server=False)
    assert error.value.__cause__ is cause
