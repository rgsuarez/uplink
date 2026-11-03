"""Aggregate a bench run into JSON, Markdown and measured-only device receipts."""

import argparse
import csv
import json
import re
from contextlib import suppress
from pathlib import Path

GIB = 1024**3
PAGE = 16384


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def time_l(path: Path) -> dict:
    out: dict = {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for key, label in (
        ("max_rss_bytes", "maximum resident set size"),
        ("peak_footprint_bytes", "peak memory footprint"),
    ):
        m = re.findall(rf"^\s*(\d+)\s+{label}", text, re.M)
        if m:
            out[key] = int(m[-1])
    m = re.findall(r"^\s*([0-9.]+) real", text, re.M)
    if m:
        out["real_seconds"] = float(m[-1])
    return out


def samples(path: Path) -> dict:
    try:
        rows = list(csv.DictReader(path.open()))
    except OSError:
        return {}
    if not rows:
        return {}

    def col(name):
        vals = []
        for r in rows:
            with suppress(KeyError, TypeError, ValueError):
                vals.append(float(r[name]))
        return vals

    swap = col("swap_used_mb")
    used = [
        (float(r["anonymous"]) + float(r["wired"]) + float(r["compressor"])) * PAGE
        for r in rows
        if r.get("anonymous")
    ]
    free = col("mp_free_pct")
    rss = col("llama_rss_kb")
    return {
        "samples": len(rows),
        "interval_note": "about 2 s",
        "peak_llama_rss_bytes": int(max(rss) * 1024) if rss else None,
        "peak_anon_wired_compressed_bytes": int(max(used)) if used else None,
        "baseline_swap_mb": swap[0] if swap else None,
        "peak_swap_mb": max(swap) if swap else None,
        "swap_growth_mb": (max(swap) - swap[0]) if swap else None,
        "min_memory_pressure_free_pct": min(free) if free else None,
    }


def fmt(v, unit=""):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2f}{unit}"
    return f"{v}{unit}"


