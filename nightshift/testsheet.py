"""The test-case sheet QA teams keep: Excel first, the same rows as CSV, then into Jira.

The way most QA teams work: test cases are written in an Excel sheet, saved as CSV, imported into
Jira (or a test tool inside it), run by hand, and bugs are filed against them. Nightshift does the
same chain. After every run it writes that sheet, filled in, into the run folder:

    test-cases.xlsx   the sheet, formatted, with a coloured Status and links to Jira
    test-cases.csv    the same rows, for Jira's CSV import or any test-management tool
    test-cases.json   the same rows as data, so Nightshift can update the sheet later

Then `nightshift run --file-jira` (or "Send to Jira" in the web app) puts it into Jira: one issue per
test case, one per bug (jira.py, no duplicates), each bug linked to its test case, and the ticket keys
written back into the sheet.

An exploration has no test cases, so it writes the bug list a tester keeps instead: findings.xlsx (its
proven and suspected bugs, with steps to reproduce, and a Warnings tab) and findings.csv, and "Send to
Jira" files one Jira bug per finding, the screenshot attached, the keys written back.

Test data appears by name only ("email, password"), never its value. Text that starts like a formula
(= + - @) is stored as text in the .xlsx and gets a leading apostrophe in the .csv, so words from a page
can't run as one when the sheet is opened.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path

import httpx

from .checks import page_key
from .defects import analyse, load_results, record_issue
from .jira import JiraConfig, JiraError, _attach, _find_open, _issue_type, _post, file_jira_issues, to_jira_markup
from .result import RunResult
from .spec import Spec

SHEET = Path("test-cases.xlsx")  # inside the run folder; .csv and .json sit beside it
COLUMNS = ("Test Case ID", "Title", "Requirement", "Priority", "Preconditions", "Test Steps", "Test Data",
           "Expected Result", "Actual Result", "Status", "Bug ID", "Jira Test Case", "Executed On", "Executed By",
           "Evidence")
STATUS = {"pass": "Pass", "fail": "Fail", "flaky": "Flaky", "error": "Blocked"}  # error: the tester couldn't test it
WIDTHS = {"Test Case ID": 24, "Title": 36, "Requirement": 14, "Priority": 10, "Preconditions": 30, "Test Steps": 48,
          "Test Data": 18, "Expected Result": 42, "Actual Result": 48, "Status": 10, "Bug ID": 14,
          "Jira Test Case": 15, "Executed On": 17, "Executed By": 26, "Evidence": 28}
FILLS = {"Pass": "C6EFCE", "Fail": "FFC7CE", "Flaky": "FFEB9C", "Blocked": "FFEB9C", "Not Run": "D9D9D9",
         "Proven bug": "FFC7CE", "Suspected bug": "FFEB9C"}
FINDINGS = Path("findings.xlsx")  # an exploration's bug list, beside its explore.json
FINDING_COLUMNS = ("Finding ID", "Title", "Type", "Page", "Steps to Reproduce", "Details", "Screenshot", "Found On",
                   "Found By", "Jira Bug")
WARNING_COLUMNS = ("Warning", "Times", "Pages")
WIDTHS.update({"Finding ID": 11, "Type": 14, "Page": 36, "Steps to Reproduce": 52, "Details": 52, "Screenshot": 18,
               "Found On": 17, "Found By": 26, "Jira Bug": 14, "Warning": 70, "Times": 8, "Pages": 60})
TEST_TYPES = ("Test", "Test Case", "Task", "Story")  # a project without a Test issue type gets Tasks


def sheet_rows(specs: list[Spec], results: list[RunResult], base: Path) -> list[dict[str, str]]:
    """One row per test case, in the columns QA teams use, filled in from the run. Evidence links are
    relative to `base`, the folder the sheet goes in."""
    by_spec = {result.spec: result for result in results}
    rows = []
    for spec in specs:
        result = by_spec.get(spec.name)
        if spec.steps:
            steps = list(spec.steps)
        else:  # an API test: its requests are its steps
            steps = [f"{r.method} {r.path}" + (f" ({r.name})" if r.name else "") for r in spec.requests]
        precondition = f"A fresh browser at {spec.url}" if spec.kind == "ui" else f"The API at {spec.url}"
        if spec.session_from:
            precondition += f", logged in as in {spec.session_from}"
        evidence = ""
        if result is not None and (Path(result.out_dir) / "report.html").exists():
            try:
                evidence = Path(os.path.relpath(Path(result.out_dir) / "report.html", base)).as_posix()
            except ValueError:  # another drive: no relative link possible
                evidence = ""
        rows.append({
            "Test Case ID": spec.name,
            "Title": spec.title or (steps[0] if steps else spec.name),
            "Requirement": ", ".join(spec.requirements),
            "Priority": spec.priority,
            "Preconditions": precondition,
            "Test Steps": "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1)),
            "Test Data": ", ".join(spec.data),
            "Expected Result": "\n".join(f"• {item}" for item in spec.expect)
                               or ("• each request gets the answer its test expects" if spec.kind == "api" else ""),
            "Actual Result": _actual(result),
            "Status": STATUS.get(result.verdict, "Not Run") if result else "Not Run",
            "Bug ID": "",
            "Jira Test Case": "",
            "Executed On": result.started_at.replace("T", " ")[:16] if result else "",
            "Executed By": f"Nightshift ({result.model})" if result else "",
            "Evidence": evidence,
        })
    return rows


def sheet_for_old_run(run_dir: Path, results: list[RunResult], spec_files: list[Path]) -> Path:
    """The sheet for a run from before sheets existed, from its results and the test files as they are now
    (a test since deleted keeps its name and result, with its steps left blank)."""
    from dataclasses import replace

    from .spec import SpecError, load_spec

    specs: dict[str, Spec] = {}
    for path in spec_files:
        try:
            spec = load_spec(path, expand_env=False)  # never needs the secrets: only names reach the sheet
        except SpecError:
            continue
        specs[spec.name] = spec
    chosen = []
    for result in results:
        spec = specs.get(result.spec) or specs.get(result.spec.split("@")[0])  # "checkout@firefox" is checkout
        chosen.append(replace(spec, name=result.spec) if spec else Spec(name=result.spec, url=result.url, steps=(), expect=()))
    return write_sheet(sheet_rows(chosen, results, run_dir), run_dir / SHEET)


def _actual(result: RunResult | None) -> str:
    """What happened, in the words a tester would write in the Actual Result column."""
    if result is None:
        return ""
    if result.verdict == "pass":
        quotes = [quote for check in result.checks for quote in check.evidence][:3]
        text = "As expected." + (" Shown: " + "; ".join(f'"{q}"' for q in quotes) if quotes else "")
        loaded = [w for w in result.warnings if w.startswith("uncaught JS error while the page loaded")]
        return text + (f" Note: {loaded[0]}" if loaded else "")
    if result.verdict == "error":
        return f"Couldn't test: {result.reason}"
    if result.verdict == "flaky":
        return f"Failed, then passed when tried again: {result.reason}"
    return result.reason


def write_sheet(rows: list[dict[str, str]], path: Path, jira_url: str = "") -> Path:
    """The sheet as .xlsx, and the same rows as .csv and .json beside it. Returns the .xlsx path."""
    _save_book(path, [("Test cases", COLUMNS, rows)], jira_url)
    _save_csv(path.with_suffix(".csv"), COLUMNS, rows)
    path.with_suffix(".json").write_text(json.dumps({"jira_url": jira_url, "rows": rows}, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    return path


_JIRA_COLUMNS = ("Bug ID", "Jira Test Case", "Jira Bug")
_FILE_COLUMNS = ("Evidence", "Screenshot")


def _save_book(path: Path, tabs: list[tuple[str, tuple[str, ...], list[dict]]], jira_url: str) -> None:
    """A workbook with one formatted tab per (title, columns, rows): bold header, wrapped text, a
    coloured Status or Type, Jira keys and evidence files as links, the header row frozen."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    book.remove(book.active)
    for title, columns, rows in tabs:
        sheet = book.create_sheet(title)
        sheet.append(list(columns))
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3864")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in rows:
            sheet.append([str(row.get(column, "") or "") for column in columns])
            line = sheet.max_row
            for index, column in enumerate(columns, 1):
                cell = sheet.cell(line, index)
                cell.data_type = "s"  # text, always: "=..." from a page is shown, never run as a formula
                value = row.get(column, "")
                if column in ("Status", "Type") and value in FILLS:
                    cell.fill = PatternFill("solid", fgColor=FILLS[value])
                    cell.font = Font(bold=True)
                elif column in _JIRA_COLUMNS and value and jira_url:
                    cell.hyperlink = f"{jira_url}/browse/{value.split(',')[0].strip()}"
                    cell.style = "Hyperlink"
                elif column in _FILE_COLUMNS and value:
                    cell.hyperlink = value  # relative: opens from the downloaded run folder
                    cell.style = "Hyperlink"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, column in enumerate(columns, 1):
            sheet.column_dimensions[get_column_letter(index)].width = WIDTHS.get(column, 18)
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
    book.save(path)


