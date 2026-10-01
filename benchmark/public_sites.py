"""Nightshift on public demo sites: pass/fail, time and model cost per run.

    uv run python -m benchmark.public_sites                  every spec in benchmark/public/
    uv run python -m benchmark.public_sites --only saucedemo-login,greenkart-checkout

The sites are practice sites built for test automation (or, for opencart, one that
blocks bots: kept to record what happens). Nothing here has planted bugs, so every
spec should pass. A fail is either a bug in the site or a tester mistake, and only
reading the run tells which: the table has a "review" column for that.

Cost: the default model runs locally, so its API cost is zero. To price the same
runs on a hosted model, token counts are multiplied by OpenRouter's published
prices for the nearest hosted models of the same family (PRICES below), at USD_INR.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime
from pathlib import Path

from nightshift.cli import configure_stdout
from nightshift.model import HttpModel, ModelConfig
from nightshift.recording import RecordingStore
from nightshift.result import RunResult
from nightshift.runner import RunOptions, open_browser, run_with_retries
from nightshift.spec import load_spec

ROOT = Path(__file__).resolve().parent.parent
SPECS = Path(__file__).resolve().parent / "public"

# USD per million tokens (input, output), from https://openrouter.ai/api/v1/models on 2026-10-01.
# The local default is qwen3-vl:4b; OpenRouter has no 4B, so the 8B is its nearest hosted sibling.
PRICES = {
    "qwen3-vl-8b": (0.117, 0.455),
    "qwen3-vl-235b": (0.21, 1.90),
}
USD_INR = 96.0  # USD/INR 96.11 on 2026-09-30 (tradingeconomics.com/india/currency), rounded

FLOWS = ("login-validation", "signup-validation", "withdraw-validation", "add-to-cart",
         "checkout", "signup", "search", "login")


def flow_of(name: str) -> tuple[str, str]:
    """("saucedemo", "add-to-cart") from "saucedemo-add-to-cart". Validation flows count as form validation."""
    for flow in FLOWS:
        if name.endswith("-" + flow):
            site = name[: -len(flow) - 1]
            return site, "form validation" if flow.endswith("validation") else flow
    return name, "other"


def cost_inr(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    per_in, per_out = PRICES[model]
    return (prompt_tokens * per_in + completion_tokens * per_out) / 1_000_000 * USD_INR


def row_for(result: RunResult) -> dict:
    """One table row as plain data, so a stopped benchmark can be resumed from its JSON."""
    site, flow = flow_of(result.spec)
    return {
        "spec": result.spec, "site": site, "flow": flow, "verdict": result.verdict, "category": result.category, "reason": result.reason,
        "duration_s": result.duration_s, "model_calls": result.model_calls, "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        **{f"cost_inr_{m}": round(cost_inr(result.prompt_tokens, result.completion_tokens, m), 3) for m in PRICES},
        "out_dir": result.out_dir,
    }


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(prog="python -m benchmark.public_sites", description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated spec names (default: all)")
    parser.add_argument("--retries", type=int, default=1, help="retries per failure (default 1, as `nightshift run`)")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    parser.add_argument("--resume", type=Path, help="a stopped run's folder: keep its finished specs, run the rest")
    parser.add_argument("--rerun", action="store_true",
                        help="after each pass, run the spec again from its saved path, to measure reruns")
    args = parser.parse_args(argv)

    # Signup specs use ${NS_RUN} so every run registers a fresh account.
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    os.environ.setdefault("NS_RUN", stamp)
    only = {name.strip() for name in args.only.split(",") if name.strip()}
    specs = [load_spec(path) for path in sorted(SPECS.glob("*.yaml"))]
    specs = [spec for spec in specs if not only or spec.name in only]
    if only - {spec.name for spec in specs}:
        parser.error(f"no spec named {', '.join(sorted(only - {s.name for s in specs}))}")

    out = args.resume or args.out / f"public-{stamp}"
    rows = load_rows(out) if args.resume else []
    done = {row["spec"] for row in rows}
    model = HttpModel(ModelConfig.from_env(), ModelConfig.judge_from_env(ModelConfig.from_env()))
    # The first run is always the agent's (the store starts empty). With --rerun, a passing run's
    # saved path is replayed straight away, the way CI reruns a suite.
    store = RecordingStore(out / "recordings") if args.rerun else None
    options = RunOptions(retries=args.retries, recordings=store)
    try:
        with open_browser() as browser:
            for spec in specs:
                if spec.name in done:
                    continue
                print(f"> {spec.name}", flush=True)
                result = run_with_retries(browser, spec, model, out_dir=out / spec.name, options=options)
                row = row_for(result)
                # Signups are not rerun: the same fake email can't register twice (CI would set a new NS_RUN).
                rerunnable = flow_of(spec.name)[1] != "signup"
                if store is not None and rerunnable and result.verdict in ("pass", "flaky") and store.load(spec):
                    again = run_with_retries(browser, spec, model, out_dir=out / spec.name / "rerun", options=options)
                    row["rerun"] = {"verdict": again.verdict, "mode": again.mode, "duration_s": again.duration_s,
                                    "model_calls": again.model_calls,
                                    "cost_inr_qwen3-vl-8b": round(cost_inr(again.prompt_tokens, again.completion_tokens,
                                                                           "qwen3-vl-8b"), 3)}
                    print(f"  rerun: {again.verdict.upper()} ({again.mode}) in {again.duration_s:.0f}s, "
                          f"{again.model_calls} calls", flush=True)
                rows.append(row)
                # Written after every spec: a long benchmark that is stopped keeps what it measured.
                save(rows, out, model.name)
                print(f"  {result.verdict.upper()} in {result.duration_s:.0f}s, {result.model_calls} calls: "
                      f"{result.reason[:140]}", flush=True)
    finally:
        model.close()

    report = save(rows, out, model.name)
    print("\n" + report)
    print(f"written: {out / 'public-sites.md'}")
    return 0


def load_rows(out: Path) -> list[dict]:
    path = out / "public-sites.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save(rows: list[dict], out: Path, model_name: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    (out / "public-sites.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    report = render(rows, model_name)
    (out / "public-sites.md").write_text(report, encoding="utf-8")
    return report


def render(rows: list[dict], model_name: str) -> str:
    lines = [f"# Public demo sites: {len(rows)} runs, model {model_name}", "",
             "| Site | Flow | Verdict | Time (s) | Model calls | Tokens | ₹ at 8B hosted | ₹ at 235B hosted | Reason | Review |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        tokens = row["prompt_tokens"] + row["completion_tokens"]
        verdict = row["verdict"] + (f" [{row['category']}]" if row.get("category") else "")
        lines.append(f"| {row['site']} | {row['flow']} | {verdict} | {row['duration_s']:.0f} | {row['model_calls']} | "
                     f"{tokens:,} | {row['cost_inr_qwen3-vl-8b']:.2f} | {row['cost_inr_qwen3-vl-235b']:.2f} | "
                     f"{row['reason'][:90].replace('|', '/')} | |")
    if rows:
        verdicts = [row["verdict"] for row in rows]
        durations = [row["duration_s"] for row in rows]
        lines += ["", "Verdicts: " + ", ".join(f"{v} {verdicts.count(v)}" for v in ("pass", "fail", "flaky", "error")),
                  f"Time per run: median {statistics.median(durations):.0f}s, max {max(durations):.0f}s",
                  "Model cost per run (median): local ₹0 API cost; "
                  + "; ".join(f"{m} hosted ₹{statistics.median(row[f'cost_inr_{m}'] for row in rows):.2f}"
                              for m in PRICES)]
    reruns = [(row, row["rerun"]) for row in rows if row.get("rerun")]
    if reruns:
        lines += ["", "## Reruns from the saved path", "",
                  "| Site | Flow | First run | Rerun | Mode | First (s) | Rerun (s) | Rerun model calls | Rerun ₹ at 8B |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for row, again in reruns:
            lines.append(f"| {row['site']} | {row['flow']} | {row['verdict']} | {again['verdict']} | {again['mode']} | "
                         f"{row['duration_s']:.0f} | {again['duration_s']:.0f} | {again['model_calls']} | "
                         f"{again['cost_inr_qwen3-vl-8b']:.2f} |")
        lines += ["", f"Reruns: {sum(a['verdict'] == 'pass' for _, a in reruns)}/{len(reruns)} pass, "
                      f"median {statistics.median(a['duration_s'] for _, a in reruns):.0f}s, "
                      f"{sum(a['model_calls'] == 0 for _, a in reruns)} with no model call"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
