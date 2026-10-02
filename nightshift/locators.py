"""Finding the same element again on a later run.

Element numbers only last one turn. To replay a run without the model, each
acted-on element is saved as a list of ways to find it, ordered like
Playwright's own advice: test id, then role + accessible name, then label,
placeholder and text, with a CSS path as the last resort. Only candidates that
matched exactly that element when it was recorded are kept.

Replay tries them in order. When none matches any more (the button was renamed,
the page redesigned), the agent takes over from that step: self-healing.
"""

from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from .observe import DEEP_JS

RESOLVE_TIMEOUT_S = 4.0

_DESCRIBE_JS = r"""
(id) => {
  /*DEEP*/
  const el = deepAll(`[data-ns-id="${id}"]`)[0];
  if (!el) return null;
  const root = el.getRootNode();  // the document, or the shadow root the element lives in
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();

  const implicitRole = () => {
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    if (tag !== 'input') return null;
    if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
    if (type === 'checkbox') return 'checkbox';
    if (type === 'radio') return 'radio';
    if (type === 'number') return 'spinbutton';
    if (type === 'search') return 'searchbox';
    if (['', 'text', 'email', 'tel', 'url'].includes(type)) return 'textbox';
    return null;  // password, date, file...: no role Playwright can find them by
  };

  const unique = (node) => node.id && root.querySelectorAll('#' + CSS.escape(node.id)).length === 1;
  const cssPath = (node) => {
    if (unique(node)) return '#' + CSS.escape(node.id);
    const parts = [];
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      if (unique(node)) {
        parts.unshift('#' + CSS.escape(node.id));
        return parts.join(' > ');
      }
      const siblings = node.parentElement ? [...node.parentElement.children].filter((s) => s.tagName === node.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
      node = node.parentElement;
    }
    // Inside a shadow root the path starts at the root's top element; Playwright's CSS pierces open shadow roots.
    return (root === document ? 'body > ' : '') + parts.join(' > ');
  };

  const labelText = el.labels && el.labels.length ? clean([...el.labels].map((l) => l.innerText).join(' ')) : '';
  return {
    role: el.getAttribute('role') || implicitRole(),
    testId: el.getAttribute('data-testid') || '',
    ariaLabel: clean(el.getAttribute('aria-label')),
    labelText,
    placeholder: clean(el.getAttribute('placeholder')),
    text: ['input', 'select', 'textarea'].includes(tag) ? '' : clean(el.innerText),
    css: cssPath(el),
  };
}
""".replace("/*DEEP*/", DEEP_JS)


def to_locator(page: Page, candidate: dict) -> Locator:
    by, value = candidate["by"], candidate.get("value", "")
    match by:
        case "test_id":
            return page.get_by_test_id(value)
        case "role":
            return page.get_by_role(candidate["role"], name=candidate["name"], exact=True)
        case "label":
            return page.get_by_label(value, exact=True)
        case "placeholder":
            return page.get_by_placeholder(value, exact=True)
        case "text":
            return page.get_by_text(value, exact=True)
        case _:
            return page.locator(value)


def describe_target(page: Page, element_id: int, label: str) -> list[dict]:
    """Ways to find element `element_id` again, verified against the live page. [] if it vanished."""
    try:
        info = page.evaluate(_DESCRIBE_JS, element_id)
    except PlaywrightError:
        return []
    if not info:
        return []

    name = info["ariaLabel"] or info["labelText"] or info["text"] or label
    candidates: list[dict] = []
    if info["testId"]:
        candidates.append({"by": "test_id", "value": info["testId"]})
    if info["role"] and name:
        candidates.append({"by": "role", "role": info["role"], "name": name[:120]})
    if info["labelText"] or info["ariaLabel"]:
        candidates.append({"by": "label", "value": (info["labelText"] or info["ariaLabel"])[:120]})
    if info["placeholder"]:
        candidates.append({"by": "placeholder", "value": info["placeholder"]})
    if info["text"] and len(info["text"]) <= 80:
        candidates.append({"by": "text", "value": info["text"]})
    css = {"by": "css", "value": info["css"]}

    verified = [c for c in candidates if _matches_only(page, c, element_id)]
    # The CSS path is kept even unverified: when everything else is gone it is still worth a try.
    return verified + [css]


def resolve(page: Page, candidates: list[dict], timeout_s: float = RESOLVE_TIMEOUT_S) -> Locator | None:
    """The first candidate that finds exactly one element, polling briefly for late renders."""
    deadline = time.monotonic() + timeout_s
    while True:
        for candidate in candidates:
            locator = _single(to_locator(page, candidate))
            if locator is not None:
                return locator
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(250)


def _single(locator: Locator) -> Locator | None:
    try:
        count = locator.count()
        if count == 1:
            return locator
        if count > 1:
            # Several matches, one visible (a mobile and a desktop menu, say): take the visible one.
            visible = [locator.nth(i) for i in range(min(count, 10)) if locator.nth(i).is_visible()]
            if len(visible) == 1:
                return visible[0]
    except PlaywrightError:
        pass
    return None


def _matches_only(page: Page, candidate: dict, element_id: int) -> bool:
    try:
        locator = to_locator(page, candidate)
        if locator.count() != 1:
            return False
        return locator.get_attribute("data-ns-id", timeout=1_000) == str(element_id)
    except PlaywrightError:
        return False
