"""Exploratory testing: no spec, just "find what's broken".

The agent roams the app on a step budget: new pages first, then forms filled the
ways real users get them wrong. What the browser proves (JS crashes, 5xx, a picture
the server didn't deliver, a field labelled as something it isn't) is recorded as a
bug as it happens; 404s, slow calls and accessibility gaps as warnings. What the model
only suspects is kept apart, so nobody mistakes a guess for a proof.

The budget is spread over the site: after a few actions on one page the agent is told
it has checked it enough, and after twice that it is moved back to the start. Found
on a practice shop: 20 of 25 actions went into one long sign-up form, and the cart and
checkout were never seen.

It also writes discovered.json, a map of the pages it found, which
`nightshift generate` turns into specs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page
from playwright.sync_api import Error as PlaywrightError

from .actions import EXPLORE_KINDS, ActionFailed, InvalidAction, execute, settle, track_network, validate_action
from .checks import check_page, listen
from .model import Model, ModelError, usage_snapshot
from .observe import Observation, block_ads, observe, screenshot, watch_form_validation
from .prompts import Context, format_element, mask
from .report import page_html, step_html
from .result import PENDING, Step
from .runner import INVALID_LIMIT, VIEWPORT, RunOptions, _step_line, _stuck, adopt_new_tab
from .spec import Spec

DEFAULT_AVOID = ("delete", "remove account", "close account", "deactivate", "log out", "logout",
                 "sign out", "unsubscribe")
SEVERITY_CLASS = {"bug": "fail", "suspected": "flaky", "warning": "error"}

Log = Callable[[str], None]


@dataclass
class Finding:
    severity: str  # "bug": the browser proved it. "suspected": the model thinks so. "warning".
    title: str
    details: str = ""
    step: int | None = None
    url: str = ""
    screenshot: str | None = None


@dataclass
class ExploreResult:
    url: str
    model: str
    out_dir: str
    steps: list[Step] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    pages: dict[str, dict] = field(default_factory=dict)
    stopped: str = ""
    duration_s: float = 0.0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # The browser's raw signals; findings are built from them step by step.
    app_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    moved_on: list[str] = field(default_factory=list)  # pages left because they'd had their share of actions

    def count(self, severity: str) -> int:
        return sum(finding.severity == severity for finding in self.findings)


def explore(
    browser: Browser, url: str, model: Model, *, out_dir: Path, budget: int = 40,
    data: dict[str, str] | None = None, focus: str = "", avoid: tuple[str, ...] = DEFAULT_AVOID,
    options: RunOptions | None = None, log: Log | None = None,
) -> ExploreResult:
    options = options or RunOptions()
    log = log or (lambda line: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = Spec(name="explore", url=url, steps=(focus,) if focus else (), expect=(), data=dict(data or {}),
                max_steps=budget)
    result = ExploreResult(url=url, model=model.name, out_dir=str(out_dir))

    usage_before = usage_snapshot(model)
    context = browser.new_context(**{"viewport": VIEWPORT, **options.context})
    if options.block_ads:
        block_ads(context)
    watch_form_validation(context)
    context.tracing.start(screenshots=True, snapshots=True)
    # The site's own pictures the server didn't deliver (a 404, a refused or dropped request): with
    # one of these, "broken image" on the page is proven, not a guess (an SVG, say, can look empty).
    failed_images: dict[str, int] = {}

    def note_image(response_or_request, status: int) -> None:
        request = getattr(response_or_request, "request", response_or_request)
        if request.resource_type == "image" and _origin(request.url) == _origin(url):
            failed_images[urlsplit(request.url).path] = status

    context.on("response", lambda r: r.status >= 400 and note_image(r, r.status))
    context.on("requestfailed", lambda r: note_image(r, 0))
    page = context.new_page()
    listen(page, url, result)
    track_network(page)
    new_tabs: list[Page] = []
    context.on("page", lambda tab: new_tabs.append(tab))
    started = time.perf_counter()
    try:
        page.goto(url, wait_until="domcontentloaded")
        settle(page)
        _Explorer(page, spec, model, options, result, out_dir, avoid, log, new_tabs, failed_images).run(budget)
    except PlaywrightError as exc:
        result.stopped = f"browser error: {str(exc).strip().splitlines()[0][:300]}"
    finally:
        result.duration_s = round(time.perf_counter() - started, 2)
        try:
            context.tracing.stop(path=str(out_dir / "trace.zip"))
            context.close()
        except PlaywrightError:
            pass

    usage_after = usage_snapshot(model)
    result.model_calls = usage_after.get("calls", 0) - usage_before.get("calls", 0)
    result.prompt_tokens = usage_after.get("prompt_tokens", 0) - usage_before.get("prompt_tokens", 0)
    result.completion_tokens = usage_after.get("completion_tokens", 0) - usage_before.get("completion_tokens", 0)
    write_explore_outputs(result)
    return result


class _Explorer:
    def __init__(self, page: Page, spec: Spec, model: Model, options: RunOptions, result: ExploreResult,
                 out_dir: Path, avoid: tuple[str, ...], log: Log, new_tabs: list[Page] | None = None,
                 failed_images: dict[str, int] | None = None) -> None:
        self.page, self.spec, self.model, self.options = page, spec, model, options
        self.result, self.out_dir, self.avoid, self.log = result, out_dir, avoid, log
        self.origin = _origin(spec.url)
        self.seen_pages: set[str] = set()
        self.new_tabs = new_tabs if new_tabs is not None else []
        self.errors_seen = 0
        self.warnings_seen = 0
        self.failed_images = failed_images if failed_images is not None else {}
        self.home_key = _page_key(spec.url)
        self.page_actions: dict[str, int] = {}  # actions spent on each page
        self.enough = self.limit = 0

    def run(self, budget: int) -> None:
        steps = self.result.steps
        last_fingerprint = ""
        invalid_streak = 0
        # About a fifth of the budget is enough to check one page or form; twice that, and it moves on.
        self.enough = max(5, budget // 5)
        self.limit = 2 * self.enough
        for index in range(1, budget + 1):
            observation = observe(self.page)
            check_page(self.page, self.result, self.seen_pages)
            fingerprint = observation.fingerprint()
            if steps and steps[-1].outcome == PENDING:
                steps[-1].outcome = "changed" if fingerprint != last_fingerprint else "no change"
                self.log(_step_line(steps[-1]))
            last_fingerprint = fingerprint
            self._collect(observation)

            if stuck := _stuck(steps):
                self._add(Finding("suspected", stuck, step=steps[-1].index, url=observation.url,
                                  screenshot=steps[-1].screenshot))
            if _origin(observation.url) != self.origin:
                self.log(f"    left the site ({observation.url}); going back")
                self._return_home()
                continue
            self._remember_page(observation, index)
            here = _page_key(observation.url)
            if self.page_actions.get(here, 0) >= self.limit and here != self.home_key:
                if here not in self.result.moved_on:
                    self.result.moved_on.append(here)
                self.log(f"    moved on: {self.page_actions[here]} actions on {here} is its share")
                self._go_home()
                continue
            self.page_actions[here] = self.page_actions.get(here, 0) + 1

            shot_name = f"step-{index:02d}.jpg"
            shot = screenshot(self.page)
            (self.out_dir / shot_name).write_bytes(shot)
            context = Context(self.spec, observation, tuple(steps), shot if self.options.send_screenshot else None,
                              mode="explore", notes=self._notes(budget - index + 1, here))
            started = time.perf_counter()
            try:
                raw = self.model.decide(context)
                action = validate_action(raw, observation, self.spec.data, EXPLORE_KINDS, self.avoid)
            except ModelError as exc:
                self.result.stopped = f"model call failed: {exc}"
                break
            except InvalidAction as exc:
                invalid_streak += 1
                step = Step(index, None, "(unusable reply)", outcome=f"invalid: {exc}",
                            model_ms=_ms_since(started), screenshot=shot_name)
                steps.append(step)
                self.log(_step_line(step))
                if invalid_streak >= INVALID_LIMIT:
                    self.result.stopped = f"the model gave {INVALID_LIMIT} unusable replies in a row"
                    break
                continue
            invalid_streak = 0

            element = observation.element(action.id) if action.id is not None else None
            label = element.label if element else ""
            step = Step(index, action, action.describe(label), thought=str(raw.get("thought") or "").strip()[:300],
                        model_ms=_ms_since(started), screenshot=shot_name, target_label=label)
            steps.append(step)

            if action.kind == "done":
                step.outcome = "verdict"
                self.log(_step_line(step))
                self.result.stopped = f"the agent finished: {action.reason}"
                break
            if action.kind == "report":
                step.outcome = "reported"
                self.log(_step_line(step))
                self._add(Finding("suspected", action.title or "", details=action.details or "", step=index,
                                  url=observation.url, screenshot=shot_name))
                continue

            started = time.perf_counter()
            try:
                execute(self.page, action, self.spec.data)
                settle(self.page)
                if tab := adopt_new_tab(self.new_tabs, self.spec.url, self.result):
                    self.page = tab
            except ActionFailed as exc:
                step.outcome = f"failed: {exc}"
                self.log(_step_line(step))
            step.action_ms = _ms_since(started)
        else:
            self.result.stopped = f"used all {budget} steps"

        if steps and steps[-1].outcome == PENDING:
            steps[-1].outcome = "done"
            self.log(_step_line(steps[-1]))
        self._collect(None)  # whatever the last action set off

    def _collect(self, observation: Observation | None) -> None:
        """Turn new browser signals into findings, pinned to the action that caused them."""
        steps = self.result.steps
        cause = steps[-1] if steps else None
        where = observation.url if observation else self.page.url
        for error in self.result.app_errors[self.errors_seen:]:
            details = f"right after: {cause.description}" if cause else "while the page loaded"
            self._add(Finding("bug", error, details=details, step=cause.index if cause else None, url=where,
                              screenshot=cause.screenshot if cause else None))
        self.errors_seen = len(self.result.app_errors)
        for warning in self.result.warnings[self.warnings_seen:]:
            self._add(self._classify(warning, cause, where))
        self.warnings_seen = len(self.result.warnings)

    def _classify(self, warning: str, cause: Step | None, where: str) -> Finding:
        """A warning, or a bug when the browser proves it: a broken picture whose file the server
        refused, or a field labelled as something it isn't."""
        step, shot = (cause.index, cause.screenshot) if cause else (None, None)
        text, _, page = warning.rpartition(" on ")  # the page checks end each line with " on <page>"
        if warning.startswith("broken image: ") and text:
            path = text.removeprefix("broken image: ")
            if path in self.failed_images:
                status = self.failed_images[path]
                answer = f"HTTP {status}" if status else "the request failed"
                return Finding("bug", f"broken image: {path} ({answer})", step=step, url=where, screenshot=shot,
                               details=f"on {page}: the page shows a picture the server didn't deliver ({answer})")
        if warning.startswith("mislabelled field: ") and text:
            title, _, marks = text.partition(" (")
            return Finding("bug", title, step=step, url=where, screenshot=shot,
                           details=f"on {page}: the label and the field disagree, so people fill in the wrong thing. "
                                   f"The field: {marks.rstrip(')')}")
        return Finding("warning", warning, step=step, url=where)

    def _add(self, finding: Finding) -> None:
        if any(f.title == finding.title and f.severity == finding.severity for f in self.result.findings):
            return
        self.result.findings.append(finding)
        if finding.severity != "warning":
            self.log(f"    {finding.severity.upper()}: {finding.title}")

    def _go_home(self) -> None:
        try:
            self.page.goto(self.spec.url, wait_until="domcontentloaded")
            settle(self.page)
        except PlaywrightError:
            pass

    def _return_home(self) -> None:
        try:
            self.page.go_back(wait_until="domcontentloaded", timeout=10_000)
        except PlaywrightError:
            pass
        if _origin(self.page.url) != self.origin:
            self.page.goto(self.spec.url, wait_until="domcontentloaded")
        settle(self.page)

    def _remember_page(self, observation: Observation, index: int) -> None:
        key = _page_key(observation.url)
        if key in self.result.pages:
            return
        self.result.pages[key] = {
            "url": observation.url,
            "title": observation.title,
            "first_step": index,
            "elements": [format_element(e, self.spec.data) for e in observation.elements[:40]],
            "text": mask(observation.text, self.spec.data)[:800],
        }

    def _notes(self, actions_left: int, here: str = "") -> str:
        pages = "\n".join(f'- {key} "{page["title"]}"' for key, page in self.result.pages.items())
        found = "\n".join(f"- {f.title}" for f in self.result.findings if f.severity != "warning")
        used = self.page_actions.get(here, 0)
        this_page = f"THIS PAGE: {used} actions used here. About {self.enough} is enough to check a page or form"
        if here != self.home_key:
            this_page += f"; after {self.limit} you will be moved back to the start"
        if used >= self.enough:
            this_page += ". You have checked this page enough: move on to a part of the site you haven't visited"
        covered = f"\n\nALREADY COVERED (don't go back unless following a new lead): {', '.join(self.result.moved_on)}" \
            if self.result.moved_on else ""
        return "\n\n".join([
            f"AVOID (never click these): {', '.join(self.avoid) or '(nothing)'}",
            f"PAGES VISITED ({len(self.result.pages)}):\n{pages or '(none yet)'}",
            f"FINDINGS SO FAR:\n{found or '(none yet)'}",
            this_page + "." + covered,
            f"ACTIONS LEFT: {actions_left}",
        ])


