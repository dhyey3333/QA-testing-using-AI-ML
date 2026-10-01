"""Score Nightshift against an app with planted bugs.

    uv run python -m benchmark                    the demo shop: every spec clean, then every planted bug
    uv run python -m benchmark --app clinic       the holdout app. Never tune anything on it.
    uv run python -m benchmark --only wrong-total,js-error --no-judge

Every spec runs once on the clean app: a fail there is a false alarm. Then each
bug is switched on alone and the specs meant to catch it run: a pass there is a
missed bug. Prints a table and writes bench.md and bench.json.

Read the reasons, not just the verdicts: a fail for the wrong reason is luck,
not detection.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nightshift.cli import configure_stdout
from nightshift.model import HttpModel, ModelConfig
from nightshift.result import RunResult
from nightshift.runner import RunOptions, open_browser, run_with_retries
from nightshift.spec import load_specs

ROOT = Path(__file__).resolve().parent.parent
CLEAN = "clean"

# Each app module has make_server(port, bugs) and BUGS = {name: Bug(description, specs)}.
APPS = {
    "shop": ("demo_shop.server", ROOT / "specs"),
    "clinic": ("holdout.server", ROOT / "holdout" / "specs"),
}


@dataclass
class Row:
    config: str  # "clean" or a bug name
    result: RunResult


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(prog="python -m benchmark", description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", choices=sorted(APPS), default="shop")
    parser.add_argument("--only", default="", help="comma-separated bug names (default: every bug)")
    parser.add_argument("--skip-clean", action="store_true", help="don't run the specs on the clean app")
    parser.add_argument("--clean-only", action="store_true", help="only run the specs on the clean app (false alarms)")
    parser.add_argument("--retries", type=int, default=0, help="retries per failure (default 0: measure raw detection)")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)

    module_name, specs_dir = APPS[args.app]
    app = importlib.import_module(module_name)
    specs = {spec.name: spec for spec in load_specs([specs_dir])}
    bugs = [] if args.clean_only else [b.strip() for b in args.only.split(",") if b.strip()] or list(app.BUGS)
    unknown = [b for b in bugs if b not in app.BUGS]
    if unknown:
        parser.error(f"unknown bug(s) for {args.app}: {', '.join(unknown)}")
    missing = {name for bug in bugs for name in app.BUGS[bug].specs} - specs.keys()
    if missing:
        parser.error(f"no spec named {', '.join(sorted(missing))} in {specs_dir}")

    server = app.make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    model = HttpModel(ModelConfig.from_env(), ModelConfig.judge_from_env(ModelConfig.from_env()))
    # No recordings: the benchmark measures the agent and the judge, not a replayed path.
    options = RunOptions(send_screenshot=not args.no_vision, judge=not args.no_judge, retries=args.retries)
    out = args.out / f"bench-{args.app}-{datetime.now():%Y%m%d-%H%M%S}"
    plan = ([] if args.skip_clean else [(CLEAN, list(specs))]) + [(bug, list(app.BUGS[bug].specs)) for bug in bugs]
    total = sum(len(names) for _, names in plan)
    print(f"app: {args.app}   model: {model.name}   judge: {'on' if options.judge else 'off'}   runs: {total}")
    print(f"results: {out}\n")

    rows: list[Row] = []
    try:
        with open_browser(headed=args.headed) as browser:
            for config, names in plan:
                server.bugs = set() if config == CLEAN else {config}
                for name in names:
                    if hasattr(server, "reset"):
                        server.reset()  # apps with state (the clinic's appointments) start every run the same
                    spec = specs[name].with_base_url(base_url)
                    print(f"[{len(rows) + 1}/{total}] {config} / {name} ... ", end="", flush=True)
                    result = run_with_retries(browser, spec, model, out_dir=out / config / name, options=options)
                    print(f"{result.verdict.upper()} ({len(result.steps)} steps, {result.duration_s:.0f}s): {result.reason}")
                    rows.append(Row(config, result))
    finally:
        server.shutdown()
        server.server_close()
        model.close()

    report = render(rows, app.BUGS, args.app, model.name, options)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench.md").write_text(report, encoding="utf-8")
    (out / "bench.json").write_text(
        json.dumps([{"config": r.config, **r.result.summary(),
                     "checks": [c.__dict__ for c in r.result.checks]} for r in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("\n" + report)
    return 0


def render(rows: list[Row], bugs: dict, app: str, model: str, options: RunOptions) -> str:
    clean = [r for r in rows if r.config == CLEAN]
    planted = [bug for bug in dict.fromkeys(r.config for r in rows) if bug != CLEAN]
    caught = [bug for bug in planted if any(r.result.verdict == "fail" for r in rows if r.config == bug)]
    false_alarms = sum(r.result.verdict == "fail" for r in clean)
    errors = sum(r.result.verdict == "error" for r in rows)
    step_times = [s.model_ms / 1000 for r in rows for s in r.result.steps if s.model_ms]
    tokens = [r.result.prompt_tokens + r.result.completion_tokens for r in rows]
    durations = [r.result.duration_s for r in rows]

    lines = [
        f"# Nightshift benchmark: {app}, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Model `{model}` · {'screenshot + text' if options.send_screenshot else 'text only'} · "
        f"judge {'on' if options.judge else 'off'} · retries {options.retries}",
        "",
        "| Metric | Result |",
        "|---|---|",
    ]
    if planted:
        lines.append(f"| Planted bugs caught | **{len(caught)}/{len(planted)}** ({100 * len(caught) / len(planted):.0f}%) |")
    if clean:
        lines.append(f"| False alarms on the clean app | **{false_alarms}/{len(clean)}** |")
    lines += [
        f"| Tester errors (couldn't finish) | {errors}/{len(rows)} |",
        f"| Median model time per step | {statistics.median(step_times) if step_times else 0:.1f}s |",
        f"| Median run time | {statistics.median(durations) if durations else 0:.0f}s |",
        f"| Median tokens per run | {int(statistics.median(tokens)) if tokens else 0:,} |",
        "",
        "| App | Spec | Verdict | Steps | Time | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        reason = r.result.reason.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.config} | {r.result.spec} | {r.result.verdict} | {len(r.result.steps)} "
                     f"| {r.result.duration_s:.0f}s | {reason} |")
    missed = [bug for bug in planted if bug not in caught]
    if missed:
        lines += ["", "**Missed:**", *[f"- `{bug}`: {bugs[bug].description}" for bug in missed]]
    alarms = [r for r in clean if r.result.verdict == "fail"]
    if alarms:
        lines += ["", "**False alarms:**", *[f"- `{r.result.spec}`: {r.result.reason}" for r in alarms]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