def gib(v):
    return "n/a" if v is None else f"{v / GIB:.2f} GiB"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--spark-ref", type=Path, required=True)
    a = p.parse_args()
    out = a.out
    phases = []
    if (out / "phases.tsv").exists():
        for line in (out / "phases.tsv").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                phases.append(
                    {
                        "phase": parts[0],
                        "status": parts[1],
                        "start": parts[2],
                        "end": parts[3],
                        "seconds": parts[4],
                    }
                )
    bench = load(out / "llama-bench.json") or []
    bench_rows = []
    for r in bench if isinstance(bench, list) else []:
        bench_rows.append(
            {
                k: r.get(k)
                for k in (
                    "n_prompt",
                    "n_gen",
                    "avg_ts",
                    "stddev_ts",
                    "n_gpu_layers",
                    "flash_attn",
                    "n_threads",
                    "type_k",
                    "type_v",
                    "model_size",
                    "model_n_params",
                    "build_commit",
                    "build_number",
                    "gpu_info",
                    "cpu_info",
                    "backends",
                )
            }
        )
    pp = next((r["avg_ts"] for r in bench_rows if r.get("n_prompt") and not r.get("n_gen")), None)
    tg = next((r["avg_ts"] for r in bench_rows if r.get("n_gen") and not r.get("n_prompt")), None)
    spike_dir = out / "device-spike"
    spike = load(spike_dir / "summary.json") if spike_dir.exists() else None
    spike_fail = load(spike_dir / "failure.json") if spike_dir.exists() else None
    probe = load(out / "grammar-probe" / "verdict.json")
    evals = {}
    for label in ("parity", "relaxed"):
        r = load(out / f"eval-{label}" / "evaluation-receipt.json")
        if r:
            evals[label] = {
                "route": r.get("constraint_route"),
                "budget": r.get("budget"),
                "comparable_budget": r.get("comparable_budget"),
                "stop_reason": r.get("stop_reason"),
                "metrics": r.get("metrics"),
                "call_timings": r.get("call_timings", []),
                "per_task": [
                    {
                        "task_id": x["task_id"],
                        "outcome": x["result"]["outcome"],
                        "reason": x["result"]["reason"],
                        "verified_green": x["verified_green"],
                        "tests_green_at_expiry": x["result"].get("tests_green_at_expiry"),
                        "steps": x["result"]["steps"],
                        "elapsed_s": round(x["result"]["elapsed_s"], 1),
                    }
                    for x in r.get("rows", [])
                ],
            }
    spark = load(a.spark_ref) or {}
    srows = spark.get("rows", [])
    spark_sum = {
        "solved_verified": sum(1 for x in srows if x["outcome"] == "fixed" and x["verified_green"]),
        "solvable": 12,
        "giveups_on_impossible": sum(
            1 for x in srows if x["outcome"] == "cannot_fix" and "impossible" in x["task_id"]
        ),
        "impossible": 3,
        "false_claims": sum(x["false_claims"] for x in srows),
        "invalid_actions": sum(x["invalid_actions"] for x in srows),
    }
    memory = {
        "sampler": samples(out / "mem-samples.csv"),
        "llama_bench_time_l": time_l(out / "llama-bench.stderr"),
        "llama_server_time_l": time_l(out / "server.time"),
    }
    pre = load(out / "preflight.json") or {}
    summary = {
        "run_directory": str(out),
        "dry_run": (out / "DRY_RUN").exists(),
        "hard_stop_hit": (out / "HARD_STOP").exists(),
        "host": pre,
        "phases": phases,
        "llama_bench": {"pp512_tok_s": pp, "tg128_tok_s": tg, "rows": bench_rows},
        "device_spike": spike if spike else {"measured": False, "failure": spike_fail},
        "grammar_route": {
            k: probe.get(k)
            for k in (
                "chosen_route",
                "chosen_reason",
                "eval_allowed",
                "routes",
                "high_kwargs_changed_prompt_tokens",
            )
        }
        if probe
        else None,
        "base_low_15": evals,
        "spark_cloud_same_15": spark_sum,
        "memory": memory,
        "llama_processes_after": (out / "llama-procs-after.txt").read_text().strip()
        if (out / "llama-procs-after.txt").exists()
        else None,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    if spike and spike.get("measured"):
        receipt = {
            "measured": True,
            "arm": "base-low",
            "chip_name": "Apple M4 Max (lab, Mac16,6, 36 GB)",
            "gguf_sha256": spike.get("gguf_sha256"),
            "grammar_sha256": spike.get("grammar_sha256"),
            "median_generation_tokens_per_s": spike.get("median_generation_tokens_per_s"),
            "median_prompt_tokens_per_s": spike.get("median_prompt_tokens_per_s"),
            "peak_server_rss_bytes": spike.get("peak_server_rss_bytes"),
            "peak_swap_growth_bytes": spike.get("peak_swap_growth_bytes"),
            "source": "device-spike/summary.json",
        }
        (out / "device-receipt-base-low.json").write_text(json.dumps(receipt, indent=2) + "\n")

    spike_status = "measured" if spike else "not run"
    if spike_fail:
        spike_status = "refused: " + str(spike_fail.get("reason"))
    L = [
        "# lab bench: Muse-Glimmer-30B Q4_K_M, llama.cpp b10353 Metal",
        "",
        f"Run: `{out.name}`{' (DRY RUN: no model loaded)' if summary['dry_run'] else ''}"
        f"{' - HARD STOP HIT' if summary['hard_stop_hit'] else ''}. "
        f"Host: {pre.get('model', '?')} {pre.get('chip', '')} "
        f"{pre.get('memsize_gib', '?')} GiB, macOS {pre.get('macos', '?')}. Times are CT.",
        "",
        "## Speed",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| llama-bench pp512 (tok/s, -ngl 99) | {fmt(pp)} |",
        f"| llama-bench tg128 (tok/s, -ngl 99) | {fmt(tg)} |",
        f"| Device spike gen tok/s under GBNF (median of 3) | "
        f"{fmt((spike or {}).get('median_generation_tokens_per_s'))} |",
        f"| Device spike prompt-eval tok/s | "
        f"{fmt((spike or {}).get('median_prompt_tokens_per_s'))} |",
        f"| Device spike result | {spike_status} |",
        "",
        "## Memory",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| llama-bench peak footprint (/usr/bin/time -l) | "
        f"{gib(memory['llama_bench_time_l'].get('peak_footprint_bytes'))} |",
        f"| llama-server peak footprint (/usr/bin/time -l) | "
        f"{gib(memory['llama_server_time_l'].get('peak_footprint_bytes'))} |",
        f"| llama-server max RSS (/usr/bin/time -l) | "
        f"{gib(memory['llama_server_time_l'].get('max_rss_bytes'))} |",
        f"| Sampled peak llama RSS | {gib(memory['sampler'].get('peak_llama_rss_bytes'))} |",
        f"| Sampled peak system used (anonymous+wired+compressor) | "
        f"{gib(memory['sampler'].get('peak_anon_wired_compressed_bytes'))} |",
        f"| Swap growth during run (MB) | {fmt(memory['sampler'].get('swap_growth_mb'))} |",
        f"| Min memory_pressure free % | "
        f"{fmt(memory['sampler'].get('min_memory_pressure_free_pct'))} |",
        "",
        "## Grammar route",
        "",
    ]
    if probe:
        L += [
            f"Chosen: **{probe.get('chosen_route')}** ({probe.get('chosen_reason')}).",
            "",
            "| Route @ reasoning | Verdict |",
            "| --- | --- |",
        ]
        L += [f"| {k} | {v['verdict']} |" for k, v in (probe.get("routes") or {}).items()]
    else:
        L += ["Not run."]
    L += ["", "## base-low, 15-task stratified held-out parity subset", ""]
    if evals:
        L += [
            "| Pass | Route | Budget s/task | Solved (of solvable run) | "
            "Give-ups correct / impossible run | False fixed attempts | Invalid / steps | "
            "Green at expiry (uncredited) | Non-terminal |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for label, e in evals.items():
            m = e["metrics"]
            L.append(
                f"| {label}{'' if e['comparable_budget'] else ' (non-comparable)'} | "
                f"{e['route']} | {e['budget']['max_seconds']} | "
                f"{m['solved_verified']} / {m['solvable_run']} | "
                f"{m['correct_giveups']} / {m['impossible_run']} | "
                f"{m['attempted_false_fixed_claims']} | "
                f"{m['invalid_actions']} / {m['steps']} | "
                f"{m.get('green_at_expiry', 'n/a')} "
                f"(final oracle: {m.get('verified_green_at_expiry', 'n/a')}) | "
                f"{json.dumps(m['non_terminal_outcomes'])} |"
            )
    else:
        L += ["Not run."]
    L += [
        "",
        f"Spark cloud on the same 15 (measured earlier, 30 s budget): "
        f"{spark_sum['solved_verified']}/12 solved, "
        f"{spark_sum['giveups_on_impossible']}/3 correct give-ups, "
        f"{spark_sum['false_claims']} false claims.",
        "",
        "Green at expiry is diagnostic only; a fixed terminal plus the independent "
        "final oracle is required for solved credit.",
        "15 of 50 tasks: a parity spot check, not the frozen 50-task arm; "
        "`pack-eval` requires all 50.",
        "RSS is sampled process residency, not total unified-memory accounting.",
        "",
        "## Phases",
        "",
        "| Phase | Status | Seconds |",
        "| --- | --- | ---: |",
    ]
    L += [f"| {x['phase']} | {x['status']} | {x['seconds']} |" for x in phases]
    L += ["", f"llama processes after cleanup: {summary['llama_processes_after'] or 'none'}", ""]
    (out / "RESULTS.md").write_text("\n".join(L))
    print(f"wrote {out / 'summary.json'} and {out / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
