"""Command line.

    nightshift run specs/            run specs (replays saved paths, judges, retries failures)
    nightshift explore URL           roam an app looking for bugs, no spec needed
    nightshift generate ...          draft specs from a user story and/or an explored app
    nightshift export specs/         turn saved paths into plain Playwright tests
    nightshift report                rebuild runs/index.html, the history dashboard
    nightshift validate reqs.md      design tests per requirement, run them, write the traceability matrix
    nightshift cases specs/          the test-case document (CSV for Excel or a test-management tool)
    nightshift triage runs/<run>     defect analysis of a finished run; can file GitHub issues
    nightshift serve                 the dashboard: a local web page for all of the above

`run` exit codes are for CI: 0 everything passed (flaky counts as passed, with a
warning), 1 at least one spec found a bug, 2 the tester couldn't finish something.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx

from .actions import InvalidAction
from .defects import Defect, analyse, file_github_issues, load_results, write_defects
from .explore import DEFAULT_AVOID, explore
from .export import write_export
from .jira import JiraConfig, JiraError, file_jira_issues
from .generate import design_tests, env_name, generate_specs, load_pages, parse_requirements, write_specs
from .model import HttpModel, ModelConfig, ModelError
from .notify import append_github_summary, github_run_url, post_slack, slack_payload
from .recording import RecordingStore
from .report import write_history, write_junit, write_run_index
from .result import RunResult
from .runner import RunOptions, device_options, open_browser, run_with_retries
from .spec import Spec, SpecError, load_specs
from .traceability import build_matrix, write_test_cases, write_traceability

DEFAULT_RECORDINGS = Path(".nightshift/recordings")


def configure_stdout() -> None:
    """Page text can hold any character (₹, Devanagari...); printing one must never crash a run.

    Windows pipes default to cp1252, so output going to a file or a CI log is
    switched to UTF-8. An interactive console keeps its own encoding, with "?"
    for anything it can't show.
    """
    if not hasattr(sys.stdout, "reconfigure"):
        return
    if sys.stdout.isatty():
        sys.stdout.reconfigure(errors="replace")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(
        prog="nightshift", description="An AI QA tester: plain-English test specs, run by an agent in a real browser."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run specs against a web app")
    run.add_argument("specs", nargs="+", type=Path, help="spec files, or folders of them")
    _run_options(run)
    _common(run)

    val = commands.add_parser("validate", help="validate an app against a requirements document")
    val.add_argument("requirements", type=Path, help="a text or Markdown file: one requirement per bullet or numbered line")
    val.add_argument("--url", required=True, help="the app's start URL")
    val.add_argument("--data", action="append", default=[], metavar="KEY=VALUE",
                     help="test data, e.g. username=... password=... (repeatable)")
    val.add_argument("--from", dest="from_file", type=Path, help="discovered.json from an explore run, to design with real labels")
    val.add_argument("--explore-steps", type=int, default=0, help="explore the app first for this many steps (default: 0)")
    val.add_argument("--cases", type=int, default=2, help="at most this many test cases per requirement (default: 2)")
    val.add_argument("--specs-out", type=Path, default=Path("specs/requirements"),
                     help="where designed test cases go; existing ones are reused (default: specs/requirements)")
    val.add_argument("--redesign", nargs="?", const="all", default="",
                     help="design tests again: for every requirement, or only the ids given (R5,R6)")
    val.add_argument("--design-only", action="store_true",
                     help="design and write the test cases, then stop so they can be reviewed")
    _run_options(val)
    _common(val)

    exp = commands.add_parser("explore", help="roam an app looking for bugs, no spec needed")
    exp.add_argument("url")
    exp.add_argument("--steps", type=int, default=40, help="action budget (default: 40)")
    exp.add_argument("--focus", default="", help='what to concentrate on, e.g. "the checkout"')
    exp.add_argument("--data", action="append", default=[], metavar="KEY=VALUE", help="test data it may type, repeatable")
    exp.add_argument("--avoid", help=f"comma-separated words it must never click (default: {', '.join(DEFAULT_AVOID)})")
    _common(exp)

    gen = commands.add_parser("generate", help="draft specs from a user story and/or an explored app")
    gen.add_argument("--url", help="the app's start URL")
    gen.add_argument("--from", dest="from_file", type=Path, help="discovered.json from an explore run")
    gen.add_argument("--explore-steps", type=int, default=0,
                     help="explore --url first for this many steps to learn the app (default: 0, don't)")
    gen.add_argument("--story", help="a requirement or user story to write tests for")
    gen.add_argument("--story-file", type=Path, help="read the requirement from a file (a ticket, a PRD section)")
    gen.add_argument("--count", type=int, default=5, help="how many specs to draft (default: 5)")
    gen.add_argument("--data", action="append", default=[], metavar="KEY=VALUE", help="known test data, repeatable")
    gen.add_argument("--specs-out", type=Path, default=Path("specs/generated"),
                     help="where the drafts go (default: specs/generated)")
    _common(gen)

    ex = commands.add_parser("export", help="turn saved paths into plain Playwright tests (TypeScript)")
    ex.add_argument("specs", nargs="+", type=Path)
    ex.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    ex.add_argument("--to", type=Path, default=Path("e2e"), help="output folder (default: e2e/)")

    rep = commands.add_parser("report", help="rebuild the history dashboard (runs/index.html)")
    rep.add_argument("--out", type=Path, default=Path("runs"))

    ini = commands.add_parser("init", help="set up a project: a starter spec, a CI workflow, .gitignore")
    ini.add_argument("--url", default="http://localhost:3000/", help="the app's start URL")

    cas = commands.add_parser("cases", help="write the test-case document (CSV and Markdown)")
    cas.add_argument("specs", nargs="+", type=Path)
    cas.add_argument("--run", type=Path, help="a run folder, to include each case's last result")
    cas.add_argument("--to", type=Path, default=Path("test-cases.csv"), help="output file (default: test-cases.csv)")

    tri = commands.add_parser("triage", help="defect analysis of a finished run")
    tri.add_argument("run", type=Path, help="the run folder, e.g. runs/20261001-120000")
    tri.add_argument("--file-github", metavar="OWNER/REPO", help="file each defect as a GitHub issue (uses the gh CLI)")
    tri.add_argument("--file-jira", action="store_true", help="file each defect in Jira (see the JIRA_* settings)")

    srv = commands.add_parser("serve", help="the dashboard: a local web page for all of the above")
    srv.add_argument("--port", type=int, default=8765, help="(default: 8765)")
    srv.add_argument("--specs", action="append", type=Path, help="a folder of test cases to show, repeatable (default: specs)")
    srv.add_argument("--out", type=Path, default=Path("runs"), help="where runs are kept (default: runs/)")
    srv.add_argument("--no-open", action="store_true", help="don't open the browser")

    args = parser.parse_args(argv)
    handler = {"run": _run, "explore": _explore, "generate": _generate, "export": _export, "report": _report,
               "init": _init, "validate": _validate, "cases": _cases, "triage": _triage,
               "serve": _serve}
    try:
        return handler[args.command](args)
    except (SpecError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="run against another deployment (e.g. staging), keeping each spec's path")
    parser.add_argument("--max-steps", type=int, help="override every spec's step budget")
    parser.add_argument("--retries", type=int, default=1,
                        help="re-run a failure this many times with a fresh agent before reporting it (default: 1)")
    parser.add_argument("--no-judge", action="store_true", help="trust the agent's own pass (faster, less honest)")
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS,
                        help=f"where saved paths live (default: {DEFAULT_RECORDINGS}); commit it so CI replays them")
    parser.add_argument("--no-replay", action="store_true", help="always use the agent, even when a saved path exists")
    parser.add_argument("--no-record", action="store_true", help="don't save passing paths for replay")
    parser.add_argument("--junit", type=Path, help="also write JUnit XML here, for CI test reporters")
    parser.add_argument("--slack-webhook", default=os.getenv("SLACK_WEBHOOK_URL", ""),
                        help="post a summary to Slack (default: $SLACK_WEBHOOK_URL)")
    parser.add_argument("--notify", choices=["failures", "always"], default="failures",
                        help="when to post to Slack (default: failures)")
    parser.add_argument("--file-jira", action="store_true",
                        help="file each defect in Jira (JIRA_URL, JIRA_PROJECT, JIRA_EMAIL + JIRA_API_TOKEN or JIRA_TOKEN)")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--headed", action="store_true", help="show the browser while it runs")
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default="chromium")
    parser.add_argument("--device", help='emulate a device, e.g. "iPhone 13" or "Pixel 7"')
    parser.add_argument("--video", action="store_true", help="record a video of every run")
    parser.add_argument("--no-vision", action="store_true",
                        help="send the model page text and elements only, no screenshot (faster on small models)")
    parser.add_argument("--out", type=Path, default=Path("runs"), help="where results go (default: runs/)")
    parser.add_argument("--quiet", action="store_true", help="print results only, not every step")


def _model() -> HttpModel:
    agent = ModelConfig.from_env()
    judge = ModelConfig.judge_from_env(agent)
    model = HttpModel(agent, judge)
    print(f"model: {model.name} at {agent.base_url}")
    return model


def _options(args: argparse.Namespace, **extra) -> RunOptions:
    context = device_options(args.device) if args.device else {}
    return RunOptions(send_screenshot=not args.no_vision, video=args.video, context=context, **extra)


def _log(args: argparse.Namespace):
    return (lambda line: None) if args.quiet else (lambda line: print(f"   {line}", flush=True))


# --- run ------------------------------------------------------------------------

def _run(args: argparse.Namespace) -> int:
    specs = load_specs(args.specs)
    results, run_dir, defects = _execute(specs, args, _model())
    return _exit_code(results)


def _exit_code(results: list[RunResult]) -> int:
    counts = Counter(result.verdict for result in results)
    return 1 if counts["fail"] else 2 if counts["error"] else 0


def _execute(specs: list[Spec], args: argparse.Namespace, model: HttpModel) -> tuple[list[RunResult], Path, list[Defect]]:
    """Run specs and write everything a run produces: reports, history, defects, JUnit, notifications."""
    if args.base_url:
        specs = [spec.with_base_url(args.base_url) for spec in specs]
    if args.max_steps:
        specs = [replace(spec, max_steps=args.max_steps) for spec in specs]

    store = RecordingStore(args.recordings) if not (args.no_replay and args.no_record) else None
    options = _options(args, judge=not args.no_judge, retries=max(0, args.retries), recordings=store,
                       replay=not args.no_replay, record=not args.no_record)
    run_dir = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
    log = _log(args)

    results: list[RunResult] = []
    try:
        with open_browser(headed=args.headed, browser=args.browser) as browser:
            for spec in specs:
                print(f"\n> {spec.name}  {spec.url}", flush=True)
                result = run_with_retries(browser, spec, model, out_dir=run_dir / spec.name, options=options, log=log)
                results.append(result)
                _print_verdict(result)
    finally:
        model.close()

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps([r.summary() for r in results], indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    index = write_run_index(results, run_dir)
    write_history(args.out)
    if args.junit:
        write_junit(results, args.junit)
    append_github_summary(results)
    counts = Counter(result.verdict for result in results)
    if args.slack_webhook and (args.notify == "always" or counts["fail"] or counts["error"] or counts["flaky"]):
        try:
            post_slack(args.slack_webhook, slack_payload(results, run_dir, github_run_url()))
        except httpx.HTTPError as exc:
            print(f"warning: could not post to Slack ({type(exc).__name__})", file=sys.stderr)

    defects = analyse(results, args.out)
    if defects:
        write_defects(defects, run_dir)

    tokens = sum(r.prompt_tokens + r.completion_tokens for r in results)
    calls = sum(r.model_calls for r in results)
    print(f"\n{len(results)} spec(s): {counts['pass']} passed, {counts['fail']} failed, {counts['flaky']} flaky, "
          f"{counts['error']} errors. {calls} model calls, {tokens:,} tokens.")
    if defects:
        failing = sum(len(d.failures) for d in defects)
        print(f"defect analysis: {failing} failing test(s) -> {len(defects)} defect(s)")
        for defect in defects:
            print(f"   {defect.id} [{defect.severity.split(':')[0]}] {defect.title}  (affects {len(defect.specs)})")
        print(f"defects: {run_dir / 'defects.html'}")
        if args.file_jira:
            _file_in_jira(defects, run_dir)
    print(f"report: {index}")
    return results, run_dir, defects


def _print_verdict(result: RunResult) -> None:
    mode = {"replay": " [replayed, no model steps]", "healed": f" [replay broke at step {result.healed_at}, healed]"}
    print(f"   {result.verdict.upper()} after {len(result.steps)} steps, {result.duration_s:.1f}s "
          f"(model {result.model_s:.1f}s){mode.get(result.mode, '')}: {result.reason}")
    for warning in result.warnings[:3]:
        print(f"   warning: {warning}")
    if len(result.warnings) > 3:
        print(f"   ...and {len(result.warnings) - 3} more warnings in the report")
    if result.verdict != "pass":
        print(f"   bug report: {Path(result.out_dir) / 'bug.md'}" if result.verdict in ("fail", "flaky")
              else f"   replay it: uv run playwright show-trace {Path(result.out_dir) / 'trace.zip'}")


# --- explore --------------------------------------------------------------------

def _explore(args: argparse.Namespace) -> int:
    data = _pairs(args.data)
    avoid = tuple(w.strip() for w in args.avoid.split(",") if w.strip()) if args.avoid is not None else DEFAULT_AVOID
    options = _options(args)
    model = _model()
    out_dir = args.out / f"explore-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        with open_browser(headed=args.headed, browser=args.browser) as browser:
            result = explore(browser, args.url, model, out_dir=out_dir, budget=args.steps, data=data,
                             focus=args.focus, avoid=avoid, options=options, log=_log(args))
    finally:
        model.close()
    print(f"\n{result.count('bug')} bugs proven, {result.count('suspected')} suspected, {result.count('warning')} warnings "
          f"across {len(result.pages)} pages in {len(result.steps)} actions ({result.duration_s:.0f}s). {result.stopped}.")
    print(f"report: {out_dir / 'report.html'}")
    print(f"findings: {out_dir / 'findings.md'}")
    print(f"next: nightshift generate --from {out_dir / 'discovered.json'}")
    return 1 if result.count("bug") else 0


# --- generate -------------------------------------------------------------------

def _generate(args: argparse.Namespace) -> int:
    story = args.story or (args.story_file.read_text(encoding="utf-8") if args.story_file else "")
    url, pages = args.url or "", {}
    if args.from_file:
        found_url, pages = load_pages(args.from_file)
        url = url or found_url
    if not url:
        raise ValueError("give --url, or --from a discovered.json that has one")
    known = _pairs(args.data)

    model = _model()
    try:
        if args.explore_steps > 0 and not pages:
            out_dir = args.out / f"explore-{datetime.now():%Y%m%d-%H%M%S}"
            print(f"exploring {url} for {args.explore_steps} steps to learn the app...")
            with open_browser(headed=args.headed, browser=args.browser) as browser:
                pages = explore(browser, url, model, out_dir=out_dir, budget=args.explore_steps, data=known,
                                options=_options(args), log=_log(args)).pages
        if not pages and not story:
            raise ValueError("nothing to work from: give --story/--story-file, --from, or --explore-steps")
        specs = generate_specs(model, url=url, pages=pages, story=story, count=args.count)
    except (ModelError, InvalidAction) as exc:
        print(f"error: the model could not write specs ({exc})", file=sys.stderr)
        return 2
    finally:
        model.close()

    source = "a user story" if story and not pages else "an explored app" if pages and not story else "a story and an explored app"
    paths = write_specs(specs, args.specs_out, url=url, known_data=known, source=source)
    print(f"\nwrote {len(paths)} draft spec(s):")
    for path in paths:
        print(f"   {path}")
    print("read them, fix what's wrong, then: nightshift run " + str(args.specs_out))
    return 0 if paths else 2


# --- export and report ----------------------------------------------------------

def _export(args: argparse.Namespace) -> int:
    store = RecordingStore(args.recordings)
    written, missing = [], []
    for spec in load_specs(args.specs):
        recording = store.load(spec)
        if recording is None:
            missing.append(spec.name)
            continue
        written.append(write_export(spec, recording, args.to))
    for path in written:
        print(f"wrote {path}")
    if missing:
        print(f"no saved path for: {', '.join(missing)}. Run them first (a path is saved when a spec passes, "
              "and dropped when the spec changes).")
    if written:
        print(f"run them with Playwright: npx playwright test {args.to}")
    return 0 if written else 2


def _report(args: argparse.Namespace) -> int:
    path = write_history(args.out)
    print(f"history: {path}" if path else f"no runs in {args.out}")
    return 0


STARTER_SPEC = """\
# One user flow, in plain English. Nightshift's agent runs it in a real browser.
# Write steps the way you'd brief a new tester, and expected results that can be
# checked by reading the final page (name the text or number to look for).
name: sign-in
url: {url}
steps:
  - open the sign-in page
  - sign in with the test account
