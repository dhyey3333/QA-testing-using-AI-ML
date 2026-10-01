"""Running a spec: replay, the agent loop, the judge, and retries.

One run:
  1. If a saved recording matches the spec, replay it with no model calls. If a
     step can't be found any more (a renamed button, a redesign), the agent takes
     over from that step: self-healing.
  2. Otherwise the agent reads the page, picks one action, acts, and repeats until
     it says pass or fail.
  3. A pass counts only once the judge has proven every expected result on the
     page (judge.py) and the browser saw no uncaught JS error or 5xx on the way.
With retries on, a failure is re-run by a fresh agent before it is reported.
"""

from __future__ import annotations

import difflib
import json
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .actions import TEST_KINDS, ActionFailed, InvalidAction, execute, settle, track_network, validate_action
from .checks import check_page, listen
from .inbox import DYNAMIC, InboxError, extract_code, extract_link, inbox_for, recipient, wait_for_email
from .export import write_export
from .judge import is_on_page, judge_page
from .locators import describe_target, resolve
from .model import Model, ModelError, usage_snapshot
from .observe import JPEG_QUALITY, Observation, observe, screenshot, watch_form_validation
from .prompts import Context, mask
from .recording import Recording, RecordingStore
from .report import write_spec_report
from .result import PENDING, Check, RunResult, Step
from .spec import Spec

VIEWPORT = {"width": 1280, "height": 800}
STUCK_REPEATS = 3  # the same action with no effect this many times in a row is a bug
INVALID_LIMIT = 3  # this many unusable model replies in a row ends the run as an error

Log = Callable[[str], None]


def _silent(line: str) -> None:
    pass


@dataclass(frozen=True)
class RunOptions:
    send_screenshot: bool = True
    judge: bool = True
    retries: int = 0
    recordings: RecordingStore | None = None  # None: never replay, never record
    replay: bool = True
    record: bool = True
    video: bool = False
    context: dict = field(default_factory=dict)  # extra browser-context options, e.g. a device profile


@contextmanager
def open_browser(headed: bool = False, browser: str = "chromium") -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = getattr(playwright, browser).launch(headless=not headed)
        try:
            yield instance
        finally:
            instance.close()


def device_options(name: str) -> dict:
    """Viewport, user agent and touch settings for a named device, e.g. "iPhone 13" or "Pixel 7"."""
    with sync_playwright() as playwright:
        devices = playwright.devices
        if name not in devices:
            close = difflib.get_close_matches(name, list(devices), n=5, cutoff=0.4)
            hint = f"; did you mean: {', '.join(close)}" if close else ""
            raise ValueError(f"unknown device {name!r}{hint}")
        options = dict(devices[name])
    options.pop("default_browser_type", None)
    return options


def run_with_retries(
    browser: Browser, spec: Spec, model: Model, *, out_dir: Path,
    options: RunOptions | None = None, log: Log | None = None,
) -> RunResult:
    """Run a spec, and re-run any failure with a fresh agent before believing it."""
    options = options or RunOptions()
    log = log or _silent
    if spec.kind == "api":
        # Deterministic: a retry would only repeat the same answer.
        return run_spec(browser, spec, model, out_dir=out_dir, options=options, log=log)
    attempts = [run_spec(browser, spec, model, out_dir=out_dir, options=options, log=log)]
    for n in range(1, options.retries + 1):
        if attempts[-1].verdict == "pass":
            break
        log(f"{attempts[-1].verdict.upper()}: {attempts[-1].reason}")
        log(f"re-running with a fresh agent to confirm (retry {n} of {options.retries})")
        # Retries never replay: a failure is only confirmed by an agent finding the path itself.
        attempts.append(run_spec(browser, spec, model, out_dir=out_dir / f"retry-{n}",
                                 options=replace(options, replay=False), log=log))
    if len(attempts) == 1:
        return attempts[0]

    history = [{"verdict": a.verdict, "reason": a.reason, "mode": a.mode,
                "duration_s": a.duration_s, "out_dir": a.out_dir} for a in attempts]
    final = _combine(attempts)
    final.attempts = history
    final.model_calls = sum(a.model_calls for a in attempts)
    final.prompt_tokens = sum(a.prompt_tokens for a in attempts)
    final.completion_tokens = sum(a.completion_tokens for a in attempts)
    _write_artifacts(final, spec)
    return final


