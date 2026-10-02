"""The report a QA team sends its client: one self-contained HTML file per run.

    nightshift client-report runs/20261002-110012 --client "Acme Retail" --brand "Your QA Co" [--logo logo.png]
    nightshift run specs/ --client "Acme Retail" --brand "Your QA Co"

What a client reads first: can we release, what is broken and how badly, which requirements
are covered, and the evidence. Screenshots are embedded, so the file can be emailed, attached
to a ticket or printed to PDF as is. With --brand it carries the agency's name, not ours.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from .defects import Defect, analyse, load_results
from .outcome import BUG, ENV_ISSUE, FLAKY, TEST_OUTDATED, categorize
from .report import reproduction
from .result import RunResult
from .spec import Spec

# Light only and printable: this is a document for people outside the team.
CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:#1c1c1a; font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:980px; margin:0 auto; padding:28px 18px 56px; }
header { display:flex; align-items:center; gap:14px; border-bottom:2px solid #1c1c1a; padding-bottom:12px; }
header img { max-height:44px; } header .brand { font-weight:700; font-size:16px; }
h1 { font-size:24px; margin:18px 0 2px; } .sub { color:#5f5f5a; margin:0 0 18px; }
h2 { font-size:15px; margin:30px 0 10px; text-transform:uppercase; letter-spacing:.05em; color:#5f5f5a; }
h3 { font-size:16px; margin:0 0 6px; }
.verdict { padding:14px 16px; border-radius:10px; font-size:17px; font-weight:600; }
.verdict.no { background:#fde7e4; color:#8f1d12; } .verdict.yes { background:#e3f4e8; color:#17602f; }
.verdict small { display:block; font-weight:400; font-size:13px; color:#5f5f5a; margin-top:4px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:14px; }
.tile { border:1px solid #e2e1dc; border-radius:10px; padding:10px 12px; }
.tile b { display:block; font-size:24px; } .tile span { color:#5f5f5a; font-size:12px; }
table { width:100%; border-collapse:collapse; border:1px solid #e2e1dc; }
th, td { text-align:left; padding:8px 10px; border-bottom:1px solid #e2e1dc; vertical-align:top; }
th { font-size:12px; color:#5f5f5a; background:#fafaf8; }
.pill { display:inline-block; padding:1px 9px; border-radius:999px; font-size:12px; font-weight:700; white-space:nowrap; }
.pass { color:#17602f; background:#e3f4e8; } .fail { color:#8f1d12; background:#fde7e4; }
.flaky { color:#7a4d00; background:#fdf1d8; } .error { color:#45454a; background:#ececef; }
.sev-High { color:#8f1d12; font-weight:700; } .sev-Medium { color:#7a4d00; font-weight:700; } .sev-Low { color:#45454a; }
.defect { border:1px solid #e2e1dc; border-radius:10px; padding:14px 16px; margin:12px 0; page-break-inside:avoid; }
.defect img { width:100%; max-width:640px; border:1px solid #e2e1dc; border-radius:6px; margin-top:8px; }
.defect ol { margin:6px 0; padding-left:22px; } .muted { color:#5f5f5a; }
footer { margin-top:36px; padding-top:12px; border-top:1px solid #e2e1dc; color:#5f5f5a; font-size:12px; }
@media print { main { padding:0; } h2 { page-break-after:avoid; } }
"""

LABEL = {"pass": "PASSED", "fail": "DEFECT", "flaky": "FLAKY", "error": "NOT TESTED"}


def write_client_report(run_dir: Path, *, client: str = "", brand: str = "", logo: Path | None = None,
                        specs: dict[str, Spec] | None = None, out: Path | None = None) -> Path:
    results = load_results(run_dir)
    defects = analyse(results, run_dir.parent)
    issues = _issues(run_dir)
    path = out or run_dir / "client-report.html"
    path.write_text(client_report_html(results, defects, run_dir, client=client, brand=brand, logo=logo,
                                       specs=specs or {}, issues=issues), encoding="utf-8")
    return path


