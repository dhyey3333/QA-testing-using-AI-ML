"""What the browser can prove without a model.

Two signals fail a run on their own, even when the page looks fine:
  - an uncaught JavaScript error right after the test acted (one while a page loads is reported and
    the test carries on; js_errors: fail fails on both, js_errors: warn fails on neither)
  - an HTTP 5xx from the app's own origin
The rest are warnings a tester would note: console errors, 404s, slow calls,
dialogs, basic accessibility gaps (unlabelled fields, images without alt text), broken
images and mislabelled fields. Exploring promotes the last two to bugs when they are
proven (explore.py); a test run keeps them as warnings, so a broken footer picture
doesn't fail a checkout test.
"""

from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import unquote, urlsplit
from weakref import WeakKeyDictionary

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .observe import AD_REQUEST_RE, record_dialog

SLOW_MS = 3_000
MAX_WARNINGS = 100

_PAGE_CHECKS_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (el.checkVisibility && !el.checkVisibility()) return false;  // inside a closed <details>, say
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  const named = (el) => clean(el.getAttribute('aria-label')) || clean(el.getAttribute('title'))
    || (el.getAttribute('aria-labelledby') || '').split(/\s+/).some((id) => clean(document.getElementById(id)?.innerText))
    || (el.labels && [...el.labels].some((l) => clean(l.innerText)));
  const describe = (el) => {
    const hint = el.getAttribute('name') || el.id || el.getAttribute('type') || '';
    return `<${el.tagName.toLowerCase()}${hint ? ' ' + hint : ''}>`;
  };
  const issues = [];
  for (const img of document.images) {
    if (!visible(img)) continue;
    const src = img.getAttribute('src') || '';
    if (!img.hasAttribute('alt')) issues.push(`a11y: image without alt text (${src.split('/').pop().slice(0, 60)})`);
    if (src && img.complete && img.naturalWidth === 0) {
      // The resolved address, as a path for the site's own images, so exploring can match it with the
      // server's answer for that file.
      let where = src;
      try { const u = new URL(img.currentSrc || img.src, location.href); where = u.origin === location.origin ? u.pathname : u.href; } catch (e) {}
      issues.push(`broken image: ${where.slice(0, 100)}`);
    }
  }
  // A field whose label names one thing while the field itself names another known thing: the
  // label "Country" on a field named "state" whose placeholder says "Your state". The id is left out,
  // since it is usually the label's own anchor; name, test ids, autocomplete and the visible hint
  // are the field speaking for itself. Fields with meaningless names (field_7) are never flagged.
  const KINDS = {country: ['country'], state: ['state', 'province', 'region', 'county'], city: ['city', 'town'],
    postcode: ['postcode', 'postal', 'zip', 'zipcode', 'postalcode', 'pincode'], email: ['email', 'mail'],
    phone: ['phone', 'mobile', 'tel', 'telephone'], password: ['password', 'passwd', 'pwd'],
    'first name': ['firstname', 'fname', 'first', 'given'], 'last name': ['lastname', 'lname', 'last', 'surname', 'family'],
    address: ['address', 'street', 'addr'], 'date of birth': ['birth', 'dob', 'birthday', 'birthdate', 'bday']};
  const AUTOCOMPLETE = {country: 'country', 'country-name': 'country', 'address-level1': 'state', 'address-level2': 'city',
    'postal-code': 'postcode', email: 'email', tel: 'phone', 'current-password': 'password', 'new-password': 'password',
    'given-name': 'first name', 'family-name': 'last name', 'street-address': 'address', 'address-line1': 'address',
    bday: 'date of birth'};
  const words = (s) => (s || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z]+/).filter(Boolean);
  const kindsOf = (text) => { const w = new Set(words(text)); return Object.keys(KINDS).filter((k) => KINDS[k].some((x) => w.has(x))); };
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')) {
    if (!visible(el)) continue;
    const label = clean((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label'));
    const said = kindsOf(label);
    if (!said.length) continue;
    const own = new Set();
    const marks = [];
    for (const attr of ['name', 'data-test', 'data-testid', 'formcontrolname']) {
      const value = el.getAttribute(attr);
      if (value) { kindsOf(value).forEach((k) => own.add(k)); marks.push(`${attr}=${value}`); }
    }
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).pop();
    if (AUTOCOMPLETE[auto]) { own.add(AUTOCOMPLETE[auto]); marks.push(`autocomplete=${auto}`); }
    const hint = clean(el.getAttribute('placeholder') || (el.tagName === 'SELECT' && el.options[0] ? el.options[0].text : ''));
    if (hint) { kindsOf(hint).forEach((k) => own.add(k)); marks.push(`"${hint.slice(0, 40)}"`); }
    if (own.size && !said.some((k) => own.has(k))) {
      issues.push(`mislabelled field: labelled "${label.slice(0, 40)}" but it is the ${[...own].join('/')} field (${marks.join(', ').slice(0, 160)})`);
    }
  }
  const fields = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), select, textarea';
  for (const el of document.querySelectorAll(fields)) {
    if (visible(el) && !named(el) && !clean(el.getAttribute('placeholder'))) issues.push(`a11y: form field without a label ${describe(el)}`);
  }
  for (const el of document.querySelectorAll('button, a[href], [role=button]')) {
    if (visible(el) && !clean(el.innerText) && !named(el) && !el.querySelector('img[alt]:not([alt=""]), svg title')) {
      issues.push(`a11y: ${el.tagName.toLowerCase()} with no accessible name ${describe(el)}`);
    }
  }
  if (!clean(document.title)) issues.push('a11y: page has no <title>');
  if (!document.documentElement.getAttribute('lang')) issues.push('a11y: page has no lang attribute');
  return issues;
}
"""


class Sink(Protocol):
    app_errors: list[str]
    warnings: list[str]


def warn(sink: Sink, message: str) -> None:
    if message not in sink.warnings and len(sink.warnings) < MAX_WARNINGS:
        sink.warnings.append(message)


# Pages the test has acted on since their document loaded. An uncaught error before that came from the page
# loading by itself; after it, it follows something the test did.
_ACTED: WeakKeyDictionary = WeakKeyDictionary()


def note_action(page: Page) -> None:
    """The test is about to act on this page (actions.execute calls this)."""
    _ACTED[page] = True


def listen(page: Page, url: str, sink: Sink, js_errors: str = "fail") -> None:
    """Wire the browser's own signals into `sink` for the life of the page.

    Uncaught JS errors: "fail" makes every one an app error. "report" (the default for tests, decided
    2026-10-13) makes one thrown while a page loads, before the test has acted on it, a warning at the
    top of the report, and the test carries on: its steps and expected results decide. Found on a
    practice site (blind set 3): two pages threw on load while their flows worked, and both tests failed
    at once without trying a step. An error right after an action still fails. "warn" makes every one a
    warning; found on a big real shopping site, whose analytics and ad scripts threw on every page.
    The app's 5xx still fail.
    """
    origin = _origin(url)
    _ACTED.setdefault(page, False)

    def on_request(request) -> None:
        # A new document is on its way: what its scripts throw while it loads is the page's own doing.
        # (A same-page route change makes no navigation request, so it doesn't count as a new page.)
        try:
            if request.is_navigation_request() and request.frame == page.main_frame:
                _ACTED[page] = False
        except PlaywrightError:
            pass

    def on_page_error(error) -> None:
        if not same_site(page.url, url):
            return  # another site's script error, after a link led there: not this app's bug
        message = f"uncaught JS error: {_first_line(error.message)}"
        if js_errors == "warn":
            warn(sink, f"{message} (a warning only: js_errors is warn)")
        elif js_errors == "report" and not _ACTED.get(page, False):
            report = f"uncaught JS error while the page loaded: {_first_line(error.message)} (reported; the test carried on)"
            if report not in sink.warnings:
                sink.warnings.insert(0, report)  # first in the list, so it isn't lost among the minor ones
        else:
            sink.app_errors.append(message)

    def on_response(response) -> None:
        # Only the app's own origin: a third-party analytics 503 is not this app's bug.
        if _origin(response.url) != origin:
            return
        path = urlsplit(response.url).path
        if path == "/favicon.ico":
            return  # browsers ask for it on every site; a missing one is not the app's bug
        if response.status >= 500:
            sink.app_errors.append(f"HTTP {response.status} on {response.request.method} {path}")
        elif response.status in (404, 410):
            warn(sink, f"broken link: HTTP {response.status} on {response.request.method} {path}")
        elif response.status >= 400 and response.request.resource_type in ("xhr", "fetch"):
            # The app's own API refusing a call (a 401, a 422) is often why a flow went wrong later.
            warn(sink, f"api error: HTTP {response.status} on {response.request.method} {path}")

    def on_request_finished(request) -> None:
        try:
            took = request.timing.get("responseEnd", -1)
        except PlaywrightError:
            return
        if took and took > SLOW_MS and _origin(request.url) == origin:
            warn(sink, f"slow: {request.method} {urlsplit(request.url).path} took {took / 1000:.1f}s")

    def on_console(message) -> None:
        if message.type != "error":
            return
        where = str((message.location or {}).get("url", ""))
        if where.endswith("/favicon.ico"):
            return
        # Nightshift aborts ad networks' requests (observe.block_ads) and Chrome logs each abort as
        # "Failed to load resource". Found on a practice site: that self-inflicted error was reported
        # as the site's. The site's own failed requests still count.
        if AD_REQUEST_RE.match(where):
            return
        # The page has left the app (a Facebook or Twitter link in the footer). Found exploring a
        # practice site: facebook.com's own console error was filed as one of the site's findings.
        if not same_site(page.url, url):
            return
        text = message.text or ""
        if text.strip() == "STACKTRACE:":
            return  # Angular 2 logs this header line alone before the stack: noise, not a message
        # Angular, React and Vue catch the app's own crashes and log them instead of letting them
        # reach the browser, so they never count as uncaught JS errors. Found exploring a practice
        # site: a link to a route that doesn't exist crashed the app as four console lines, filed as
        # four warnings. One crash from the site's own code becomes one "app error" line.
        if _APP_CRASH_RE.search(text.strip().split("\n")[0]) and (not where or same_site(where, url)):
            warn(sink, f"app error: {crash_core(text)}")
            return
        warn(sink, f"console: {text[:300]}")

    def on_dialog(dialog) -> None:
        # Playwright dismisses dialogs by default, which silently cancels confirm() flows.
        warn(sink, f"{dialog.type} dialog accepted: {dialog.message[:200]}")
        answer = {"alert": "closed with OK", "confirm": "answered OK", "beforeunload": "answered Leave",
                  "prompt": "answered with its default value"}.get(dialog.type, "accepted")
        record_dialog(page, dialog.type, dialog.message, answer)
        dialog.accept()

    page.on("request", on_request)
    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("requestfinished", on_request_finished)
    page.on("console", on_console)
    page.on("dialog", on_dialog)


# Query values that are what someone typed or where they came from, never which page this is.
_FREE_TEXT_KEYS = {"search", "q", "query", "s", "keyword", "keywords", "term", "filter_name", "tag", "email",
                   "return", "redirect", "next", "ref", "fbclid", "gclid", "sid", "session", "token"}
_ROUTE_VALUE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{0,60}")


def page_key(url: str) -> str:
    """Which page of the site a URL is: its path, the query values that name a page, and the #route.

    Found exploring a store built on OpenCart, where every page is /index.php and the query says which
    (?route=checkout/cart, ?route=product/product&product_id=40): the whole store counted as one page,
    the explorer was sent back to the start six times for spending "too long" on it, and only one
    page got accessibility checks. A query value that reads like a name (route=checkout/cart) is part
    of the page; a number (product_id=40) is not, so every product page is one kind of page; and what
    people type or tracking tags never are.
    """
    parts = urlsplit(url)
    kept = []
    for pair in sorted(p for p in parts.query.split("&") if p):
        key, _, value = pair.partition("=")
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _FREE_TEXT_KEYS:
            continue
        kept.append(f"{key}={value}" if _ROUTE_VALUE_RE.fullmatch(unquote(value)) else key)
    fragment = parts.fragment.split("?")[0]
    return parts.path + (f"?{'&'.join(kept)}" if kept else "") + (f"#{fragment}" if fragment else "")


def check_page(page: Page, sink: Sink, seen: set[str]) -> None:
    """Accessibility and broken-image checks, once per distinct page in a run."""
    if not page.url.startswith(("http://", "https://")):
        return  # about:blank after going back past the start, a browser error page: not the app's
    key = page_key(page.url)
    if key in seen:
        return
    seen.add(key)
    try:
        issues = page.evaluate(_PAGE_CHECKS_JS)
    except PlaywrightError:
        return
    for issue in issues:
        warn(sink, f"{issue} on {key}")


# What frameworks log when they catch the app's own crash.
_APP_CRASH_RE = re.compile(r"Uncaught \(in promise\)|Unhandled Promise rejection|^EXCEPTION:|^ERROR Error|"
                           r"Cannot match any routes|The above error occurred in|Minified React error|"
                           r"\[Vue warn\]: Error in")
_CRASH_PREFIX_RE = re.compile(r"^(?:\s*(?:EXCEPTION:|ERROR\b|Unhandled Promise rejection:|Uncaught \(in promise\):?|"
                              r"Error:))+\s*")


def crash_core(text: str) -> str:
    """The error itself, without the framework's wrapping: "EXCEPTION: Error: Uncaught (in promise):
    Error: Cannot match any routes: 'broken'" and "Unhandled Promise rejection: Cannot match any
    routes: 'broken' ; Zone: angular ; ..." are both "Cannot match any routes: 'broken'"."""
    line = text.strip().split("\n")[0].split(" ; Zone:")[0]
    return (_CRASH_PREFIX_RE.sub("", line).strip() or line.strip())[:200]


def same_site(page_url: str, app_url: str) -> bool:
    """Is the page part of the app under test? The same host, or one is a subdomain of the other,
    ignoring "www." (a site that redirects example.com to www.example.com or app.example.com is
    still itself). A blank or browser page (about:blank, data:) counts as the app's."""
    page_host = (urlsplit(page_url).hostname or "").removeprefix("www.")
    app_host = (urlsplit(app_url).hostname or "").removeprefix("www.")
    if not page_host or not app_host:
        return True
    return page_host == app_host or page_host.endswith("." + app_host) or app_host.endswith("." + page_host)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:300] if lines else ""
