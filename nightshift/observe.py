"""Reading the page: what the agent can see and act on.

Every visible interactive element gets a number (stored in a data-ns-id attribute)
so the model acts by id instead of guessing pixel coordinates, which small models
are bad at. The same numbers are drawn on the screenshot as red badges, so the
text list and the picture agree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

MAX_ELEMENTS = 80
JPEG_QUALITY = 60

_OBSERVE_JS = r"""
(maxElements) => {
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[role="menuitem"]', '[role="switch"]', '[role="option"]',
    '[contenteditable="true"]', '[onclick]', '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (clean(aria)) return clean(aria);
    const ids = el.getAttribute('aria-labelledby');
    if (ids) {
      const text = clean(ids.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' '));
      if (text) return text;
    }
    if (el.labels && el.labels.length) {
      const text = clean([...el.labels].map((l) => l.innerText).join(' '));
      if (text) return text;
    }
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type)) {
      // <input type="submit"> with no value: the browser draws "Submit" (found on a real site).
      return clean(el.value) || ({ submit: 'Submit', reset: 'Reset' })[el.type] || '';
    }
    if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) {
      const text = clean(el.innerText);
      if (text) return text;
    }
    return clean(el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt')
      || el.querySelector('img')?.getAttribute('alt') || el.getAttribute('name') || '');
  };

  // A field the browser is complaining about right now: it fired "invalid" (the browser
  // blocked a submit because of it, see _INVALID_WATCH_JS) and it is still invalid.
  const complaint = (el) => el.dataset.nsInvalid === '1' && el.willValidate && !el.validity.valid
    && el.validationMessage;

  const isVisible = (el) => {
    if (el.type === 'hidden') return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0;
  };

  document.querySelectorAll('[data-ns-id]').forEach((el) => el.removeAttribute('data-ns-id'));
  const found = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (!isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    found.push({ el, inView: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth });
  }
  // On-screen elements first (sort is stable, so document order holds within each group),
  // so the cap drops what is far down a long page rather than what the user sees.
  found.sort((a, b) => b.inView - a.inView);

  // Twelve buttons all called "Add to cart" can't be told apart by label. Found on public demo
  // shops: the model clicked the wrong product's button, or none. So a control whose label is
  // shared gets the name of its own item: the closest ancestor that holds no other control
  // with that label (a product card, a table row), named by its heading or first text line.
  const PRICE = /^(rs\.?|inr|₹|\$|€|£)?\s*[\d.,]+\s*(rs\.?|inr|₹|\$|€|£)?$/i;
  const itemName = (el, label) => {
    const same = (node) => [...node.querySelectorAll(SELECTOR)]
      .some((other) => other !== el && isVisible(other) && labelOf(other).toLowerCase() === label);
    let item = null;
    for (let node = el.parentElement, depth = 0; node && node !== document.body && depth < 8;
         node = node.parentElement, depth++) {
      if (same(node)) break;
      item = node;
    }
    if (!item) return '';
    const headings = [...item.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading],[class*="name" i],[class*="title" i]')]
      .map((h) => clean(h.innerText));
    const lines = (item.innerText || '').split(/[\n\t]/).map(clean);  // table cells are tab-separated
    const name = [...headings, ...lines].find((text) => text && text.length <= 60 && /[a-z]{2}/i.test(text)
      && text.toLowerCase() !== label && !PRICE.test(text));
    return name || '';
  };
  const labels = found.map(({ el }) => labelOf(el));
  const counts = {};
  labels.forEach((l) => { counts[l.toLowerCase()] = (counts[l.toLowerCase()] || 0) + 1; });

  const elements = found.slice(0, maxElements).map(({ el, inView }, i) => {
    const id = i + 1;
    el.setAttribute('data-ns-id', String(id));
    const tag = el.tagName.toLowerCase();
    let label = labels[i];
    const field = ['select', 'textarea'].includes(tag)
      || (tag === 'input' && !['submit', 'button', 'reset', 'image'].includes(el.type));
    if (label && counts[label.toLowerCase()] > 1 && !field) {
      const name = itemName(el, label.toLowerCase());
      if (name) label = `${label} — ${name}`;
    }
    const item = { id, tag, label: label.slice(0, 120), inView };
    const role = el.getAttribute('role');
    if (role) item.role = role;
    if (tag === 'input') item.type = el.type;
    if ((tag === 'input' && !['checkbox', 'radio', 'submit', 'button', 'reset', 'file'].includes(el.type)) || tag === 'textarea') {
      item.value = el.type === 'password' ? (el.value ? '********' : '') : el.value.slice(0, 120);
    }
    if (tag === 'select') {
      item.value = clean(el.selectedOptions[0]?.text);
      item.options = [...el.options].slice(0, 20).map((o) => clean(o.text));
    }
    if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
    if (el.disabled) item.disabled = true;
    if (complaint(el)) item.invalid = el.validationMessage;
    return item;
  });

  let text = (document.body?.innerText || '')
    .split('\n').map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
  // The browser's own form validation ("Please include an '@'...") is a tooltip outside the
  // page, so innerText never has it. Add it as text the agent can read and the judge can quote.
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (complaint(el)) text += `\n[browser says] ${labelOf(el) || el.name || 'field'}: ${el.validationMessage}`;
  }
  return { url: location.href, title: document.title, text: text.slice(0, 20000), scrollY: Math.round(scrollY), elements };
}
"""

# Runs in every page before its own scripts. The browser fires "invalid" on each field
# when it blocks a form submit; marking those fields lets observe() report exactly
# what the browser's tooltip says, only once the browser has actually said it.
_INVALID_WATCH_JS = """
document.addEventListener('invalid', (event) => { event.target.dataset.nsInvalid = '1'; }, true);
"""


def watch_form_validation(context) -> None:
    """Call once per browser context, before its first page."""
    context.add_init_script(_INVALID_WATCH_JS)


_MARKS_JS = r"""
(show) => {
  document.getElementById('__ns_marks')?.remove();
  if (!show) return;
  const layer = document.createElement('div');
  layer.id = '__ns_marks';
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  for (const el of document.querySelectorAll('[data-ns-id]')) {
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight) continue;
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;outline:2px solid #e11d48`;
    const badge = document.createElement('div');
    badge.textContent = el.getAttribute('data-ns-id');
    badge.style.cssText = `position:fixed;left:${Math.max(0, r.left - 4)}px;top:${Math.max(0, r.top - 8)}px;`
      + 'background:#e11d48;color:#fff;font:bold 11px/14px monospace;padding:0 3px;border-radius:3px';
    layer.append(box, badge);
  }
  // On <html>, not <body>, so the badges never show up in body.innerText.
  document.documentElement.append(layer);
}
"""


@dataclass(frozen=True)
class Element:
    id: int
    tag: str
    label: str
    in_view: bool = True
    role: str | None = None
    type: str | None = None
    value: str | None = None
    options: tuple[str, ...] | None = None
    checked: bool | None = None
    disabled: bool = False
    invalid: str | None = None  # the browser's validation message, when it is complaining about this field

    @classmethod
    def from_js(cls, raw: dict) -> Element:
        return cls(
            id=int(raw["id"]),
            tag=raw["tag"],
            label=raw.get("label", ""),
            in_view=bool(raw.get("inView", True)),
            role=raw.get("role"),
            type=raw.get("type"),
            value=raw.get("value"),
            options=tuple(raw["options"]) if raw.get("options") else None,
            checked=raw.get("checked"),
            disabled=bool(raw.get("disabled", False)),
            invalid=raw.get("invalid"),
        )


@dataclass(frozen=True)
class Observation:
    url: str
    title: str
    text: str
    elements: tuple[Element, ...]
    scroll_y: int = 0

    def element(self, element_id: int) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def fingerprint(self) -> str:
        """A hash of everything the agent can perceive.

        If an action leaves this unchanged, the action had no visible effect. That
        one bit, shown to the model as "no change", is how dead buttons get caught.
        """
        state = [self.url, str(self.scroll_y), self.text]
        state += [f"{e.label}|{e.value}|{e.checked}|{e.disabled}|{e.in_view}" for e in self.elements]
        return hashlib.sha1("\n".join(state).encode("utf-8")).hexdigest()


def observe(page: Page, max_elements: int = MAX_ELEMENTS) -> Observation:
    raw = _evaluate_settled(page, _OBSERVE_JS, max_elements)
    return Observation(
        url=raw["url"],
        title=raw["title"],
        text=raw["text"],
        elements=tuple(Element.from_js(e) for e in raw["elements"]),
        scroll_y=raw["scrollY"],
    )


def _evaluate_settled(page: Page, script: str, arg=None):
    """page.evaluate, retried when the page navigates underneath it.

    Found on a real site: a form post answered with a server error, the browser was
    still swapping pages when Nightshift read the page, and the run ended as a
    tester error instead of reporting the 500.
    """
    for attempt in range(3):
        try:
            return page.evaluate(script, arg)
        except PlaywrightError as exc:
            navigating = "context was destroyed" in str(exc) or "navigat" in str(exc)
            if attempt == 2 or not navigating:
                raise
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightError:
                pass


def screenshot(page: Page) -> bytes:
    """A JPEG of the viewport with element ids drawn on. Call right after observe()."""
    _evaluate_settled(page, _MARKS_JS, True)
    try:
        return page.screenshot(type="jpeg", quality=JPEG_QUALITY)
    finally:
        try:
            page.evaluate(_MARKS_JS, False)
        except PlaywrightError:
            pass  # the page navigated away mid-screenshot; the badges went with it
