"""The actions the agent can take: validating a model reply, then running it."""

from __future__ import annotations

import contextlib
import re
from urllib.parse import urljoin
import time
from collections.abc import Iterable
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from .observe import Observation

ACTION_TIMEOUT_MS = 5_000
SCROLL_PX = 600

INTERACTIONS = {"click", "type", "select"}  # the ones that need an element id
BROWSING = INTERACTIONS | {"press", "scroll", "wait", "back", "goto"}
TEST_KINDS = BROWSING | {"pass", "fail"}
EXPLORE_KINDS = BROWSING | {"report", "done"}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# Playwright's errors are long call logs. The model gets one plain reason instead.
_HINTS = (
    ("not enabled", "the element is disabled"),
    ("intercepts pointer events", "another element is covering it"),
    ("not visible", "the element is not visible"),
    ("not an <input>", "the element is not a text field"),
    ("not a <select>", "the element is not a dropdown"),
)


class InvalidAction(ValueError):
    """The model's reply can't be run. The message is shown to the model next turn."""


class ActionFailed(RuntimeError):
    """The browser refused the action: element disabled, covered, not a text field..."""


@dataclass(frozen=True)
class Action:
    kind: str
    id: int | None = None
    text: str | None = None  # type: may hold {{placeholders}}
    value: str | None = None  # select: the option to pick
    key: str | None = None  # press
    direction: str | None = None  # scroll
    reason: str | None = None  # pass / fail / done
    title: str | None = None  # report (explore mode)
    details: str | None = None  # report (explore mode)

    @property
    def signature(self) -> tuple:
        return (self.kind, self.id, self.text, self.value, self.key, self.direction)

    def describe(self, label: str = "") -> str:
        """One readable line, e.g. `click [12] "Add to cart"`. Test data stays as placeholders."""
        target = ""
        if self.id is not None:
            target = f' [{self.id}] "{label}"' if label else f" [{self.id}]"
        elif label:
            target = f' "{label}"'
        match self.kind:
            case "type":
                return f'type{target} <- "{self.text}"'
            case "select":
                return f'select{target} <- "{self.value}"'
            case "press":
                return f"press {self.key}"
            case "scroll":
                return f"scroll {self.direction}"
            case "back":
                return "go back"
            case "goto":
                return f"go to {self.value}"
            case "pass" | "fail" | "done":
                return f"{self.kind.upper()}: {self.reason}"
            case "report":
                return f"REPORT: {self.title}"
            case _:
                return f"{self.kind}{target}"


