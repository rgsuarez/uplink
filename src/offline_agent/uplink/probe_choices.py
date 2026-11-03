"""Evidence-bound Wi-Fi probe choices and repair explanations, never new effect authority."""

from offline_agent.contracts import ContractError, Json, object_json
from offline_agent.uplink.contracts import Scope, Tool, enum
from offline_agent.uplink.layer_policy import layer_action_schema

PROBE_LAYERS = {"dns_query": "dns", "https_head": "proxy", "captive_check": "captive"}
KINDS = {"dns": ("dns",), "proxy": ("tls",), "captive": ("portal",)}


def evidence_ids(facts: dict[str, Json], layer: str) -> tuple[str, ...]:
    """Cite observed drift or completed measurements, never a requested probe."""
    result = []
    for name, value in facts.items():
        if not isinstance(value, dict):
            continue
        probe = value.get("probe")
        if (
            name == "snapshot-" + layer
            and value.get("changed") is True
            or value.get("kind") in KINDS[layer]
            and type(value.get("ok")) is bool
            or isinstance(probe, dict)
            and PROBE_LAYERS.get(str(probe.get("op"))) == layer
            and value.get("status") == "completed"
        ):
            result.append(name)
    return tuple(result)


def targeted_wifi_probes(program: dict[str, Json], facts: dict[str, Json]) -> list[Json]:
    """Offer short real measurements; state-only reads remain valid for healthy controls."""
    fields = object_json(program["properties"])
    probes = object_json(fields["probes"])
    variants = object_json(probes["items"])["oneOf"]
    assert isinstance(variants, list)
    selected: list[Json] = []
    choices: list[Json] = []
    purposes = {
        "dns_query": "Resolve an approved name using the configured resolver.",
        "https_head": (
            "Check HTTPS on the current Wi-Fi path using its effective proxy route; "
            "do not bypass the proxy."
        ),
        "captive_check": "Check the approved captive endpoint on its current effective route.",
    }
    for item in variants:
        variant = object_json(item)
        args = object_json(variant["properties"])
        op = str(object_json(args["op"])["const"])
        if op not in PROBE_LAYERS:
            continue
        ids = evidence_ids(facts, PROBE_LAYERS[op])
        if not ids:
            continue
        args["timeout_ms"] = {"const": 1000}
        example: dict[str, Json] = {"op": op, "timeout_ms": 1000}
        if op == "dns_query":
            args["resolver"] = {"const": "configured"}
            example.update(name="one", resolver="configured")
        else:
            example["endpoint"] = "one"
            if op == "https_head":
                example["path"] = "current"
        selected.append(variant)
        choices.append(
            {
                "layer": PROBE_LAYERS[op],
                "evidence_ids": list(ids),
                "purpose": purposes[op],
                "probe": example,
            }
        )
    if not selected:
        raise ContractError("targeted_probe_evidence_unavailable")
    probes.update(items={"oneOf": selected}, minItems=1, maxItems=3)
    return choices


def wifi_repair_schema(
    schema: dict[str, Json], scope: Scope, tools: tuple[Tool, ...], facts: dict[str, Json]
) -> dict[str, Json]:
    """Correlate the explanation and citation with the selected measured restore.

    This does not relabel model output or select a repair. The existing admission
    policy supplies tools; invalid explanations are refused and retained verbatim.
    """
    variants: list[Json] = []
    original = object_json(schema["properties"])
    for tool in tools:
        hypothesis = original["hypothesis_after"]
        action = layer_action_schema(scope, (tool,), snapshot_only=True)
        layer = {Tool.SET_DNS: "dns", Tool.RESTORE_PROXY: "proxy"}.get(tool)
        if layer is not None:
            citations = evidence_ids(facts, layer)
            oracle_ids = tuple(
                name
                for name in citations
                if name in scope.evidence
                and isinstance(facts[name], dict)
                and object_json(facts[name]).get("kind") in KINDS[layer]
            )
            if not citations or not oracle_ids:
                raise ContractError("repair_layer_evidence_unavailable")
            hypothesis = {
                "type": "object",
                "properties": {
                    "layer": {"const": layer},
                    "evidence": {
                        "type": "array",
                        "items": enum(citations),
                        "minItems": 1,
                        "maxItems": 1,
                    },
                },
                "required": ["layer", "evidence"],
                "additionalProperties": False,
            }
            actions = action["oneOf"]
            assert isinstance(actions, list) and len(actions) == 1
            args = object_json(object_json(object_json(actions[0])["properties"])["args"])
            object_json(args["properties"])["evidence_ids"] = {
                "type": "array",
                "items": enum(oracle_ids),
                "minItems": 1,
                "maxItems": 1,
            }
        variants.append(
            {
                "type": "object",
                "properties": {"hypothesis_after": hypothesis, "action": action},
                "required": ["hypothesis_after", "action"],
                "additionalProperties": False,
            }
        )
    return {"oneOf": variants}