def _combine(attempts: list[RunResult]) -> RunResult:
    first = attempts[0]
    passed = next((a for a in attempts if a.verdict == "pass"), None)
    if passed is not None:
        if first.mode in ("replay", "healed"):
            # The saved path failed but a fresh agent got through: the recording was stale,
            # not the app. The fresh run re-recorded it.
            passed.warnings.insert(0, f"the saved path failed ({first.reason}); a fresh agent passed and re-recorded it")
            return passed
        first.verdict = "flaky"
        first.reason = f"failed ({first.reason}), then passed on retry"
        return first
    failed = [a for a in attempts if a.verdict == "fail"]
    if failed:
        primary = failed[0]
        primary.reason = f"{primary.reason} (failed {len(failed)} of {len(attempts)} runs)"
        return primary
    return first


def run_spec(
    browser: Browser, spec: Spec, model: Model, *, out_dir: Path,
    options: RunOptions | None = None, log: Log | None = None,
) -> RunResult:
    """One attempt at a spec in a fresh browser context. Writes screenshots, trace.zip, result.json and report.html."""
    options = options or RunOptions()
    log = log or _silent
    if spec.kind == "api":
        from .api import run_api_spec

        return run_api_spec(spec, out_dir=out_dir, log=log)
    out_dir.mkdir(parents=True, exist_ok=True)

    context_options = {"viewport": VIEWPORT, **options.context}
    if options.video:
        context_options["record_video_dir"] = str(out_dir / "_video")
    viewport = context_options.get("viewport") or VIEWPORT
    result = RunResult(
        spec=spec.name, url=spec.url, model=model.name, out_dir=str(out_dir),
        started_at=datetime.now().isoformat(timespec="seconds"),
        browser=browser.browser_type.name, viewport=f"{viewport['width']}x{viewport['height']}",
    )
    recording = options.recordings.load(spec) if options.recordings and options.replay else None

    usage_before = usage_snapshot(model)
    context = browser.new_context(**context_options)
    watch_form_validation(context)
    # The trace is the "steps to reproduce": DOM snapshots, network and console for
    # every action, replayable with `playwright show-trace`.
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    listen(page, spec.url, result)
    track_network(page)
    new_tabs: list[Page] = []
    context.on("page", lambda tab: new_tabs.append(tab))

    started = time.perf_counter()
    run = _Run(page, spec, model, options, result, out_dir, log, new_tabs)
    try:
        result.verdict, result.reason = run.go(recording)
    except PlaywrightError as exc:
        result.verdict, result.reason = "error", f"browser error: {str(exc).strip().splitlines()[0][:300]}"
    finally:
        result.duration_s = round(time.perf_counter() - started, 2)
        with suppress(PlaywrightError):
            final = observe(run.page)
            result.final_url, result.final_text = final.url, mask(final.text, spec.data)[:6_000]
        video = page.video if options.video else None
        with suppress(PlaywrightError):
            context.tracing.stop(path=str(out_dir / "trace.zip"))
        with suppress(PlaywrightError):
            context.close()
        if video is not None:
            _keep_video(video, out_dir)

    # A crash the user never saw is still a bug.
    if result.verdict == "pass" and result.app_errors:
        result.verdict, result.reason = "fail", result.app_errors[0]

    usage_after = usage_snapshot(model)
    result.model_calls = usage_after.get("calls", 0) - usage_before.get("calls", 0)
    result.prompt_tokens = usage_after.get("prompt_tokens", 0) - usage_before.get("prompt_tokens", 0)
    result.completion_tokens = usage_after.get("completion_tokens", 0) - usage_before.get("completion_tokens", 0)

    if result.verdict == "pass" and options.recordings and options.record and result.mode != "replay":
        saved = options.recordings.save(spec, result)
        log(f"saved the path for replay: {saved}" if saved else "not saved for replay: a step could not be re-found reliably")
        if saved and (recording := options.recordings.load(spec)) is not None:
            # The same path as a plain Playwright test, for teams that want it in their own suite.
            exported = write_export(spec, recording, options.recordings.root.parent / "playwright")
            log(f"saved as a Playwright test: {exported}")

    _write_artifacts(result, spec)
    return result