def _save_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:  # the BOM: Excel reads ₹
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows([_safe(row.get(column, "")) for column in columns] for row in rows)


def _safe(value: str) -> str:
    """In a CSV, a cell starting with = + - @ (or a tab or return) is a formula to Excel. Text from a web
    page must never run as one, so it gets the apostrophe Excel itself uses to mean "this is text"."""
    value = str(value or "")
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def write_markdown(rows: list[dict[str, str]], path: Path) -> Path:
    """The same test cases as a document to read."""
    lines = ["# Test cases", "", f"{len(rows)} case(s).", ""]
    for row in rows:
        lines += [f"## {row['Test Case ID']}: {row['Title']}", ""]
        facts = [f"{name}: {row[name]}" for name in ("Requirement", "Priority", "Status") if row.get(name)]
        lines += [" · ".join(facts), ""] if facts else []
        lines += [f"**Preconditions:** {row['Preconditions']}", "", "**Steps:**", "", row["Test Steps"], "",
                  "**Expected:**", "", row["Expected Result"], ""]
        if row.get("Test Data"):
            lines += [f"**Test data:** {row['Test Data']}", ""]
        if row.get("Actual Result"):
            lines += [f"**Actual result:** {row['Actual Result']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def findings_sheet(run_dir: Path, jira_url: str = "") -> Path:
    """The bug list of the exploration in run_dir, from its explore.json: findings.xlsx (a Findings tab with
    its proven and suspected bugs, a Warnings tab), findings.csv (the findings) and findings-sheet.json."""
    data = json.loads((run_dir / "explore.json").read_text(encoding="utf-8"))
    steps = [s for s in data.get("steps", []) if s.get("action") and s["action"].get("kind") not in ("report", "done")
             and not str(s.get("outcome", "")).startswith(("invalid", "not reported", "not yet"))]
    stamp = re.search(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", run_dir.name)
    found_on = f"{stamp[1]}-{stamp[2]}-{stamp[3]} {stamp[4]}:{stamp[5]}" if stamp else ""
    rows, warnings = [], {}
    for finding in data.get("findings", []):
        if finding["severity"] == "warning":
            entry = warnings.setdefault(finding["title"], {"Warning": finding["title"], "Times": 0, "pages": []})
            entry["Times"] += 1
            if finding.get("url") and finding["url"] not in entry["pages"]:
                entry["pages"].append(finding["url"])
            continue
        before = [s for s in steps if finding.get("step") is None or s["index"] <= finding["step"]]
        if finding.get("step") is None:
            before = []  # it showed while the page loaded
        repro = [f"Open {data['url']}"] + [_human(s["description"]) for s in before]
        rows.append({
            "Finding ID": f"F{len(rows) + 1}",
            "Title": finding["title"],
            "Type": "Proven bug" if finding["severity"] == "bug" else "Suspected bug",
            "Page": finding.get("url", ""),
            "Steps to Reproduce": "\n".join(f"{i}. {step}" for i, step in enumerate(repro, 1)),
            "Details": finding.get("details", ""),
            "Screenshot": finding.get("screenshot") or "",
            "Found On": found_on,
            "Found By": f"Nightshift exploring ({data.get('model', '')})",
            "Jira Bug": "",
        })
    warning_rows = [{"Warning": w["Warning"], "Times": str(w["Times"]), "Pages": "\n".join(w["pages"][:5])}
                    for w in warnings.values()]
    return write_findings(rows, warning_rows, run_dir, jira_url)


def _human(step: str) -> str:
    """An action as a person writes it in steps to reproduce: no element numbers (they change every turn).
    'type [4] "Email" <- "x"' is 'Type "x" into "Email"'."""
    step = re.sub(r"\s*\[\d+\]", "", step).strip()
    if m := re.fullmatch(r'type(?: "(.*?)")? <- "(.*)"', step, re.S):
        return f'Type "{m[2]}"' + (f' into "{m[1]}"' if m[1] else "")
    if m := re.fullmatch(r'select(?: "(.*?)")? <- "(.*)"', step, re.S):
        return f'Choose "{m[2]}"' + (f' in "{m[1]}"' if m[1] else "")
    return step[:1].upper() + step[1:]


def write_findings(rows: list[dict], warnings: list[dict], run_dir: Path, jira_url: str = "") -> Path:
    path = run_dir / FINDINGS
    _save_book(path, [("Findings", FINDING_COLUMNS, rows), ("Warnings", WARNING_COLUMNS, warnings)], jira_url)
    _save_csv(path.with_suffix(".csv"), FINDING_COLUMNS, rows)
    (run_dir / "findings-sheet.json").write_text(
        json.dumps({"jira_url": jira_url, "rows": rows, "warnings": warnings}, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def ensure_sheet(run_dir: Path, spec_files: list[Path]) -> Path | None:
    """The finished run's sheet, made now if the run is from before sheets existed: findings.xlsx for an
    exploration, test-cases.xlsx for a test run. None when there is nothing to put in one."""
    if (run_dir / "explore.json").exists():
        if not (run_dir / "findings-sheet.json").exists():
            findings_sheet(run_dir)
        return run_dir / FINDINGS
    if (run_dir / "summary.json").exists():
        if not (run_dir / SHEET).with_suffix(".json").exists():
            results = load_results(run_dir)
            if not results:
                return None
            sheet_for_old_run(run_dir, results, spec_files)
        return run_dir / SHEET
    return None


def file_findings_in_jira(run_dir: Path, config: JiraConfig, client: httpx.Client | None = None,
                          log=None) -> list[tuple[str, str, str]]:
    """One Jira bug per finding of an exploration, its screenshot attached; a suspected one is labelled
    needs-review. A finding whose issue is still open gets a "seen again" comment instead, and one already
    sent from this run is left alone. The keys go back into the sheet."""
    log = log or (lambda line: None)
    if not (run_dir / "findings-sheet.json").exists():
        findings_sheet(run_dir)
    data = json.loads((run_dir / "findings-sheet.json").read_text(encoding="utf-8"))
    rows, warnings = data["rows"], data.get("warnings", [])
    own = client is None
    client = client or config.client()
    done, issue_type = [], None
    try:
        for row in rows:
            if row.get("Jira Bug"):
                done.append((row["Finding ID"], "already sent", row["Jira Bug"]))
                continue
            label = "nsf-" + hashlib.sha1(f"{row['Title']}|{page_key(row['Page'])}".encode("utf-8")).hexdigest()[:10]
            existing = _find_open(client, config, label)
            if existing:
                _post(client, f"/rest/api/2/issue/{existing}/comment",
                      {"body": f"Seen again by Nightshift exploring {row['Page']} in run {run_dir.name}."})
                row["Jira Bug"] = existing
                done.append((row["Finding ID"], "commented", existing))
                continue
            issue_type = issue_type or _issue_type(client, config, log)
            suspected = row["Type"] == "Suspected bug"
            fields = {
                "project": {"key": config.project},
                "summary": (f"[Suspected] {row['Title']}" if suspected else row["Title"])[:250],
                "description": finding_markup(row, run_dir.name),
                "issuetype": {"name": issue_type},
                "labels": ["nightshift", "exploration", label] + (["needs-review"] if suspected else []),
            }
            key = _post(client, "/rest/api/2/issue", {"fields": fields})["key"]
            if row.get("Screenshot") and (run_dir / row["Screenshot"]).exists():
                _attach(client, key, run_dir / row["Screenshot"])
            row["Jira Bug"] = key
            done.append((row["Finding ID"], "created", key))
    finally:
        if own:
            client.close()
        write_findings(rows, warnings, run_dir, jira_url=config.url)  # whatever was filed keeps its key
    return done


def finding_markup(row: dict[str, str], run_name: str) -> str:
    kind = ("Suspected by the agent: check it by hand before fixing anything." if row["Type"] == "Suspected bug"
            else "Proven by the browser.")
    lines = [kind, "", f"*Page:* {row['Page']}", "", "h3. Steps to reproduce"]
    lines += ["# " + re.sub(r"^\d+\.\s*", "", step) for step in row["Steps to Reproduce"].splitlines()]
    if row.get("Details"):
        lines += ["", "h3. What happened", to_jira_markup(row["Details"])]
    lines += ["", f"Found by Nightshift exploring the site (run {run_name}, {row['Found On']}).",
              "The screenshot of the moment is attached." if row.get("Screenshot") else ""]
    return "\n".join(lines).rstrip()


def load_rows(run_dir: Path) -> tuple[list[dict[str, str]], str]:
    data = json.loads((run_dir / SHEET).with_suffix(".json").read_text(encoding="utf-8"))
    return data["rows"], data.get("jira_url", "")


def send_to_jira(run_dir: Path, config: JiraConfig, client: httpx.Client | None = None, log=None) -> dict:
    """The sheet into Jira: one issue per test case, one per bug, each bug linked to its test cases, and the
    keys written back into the sheet. Returns {"cases": [(test id, action, key)], "bugs": [(defect id, action, key)]}."""
    log = log or (lambda line: None)
    rows, _ = load_rows(run_dir)
    defects = analyse(load_results(run_dir), run_dir.parent)
    own = client is None
    client = client or config.client()
    try:
        cases = file_test_cases(rows, run_dir, config, client, log)
        case_keys = {test_id: key for test_id, _, key in cases}
        bugs = file_jira_issues(defects, run_dir, config, client, log) if defects else []
        by_id = {defect.id: defect for defect in defects}
        bug_keys: dict[str, list[str]] = {}
        for defect_id, action, key in bugs:
            record_issue(run_dir, defect_id, "jira", key, f"{config.url}/browse/{key}")
            for test_id in by_id[defect_id].specs:
                bug_keys.setdefault(test_id, []).append(key)
                if action == "created" and test_id in case_keys:
                    _link(client, case_keys[test_id], key, log)
    finally:
        if own:
            client.close()
    for row in rows:
        row["Jira Test Case"] = case_keys.get(row["Test Case ID"], row.get("Jira Test Case", ""))
        if row["Test Case ID"] in bug_keys:
            row["Bug ID"] = ", ".join(dict.fromkeys(bug_keys[row["Test Case ID"]]))
    write_sheet(rows, run_dir / SHEET, jira_url=config.url)
    return {"cases": cases, "bugs": bugs}


def file_test_cases(rows: list[dict[str, str]], run_dir: Path, config: JiraConfig, client: httpx.Client,
                    log) -> list[tuple[str, str, str]]:
    """One Jira issue per test case. A test case that has an issue already (found by its label, open or
    closed) gets this run's result as a comment instead; one already sent from this run is left alone."""
    done, issue_type = [], None
    for row in rows:
        test_id = row["Test Case ID"]
        if row.get("Jira Test Case"):
            done.append((test_id, "already sent", row["Jira Test Case"]))
            continue
        label = "nstc-" + hashlib.sha1(test_id.encode("utf-8")).hexdigest()[:10]
        existing = _find_open(client, config, label, open_only=False)
        if existing:
            _post(client, f"/rest/api/2/issue/{existing}/comment",
                  {"body": f"Run {run_dir.name}: *{row['Status']}*. {row['Actual Result']}"})
            done.append((test_id, "updated", existing))
            continue
        issue_type = issue_type or _issue_type(client, config, log, wanted=config.test_issue_type, fallbacks=TEST_TYPES)
        fields = {
            "project": {"key": config.project},
            "summary": f"Test case: {row['Title']}"[:250],
            "description": case_markup(row, run_dir.name),
            "issuetype": {"name": issue_type},
            "labels": ["nightshift", "test-case", label],
        }
        done.append((test_id, "created", _post(client, "/rest/api/2/issue", {"fields": fields})["key"]))
    return done


def case_markup(row: dict[str, str], run_name: str) -> str:
    """A test case in Jira's wiki markup: the sheet's row, laid out the way a person would write it."""
    lines = [f"*Test case ID:* {row['Test Case ID']}"]
    lines += [f"*{name}:* {row[name]}" for name in ("Requirement", "Priority") if row.get(name)]
    lines += [f"*Preconditions:* {row['Preconditions']}", "", "h3. Steps"]
    lines += ["# " + re.sub(r"^\d+\.\s*", "", step) for step in row["Test Steps"].splitlines()]
    lines += ["", "h3. Expected result"]
    lines += ["* " + item.removeprefix("• ") for item in row["Expected Result"].splitlines()]
    if row.get("Test Data"):
        lines += ["", f"*Test data:* {row['Test Data']}"]
    lines += ["", f"h3. Last run ({run_name})", f"*Status:* {row['Status']}", f"*Actual result:* {row['Actual Result']}"]
    if row.get("Executed On"):
        lines.append(f"*Executed on:* {row['Executed On']} by {row['Executed By']}")
    return "\n".join(lines)


def _link(client: httpx.Client, test_key: str, bug_key: str, log) -> None:
    """Link a bug to the test case that found it. A Jira without the usual "Relates" link type keeps
    both issues and says so; the link is a convenience, not worth failing the whole send over."""
    try:
        _post(client, "/rest/api/2/issueLink",
              {"type": {"name": "Relates"}, "inwardIssue": {"key": test_key}, "outwardIssue": {"key": bug_key}})
    except JiraError as exc:
        log(f"jira: could not link {bug_key} to {test_key} ({exc})")
