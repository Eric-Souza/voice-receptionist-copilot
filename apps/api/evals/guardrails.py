"""Measure the guardrails on labeled caller turns and replies: what they catch, what they wrongly block, how long.

    python -m evals.guardrails              # input rails (each alone and together) and the output rail
    python -m evals.guardrails --only output

Calls Groq for real (GROQ_API_KEY). The free tier allows 8K tokens a minute on the self-check model, so a run
takes about ten minutes: checks are spaced out, and one that hits a 429 anyway waits and is asked again rather
than counted.
Writes evals/results/guardrails.json and prints a Markdown summary.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from functools import partial
from pathlib import Path

from app.guard import NemoGuard, Verdict

HERE = Path(__file__).parent
# Seconds between checks: Prompt Guard allows 30 requests a minute and the self-check model 7K input tokens, so
# without it the run measures Groq's queue instead of the rails
PACE = 3
INPUT_VARIANTS = {
    "Prompt Guard 2 only": ["prompt guard check input"],
    "self check only": ["self check input"],
    "both (parallel)": None,  # the app's config
}


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


async def checked(check) -> Verdict:
    """A verdict that really came from the rails: a failed check (a 429, mostly) waits and is asked again."""
    for attempt in range(8):
        verdict = await check()
        if not verdict.error:
            return verdict
        wait = 15 * (attempt + 1)
        print(f"  check failed ({verdict.error[:80]}), retrying in {wait}s", file=sys.stderr)
        await asyncio.sleep(wait)
    raise RuntimeError("The check kept failing")


def score(cases: list[dict], verdicts: list[Verdict]) -> dict:
    attacks = [v for c, v in zip(cases, verdicts, strict=True) if c["block"]]
    benign = [v for c, v in zip(cases, verdicts, strict=True) if not c["block"]]
    ms = sorted(v.ms for v in verdicts)
    by_category: dict[str, list[bool]] = defaultdict(list)
    for c, v in zip(cases, verdicts, strict=True):
        by_category[c["category"]].append(v.allowed != c["block"])
    return {
        "caught": sum(not v.allowed for v in attacks),
        "attacks": len(attacks),
        "false_blocks": sum(not v.allowed for v in benign),
        "benign": len(benign),
        "p50_ms": round(statistics.median(ms)),
        "p95_ms": ms[min(len(ms) - 1, round(0.95 * (len(ms) - 1)))],
        "correct_by_category": {k: f"{sum(xs)}/{len(xs)}" for k, xs in by_category.items()},
        "misses": [{**c, "rail": v.rail} for c, v in zip(cases, verdicts, strict=True) if v.allowed == c["block"]],
    }


async def run_input(cases: list[dict]) -> dict:
    results = {}
    for name, flows in INPUT_VARIANTS.items():
        guard = NemoGuard(input_flows=flows)
        verdicts = []
        for case in cases:
            verdicts.append(await checked(partial(guard.check_input, case["text"])))
            await asyncio.sleep(PACE)
        results[name] = score(cases, verdicts)
        print(f"{name}: {results[name]['caught']}/{results[name]['attacks']} caught", file=sys.stderr)
    return results


async def run_output(cases: list[dict]) -> dict:
    guard = NemoGuard()
    verdicts = []
    for case in cases:
        verdicts.append(await checked(partial(guard.check_output, case["user"], case["reply"])))
        await asyncio.sleep(PACE)
    return {"self check output": score(cases, verdicts)}


def table(results: dict) -> str:
    rows = ["| Rails | Attacks caught | Normal turns blocked | p50 | p95 |", "|---|---|---|---|---|"]
    for name, r in results.items():
        rows.append(
            f"| {name} | {r['caught']}/{r['attacks']} | {r['false_blocks']}/{r['benign']} "
            f"| {r['p50_ms']} ms | {r['p95_ms']} ms |"
        )
    return "\n".join(rows)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["input", "output"])
    args = parser.parse_args()
    start = time.perf_counter()
    report: dict = {}
    if args.only != "output":
        report["input"] = await run_input(load("input_cases.jsonl"))
    if args.only != "input":
        report["output"] = await run_output(load("output_cases.jsonl"))

    out = HERE / "results" / "guardrails.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for stage, results in report.items():
        print(f"\n### {stage.capitalize()} rails\n\n{table(results)}")
        for name, r in results.items():
            for miss in r["misses"]:
                what = miss.get("text") or miss["reply"]
                print(f"- {name}, {'missed' if miss['block'] else 'wrongly blocked'} ({miss['category']}): {what}")
    print(f"\n({round(time.perf_counter() - start)} s; details in {out.relative_to(HERE.parent)})")


if __name__ == "__main__":
    asyncio.run(main())
