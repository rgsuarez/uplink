"""Existing synthetic base-model receipt producer for file-only rendering tests."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from offline_agent.contracts import Json, object_json
from offline_agent.uplink import probe_serving as serving
from offline_agent.uplink.contracts import Action, Scope, Tool
from offline_agent.uplink.lab_runner import Emission
from offline_agent.uplink.model_identity import GGUF_SHA256, LLAMA_SERVER_BUILD
from offline_agent.uplink.oracle import OracleResult
from offline_agent.uplink.real_protocol import RealSpec
from offline_agent.uplink.real_runner import recover
from offline_agent.uplink.real_simulator import SimulatedLab
from offline_agent.uplink.records import RawObservation

from .probe_receipts import returned_measurement


def receipt(
    tmp_path: Path, *, arm: str = "base", mode: str = "open", false_online: bool = False
) -> Path:
    execution = returned_measurement()

    class Probed(SimulatedLab):
        def choose(self, scope: Scope, observed: dict[str, Json], oracle: OracleResult) -> Emission:
            emission = super().choose(scope, observed, oracle)
            action = object_json(json.loads(emission.text))
            if false_online:
                action = Action(Tool.DECLARE_ONLINE, {}).as_json()
            layer = "dns" if action["tool"] == "set_dns" else "proxy"
            after: dict[str, Json] = {"layer": layer, "evidence": ["dns-1"]}
            raw = json.dumps({"action": action, "hypothesis_after": after})
            episode: dict[str, Json] = {
                "program": execution["requested"],
                "execution": execution,
                "hypothesis_before": {"layer": "dns", "evidence": ["dns-1"]},
                "hypothesis_after": after,
                "action": action,
                "action_verbatim": raw,
                "action_mode": mode,
                "admitted_tools": [action["tool"], "report"],
                "serving": {
                    "version": serving.VERSION,
                    "arm": arm,
                    "base_gguf_sha256": GGUF_SHA256,
                    "adapter_gguf_sha256": None,
                    "adapter_count": 0,
                    "build": LLAMA_SERVER_BUILD,
                    "runtime_sha256": "a" * 64,
                    "checked_at": time.time() - 1,
                },
            }
            self.last_layer_probe = RawObservation(
                (),
                json.dumps(episode),
                exit_code=0,
                source="generated_probe_episode",
            )
            return Emission(json.dumps(action), emission.timings, raw_text=raw)

    spec = RealSpec(uuid.uuid4().hex[:12], "dns_and_proxy", 55, True)
    row = recover(Probed(spec), spec, tmp_path, confirm=lambda _: True)
    return tmp_path / (str(row["run_id"]) + "-receipt.json")