def client_report_html(results: list[RunResult], defects: list[Defect], run_dir: Path, *, client: str, brand: str,
                       logo: Path | None, specs: dict[str, Spec], issues: dict) -> str:
    # Runs saved before failures had categories get one worked out now.
    counts = Counter(r.category or categorize(r) or "pass" for r in results)
    bugs = counts[BUG]
    when = _when(results)
    hosts = sorted({urlsplit(r.url).netloc for r in results if r.url})
    title = f"Test report: {client}" if client else "Test report"

    parts = [f"<header>{_logo(logo)}<span class='brand'>{escape(brand or 'Nightshift')}</span></header>",
             f"<h1>{escape(title)}</h1>",
             f"<p class='sub'>{escape(when)} · {escape(', '.join(hosts) or 'no environment')} · {len(results)} tests</p>"]

    high = sum(d.severity.startswith("High") for d in defects)
    if defects:
        verdict = (f"Not ready to release: {len(defects)} defect{'s' if len(defects) != 1 else ''} found"
                   + (f", {high} of high severity" if high else ""))
        parts.append(f"<div class='verdict no'>{escape(verdict)}"
                     "<small>A recommendation from automated tests, not a sign-off.</small></div>")
    elif counts[ENV_ISSUE] or counts[TEST_OUTDATED]:
        parts.append("<div class='verdict no'>No defects found, but some tests could not be run"
                     "<small>See \"Not tested\" below before deciding.</small></div>")
    else:
        parts.append("<div class='verdict yes'>No defects found in this run"
                     "<small>Every passed test was checked against evidence quoted from the page.</small></div>")

    tiles = [(counts["pass"], "passed"), (bugs, "failed: a defect in the app"), (counts[FLAKY], "flaky: failed, then passed"),
             (counts[ENV_ISSUE] + counts[TEST_OUTDATED], "not tested: site down, or the test needs updating")]
    parts.append("<div class='tiles'>" + "".join(f"<div class='tile'><b>{n}</b><span>{escape(t)}</span></div>"
                                                 for n, t in tiles) + "</div>")

    if defects:
        parts.append("<h2>Defects</h2>")
        rows = "".join(
            f"<tr><td>{escape(d.id)}</td><td class='sev-{escape(d.severity.split(':')[0])}'>{escape(d.severity.split(':')[0])}</td>"
            f"<td>{escape(d.title)}</td><td>{escape(', '.join(_name(s, specs) for s in d.specs))}</td>"
            f"<td>{_ticket(issues.get(d.id))}</td></tr>" for d in defects)
        parts.append("<table><tr><th>ID</th><th>Severity</th><th>Defect</th><th>Tests affected</th><th>Ticket</th></tr>"
                     f"{rows}</table>")
        parts += [_defect_detail(d, specs, issues) for d in defects]

    covered = _requirements(results, specs)
    if covered:
        parts.append("<h2>Requirements</h2><table><tr><th>Requirement</th><th>Tests</th><th>Result</th></tr>")
        for req, rows in covered.items():
            worst = _worst([r.verdict for r in rows])
            tests = ", ".join(_name(r.spec, specs) for r in rows)
            parts.append(f"<tr><td>{escape(req)}</td><td>{escape(tests)}</td><td>{_pill(worst)}</td></tr>")
        parts.append("</table>")

    parts.append("<h2>All tests</h2><table><tr><th>Test</th><th>Result</th><th>What happened</th><th>Time</th></tr>")
    for r in sorted(results, key=lambda r: {"fail": 0, "error": 1, "flaky": 2}.get(r.verdict, 3)):
        parts.append(f"<tr><td>{escape(_name(r.spec, specs))}</td><td>{_pill(r.verdict)}</td>"
                     f"<td>{escape(_plain(r))}</td><td>{r.duration_s:.0f}s</td></tr>")
    parts.append("</table>")

    parts.append("<footer><p><b>How to read this.</b> PASSED: every expected result was found on the page, quoted "
                 "and checked. DEFECT: the app did something wrong; each defect lists how to reproduce it. FLAKY: "
                 "failed, then passed when run again. NOT TESTED: the site was unreachable or behind a bot check, "
                 "or the test could not be carried out as written and needs updating.</p>"
                 + (f"<p>Prepared by {escape(brand)}.</p>" if brand else "<p>Prepared with Nightshift.</p>") + "</footer>")
    return ("<!doctype html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
            f"<title>{escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n<main>\n"
            + "\n".join(parts) + "\n</main>\n</body>\n</html>\n")


