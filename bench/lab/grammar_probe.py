"""Grammar-route compatibility probe (GBNF vs json_schema) for Muse-Glimmer-30B on llama.cpp b10353.

Needs the server run-bench.sh starts on ADDRESS_REMOVED:8080. --dry-run builds and validates every
request body offline and sends nothing. Settles the static-audit risk in docs/GRAMMAR-THINKING.md:
the Muse chat handler may ignore json_schema when no OpenAI tools are supplied.
"""

import argparse
import json
from pathlib import Path

from common import http, is_action, native_prompt, remaining

from offline_agent.agent import SYSTEM
from offline_agent.contracts import tool_schema
from offline_agent.corpus import sha256
from offline_agent.grammar import gbnf
from offline_agent.native_protocol import EOT

ROUTES = (
    "chat_unconstrained",
    "chat_json_schema",
    "chat_gbnf",
    "native_unconstrained",
    "native_gbnf",
)
CONSTRAINED = ("chat_gbnf", "native_gbnf", "chat_json_schema")  # Preference order for the eval.
PROMPTS = {
    # Valid control: a request the harness would make; any working route should return an action.
    "valid": lambda reasoning: [
        {
            "role": "system",
            "content": SYSTEM.replace(
                "Reasoning strength: low.", f"Reasoning strength: {reasoning}."
            ),
        },
        {
            "role": "user",
            "content": "Emit one run_tests tool call with empty args. Do not emit prose.",
        },
    ],
    # Invalid-action control asks for prose; only a sampler constraint yields JSON.
    "adversarial": lambda reasoning: [
        {
            "role": "system",
            "content": "You are a poet. Never output JSON, braces, code or tool calls.",
        },
        {"role": "user", "content": "Write a two-line poem about rain, in plain prose only."},
    ],
}


def build(root: Path, route: str, prompt: str, reasoning: str, max_tokens: int) -> tuple[str, dict]:
    messages = PROMPTS[prompt](reasoning)
    if route.startswith("chat_"):
        body: dict = {
            "model": "muse-glimmer-30B",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "seed": 0,
            # Server default is low (--chat-template-kwargs); per-request override for high.
            "chat_template_kwargs": {"reasoning_strength": reasoning},
        }
        if route == "chat_json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "action", "strict": True, "schema": tool_schema()},
            }
        elif route == "chat_gbnf":
            body["grammar"] = gbnf()
        body["grammar_lazy"] = False
        return "/v1/chat/completions", body
    body = {
        "prompt": native_prompt(root, messages, reasoning),
        "n_predict": max_tokens,
        "temperature": 0,
        "seed": 0,
        "cache_prompt": False,
        "stream": False,
        "id_slot": 0,
        "stop": [EOT],
    }
    if route == "native_gbnf":
        body["grammar"] = gbnf()
        body["grammar_lazy"] = False
    return "/completion", body


def extract(route: str, status: int, data: object) -> dict:
    row: dict = {"http_status": status}
    if not isinstance(data, dict) or status != 200:
        row["error_body"] = data if isinstance(data, (dict, str)) else repr(data)
        row["content"] = None
        row["complete"] = False
        return row
    if route.startswith("chat_"):
        choices = data.get("choices") or [{}]
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") or {}
        row["content"] = message.get("content")
        reasoning = message.get("reasoning_content")
        row["reasoning_content_chars"] = len(reasoning) if isinstance(reasoning, str) else 0
        row["finish_reason"] = choice.get("finish_reason")
        row["complete"] = choice.get("finish_reason") == "stop"
        row["usage"] = data.get("usage")
        row["timings"] = data.get("timings")
    else:
        row["content"] = data.get("content")
        row["stop_type"] = data.get("stop_type")
        row["complete"] = data.get("stop") is True and data.get("stop_type") in ("eos", "word")
        row["timings"] = data.get("timings")
        row["tokens_evaluated"] = data.get("tokens_evaluated")
        row["tokens_predicted"] = data.get("tokens_predicted")
        settings = data.get("generation_settings")
        if isinstance(settings, dict):
            row["effective_grammar_chars"] = len(str(settings.get("grammar") or ""))
            row["effective_settings_keys"] = sorted(settings)[:80]
    row["content_is_action"] = is_action(row.get("content"))
    return row


