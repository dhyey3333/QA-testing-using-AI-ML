"""What people read: an HTML report per spec, a bug report per failure, a run index,
a history dashboard across runs, JUnit XML for CI, and a Markdown summary.

Plain files with no server and no JavaScript: open them from disk, attach them to
a CI run, or serve the runs/ folder as is.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from pathlib import Path

from .result import Check, RunResult, Step
from .spec import Spec

VERDICT_LABEL = {"pass": "PASS", "fail": "FAIL", "flaky": "FLAKY", "error": "ERROR"}

CSS = """
:root { --bg:#f7f7f5; --panel:#fff; --ink:#1c1c1a; --muted:#6b6b66; --line:#e4e3de;
  --pass:#1f7a3d; --pass-bg:#e3f4e8; --fail:#b42318; --fail-bg:#fde7e4; --flaky:#9a6200; --flaky-bg:#fdf1d8;
  --error:#55555a; --error-bg:#ececef; --accent:#3552d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141413; --panel:#1d1d1b; --ink:#ecebe6;
  --muted:#a3a29c; --line:#34332f; --pass:#6fd08c; --pass-bg:#16301f; --fail:#ff8a7a; --fail-bg:#3a1a16;
  --flaky:#f0b450; --flaky-bg:#352810; --error:#b9b9c0; --error-bg:#2a2a2e; --accent:#8fa2ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
a { color:var(--accent); }
h1 { font-size:24px; margin:8px 0 4px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
h2 { font-size:16px; margin:32px 0 10px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.reason { font-size:17px; margin:6px 0 16px; }
.pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:13px; font-weight:700; letter-spacing:.03em; }
.pass { color:var(--pass); background:var(--pass-bg); } .fail { color:var(--fail); background:var(--fail-bg); }
.flaky { color:var(--flaky); background:var(--flaky-bg); } .error { color:var(--error); background:var(--error-bg); }
.meta { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:8px 20px; margin:0; }
.meta div { min-width:0; } .meta dt { color:var(--muted); font-size:12px; } .meta dd { margin:0; overflow-wrap:anywhere; }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; color:var(--muted); font-weight:600; }
tr:last-child td { border-bottom:none; }
.wrap { overflow-x:auto; }
ol.steps { list-style:none; padding:0; margin:0; display:grid; gap:10px; }
ol.steps li { display:grid; grid-template-columns:minmax(0,240px) minmax(0,1fr); gap:14px; background:var(--panel);
  border:1px solid var(--line); border-radius:10px; padding:10px; }
ol.steps img { width:100%; border-radius:6px; border:1px solid var(--line); display:block; }
.desc { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:13px; overflow-wrap:anywhere; margin:0 0 4px; }
.thought { color:var(--muted); margin:0 0 6px; }
.chip { display:inline-block; font-size:12px; padding:1px 8px; border-radius:6px; border:1px solid var(--line); color:var(--muted); }
.chip.bad { color:var(--fail); border-color:var(--fail); } .chip.good { color:var(--pass); border-color:var(--pass); }
ul.plain { margin:0; padding-left:20px; } ul.plain li { margin:2px 0; overflow-wrap:anywhere; }
.muted { color:var(--muted); }
.grid-cell { display:inline-block; width:14px; height:14px; border-radius:3px; }
.grid-cell.pass { background:var(--pass); } .grid-cell.fail { background:var(--fail); }
.grid-cell.flaky { background:var(--flaky); } .grid-cell.error { background:var(--error); }
@media (max-width: 640px) { ol.steps li { grid-template-columns:1fr; } }
"""


def page_html(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def pill(verdict: str) -> str:
    return f'<span class="pill {escape(verdict)}">{VERDICT_LABEL.get(verdict, verdict.upper())}</span>'


# --- one spec -------------------------------------------------------------------

def write_spec_report(result: RunResult, spec: Spec) -> None:
    out_dir = Path(result.out_dir)
    (out_dir / "report.html").write_text(spec_report_html(result, spec), encoding="utf-8")
    if result.verdict in ("fail", "flaky"):
        (out_dir / "bug.md").write_text(bug_report(result, spec), encoding="utf-8")


def spec_report_html(result: RunResult, spec: Spec) -> str:
    out_dir = Path(result.out_dir)
    tokens = result.prompt_tokens + result.completion_tokens
    mode = {"agent": "agent", "replay": "replayed saved path (no model steps)",
            "healed": f"replay broke at step {result.healed_at}; agent healed it"}.get(result.mode, result.mode)
    meta = [
        ("URL", f'<a href="{escape(result.url)}">{escape(result.url)}</a>'),
        ("Mode", escape(mode)),
        ("Steps", str(len(result.steps))),
        ("Time", f"{result.duration_s:.1f}s (model {result.model_s:.1f}s)"),
        ("Model", escape(result.model)),
        ("Model calls", f"{result.model_calls} ({tokens:,} tokens)" if result.model_calls else "0"),
        ("Browser", escape(f"{result.browser} {result.viewport}")),
        ("Started", escape(result.started_at)),
    ]
    body = [
        '<p><a href="../index.html">All specs in this run</a></p>',
        f"<h1>{pill(result.verdict)} {escape(result.spec)}</h1>",
        f'<p class="reason">{escape(result.reason)}</p>',
        '<dl class="meta panel">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in meta) + "</dl>",
    ]

    if result.attempts:
        rows = "".join(
            f"<tr><td>{i}</td><td>{pill(a['verdict'])}</td><td>{escape(a['reason'])}</td>"
            f"<td>{escape(a['mode'])}</td><td><a href=\"{escape(_rel(a['out_dir'], out_dir))}/report.html\">report</a></td></tr>"
            for i, a in enumerate(result.attempts, 1)
        )
        body.append(f'<h2>Attempts</h2><div class="wrap"><table><tr><th>#</th><th>Verdict</th><th>Reason</th>'
                    f"<th>Mode</th><th></th></tr>{rows}</table></div>")

    if result.checks:
        rows = "".join(
            f"<tr><td>{pill('pass' if c.holds else 'fail')}</td><td>{escape(c.expected)}</td>"
            f"<td>{_evidence_cell(c)}</td><td>{escape(c.why)}</td></tr>"
            for c in result.checks
        )
        body.append('<h2>Expected results (the judge)</h2><div class="wrap"><table><tr><th></th><th>Expected</th>'
                    f"<th>Evidence quoted from the page</th><th>Why</th></tr>{rows}</table></div>")
    else:
        body.append("<h2>Expected results</h2><ul class=\"plain panel\">"
                    + "".join(f"<li>{escape(e)}</li>" for e in spec.expect) + "</ul>")

    if result.app_errors:
        body.append('<h2>Errors the browser saw</h2><ul class="plain panel">'
                    + "".join(f"<li>{escape(e)}</li>" for e in result.app_errors) + "</ul>")
    if result.warnings:
        body.append('<h2>Warnings</h2><ul class="plain panel">'
                    + "".join(f"<li>{escape(w)}</li>" for w in result.warnings) + "</ul>")

    body.append("<h2>Steps</h2><ol class=\"steps\">" + "".join(step_html(s) for s in result.steps) + "</ol>")

    files = [("trace.zip", "Playwright trace (open with <code>uv run playwright show-trace trace.zip</code>)"),
             ("video.webm", "Video"), ("bug.md", "Bug report (Markdown, paste into Jira or GitHub)"),
             ("result.json", "Raw result (JSON)")]
    links = "".join(f'<li><a href="{name}">{name}</a>: {label}</li>'
                    for name, label in files if (out_dir / name).exists() or name == "bug.md" and result.verdict in ("fail", "flaky"))
    body.append(f'<h2>Files</h2><ul class="plain panel">{links}</ul>')

    steps = "".join(f"<li>{escape(s)}</li>" for s in spec.steps)
    body.append(f'<h2>The spec</h2><div class="panel"><ol>{steps}</ol>'
                f'<p class="muted">{escape(str(spec.path or ""))}</p></div>')
    return page_html(f"{result.spec}: {VERDICT_LABEL.get(result.verdict, result.verdict)}", "\n".join(body))


def _evidence_cell(check: Check) -> str:
    parts = [escape(q) for q in check.evidence]
    parts += [f'<span class="muted">not on the page:</span> {escape(q)}' for q in check.absent]
    return "<br>".join(parts) or '<span class="muted">nothing on the page</span>'


def step_html(step: Step) -> str:
    image = (f'<a href="{escape(step.screenshot)}"><img loading="lazy" src="{escape(step.screenshot)}" '
             f'alt="Page before step {step.index}"></a>') if step.screenshot else ""
    bad = step.outcome.startswith(("failed", "invalid")) or step.outcome == "no change"
    tone = "bad" if bad else "good" if step.outcome in ("changed", "replayed") else ""
    timing = []
    if step.model_ms:
        timing.append(f"model {step.model_ms / 1000:.1f}s")
    if step.action_ms:
        timing.append(f"action {step.action_ms / 1000:.1f}s")
    thought = f'<p class="thought">{escape(step.thought)}</p>' if step.thought else ""
    return (f"<li><div>{image}</div><div><p class=\"desc\">{step.index}. {escape(step.description)}</p>{thought}"
            f'<span class="chip {tone}">{escape(step.outcome)}</span> <span class="muted">{" · ".join(timing)}</span></div></li>')


# --- bug reports ------------------------------------------------------------------

def instruction(step: Step) -> str | None:
    """A step as a person would write it in a bug report. None for steps nobody needs to repeat."""
    action = step.action
    if action is None:
        return None
    target = f'"{step.target_label}"' if step.target_label else f"element [{action.id}]"
    match action.kind:
        case "click":
            return f"Click {target}"
        case "type":
            return f"Type `{action.text}` into {target}"
        case "select":
            return f'Choose "{action.value}" in {target}'
        case "press":
            return f"Press {action.key}"
        case "back":
            return "Go back"
        case "goto":
            return f"Go to {action.value}"
    return None


def severity(result: RunResult) -> str:
    if result.verdict == "flaky":
        return "Low: intermittent, passed on retry"
    reason = result.reason
    if reason.startswith(("HTTP 5", "uncaught JS error")):
        return "High: the app crashed or the server failed"
    if "had no effect" in reason or "times in a row" in reason:
        return "High: a control the flow needs does not work"
    return "Medium: the page shows a wrong or missing result"


def bug_report(result: RunResult, spec: Spec) -> str:
    out_dir = Path(result.out_dir)
    lines = [f"# {result.reason}", ""]
    if result.attempts:
        runs = f"failed {sum(a['verdict'] == 'fail' for a in result.attempts)} of {len(result.attempts)} runs"
    else:
        runs = "1 run (no retries)"
    rows = [
        ("Spec", f"`{spec.name}`" + (f" ({spec.path})" if spec.path else "")),
        ("URL", result.url),
        ("Browser", f"{result.browser}, {result.viewport}"),
        ("Found", f"{result.started_at} by Nightshift ({result.model})"),
        ("Reproduced", runs),
        ("Severity (suggested)", severity(result)),
    ]
    lines += ["| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], ""]

    lines += ["## Steps to reproduce", "", f"1. Open {result.url}"]
    n = 2
    for step in result.steps:
        if step.outcome.startswith("invalid"):
            continue
        text = instruction(step)
        if text is None:
            continue
        if step.outcome.startswith("failed"):
            text += f" (this fails: {step.outcome.removeprefix('failed: ')})"
        elif step.outcome == "no change":
            text += " (nothing happens)"
        lines.append(f"{n}. {text}")
        n += 1
    if spec.data:
        lines += ["", "Values in `{{double braces}}` are test data from the spec."]

    lines += ["", "## Expected", "", *[f"- {e}" for e in spec.expect], "", "## Actual", "", f"- {result.reason}"]
    for check in result.checks:
        if not check.holds:
            lines.append(f"- Not shown: {check.expected}" + (f" ({check.why})" if check.why else ""))
    lines += [f"- Browser: {e}" for e in result.app_errors]

    last_shot = next((s.screenshot for s in reversed(result.steps) if s.screenshot), None)
    lines += ["", "## Evidence", ""]
    if last_shot:
        lines.append(f"- Last screenshot: `{out_dir / last_shot}`")
    lines.append(f"- Trace (every action, DOM, network, console): `uv run playwright show-trace {out_dir / 'trace.zip'}`")
    if (out_dir / "video.webm").exists():
        lines.append(f"- Video: `{out_dir / 'video.webm'}`")
    if result.warnings:
        lines += ["- Warnings:", *[f"  - {w}" for w in result.warnings[:10]]]
    return "\n".join(lines) + "\n"


# --- a whole run --------------------------------------------------------------------

def write_run_index(results: list[RunResult], run_dir: Path, title: str = "Nightshift run") -> Path:
    counts = {v: sum(r.verdict == v for r in results) for v in VERDICT_LABEL}
    summary = " · ".join(f"{counts[v]} {v}" for v in VERDICT_LABEL if counts[v])
    rows = "".join(
        f"<tr><td>{pill(r.verdict)}</td><td><a href=\"{escape(_rel(r.out_dir, run_dir))}/report.html\">{escape(r.spec)}</a></td>"
        f"<td>{escape(r.reason)}</td><td>{len(r.steps)}</td><td>{r.duration_s:.0f}s</td><td>{escape(r.mode)}</td></tr>"
        for r in results
    )
    body = (f'<p><a href="../index.html">History</a></p><h1>{escape(title)}</h1>'
            f'<p class="reason">{escape(summary)}</p>'
            '<div class="wrap"><table><tr><th>Verdict</th><th>Spec</th><th>Reason</th><th>Steps</th><th>Time</th>'
            f"<th>Mode</th></tr>{rows}</table></div>")
    path = run_dir / "index.html"
    path.write_text(page_html(title, body), encoding="utf-8")
    return path


def write_history(runs_root: Path, limit: int = 30) -> Path | None:
    """runs/index.html: every spec's verdict across the last `limit` runs. Spots flaky specs at a glance."""
    runs = []
    for folder in sorted((p for p in runs_root.iterdir() if p.is_dir()), reverse=True) if runs_root.exists() else []:
        summary = folder / "summary.json"
        if summary.exists():
            try:
                runs.append((folder, json.loads(summary.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        if len(runs) >= limit:
            break
    if not runs:
        return None

    specs = sorted({row["spec"] for _, rows in runs for row in rows})
    header = "".join(f'<th title="{escape(folder.name)}"><a href="{escape(folder.name)}/index.html">{escape(folder.name[4:13])}</a></th>'
                     for folder, _ in runs)
    body_rows = []
    for spec in specs:
        cells = []
        for folder, rows in runs:
            row = next((r for r in rows if r["spec"] == spec), None)
            if row is None:
                cells.append("<td></td>")
                continue
            link = f"{_rel(row['out_dir'], runs_root)}/report.html"
            cells.append(f'<td><a href="{escape(link)}" title="{escape(row["verdict"])}: {escape(row["reason"])}">'
                         f'<span class="grid-cell {escape(row["verdict"])}"></span></a></td>')
        passes = sum(1 for _, rows in runs for r in rows if r["spec"] == spec and r["verdict"] == "pass")
        total = sum(1 for _, rows in runs for r in rows if r["spec"] == spec)
        body_rows.append(f"<tr><td>{escape(spec)}</td><td>{passes}/{total}</td>{''.join(cells)}</tr>")

    body = ("<h1>Nightshift history</h1>"
            f'<p class="reason">The last {len(runs)} runs, newest first. Each square is one spec in one run.</p>'
            f'<div class="wrap"><table><tr><th>Spec</th><th>Passed</th>{header}</tr>{"".join(body_rows)}</table></div>'
            f'<p class="muted">Updated {datetime.now():%Y-%m-%d %H:%M}</p>')
    path = runs_root / "index.html"
    path.write_text(page_html("Nightshift history", body), encoding="utf-8")
    return path


def write_junit(results: list[RunResult], path: Path) -> None:
    """JUnit XML, the format Jenkins, GitLab, CircleCI and GitHub test reporters all read."""
    suite = ET.Element("testsuite", name="nightshift", tests=str(len(results)),
                       failures=str(sum(r.verdict == "fail" for r in results)),
                       errors=str(sum(r.verdict == "error" for r in results)),
                       time=f"{sum(r.duration_s for r in results):.2f}")
    for result in results:
        case = ET.SubElement(suite, "testcase", classname="nightshift", name=result.spec, time=f"{result.duration_s:.2f}")
        if result.verdict == "fail":
            failure = ET.SubElement(case, "failure", message=result.reason)
            bug = Path(result.out_dir) / "bug.md"
            failure.text = bug.read_text(encoding="utf-8") if bug.exists() else result.reason
        elif result.verdict == "error":
            ET.SubElement(case, "error", message=result.reason)
        elif result.verdict == "flaky":
            ET.SubElement(case, "system-out").text = f"flaky: {result.reason}"
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def markdown_summary(results: list[RunResult]) -> str:
    """For a GitHub job summary or a chat message."""
    counts = {v: sum(r.verdict == v for r in results) for v in VERDICT_LABEL}
    lines = ["## Nightshift", "", " · ".join(f"**{counts[v]}** {v}" for v in VERDICT_LABEL if counts[v]), "",
             "| | Spec | Reason | Steps | Time |", "|---|---|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "flaky": "⚠️", "error": "⚪"}
    for r in results:
        reason = r.reason.replace("|", "\\|")
        lines.append(f"| {icon.get(r.verdict, '')} | {r.spec} | {reason} | {len(r.steps)} | {r.duration_s:.0f}s |")
    return "\n".join(lines) + "\n"


def _rel(target: str | Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(Path(target).resolve(), start.resolve())).as_posix()
    except ValueError:  # different drives on Windows
        return Path(target).resolve().as_posix()
