"""Defect analysis: from failing tests to defects a developer can act on.

A run with five failing specs may hold one defect (a shared script that crashes on
every page) or five. For every failure this works out:
  - the category and the likely area: server error (backend), frontend crash,
    dead control (frontend), wrong or missing result
  - a signature, so failures with the same root cause become one defect
  - the step where it broke, with the screenshot the agent saw there
  - what changed since the same spec last passed: the page text, line by line
Then it writes defects.md and defects.html in the run folder, and can file each
defect as a GitHub issue.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from .report import page_html, severity
from .result import RunResult

_RUNS_SUFFIX = re.compile(r"\s*\(failed \d+ of \d+ runs\)$")

HINTS = {
    "server error": "The server failed on {where}. Start with the backend handler for that endpoint; "
                    "the request and its payload are in the trace.",
    "frontend crash": "A script threw “{where}”. It breaks every page that runs it, so look at "
                      "recent changes to shared scripts.",
    "dead control": "“{where}” does nothing when used. Check its click handler and whether "
                    "something covers it.",
    "wrong or missing result": "The flow completed but the page shows the wrong thing. Compare it with "
                               "the last passing run below.",
}
AREA = {"server error": "backend", "frontend crash": "frontend", "dead control": "frontend",
        "wrong or missing result": "backend or frontend"}


@dataclass
class Failure:
    result: RunResult
    category: str
    signature: str
    where: str  # the endpoint, the script error, the control, or the page
    step: int | None  # the last action before the failure showed
    changes: list[str] = field(default_factory=list)  # page lines removed (-) and added (+) since the last pass
    last_pass: str = ""  # out_dir of the last passing run of this spec


@dataclass
class Defect:
    id: str
    title: str
    category: str
    area: str
    severity: str
    hint: str
    failures: list[Failure]

    @property
    def specs(self) -> list[str]:
        return list(dict.fromkeys(f.result.spec for f in self.failures))


def classify(result: RunResult) -> tuple[str, str, str]:
    """(category, signature, where) for one failing result."""
    reason = _RUNS_SUFFIX.sub("", result.reason)
    if result.app_errors:
        first = result.app_errors[0]
        if first.startswith("HTTP 5"):
            return "server error", first, first.split(" on ", 1)[-1]
        if first.startswith("uncaught JS error"):
            message = first.removeprefix("uncaught JS error: ")
            return "frontend crash", f"js:{message}", message
    if "had no effect" in reason or "times in a row" in reason:
        last = _last_action(result)
        label = last.target_label if last and last.target_label else reason.split(" had ")[0]
        return "dead control", f"control:{label.lower()}", label
    page = urlsplit(result.final_url).path or "/"
    failing = next((c.expected for c in result.checks if not c.holds), "")
    detail = failing or reason
    return "wrong or missing result", f"result:{result.spec}:{page}:{detail.lower()[:80]}", page


def analyse(results: list[RunResult], runs_root: Path | None = None) -> list[Defect]:
    failures = []
    for result in results:
        if result.verdict not in ("fail", "flaky"):
            continue
        category, signature, where = classify(result)
        last = _last_action(result)
        failure = Failure(result, category, signature, where, last.index if last else None)
        if runs_root is not None:
            _compare_with_last_pass(failure, runs_root)
        failures.append(failure)

    groups: dict[str, list[Failure]] = {}
    for failure in failures:
        groups.setdefault(failure.signature, []).append(failure)

    rank = {"High": 0, "Medium": 1, "Low": 2}
    defects = []
    for failures_in_group in groups.values():
        lead = failures_in_group[0]
        category = lead.category
        title = _title(lead)
        defects.append(Defect(
            id="", title=title, category=category, area=AREA[category],
            severity=severity(lead.result), hint=HINTS[category].format(where=lead.where),
            failures=failures_in_group,
        ))
    defects.sort(key=lambda d: (rank.get(d.severity.split(":")[0], 3), -len(d.failures)))
    for number, defect in enumerate(defects, 1):
        defect.id = f"D{number}"
    return defects


def write_defects(defects: list[Defect], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "defects.md").write_text(defects_markdown(defects, run_dir), encoding="utf-8")
    (run_dir / "defects.html").write_text(defects_html(defects, run_dir), encoding="utf-8")
    summary = [{"id": d.id, "title": d.title, "category": d.category, "area": d.area,
                "severity": d.severity.split(":")[0], "specs": d.specs} for d in defects]
    (run_dir / "defects.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir / "defects.md"


def defects_markdown(defects: list[Defect], run_dir: Path) -> str:
    failing = sum(len(d.failures) for d in defects)
    lines = [f"# Defects: {len(defects)} from {failing} failing test(s)", ""]
    for defect in defects:
        lines += [f"## {defect.id}. {defect.title}", "", defect_body(defect, run_dir), ""]
    return "\n".join(lines)


def defect_body(defect: Defect, run_dir: Path) -> str:
    """The issue text for one defect: what, where, how bad, how to reproduce, what changed."""
    lead = defect.failures[0]
    lines = [
        "| | |", "|---|---|",
        f"| Category | {defect.category} (likely {defect.area}) |",
        f"| Severity (suggested) | {defect.severity} |",
        f"| Affects | {', '.join(f'`{s}`' for s in defect.specs)} |",
        f"| Breaks at | step {lead.step} of `{lead.result.spec}`: {_step_text(lead)} |" if lead.step else "",
        "",
        f"**Analysis.** {defect.hint}",
        "",
        f"**What the test saw.** {_RUNS_SUFFIX.sub('', lead.result.reason)}",
    ]
    for check in lead.result.checks:
        if not check.holds:
            lines.append(f"- Expected: {check.expected}" + (f" ({check.why})" if check.why else ""))
    if lead.changes:
        lines += ["", f"**What changed since it last passed** (`{_rel(lead.last_pass, run_dir)}`):", "", "```diff",
                  *lead.changes, "```"]
    elif lead.last_pass == "" and len(defect.failures) == 1:
        lines += ["", "No earlier passing run of this spec to compare with."]
    lines += ["", "**Evidence and steps to reproduce:**"]
    for failure in defect.failures:
        out = Path(failure.result.out_dir)
        lines.append(f"- `{failure.result.spec}`: [bug report]({_rel(out / 'bug.md', run_dir)}), "
                     f"[report]({_rel(out / 'report.html', run_dir)}), trace `{out / 'trace.zip'}`")
    return "\n".join(line for line in lines if line is not None)


def defects_html(defects: list[Defect], run_dir: Path) -> str:
    sections = []
    for defect in defects:
        lead = defect.failures[0]
        out = Path(lead.result.out_dir)
        shot = next((s.screenshot for s in lead.result.steps if s.index == lead.step and s.screenshot), None)
        before = _last_pass_shot(lead)
        images = ""
        if shot:
            images += (f'<figure><figcaption>This run, step {lead.step}</figcaption>'
                       f'<img src="{escape(_rel(out / shot, run_dir))}" alt="Failing run at step {lead.step}"></figure>')
        if before:
            images += (f'<figure><figcaption>Last pass, same step</figcaption>'
                       f'<img src="{escape(_rel(before, run_dir))}" alt="Last passing run at the same step"></figure>')
        diff = "".join(f'<div class="{"del" if line.startswith("-") else "add"}">{escape(line)}</div>'
                       for line in lead.changes)
        checks = "".join(f"<li>{escape(c.expected)}" + (f" <span class=muted>({escape(c.why)})</span>" if c.why else "")
                         + "</li>" for c in lead.result.checks if not c.holds)
        links = "".join(f'<li><a href="{escape(_rel(Path(f.result.out_dir) / "report.html", run_dir))}">'
                        f'{escape(f.result.spec)}</a>: {escape(_RUNS_SUFFIX.sub("", f.result.reason))}</li>'
                        for f in defect.failures)
        sections.append(
            f'<section class="panel defect"><h2 class="title"><span class="pill fail">{defect.id}</span> {escape(defect.title)}</h2>'
            f'<p class="muted">{escape(defect.category)} · likely {escape(defect.area)} · {escape(defect.severity)}'
            f' · affects {len(defect.specs)} test(s)</p>'
            f"<p><strong>Analysis.</strong> {escape(defect.hint)}</p>"
            + (f"<p><strong>Not shown:</strong></p><ul>{checks}</ul>" if checks else "")
            + (f'<p><strong>What changed since it last passed:</strong></p><div class="diff">{diff}</div>' if diff else "")
            + (f'<div class="shots">{images}</div>' if images else "")
            + f"<p><strong>Failing tests:</strong></p><ul>{links}</ul></section>"
        )
    style = ("<style>.defect h2.title{text-transform:none;letter-spacing:0;color:inherit;font-size:18px;margin:0 0 6px}"
             ".defect{margin:0 0 16px}.diff{font:13px/1.5 ui-monospace,Consolas,monospace;border:1px solid var(--line);"
             "border-radius:8px;overflow:auto}.diff .del{background:var(--fail-bg);color:var(--fail);padding:0 8px}"
             ".diff .add{background:var(--pass-bg);color:var(--pass);padding:0 8px}.shots{display:grid;"
             "grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:12px}.shots img{width:100%;"
             "border:1px solid var(--line);border-radius:6px}figure{margin:0}figcaption{font-size:12px;color:var(--muted)}</style>")
    failing = sum(len(d.failures) for d in defects)
    body = (style + '<p><a href="index.html">This run</a></p>'
            f"<h1>Defects</h1><p class=\"reason\">{len(defects)} defect(s) from {failing} failing test(s)</p>"
            + ("".join(sections) or "<p>No defects: nothing failed.</p>"))
    return page_html("Defects", body)


def file_github_issues(defects: list[Defect], run_dir: Path, repo: str, label: str = "bug") -> list[str]:
    """One issue per defect via the GitHub CLI (`gh`, already signed in). Returns the issue URLs."""
    urls = []
    for defect in defects:
        body = run_dir / f"{defect.id}.issue.md"
        body.write_text(defect_body(defect, run_dir) + "\n\n_Found by Nightshift._\n", encoding="utf-8")
        done = subprocess.run(["gh", "issue", "create", "--repo", repo, "--title", f"[{defect.category}] {defect.title}",
                               "--body-file", str(body), "--label", label],
                              capture_output=True, text=True, encoding="utf-8")
        if done.returncode != 0:
            raise RuntimeError(f"gh issue create failed for {defect.id}: {done.stderr.strip()[:300]}")
        urls.append(done.stdout.strip())
    return urls


def load_results(run_dir: Path) -> list[RunResult]:
    """The results of a finished run, read back from its result.json files."""
    results = []
    for summary_row in json.loads((run_dir / "summary.json").read_text(encoding="utf-8")):
        path = Path(summary_row["out_dir"]) / "result.json"
        if path.exists():
            results.append(_result_from_json(json.loads(path.read_text(encoding="utf-8"))))
    return results


# --- helpers ----------------------------------------------------------------------

def _last_action(result: RunResult):
    return next((s for s in reversed(result.steps) if s.action is not None
                 and s.action.kind not in ("pass", "fail")), None)


def _step_text(failure: Failure) -> str:
    step = next((s for s in failure.result.steps if s.index == failure.step), None)
    return step.description if step else ""


def _title(failure: Failure) -> str:
    match failure.category:
        case "server error":
            return f"Server error: {failure.where} returns HTTP {failure.signature.split()[1]}"
        case "frontend crash":
            return f"Script crash: {failure.where}"
        case "dead control":
            return f"“{failure.where}” does nothing"
    failing = next((c for c in failure.result.checks if not c.holds), None)
    if failing:
        return f"{failure.result.spec}: expected “{failing.expected}”, not met"
    return f"{failure.result.spec}: {_RUNS_SUFFIX.sub('', failure.result.reason)}"[:160]


def _compare_with_last_pass(failure: Failure, runs_root: Path) -> None:
    """Find the newest run where this spec passed, and diff the final pages."""
    this_run = Path(failure.result.out_dir).resolve()
    for summary in sorted(runs_root.glob("*/summary.json"), reverse=True):
        try:
            rows = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        row = next((r for r in rows if r.get("spec") == failure.result.spec and r.get("verdict") == "pass"), None)
        if row is None or Path(row["out_dir"]).resolve() == this_run:
            continue
        path = Path(row["out_dir"]) / "result.json"
        if not path.exists():
            continue
        before = json.loads(path.read_text(encoding="utf-8")).get("final_text", "")
        if not before:
            continue
        failure.last_pass = row["out_dir"]
        diff = difflib.unified_diff(before.splitlines(), failure.result.final_text.splitlines(), lineterm="", n=0)
        failure.changes = [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")][:20]
        return


def _last_pass_shot(failure: Failure) -> Path | None:
    if not failure.last_pass or not failure.step:
        return None
    shot = Path(failure.last_pass) / f"step-{failure.step:02d}.jpg"
    return shot if shot.exists() else None


def _result_from_json(data: dict) -> RunResult:
    from .actions import Action
    from .result import Check, Step

    steps = [Step(**{**s, "action": Action(**s["action"]) if s.get("action") else None}) for s in data.get("steps", [])]
    checks = [Check(**c) for c in data.get("checks", [])]
    fields = {k: v for k, v in data.items() if k in RunResult.__dataclass_fields__ and k not in ("steps", "checks")}
    return RunResult(**fields, steps=steps, checks=checks)


def _rel(target: str | Path, start: Path) -> str:
    import os

    try:
        return Path(os.path.relpath(Path(target).resolve(), start.resolve())).as_posix()
    except ValueError:
        return Path(target).resolve().as_posix()