def write_explore_outputs(result: ExploreResult) -> None:
    out_dir = Path(result.out_dir)
    data = asdict(result)
    (out_dir / "explore.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    discovered = {"url": result.url, "pages": result.pages}
    (out_dir / "discovered.json").write_text(json.dumps(discovered, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "findings.md").write_text(findings_markdown(result), encoding="utf-8")
    (out_dir / "report.html").write_text(explore_report_html(result), encoding="utf-8")


def findings_markdown(result: ExploreResult) -> str:
    out_dir = Path(result.out_dir)
    lines = [f"# Exploratory test of {result.url}", "",
             f"{len(result.steps)} actions, {len(result.pages)} pages, {result.duration_s:.0f}s. Stopped: {result.stopped}.", ""]
    if result.moved_on:
        lines += [f"Moved on after their share of actions: {', '.join(result.moved_on)}.", ""]
    sections = [("bug", "Bugs the browser proved"), ("suspected", "Suspected by the agent (check by hand)"),
                ("warning", "Warnings")]
    for severity, heading in sections:
        items = [f for f in result.findings if f.severity == severity]
        lines += [f"## {heading} ({len(items)})", ""]
        for finding in items:
            lines.append(f"- **{finding.title}**")
            if finding.details:
                lines.append(f"  - {finding.details}")
            where = [finding.url] if finding.url else []
            if finding.step:
                where.append(f"step {finding.step}")
            if finding.screenshot:
                where.append(f"`{out_dir / finding.screenshot}`")
            if where:
                lines.append(f"  - {', '.join(where)}")
        lines.append("")
    return "\n".join(lines)


def explore_report_html(result: ExploreResult) -> str:
    counts = f"{result.count('bug')} bugs · {result.count('suspected')} suspected · {result.count('warning')} warnings"
    rows = "".join(
        f'<tr><td><span class="pill {SEVERITY_CLASS[f.severity]}">{f.severity.upper()}</span></td>'
        f"<td>{escape(f.title)}<div class=\"muted\">{escape(f.details)}</div></td>"
        f"<td>{escape(f.url)}</td><td>{_evidence_link(f)}</td></tr>"
        for f in sorted(result.findings, key=lambda f: list(SEVERITY_CLASS).index(f.severity))
    )
    pages = "".join(f'<li><a href="{escape(p["url"])}">{escape(key)}</a> <span class="muted">{escape(p["title"])} '
                    f'(step {p["first_step"]})</span></li>' for key, p in result.pages.items())
    tokens = result.prompt_tokens + result.completion_tokens
    body = (f"<h1>Exploratory test</h1><p class=\"reason\">{escape(result.url)}: {escape(counts)}</p>"
            f'<p class="muted">{len(result.steps)} actions · {len(result.pages)} pages · {result.duration_s:.0f}s · '
            f"{result.model_calls} model calls ({tokens:,} tokens) · stopped: {escape(result.stopped)}</p>"
            + (f'<p class="muted">Moved on after their share of actions: {escape(", ".join(result.moved_on))}</p>'
               if result.moved_on else "") +
            f'<h2>Findings</h2><div class="wrap"><table><tr><th></th><th>Finding</th><th>Where</th><th>Evidence</th></tr>'
            f"{rows or '<tr><td colspan=4>Nothing found.</td></tr>'}</table></div>"
            f'<h2>Pages visited</h2><ul class="plain panel">{pages}</ul>'
            f'<h2>Steps</h2><ol class="steps">{"".join(step_html(s) for s in result.steps)}</ol>'
            '<h2>Files</h2><ul class="plain panel"><li><a href="findings.md">findings.md</a></li>'
            '<li><a href="discovered.json">discovered.json</a>: feed it to <code>nightshift generate</code></li>'
            '<li><a href="trace.zip">trace.zip</a></li></ul>')
    return page_html("Exploratory test", body)


def _evidence_link(finding: Finding) -> str:
    if finding.screenshot:
        return f'<a href="{escape(finding.screenshot)}">step {finding.step}</a>'
    return f"step {finding.step}" if finding.step else ""


def _page_key(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"#{parts.fragment.split('?')[0]}" if parts.fragment else "")


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