def validate_action(
    raw: dict,
    observation: Observation,
    data: dict[str, str],
    allowed: Iterable[str] = TEST_KINDS,
    avoid: Iterable[str] = (),
) -> Action:
    """Turn a parsed model reply into an Action, or say exactly what is wrong with it.

    Small models drift on field names, so a few obvious aliases are accepted:
    "element_id" for "id", "option" for "value", "summary" for "reason".
    """
    allowed = set(allowed)
    kind = str(raw.get("action", "")).strip().lower()
    if kind not in allowed:
        raise InvalidAction(f'unknown action "{kind}"; use one of: {", ".join(sorted(allowed))}')
    fields: dict = {"kind": kind}

    if kind in INTERACTIONS:
        raw_id = raw.get("id", raw.get("element_id"))
        try:
            element_id = int(raw_id)
        except (TypeError, ValueError):
            raise InvalidAction(f'"{kind}" needs a numeric "id" from the ELEMENTS list') from None
        element = observation.element(element_id)
        if element is None:
            raise InvalidAction(f"there is no element [{element_id}] on this page")
        banned = next((word for word in avoid if word and word.lower() in element.label.lower()), None)
        if banned:
            raise InvalidAction(f'[{element_id}] "{element.label}" is on the AVOID list ({banned}); pick something else')
        _check_control(kind, element_id, element)
        fields["id"] = element_id

    if kind == "type":
        text = raw.get("text")
        if not isinstance(text, str):
            raise InvalidAction('"type" needs a "text" string')
        unknown = [key for key in _PLACEHOLDER_RE.findall(text) if key not in data]
        if unknown:
            available = ", ".join("{{" + key + "}}" for key in data) or "none"
            raise InvalidAction("there is no test data called {{" + unknown[0] + "}}; available: " + available)
        fields["text"] = text
    elif kind == "select":
        value = raw.get("value", raw.get("option"))
        if not isinstance(value, str) or not value:
            raise InvalidAction('"select" needs a "value": the option to pick')
        current = observation.element(fields["id"]).value or ""
        if current.strip().casefold() == value.strip().casefold():
            # Re-selecting changes nothing, and a model that keeps trying looks like a dead control.
            raise InvalidAction(f'[{fields["id"]}] already shows "{current}"; go on to the next step')
        fields["value"] = value
    elif kind == "goto":
        path = str(raw.get("url") or raw.get("value") or "").strip()
        if not path.startswith("/") or path.startswith("//"):
            raise InvalidAction('"goto" needs "url": a path on this site that starts with /, e.g. "/admin/"')
        fields["value"] = path
    elif kind == "press":
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise InvalidAction('"press" needs a "key", e.g. "Enter"')
        fields["key"] = key
    elif kind == "scroll":
        direction = str(raw.get("direction", "down")).strip().lower()
        if direction not in ("up", "down"):
            raise InvalidAction('"scroll" needs "direction": "up" or "down"')
        fields["direction"] = direction
    elif kind in ("pass", "fail", "done"):
        reason = str(raw.get("reason") or raw.get("summary") or "").strip()
        fields["reason"] = reason or "(no reason given)"
    elif kind == "report":
        title = str(raw.get("title") or "").strip()
        if not title:
            raise InvalidAction('"report" needs a "title": the bug in one line')
        fields["title"] = title[:200]
        fields["details"] = str(raw.get("details") or "").strip()[:1000]

    return Action(**fields)


def _check_control(kind: str, element_id: int, element) -> None:
    """Refuse actions that can't work on this kind of control, and say what does.

    Found on the holdout app: a small model clicks a native <select> or a date
    field like a button. The browser draws their pickers outside the page, so the
    click changes nothing, the model clicks again, and a working form gets
    reported as broken. Telling it the right action fixes that on the next turn.
    """
    if element.tag == "select" and kind in ("click", "type"):
        options = ", ".join(f'"{o}"' for o in (element.options or ())[:12]) or "(none listed)"
        raise InvalidAction(f'[{element_id}] "{element.label}" is a dropdown; {kind} does nothing. Use '
                            f'{{"action": "select", "id": {element_id}, "value": "<option>"}} with one of: {options}')
    if element.tag == "input" and element.type in DATE_FORMATS and kind == "click":
        raise InvalidAction(f'[{element_id}] "{element.label}" is a {element.type} field; clicking does nothing. Type '
                            f'the value instead: {{"action": "type", "id": {element_id}, "text": "{DATE_FORMATS[element.type]}"}}')
    if kind == "select" and element.tag != "select" and element.type not in ("radio",) and element.role != "radio":
        raise InvalidAction(f'[{element_id}] "{element.label}" is not a dropdown; use click or type')


# The format a browser expects when you type into these inputs, as an example value.
DATE_FORMATS = {"date": "2031-01-31", "time": "14:30", "datetime-local": "2031-01-31T14:30",
                "month": "2031-01", "week": "2031-W05"}


def fill_placeholders(text: str, data: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda match: data[match.group(1)], text)


def element_locator(page: Page, element_id: int) -> Locator:
    """The element observe() numbered. Only valid until the next observe()."""
    return page.locator(f'[data-ns-id="{element_id}"]')


def execute(page: Page, action: Action, data: dict[str, str], target: Locator | None = None) -> None:
    """Run one action. Raises ActionFailed with a short reason the model can read.

    `target` is the element to act on. The agent passes nothing and the element
    observe() numbered is used; replay passes the locator it re-found.
    """
    if target is None and action.id is not None:
        target = element_locator(page, action.id)
    try:
        match action.kind:
            case "click":
                target.click(timeout=ACTION_TIMEOUT_MS)
            case "type":
                target.fill(fill_placeholders(action.text, data), timeout=ACTION_TIMEOUT_MS)
            case "select":
                _select(target, action.value)
            case "press":
                page.keyboard.press(action.key)
            case "scroll":
                page.mouse.wheel(0, SCROLL_PX if action.direction == "down" else -SCROLL_PX)
            case "wait":
                page.wait_for_timeout(1_000)
            case "back":
                page.go_back(wait_until="domcontentloaded", timeout=10_000)
            case "goto":
                # A path on the site the page is already on: a test never leaves for another host.
                page.goto(urljoin(page.url, action.value), wait_until="domcontentloaded", timeout=15_000)
    except PlaywrightError as exc:
        raise ActionFailed(_short_reason(exc)) from None