def _write_artifacts(result: RunResult, spec: Spec) -> None:
    out_dir = Path(result.out_dir)
    (out_dir / "result.json").write_text(json.dumps(result.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    write_spec_report(result, spec)


def _keep_video(video, out_dir: Path) -> None:
    with suppress(PlaywrightError, OSError):
        source = Path(video.path())
        if source.exists():
            shutil.move(str(source), out_dir / "video.webm")
    shutil.rmtree(out_dir / "_video", ignore_errors=True)


class _Run:
    """The state of one attempt. `go` returns (verdict, reason)."""

    def __init__(self, page: Page, spec: Spec, model: Model, options: RunOptions,
                 result: RunResult, out_dir: Path, log: Log, new_tabs: list[Page] | None = None) -> None:
        self.page, self.spec, self.model, self.options = page, spec, model, options
        self.result, self.out_dir, self.log = result, out_dir, log
        self.seen_pages: set[str] = set()
        self.new_tabs = new_tabs if new_tabs is not None else []
        self.started = time.time()

    def go(self, recording: Recording | None) -> tuple[str, str]:
        self.page.goto(self.spec.url, wait_until="domcontentloaded")
        settle(self.page)
        if recording is not None:
            verdict = self.replay(recording)
            if verdict is not None:
                return verdict
            if self.result.mode == "replay":
                return self.recheck(recording) or self.finish(
                    reason_if_unjudged="replayed the saved path with no errors (not judged)")
        return self.agent()

    # --- replay -----------------------------------------------------------------

    def replay(self, recording: Recording) -> tuple[str, str] | None:
        """Walk the saved path. Returns a verdict, or None when done or when the agent must take over."""
        self.result.mode = "replay"
        self.log(f"replaying the path saved {recording.recorded_at} ({len(recording.steps)} steps, no model)")
        for index, saved in enumerate(recording.steps, 1):
            if self.result.app_errors:
                return "fail", self.result.app_errors[0]
            check_page(self.page, self.result, self.seen_pages)
            shot_name = f"step-{index:02d}.jpg"
            (self.out_dir / shot_name).write_bytes(self.page.screenshot(type="jpeg", quality=JPEG_QUALITY))

            target = None
            if saved.locators:
                target = resolve(self.page, saved.locators)
                if target is None:
                    return self._broke(index, f'"{saved.target_label}" is not on the page any more')
            started = time.perf_counter()
            try:
                execute(self.page, saved.action, self._data_for(saved.action), target)
                settle(self.page)
                self._follow_new_tab()
            except ActionFailed as exc:
                return self._broke(index, str(exc))
            step = Step(index, saved.action, saved.description, outcome="replayed", action_ms=_ms_since(started),
                        screenshot=shot_name, target_label=saved.target_label, locators=saved.locators)
            self.result.steps.append(step)
            self.log(_step_line(step))
        if self.result.app_errors:
            return "fail", self.result.app_errors[0]
        return None

    def _broke(self, index: int, why: str) -> None:
        self.result.mode = "healed"
        self.result.healed_at = index
        self.log(f"{index:>2}. replay broke: {why}. The agent takes over from here.")
        return None

    # --- the agent ----------------------------------------------------------------

    def agent(self) -> tuple[str, str]:
        steps = self.result.steps
        first = len(steps) + 1
        last_fingerprint = ""
        invalid_streak = 0

        for index in range(first, first + self.spec.max_steps):
            observation = observe(self.page)
            check_page(self.page, self.result, self.seen_pages)
            fingerprint = observation.fingerprint()
            if steps and steps[-1].outcome == PENDING:
                steps[-1].outcome = "changed" if fingerprint != last_fingerprint else "no change"
                self.log(_step_line(steps[-1]))
            last_fingerprint = fingerprint

            if self.result.app_errors:
                return "fail", self.result.app_errors[0]
            if stuck := _stuck(steps):
                # Same contract as an agent's "fail": a blocked form in a negative test is
                # the expected result, and a model will keep pressing Submit at it.
                return self.overrule(stuck, observation, screenshot(self.page))

            shot_name = f"step-{index:02d}.jpg"
            shot = screenshot(self.page)
            (self.out_dir / shot_name).write_bytes(shot)

            started = time.perf_counter()
            context = Context(self.spec, observation, tuple(steps), shot if self.options.send_screenshot else None)
            try:
                raw = self.model.decide(context)
                action = validate_action(raw, observation, self.spec.data, TEST_KINDS)
            except ModelError as exc:
                return "error", f"model call failed: {exc}"
            except InvalidAction as exc:
                invalid_streak += 1
                step = Step(index, None, "(unusable reply)", outcome=f"invalid: {exc}",
                            model_ms=_ms_since(started), screenshot=shot_name)
                steps.append(step)
                self.log(_step_line(step))
                if invalid_streak >= INVALID_LIMIT:
                    return "error", f"the model gave {INVALID_LIMIT} unusable replies in a row"
                continue
            invalid_streak = 0

            element = observation.element(action.id) if action.id is not None else None
            label = element.label if element else ""
            step = Step(index, action, action.describe(label), thought=str(raw.get("thought") or "").strip()[:300],
                        model_ms=_ms_since(started), screenshot=shot_name, target_label=label)
            steps.append(step)

            if action.kind in ("pass", "fail"):
                step.outcome = "verdict"
                self.log(_step_line(step))
                if action.kind == "fail":
                    return self.overrule(action.reason or "", observation, shot)
                return self.finish(observation, shot, reason_if_unjudged=action.reason or "")

            if action.id is not None and self.options.recordings is not None:
                step.locators = describe_target(self.page, action.id, label)
            started = time.perf_counter()
            try:
                execute(self.page, action, self._data_for(action))
                settle(self.page)
                self._follow_new_tab()
            except ActionFailed as exc:
                step.outcome = f"failed: {exc}"
                self.log(_step_line(step))
            step.action_ms = _ms_since(started)

        if steps and steps[-1].outcome == PENDING:
            steps[-1].outcome = "done"
            self.log(_step_line(steps[-1]))
        return "error", f"ran out of steps ({self.spec.max_steps}) before the test finished"

    def _data_for(self, action) -> dict[str, str]:
        """The spec's data, plus {{email_code}} / {{email_link}} from the test inbox when the action uses them."""
        text = f"{action.text or ''} {action.value or ''}"
        wanted = [name for name in DYNAMIC if "{{" + name + "}}" in text.replace(" ", "")]
        if not wanted:
            return self.spec.data
        inbox = inbox_for(self.spec.inbox)
        if inbox is None:
            raise ActionFailed("no test inbox is set up: add inbox: to the spec, or set INBOX_URL or INBOX_IMAP_HOST")
        try:
            # Only mail sent since this test started: an old code from an earlier run is wrong by design.
            mail = wait_for_email(inbox, to=recipient(self.spec.data), since=self.started - 5)
            found = {"email_code": extract_code, "email_link": extract_link}
            extra = {name: found[name](mail) for name in wanted}
        except InboxError as exc:
            raise ActionFailed(str(exc)) from None
        self.log(f"    (read the email \"{mail.subject}\")")
        return {**self.spec.data, **extra}

    def _follow_new_tab(self) -> None:
        if tab := adopt_new_tab(self.new_tabs, self.spec.url, self.result):
            self.page = tab
            self.log("    (the link opened a new tab; carrying on there)")

    # --- the verdict ------------------------------------------------------------

    def recheck(self, recording: Recording) -> tuple[str, str] | None:
        """A replayed run passes with no model call when every quote that proved the recorded
        pass is on the page again, and nothing proven absent has appeared.

        That is a deterministic test, like the exported Playwright file: same path, same
        assertions. The code-level quote check is the same one the judge's answers go
        through. Anything missing (a changed total, a dynamic order number) falls back to
        the judge, so a regression is never waved through on old evidence.
        """
        if not self.options.judge or self.result.app_errors or not recording.checks:
            return None
        if [saved.get("expected") for saved in recording.checks] != list(self.spec.expect):
            return None
        observation = observe(self.page)
        page = mask(f"{observation.url}\n{observation.text}", self.spec.data)
        checks = []
        for saved in recording.checks:
            evidence, absent = list(saved.get("evidence") or []), list(saved.get("absent") or [])
            if not evidence and not absent:
                return None
            if not all(is_on_page(mask(quote, self.spec.data), page) for quote in evidence):
                return None
            if any(is_on_page(mask(quote, self.spec.data), page) for quote in absent):
                return None
            checks.append(Check(saved["expected"], evidence, "the recorded evidence is on the page again", True, absent))
        self.result.checks = checks
        self.log("checked against the recorded evidence (no model call):")
        for check in checks:
            proof = f' <- "{check.evidence[0]}"' if check.evidence else ""
            self.log(f"   [ok] {check.expected}{proof}")
        return "pass", "every expected result is shown on the page (recorded evidence re-checked, no model call)"

    def overrule(self, reason: str, observation: Observation, shot: bytes) -> tuple[str, str]:
        """The agent says fail, or keeps repeating an action with no effect. The spec is the
        contract: if the judge proves every expected result on the page anyway, the test
        passes and the complaint becomes a warning.

        This is for negative tests. A spec that expects "an error says the pincode must be
        6 digits" shows an error by design, and a small model's instinct calls any error a
        bug. A real failure mid-flow (a dead button, wrong data) leaves the expected results
        unproven, so it still fails, with the agent's own description of the bug.
        """
        if self.result.app_errors or not self.options.judge:
            return "fail", self.result.app_errors[0] if self.result.app_errors else reason
        verdict, judged_reason = self.finish(observation, shot)
        if verdict == "pass":
            self.result.warnings.insert(0, f"the agent reported a bug ({reason}), but the judge proved every "
                                           "expected result on the page, so the spec passes")
            return "pass", judged_reason
        return "fail", reason

    def finish(self, observation: Observation | None = None, shot: bytes | None = None,
               reason_if_unjudged: str = "") -> tuple[str, str]:
        if self.result.app_errors:
            return "fail", self.result.app_errors[0]
        if not self.options.judge:
            return "pass", reason_if_unjudged
        if observation is None:
            observation = observe(self.page)
            shot = screenshot(self.page)
        started = time.perf_counter()
        verdict, reason, checks = judge_page(self.model, self.spec, observation,
                                             shot if self.options.send_screenshot else None)
        self.result.judge_ms = _ms_since(started)
        self.result.checks = checks
        self.log(f"judge ({self.result.judge_ms / 1000:.1f}s):")
        for check in checks:
            mark = "ok" if check.holds else "NO"
            proof = f' <- "{check.evidence[0]}"' if check.evidence else ""
            self.log(f"   [{mark}] {check.expected}{proof}")
        return verdict, reason


def adopt_new_tab(new_tabs: list[Page], url: str, sink) -> Page | None:
    """A link with target="_blank" opens a new tab. A person carries on in it, so the tester does too.

    Found on a real site (Wagtail's "Visit the live page"): staying on the old tab,
    the agent clicked the link again and again and a working flow was reported broken.
    """
    if not new_tabs:
        return None
    tab = new_tabs[-1]
    new_tabs.clear()
    with suppress(PlaywrightError):
        tab.wait_for_load_state("domcontentloaded", timeout=10_000)
    listen(tab, url, sink)
    track_network(tab)
    settle(tab)
    return tab


def _stuck(steps: list[Step]) -> str | None:
    """Backstop for a model that keeps retrying a dead control instead of failing the test."""
    recent = steps[-STUCK_REPEATS:]
    if len(recent) < STUCK_REPEATS or any(step.action is None for step in recent):
        return None
    if len({step.action.signature for step in recent}) != 1:
        return None
    if all(step.outcome == "no change" or step.outcome.startswith("failed") for step in recent):
        last = recent[-1]
        if last.outcome.startswith("failed"):
            return f"{last.description} failed {STUCK_REPEATS} times in a row: {last.outcome.removeprefix('failed: ')}"
        return f"{last.description} had no effect {STUCK_REPEATS} times in a row"
    return None


def _step_line(step: Step) -> str:
    timing = f"  ({step.model_ms / 1000:.1f}s)" if step.model_ms else ""
    return f"{step.index:>2}. {step.description} -> {step.outcome}{timing}"


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
