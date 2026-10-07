"""What the browser can prove without a model.

Two signals fail a run on their own, even when the page looks fine:
  - an uncaught JavaScript error (unless the spec or run sets js_errors: warn)
  - an HTTP 5xx from the app's own origin
The rest are warnings a tester would note: console errors, 404s, slow calls,
dialogs, basic accessibility gaps (unlabelled fields, images without alt text), broken
images and mislabelled fields. Exploring promotes the last two to bugs when they are
proven (explore.py); a test run keeps them as warnings, so a broken footer picture
doesn't fail a checkout test.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

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


def listen(page: Page, url: str, sink: Sink, js_errors: str = "fail") -> None:
    """Wire the browser's own signals into `sink` for the life of the page.

    js_errors="warn" turns uncaught JS errors into warnings. Found on a big real shopping
    site: analytics and ad scripts threw errors in the background on every page, so every
    test failed on errors no user would notice. The app's 5xx still fail.
    """
    origin = _origin(url)

    def on_page_error(error) -> None:
        message = f"uncaught JS error: {_first_line(error.message)}"
        if js_errors == "warn":
            warn(sink, f"{message} (a warning only: js_errors is warn)")
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
        if str((message.location or {}).get("url", "")).endswith("/favicon.ico"):
            return
        warn(sink, f"console: {message.text[:300]}")

    def on_dialog(dialog) -> None:
        # Playwright dismisses dialogs by default, which silently cancels confirm() flows.
        warn(sink, f"{dialog.type} dialog accepted: {dialog.message[:200]}")
        dialog.accept()

    page.on("pageerror", on_page_error)
    page.on("response", on_response)
    page.on("requestfinished", on_request_finished)
    page.on("console", on_console)
    page.on("dialog", on_dialog)


def check_page(page: Page, sink: Sink, seen: set[str]) -> None:
    """Accessibility and broken-image checks, once per distinct page in a run."""
    parts = urlsplit(page.url)
    key = f"{parts.path}#{parts.fragment}"
    if key in seen:
        return
    seen.add(key)
    try:
        issues = page.evaluate(_PAGE_CHECKS_JS)
    except PlaywrightError:
        return
    for issue in issues:
        warn(sink, f"{issue} on {key}")


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:300] if lines else ""