def _defect_detail(defect: Defect, specs: dict[str, Spec], issues: dict) -> str:
    lead = defect.failures[0]
    result = lead.result
    failing = [c for c in result.checks if not c.holds]
    expected = "".join(f"<li>{escape(c.expected)}" + (f" <span class='muted'>({escape(c.why)})</span>" if c.why else "")
                       + "</li>" for c in failing)
    errors = "".join(f"<li>{escape(e)}</li>" for e in result.app_errors)
    steps = "".join(f"<li>{escape(s)}</li>" for s in reproduction(result))
    shot = next((s.screenshot for s in result.steps if s.index == lead.step and s.screenshot), None) \
        or next((s.screenshot for s in reversed(result.steps) if s.screenshot), None)
    image = _image(Path(result.out_dir) / shot) if shot else ""
    ticket = issues.get(defect.id)
    return (f"<div class='defect'><h3>{escape(defect.id)}. {escape(defect.title)}</h3>"
            f"<p class='muted'>{escape(defect.severity)} · {escape(defect.category)}"
            + (f" · ticket {_ticket(ticket)}" if ticket else "") + "</p>"
            f"<p>{escape(defect.hint)}</p>"
            + (f"<p><b>Expected, but not shown</b></p><ul>{expected}</ul>" if expected else "")
            + (f"<p><b>Errors the browser saw</b></p><ul>{errors}</ul>" if errors else "")
            + (f"<p><b>Steps to reproduce</b> ({escape(_name(result.spec, specs))})</p><ol>{steps}</ol>" if steps else "")
            + (f"<img alt='The page when it went wrong' src='{image}'>" if image else "")
            + "</div>")


def _plain(result: RunResult) -> str:
    if result.verdict == "pass":
        return "Every expected result was shown."
    reason = result.reason.removeprefix("environment: ")
    return f"{reason[:180]}" + ("…" if len(reason) > 180 else "")


def _requirements(results: list[RunResult], specs: dict[str, Spec]) -> dict[str, list[RunResult]]:
    covered: dict[str, list[RunResult]] = {}
    for result in results:
        spec = specs.get(result.spec)
        for req in (spec.requirements if spec else ()):
            covered.setdefault(req, []).append(result)
    return dict(sorted(covered.items()))


def _worst(verdicts: list[str]) -> str:
    for verdict in ("fail", "error", "flaky"):
        if verdict in verdicts:
            return verdict
    return "pass"


def _name(spec_name: str, specs: dict[str, Spec]) -> str:
    spec = specs.get(spec_name)
    return spec.title if spec and spec.title else spec_name


def _pill(verdict: str) -> str:
    return f"<span class='pill {escape(verdict)}'>{LABEL.get(verdict, verdict.upper())}</span>"


def _ticket(issue: dict | None) -> str:
    if not issue:
        return "<span class='muted'>not filed</span>"
    return f"<a href='{escape(issue['url'])}'>{escape(issue['key'])}</a>"


def _issues(run_dir: Path) -> dict:
    path = run_dir / "issues.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _when(results: list[RunResult]) -> str:
    started = sorted(r.started_at for r in results if r.started_at)
    if not started:
        return datetime.now().strftime("%d %b %Y")
    return datetime.fromisoformat(started[0]).strftime("%d %b %Y, %H:%M")


def _image(path: Path) -> str:
    """A screenshot as a data: URI, so the report is one file. Missing images are left out."""
    try:
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


def _logo(path: Path | None) -> str:
    if not path:
        return ""
    kind = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".svg": "svg+xml", ".webp": "webp"}.get(path.suffix.lower())
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"<img alt='' src='data:image/{kind or 'png'};base64,{data}'>"