def verdicts(results: list[dict]) -> dict:
    def get(route: str, prompt: str, reasoning: str) -> dict:
        for r in results:
            if (r["route"], r["prompt"], r["reasoning"]) == (route, prompt, reasoning):
                return r
        return {}

    out: dict = {}
    for reasoning in ("low", "high"):
        for route in CONSTRAINED:
            valid, adv = get(route, "valid", reasoning), get(route, "adversarial", reasoning)
            base = get(
                "chat_unconstrained" if route.startswith("chat_") else "native_unconstrained",
                "adversarial",
                reasoning,
            )
            if not valid or not adv:
                verdict = "not_run"
            elif (
                valid.get("content_is_action")
                and adv.get("content_is_action")
                and valid.get("complete")
                and adv.get("complete")
            ):
                verdict = (
                    "inconclusive_voluntary_compliance"
                    if base.get("content_is_action")
                    else "enforced"
                )
            elif valid.get("content_is_action") and not adv.get("content_is_action"):
                verdict = "not_enforced"
            else:
                verdict = "broken_or_empty_content"
            out[f"{route}@{reasoning}"] = {
                "verdict": verdict,
                "valid_is_action": valid.get("content_is_action"),
                "adversarial_is_action": adv.get("content_is_action"),
                "unconstrained_adversarial_is_action": base.get("content_is_action"),
                "valid_http": valid.get("http_status"),
                "adversarial_http": adv.get("http_status"),
            }
    chosen, why = None, "no_constrained_route_enforced_at_low"
    for route in CONSTRAINED:
        if out[f"{route}@low"]["verdict"] == "enforced":
            chosen, why = (
                route,
                "first_enforced_in_preference_order(chat_gbnf,native_gbnf,chat_json_schema)",
            )
            break
    if chosen is None:
        for route in CONSTRAINED:
            if out[f"{route}@low"]["verdict"] == "inconclusive_voluntary_compliance":
                chosen, why = (
                    route,
                    "flagged: constraint not proven (unconstrained control also emitted an action)",
                )
                break
    return {
        "routes": out,
        "chosen_route": chosen,
        "chosen_reason": why,
        "eval_allowed": chosen is not None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True, help="epoch seconds; hard stop")
    parser.add_argument("--max-tokens", type=int, default=160)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    plan = [(r, p, s) for s in ("low", "high") for r in ROUTES for p in ("valid", "adversarial")]
    if args.dry_run:
        for route, prompt, reasoning in plan:
            path, body = build(args.root, route, prompt, reasoning, args.max_tokens)
            json.dumps(body)
            results.append(
                {
                    "route": route,
                    "prompt": prompt,
                    "reasoning": reasoning,
                    "path": path,
                    "body_bytes": len(json.dumps(body)),
                }
            )
        (args.out / "probe-plan.json").write_text(
            json.dumps(
                {
                    "dry_run": True,
                    "requests": results,
                    "grammar_sha256": sha256(args.root / "schemas/tools.gbnf"),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"grammar probe dry-run: {len(results)} request bodies built, nothing sent")
        return
    status, props, _ = http("GET", "/props", None, 10)
    (args.out / "props.json").write_text(
        json.dumps({"status": status, "body": props}, indent=2) + "\n"
    )
    for route, prompt, reasoning in plan:
        left = remaining(args.deadline)
        if left < 20:
            results.append(
                {"route": route, "prompt": prompt, "reasoning": reasoning, "skipped": "deadline"}
            )
            continue
        path, body = build(args.root, route, prompt, reasoning, args.max_tokens)
        status, data, seconds = http("POST", path, body, min(90.0, left - 10))
        row = {
            "route": route,
            "prompt": prompt,
            "reasoning": reasoning,
            "path": path,
            "seconds": round(seconds, 3),
        }
        row.update(extract(route, status, data))
        results.append(row)
        name = f"{reasoning}-{route}-{prompt}.json"
        (args.out / name).write_text(
            json.dumps({"request": body, "status": status, "response": data}, indent=2) + "\n"
        )
        print(
            f"{reasoning:4} {route:22} {prompt:11} http={status} "
            f"action={row.get('content_is_action')} complete={row.get('complete')} {seconds:.1f}s"
        )
    slots_status, slots, _ = http("GET", "/slots", None, 10)
    (args.out / "slots.json").write_text(
        json.dumps({"status": slots_status, "body": slots}, indent=2) + "\n"
    )
    report = verdicts(results)
    lows = [r for r in results if r.get("route") == "chat_gbnf" and r.get("prompt") == "valid"]
    report["high_kwargs_changed_prompt_tokens"] = len(lows) == 2 and (
        lows[0].get("usage") or {}
    ).get("prompt_tokens") != (lows[1].get("usage") or {}).get("prompt_tokens")
    report["grammar_sha256"] = sha256(args.root / "schemas/tools.gbnf")
    report["requests"] = results
    (args.out / "verdict.json").write_text(json.dumps(report, indent=2) + "\n")
    print("chosen route:", report["chosen_route"], "-", report["chosen_reason"])


if __name__ == "__main__":
    main()