expect:
  - the page greets the signed-in user by name
data:
  # The model only ever sees {{{{email}}}} and {{{{password}}}}; values are typed in at the last moment.
  email: ${{NS_EMAIL}}
  password: ${{NS_PASSWORD}}
max_steps: 15
"""


def _init(args: argparse.Namespace) -> int:
    created = []
    spec = Path("specs/sign-in.yaml")
    if not spec.exists():
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(STARTER_SPEC.format(url=args.url), encoding="utf-8")
        created.append(spec)

    workflow = Path(".github/workflows/nightshift.yml")
    example = Path(__file__).resolve().parent.parent / "examples" / "github-workflow.yml"
    if not workflow.exists() and example.exists():
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(workflow)

    gitignore = Path(".gitignore")
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if "runs/" not in lines:
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(("\n" if lines and lines[-1] else "") + "# Nightshift results (recordings in .nightshift/ are meant to be committed)\nruns/\n")
        created.append(gitignore)

    for path in created:
        print(f"wrote {path}")
    print("\nnext:\n  1. edit specs/sign-in.yaml for your app, set NS_EMAIL and NS_PASSWORD"
          "\n  2. nightshift run specs/ --headed"
          f"\n  3. or let it find bugs on its own: nightshift explore {args.url}")
    return 0


# --- validate, cases, triage ------------------------------------------------------

def _validate(args: argparse.Namespace) -> int:
    requirements = parse_requirements(args.requirements.read_text(encoding="utf-8"))
    if not requirements:
        raise ValueError(f"{args.requirements}: no requirements found (write one per bullet or numbered line)")
    ids = {r.id for r in requirements}
    print(f"{len(requirements)} requirement(s) in {args.requirements}")

    # Values given with --data go into the environment, never into the spec files:
    # the files say ${NS_PASSWORD}, so designed test cases can be committed safely.
    known = {}
    for key, value in _pairs(args.data).items():
        os.environ[env_name(key)] = value
        known[key] = "${" + env_name(key) + "}"

    out: Path = args.specs_out
    if args.redesign and out.is_dir():
        redo = ids if args.redesign == "all" else {r.strip().upper() for r in args.redesign.split(",") if r.strip()}
        for path in out.glob("*.yaml"):
            if set(_quick_requirements(path)) & redo:
                path.unlink()
    existing = load_specs([out]) if out.is_dir() and any(out.glob("*.yaml")) else []
    covered = {rid for spec in existing for rid in spec.requirements}
    to_design = [r for r in requirements if r.id not in covered]

    model = _model()
    if to_design:
        pages: dict = {}
        if args.from_file:
            _, pages = load_pages(args.from_file)
        elif args.explore_steps > 0:
            print(f"exploring {args.url} for {args.explore_steps} steps to learn the app...")
            with open_browser(headed=args.headed, browser=args.browser) as browser:
                pages = explore(browser, args.url, model, out_dir=args.out / f"explore-{datetime.now():%Y%m%d-%H%M%S}",
                                budget=args.explore_steps, data=_pairs(args.data), options=_options(args),
                                log=_log(args)).pages
        for requirement in to_design:
            print(f"designing tests for {requirement.id}: {requirement.text[:80]}")
            try:
                cases = design_tests(model, requirement, url=args.url, pages=pages, max_cases=args.cases,
                                     data_keys=tuple(known))
            except (ModelError, InvalidAction) as exc:
                print(f"   could not design tests for {requirement.id}: {exc}", file=sys.stderr)
                continue
            for path in write_specs(cases, out, url=args.url, known_data=known, source=f"requirement {requirement.id}"):
                print(f"   {path}")
            for problem in cases[0].get("review", []) if cases else []:
                print(f"   review: {problem}")
    else:
        print(f"reusing the test cases in {out} (--redesign to design them again)")

    if args.design_only:
        model.close()
        print(f"\nthe designed test cases are in {out}. Read them, fix what is wrong, then run validate again.")
        return 0
    specs = [spec for spec in load_specs([out]) if set(spec.requirements) & ids] if out.is_dir() else []
    if not specs:
        model.close()
        raise ValueError("no test cases to run")
    results, run_dir, defects = _execute(specs, args, model)

    rows = build_matrix(requirements, specs, results, defects)
    matrix = write_traceability(rows, run_dir, results)
    write_test_cases(specs, run_dir / "test-cases.csv", {r.spec: r for r in results})
    counts = Counter(row.status for row in rows)
    print(f"\nvalidation: {len(rows)} requirement(s): {counts['pass']} passed, {counts['fail']} failed, "
          f"{counts['blocked']} blocked, {counts['untested']} not covered")
    for row in rows:
        defects_note = f"  ({', '.join(row.defects)})" if row.defects else ""
        print(f"   {row.requirement.id:<8} {row.status.upper():<9} {row.requirement.text[:70]}{defects_note}")
    print(f"traceability matrix: {matrix}")
    return 1 if counts["fail"] else 2 if counts["blocked"] or counts["untested"] else 0


def _quick_requirements(path: Path) -> list[str]:
    try:
        return list(load_specs([path])[0].requirements)
    except SpecError:
        return []


def _cases(args: argparse.Namespace) -> int:
    specs = load_specs(args.specs)
    results = {r.spec: r for r in load_results(args.run)} if args.run else {}
    path = write_test_cases(specs, args.to, results)
    print(f"wrote {path} and {path.with_suffix('.md')}: {len(specs)} test case(s)")
    return 0


def _triage(args: argparse.Namespace) -> int:
    results = load_results(args.run)
    defects = analyse(results, args.run.parent)
    write_defects(defects, args.run)
    failing = sum(len(d.failures) for d in defects)
    print(f"{failing} failing test(s) -> {len(defects)} defect(s)")
    for defect in defects:
        print(f"   {defect.id} [{defect.severity.split(':')[0]}] {defect.title}  (affects {len(defect.specs)})")
    print(f"defects: {args.run / 'defects.html'}")
    if args.file_github and defects:
        for url in file_github_issues(defects, args.run, args.file_github):
            print(f"filed {url}")
    if args.file_jira and defects:
        _file_in_jira(defects, args.run)
    return 0


def _file_in_jira(defects: list[Defect], run_dir: Path) -> None:
    config = JiraConfig.from_env()
    if config is None:
        print("jira: not set up. Set JIRA_URL, JIRA_PROJECT, and JIRA_EMAIL + JIRA_API_TOKEN (Cloud) "
              "or JIRA_TOKEN (Data Center).", file=sys.stderr)
        return
    try:
        for defect_id, action, key in file_jira_issues(defects, run_dir, config):
            print(f"jira: {defect_id} {action} {key}  {config.url}/browse/{key}")
    except (JiraError, httpx.HTTPError) as exc:
        print(f"jira: could not file the defects ({exc})", file=sys.stderr)


def _serve(args: argparse.Namespace) -> int:
    import webbrowser

    from .dashboard.server import Dashboard, serve

    dashboard = Dashboard(Path.cwd(), args.specs or [Path("specs")], args.out)
    server = serve(dashboard, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Nightshift dashboard: {url}   (only this computer can open it; Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dashboard.close()
        server.server_close()
    return 0


def _pairs(items: list[str]) -> dict[str, str]:
    pairs = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--data takes KEY=VALUE, got {item!r}")
        pairs[key.strip()] = value
    return pairs
