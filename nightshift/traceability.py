"""Application validation artifacts: the traceability matrix and the test-case document.

The traceability matrix answers the question a release sign-off asks: for every
requirement, which tests cover it, did they pass, and which defects block it.
The test-case document is the spreadsheet QA teams keep: one row per case with
its title, the requirements it covers, how it was designed, priority, steps,
expected results and last result. CSV opens in Excel and imports into most
test-management tools.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .defects import Defect, _rel
from .generate import Requirement
from .report import page_html, pill
from .result import RunResult
from .spec import Spec

STATUS_LABEL = {"pass": "Passed", "fail": "Failed", "blocked": "Blocked", "untested": "Not covered"}
STATUS_PILL = {"pass": "pass", "fail": "fail", "blocked": "error", "untested": "flaky"}


@dataclass
class Row:
    requirement: Requirement
    specs: list[Spec]
    results: list[RunResult]
    status: str  # pass | fail | blocked | untested
    defects: list[str]


def build_matrix(requirements: list[Requirement], specs: list[Spec], results: list[RunResult],
                 defects: list[Defect]) -> list[Row]:
    by_spec = {result.spec: result for result in results}
    rows = []
    for requirement in requirements:
        covering = [spec for spec in specs if requirement.id in spec.requirements]
        found = [by_spec[spec.name] for spec in covering if spec.name in by_spec]
        verdicts = [result.verdict for result in found]
        if not covering:
            status = "untested"
        elif "fail" in verdicts:
            status = "fail"
        elif "error" in verdicts or len(found) < len(covering):
            status = "blocked"  # the tester couldn't finish: no evidence either way
        else:
            status = "pass"  # flaky counts as passed; the matrix shows the flaky verdict
        names = {spec.name for spec in covering}
        blocking = [d.id for d in defects if names & set(d.specs)]
        rows.append(Row(requirement, covering, found, status, blocking))
    return rows


def write_traceability(rows: list[Row], run_dir: Path, results: list[RunResult]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter(row.status for row in rows)
    covered = len(rows) - counts["untested"]
    summary = (f"{len(rows)} requirements: {counts['pass']} passed, {counts['fail']} failed, "
               f"{counts['blocked']} blocked, {counts['untested']} not covered. Coverage {covered}/{len(rows)}.")

    # Markdown, for a pull request or a release note.
    lines = ["# Traceability matrix", "", summary, "",
             "| Requirement | Status | Tests | Defects |", "|---|---|---|---|"]
    for row in rows:
        tests = "<br>".join(f"{spec.name} ({spec.technique or 'case'}): {_verdict(row, spec)}" for spec in row.specs) or "none"
        req = f"**{row.requirement.id}** {row.requirement.text}".replace("|", "\\|")
        lines.append(f"| {req} | {STATUS_LABEL[row.status]} | {tests} | {', '.join(row.defects) or ''} |")
    health = _health(results)
    if health:
        lines += ["", "## Site health (from every page the tests visited)", "", *[f"- {h}" for h in health]]
    (run_dir / "traceability.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # CSV, for a spreadsheet.
    with (run_dir / "traceability.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Requirement", "Text", "Status", "Test case", "Technique", "Priority", "Result", "Defects"])
        for row in rows:
            for spec in row.specs or [None]:
                writer.writerow([row.requirement.id, row.requirement.text, STATUS_LABEL[row.status],
                                 spec.name if spec else "", spec.technique if spec else "",
                                 spec.priority if spec else "", _verdict(row, spec) if spec else "",
                                 " ".join(row.defects)])

    # HTML, to read.
    table = []
    for row in rows:
        tests = "".join(
            f'<li>{_verdict_pill(row, spec)} <a href="{escape(_report_link(row, spec, run_dir))}">{escape(spec.name)}</a>'
            f' <span class="muted">{escape(spec.technique or "")} {escape(spec.priority or "")}</span></li>'
            for spec in row.specs) or '<li class="muted">no tests</li>'
        defects = " ".join(f'<a href="defects.html">{escape(d)}</a>' for d in row.defects)
        table.append(f"<tr><td><strong>{escape(row.requirement.id)}</strong></td><td>{escape(row.requirement.text)}</td>"
                     f'<td><span class="pill {STATUS_PILL[row.status]}">{STATUS_LABEL[row.status]}</span></td>'
                     f'<td><ul class="plain">{tests}</ul></td><td>{defects}</td></tr>')
    health_html = ("<h2>Site health</h2><ul class=\"plain panel\">"
                   + "".join(f"<li>{escape(h)}</li>" for h in health) + "</ul>") if health else ""
    body = ('<p><a href="index.html">This run</a> · <a href="defects.html">Defects</a></p>'
            f'<h1>Traceability matrix</h1><p class="reason">{escape(summary)}</p>'
            '<div class="wrap"><table><tr><th>ID</th><th>Requirement</th><th>Status</th><th>Tests</th><th>Defects</th></tr>'
            f'{"".join(table)}</table></div>{health_html}')
    (run_dir / "traceability.html").write_text(page_html("Traceability matrix", body), encoding="utf-8")
    return run_dir / "traceability.html"


def write_test_cases(specs: list[Spec], path: Path, results: dict[str, RunResult] | None = None) -> Path:
    """The test-case document: CSV (Excel, test-management import) and Markdown next to it."""
    results = results or {}
    header = ["ID", "Title", "Requirements", "Technique", "Priority", "Preconditions", "Steps", "Expected result",
              "Test data", "Last result"]
    rows = []
    for spec in specs:
        result = results.get(spec.name)
        rows.append([
            spec.name, spec.title or spec.steps[0], ", ".join(spec.requirements), spec.technique, spec.priority,
            f"Fresh browser at {spec.url}",
            "\n".join(f"{i}. {step}" for i, step in enumerate(spec.steps, 1)),
            "\n".join(f"- {item}" for item in spec.expect),
            ", ".join(spec.data) if spec.data else "",
            f"{result.verdict}: {result.reason}" if result else "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # the BOM makes Excel read ₹ correctly
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)

    md = ["# Test cases", "", f"{len(specs)} case(s).", ""]
    for row in rows:
        md += [f"## {row[0]}: {row[1]}", "",
               f"Requirements: {row[2] or 'none'} · Technique: {row[3] or 'n/a'} · Priority: {row[4] or 'n/a'}", "",
               f"**Preconditions:** {row[5]}", "", "**Steps:**", "", row[6], "", "**Expected:**", "", row[7], ""]
        if row[8]:
            md += [f"**Test data:** {row[8]}", ""]
        if row[9]:
            md += [f"**Last result:** {row[9]}", ""]
    path.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    return path


def _verdict(row: Row, spec: Spec) -> str:
    result = next((r for r in row.results if r.spec == spec.name), None)
    return result.verdict if result else "not run"


def _verdict_pill(row: Row, spec: Spec) -> str:
    result = next((r for r in row.results if r.spec == spec.name), None)
    return pill(result.verdict) if result else '<span class="pill error">NOT RUN</span>'


def _report_link(row: Row, spec: Spec, run_dir: Path) -> str:
    result = next((r for r in row.results if r.spec == spec.name), None)
    return _rel(Path(result.out_dir) / "report.html", run_dir) if result else "#"


def _health(results: list[RunResult]) -> list[str]:
    """Warnings from every page any test visited, deduplicated: a11y gaps, broken links, slow calls, console errors."""
    seen: dict[str, None] = {}
    for result in results:
        for warning in result.warnings:
            if warning.startswith(("a11y", "broken", "slow", "console")):
                seen.setdefault(warning, None)
    return list(seen)[:40]