class _Network:
    """Counts the page's in-flight requests, so settle() can wait for the ones an action set off."""

    def __init__(self, page: Page) -> None:
        self.in_flight = 0
        self.last_change = time.monotonic()
        page.on("request", self._started)
        page.on("requestfinished", self._ended)
        page.on("requestfailed", self._ended)

    def _started(self, request) -> None:
        self.in_flight += 1
        self.last_change = time.monotonic()

    def _ended(self, request) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.last_change = time.monotonic()


_NETWORK: WeakKeyDictionary = WeakKeyDictionary()
QUIET_S = 0.3  # the network must be quiet this long before the page counts as settled
SETTLE_MAX_S = 3.0  # but never wait longer than this (polling, websockets, analytics beacons)


def track_network(page: Page) -> None:
    """Call once per page, before the first action."""
    _NETWORK[page] = _Network(page)


def settle(page: Page) -> None:
    """Wait for the page to finish reacting to the last action.

    Playwright's "networkidle" only describes the initial page load: once a page
    has loaded, it returns at once, even while a click's fetch is still in flight.
    Reading the page then shows the old state, the agent sees "no change" and
    clicks again into a re-rendering page. So in-flight requests are counted
    here and the page must be quiet for QUIET_S, capped at SETTLE_MAX_S.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except PlaywrightError:
        pass
    network = _NETWORK.get(page)
    if network is None:
        with contextlib.suppress(PlaywrightError):
            page.wait_for_load_state("networkidle", timeout=2_000)
    else:
        page.wait_for_timeout(100)  # give the action's own requests a moment to start
        deadline = time.monotonic() + SETTLE_MAX_S
        while time.monotonic() < deadline:
            if network.in_flight == 0 and time.monotonic() - network.last_change >= QUIET_S:
                break
            page.wait_for_timeout(50)  # also lets Playwright deliver the request events
    page.wait_for_timeout(150)  # let the framework paint what the last response changed


_OPTIONS_JS = "el => el.tagName === 'SELECT' ? [...el.options].map(o => [o.label || o.text, o.value]) : null"


def _select(target: Locator, wanted: str) -> None:
    """Pick the option the model meant.

    Exact label, then exact value, then the one option whose label contains what
    was asked for (or is contained in it): "Dr. Rahul Menon" finds "Dr. Rahul Menon
    (General physician)". Anything ambiguous fails with the real options listed.
    """
    options = target.evaluate(_OPTIONS_JS, timeout=ACTION_TIMEOUT_MS)
    if options is None:  # a radio button: selecting it is clicking it
        target.check(timeout=ACTION_TIMEOUT_MS)
        return
    norm = lambda s: " ".join(str(s).split()).casefold()  # noqa: E731
    want = norm(wanted)
    tiers = [
        [v for label, v in options if norm(label) == want],
        [v for label, v in options if norm(v) == want],
        [v for label, v in options if want and want in norm(label)],
        [v for label, v in options if norm(label) and norm(label) in want],
    ]
    for matches in tiers:
        if len(matches) == 1:
            target.select_option(value=matches[0], timeout=ACTION_TIMEOUT_MS)
            return
        if len(matches) > 1:
            break
    labels = ", ".join(f'"{label}"' for label, _ in options[:12])
    raise ActionFailed(f'no single option matches "{wanted}"; the options are: {labels}')


def _short_reason(exc: Exception) -> str:
    message = str(exc)
    for needle, hint in _HINTS:
        if needle in message:
            return hint
    lines = message.strip().splitlines()
    return lines[0][:200] if lines else type(exc).__name__
