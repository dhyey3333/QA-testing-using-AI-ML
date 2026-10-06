"""Reading the page: what the agent can see and act on.

Every visible interactive element gets a number (stored in a data-ns-id attribute)
so the model acts by id instead of guessing pixel coordinates, which small models
are bad at. The same numbers are drawn on the screenshot as red badges, so the
text list and the picture agree.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Page

MAX_ELEMENTS = 80
JPEG_QUALITY = 60

# Every open shadow root on the page, so queries reach inside web components. document.querySelectorAll
# stops at a shadow root: on a public demo shop built with web components (Polymer's), Nightshift saw
# no elements and no text at all. Playwright's own locators already pierce open shadow roots.
DEEP_JS = r"""
  const roots = [document];
  for (let i = 0; i < roots.length; i++) {
    for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
  }
  const deepAll = (selector) => roots.flatMap((root) => [...root.querySelectorAll(selector)]);
"""

_OBSERVE_JS = r"""
({ maxElements, firstId }) => {
  /*DEEP*/
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
      const root = el.getRootNode();  // inside a shadow root, the ids are that root's
      const byId = (id) => root.getElementById?.(id) || document.getElementById(id);
      const text = clean(ids.split(/\s+/).map((id) => byId(id)?.innerText || '').join(' '));
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
      // A link around a web component (a product tile) has its text in the component's shadow root.
      const text = clean(el.innerText) || (roots.length > 1 ? clean(renderedText(el)) : '');
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
    // Content inside a closed <details> (an accordion, "More options") still has a size in Chrome,
    // because it is hidden with content-visibility. checkVisibility() knows. Found by running
    // Nightshift on its own web app: fields in a collapsed section were listed as clickable.
    if (el.checkVisibility && !el.checkVisibility()) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0;
  };

  // innerText leaves out everything inside shadow roots. On pages that have them, the text is read
  // from the rendered tree instead: shadow content in place of its host's children, slotted
  // children where their <slot> is, a line break around block boxes, a tab between table cells.
  const BLOCK = /^(block|flex|grid|list-item|table|table-row|table-caption|flow-root)/;
  const renderedText = (body) => {
    const out = [];
    const walk = (node) => {
      if (node.nodeType === Node.TEXT_NODE) { out.push(node.data.replace(/\s+/g, ' ')); return; }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT'].includes(node.tagName)) return;
      if (node.tagName === 'SLOT') {
        const assigned = node.assignedNodes({ flatten: true });
        (assigned.length ? assigned : [...node.childNodes]).forEach(walk);
        return;
      }
      const s = getComputedStyle(node);
      if (s.display === 'none' || s.visibility === 'hidden') return;
      const block = BLOCK.test(s.display);
      if (block || node.tagName === 'BR') out.push('\n');
      [...(node.shadowRoot || node).childNodes].forEach(walk);
      if (block) out.push('\n');
      else if (s.display === 'table-cell') out.push('\t');
    };
    if (body) walk(body);
    return out.join('');
  };

  deepAll('[data-ns-id]').forEach((el) => el.removeAttribute('data-ns-id'));
  // `el` is what is described to the model; `target` is what gets the number and the click.
  // They differ only for a styled checkbox, whose label stands in for its hidden input.
  const found = [];
  const taken = new Set();
  const add = (el, target = el, extra = null) => {
    if (taken.has(target)) return;
    taken.add(target);
    const r = target.getBoundingClientRect();
    found.push({ el, target, extra, inView: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth });
  };
  const toggle = (el) => el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio');
  for (const el of deepAll(SELECTOR)) {
    const r = el.getBoundingClientRect();
    if (isVisible(el) && !(toggle(el) && (r.width < 4 || r.height < 4))) {
      add(el);
    } else if (toggle(el) && !el.disabled) {
      // A styled checkbox or radio: the real input is hidden (opacity 0, 1px, moved away) and people
      // click its label. Found on a public demo site: "I have read and agree to the Privacy Policy"
      // was missing from the list, the agent could never tick it, and a working sign-up was
      // reported broken in every benchmark run, on every model.
      const label = [...(el.labels || [])].find(isVisible);
      if (label) add(el, label);
    }
  }

  // A popup's way out is often a plain <span>, <div> or <p> with a click handler, which the selector
  // above can't see. Found on a practice site: an entry popup whose only close control was <p>Close</p>.
  const CLOSE = /^(×|✕|✖|╳|x|close|close ad|dismiss|no,? thanks|not now|maybe later|skip|got it)$/i;
  const CLOSE_HINT = '[class*="close" i], [class*="dismiss" i], [aria-label*="close" i], [title*="close" i], '
    + '[data-dismiss], [data-bs-dismiss]';
  const inside = (el) => [...taken].some((t) => t.contains(el) || el.contains(t));
  const closers = [...deepAll(CLOSE_HINT), ...deepAll('span, div, p, i, b, strong, em, svg')
    .filter((el) => el.childElementCount <= 1 && CLOSE.test(clean(el.textContent)))];
  for (const el of closers) {
    if (taken.has(el) || !isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    const text = clean(el.textContent);
    // A close icon is small; a bigger element only counts when it says "Close", "No thanks" and so on.
    if (!(CLOSE.test(text) ? r.width <= 300 && r.height <= 80 : r.width <= 64 && r.height <= 64)) continue;
    if (inside(el)) continue;
    const named = clean(el.getAttribute('aria-label') || el.getAttribute('title'));
    const label = named || (text.length > 1 ? text : '') || 'Close';
    add(el, el, { label: /^close/i.test(label) ? label : `${label} (closes it)`, role: 'button' });
  }

  // One-time-code boxes: four to eight one-character inputs side by side. Nightshift types the whole
  // code into the first one and spreads it over the boxes (actions.py), so that is what the model is told.
  const narrow = (i) => i.tagName === 'INPUT' && ['text', 'tel', 'number', 'password'].includes(i.type)
    && isVisible(i) && i.maxLength === 1;
  const codeBoxes = (el) => {
    if (!narrow(el)) return [];
    for (let node = el.parentElement, depth = 0; node && depth < 3; node = node.parentElement, depth++) {
      const boxes = [...node.querySelectorAll('input')].filter(narrow);
      if (boxes.length >= 4 && boxes.length <= 8) return boxes;
    }
    return [];
  };
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
  const labels = found.map(({ el, extra }) => extra?.label || labelOf(el));
  const counts = {};
  labels.forEach((l) => { counts[l.toLowerCase()] = (counts[l.toLowerCase()] || 0) + 1; });

  const elements = found.slice(0, maxElements).map(({ el, target, extra, inView }, i) => {
    const id = firstId + i;  // an iframe's elements are numbered after the page's
    target.setAttribute('data-ns-id', String(id));
    const tag = el.tagName.toLowerCase();
    let label = labels[i];
    const field = ['select', 'textarea'].includes(tag)
      || (tag === 'input' && !['submit', 'button', 'reset', 'image'].includes(el.type));
    if (label && counts[label.toLowerCase()] > 1 && !field) {
      const name = itemName(el, label.toLowerCase());
      if (name) label = `${label} — ${name}`;
    }
    const boxes = codeBoxes(el);
    if (boxes.length) {
      const k = boxes.indexOf(el) + 1;
      label = `${label || 'code'}, box ${k} of ${boxes.length}` + (k === 1 ? ' (type the whole code here)' : '');
    }
    const item = { id, tag, label: label.slice(0, 120), inView };
    const role = extra?.role || el.getAttribute('role');
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

  let text = (roots.length > 1 ? renderedText(document.body) : (document.body?.innerText || ''))
    .split('\n').map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
  // The browser's own form validation ("Please include an '@'...") is a tooltip outside the
  // page, so innerText never has it. Add it as text the agent can read and the judge can quote.
  for (const el of deepAll('input, select, textarea')) {
    if (complaint(el)) text += `\n[browser says] ${labelOf(el) || el.name || 'field'}: ${el.validationMessage}`;
  }
  // What a form field holds isn't page text either, so "the quantity shows 2" could never be quoted.
  // Found on a practice shop: the judge saw the 2 and still had to fail the test. Visible fields with
  // a value become lines it can quote. Passwords never do; typed test data is masked like any text.
  const NO_VALUE = ['hidden', 'password', 'checkbox', 'radio', 'submit', 'button', 'reset', 'image', 'file'];
  const fields = [];
  for (const el of deepAll('input, select, textarea')) {
    if (NO_VALUE.includes(el.type) || !isVisible(el)) continue;
    const value = el.tagName === 'SELECT' ? clean(el.selectedOptions[0]?.text) : clean(el.value);
    if (value) fields.push(`[field] ${labelOf(el) || el.name || 'field'}: ${value.slice(0, 120)}`);
  }
  const shown = fields.length ? '\n' + fields.slice(0, 40).join('\n') : '';
  text = text.slice(0, 20000 - shown.length) + shown;  // the page's text gives way, not the fields
  return { url: location.href, title: document.title, text, scrollY: Math.round(scrollY), elements };
}
""".replace("/*DEEP*/", DEEP_JS)

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
  /*DEEP*/
  const layer = document.createElement('div');
  layer.id = '__ns_marks';
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  for (const el of deepAll('[data-ns-id]')) {
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
""".replace("/*DEEP*/", DEEP_JS)


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
    raw = _evaluate_settled(page, _OBSERVE_JS, {"maxElements": max_elements, "firstId": 1})
    elements = [Element.from_js(e) for e in raw["elements"]]
    text = raw["text"]
    owners: dict[int, Frame] = {}
    observed: list[Frame] = [page.main_frame]
    # Payment widgets (Razorpay, Stripe Elements), embedded forms and many sign-in boxes live in
    # iframes, often on another origin. Each visible one is read like the page, its elements
    # numbered after the page's, and its text added under a heading so the judge can quote it.
    for frame in content_frames(page):
        try:
            sub = frame.evaluate(_OBSERVE_JS, {"maxElements": max(0, max_elements - len(elements)),
                                               "firstId": len(elements) + 1})
        except PlaywrightError:
            continue  # the frame navigated or went away while being read
        observed.append(frame)
        for item in sub["elements"]:
            elements.append(Element.from_js(item))
            owners[int(item["id"])] = frame
        if sub["text"]:
            text += f"\n[inside a frame: {_frame_name(frame)}]\n{sub['text']}"
    _FRAMES[page] = (owners, observed)
    return Observation(
        url=raw["url"],
        title=raw["title"],
        text=text,
        elements=tuple(elements),
        scroll_y=raw["scrollY"],
    )


# Frames that are ads or trackers, never part of the app under test.
_AD_HOSTS = ("doubleclick.net", "googlesyndication.com", "googleadservices.com", "adservice.google",
             "amazon-adsystem.com", "adnxs.com", "taboola.com", "outbrain.com", "criteo.", "pubmatic.com",
             "rubiconproject.com", "openx.net", "media.net", "googletagmanager.com", "facebook.com/tr")
MIN_FRAME_PX = 40  # smaller frames are trackers, pixels and hidden helpers

# Ad networks' own servers. Tag managers and analytics are left alone: some sites need them to work.
AD_REQUEST_RE = re.compile(
    r"^https?://([^/]*\.)?(doubleclick\.net|googlesyndication\.com|googleadservices\.com|adservice\.google\.[a-z.]+|"
    r"amazon-adsystem\.com|adnxs\.com|taboola\.com|outbrain\.com|criteo\.(com|net)|pubmatic\.com|"
    r"rubiconproject\.com|openx\.net|media\.net)(:\d+)?(/|$)")


def block_ads(context) -> None:
    """Ads never load in a test's browser.

    Found on a public demo shop: a full-page Google ad covered the page, its own close button sat in
    the ad's cross-origin frame (which is never read, see _AD_HOSTS), and a working add-to-cart was
    reported broken. It came and went between runs, so it made the result random as well. Ads are
    not the app under test; --allow-ads turns this off for a site whose ads are the product.
    """
    context.route(AD_REQUEST_RE, lambda route: route.abort())

# For each page: which frame owns each element id from the last observe(), and the frames read then.
_FRAMES: WeakKeyDictionary = WeakKeyDictionary()


def content_frames(page: Page) -> list[Frame]:
    """The page's visible iframes that could hold part of the app, outermost first."""
    frames = []
    for frame in page.frames:
        if frame is page.main_frame or frame.is_detached():
            continue
        url = frame.url or ""
        if not url or url == "about:blank" or any(host in url for host in _AD_HOSTS):
            continue
        try:
            box = frame.frame_element().bounding_box()
        except PlaywrightError:
            continue
        if box and box["width"] >= MIN_FRAME_PX and box["height"] >= MIN_FRAME_PX:
            frames.append(frame)
    return frames


def frame_of(page: Page, element_id: int) -> Frame:
    """The frame the last observe() found element `element_id` in (the page itself, usually)."""
    owners, _ = _FRAMES.get(page, ({}, []))
    return owners.get(element_id, page.main_frame)


def _frame_name(frame: Frame) -> str:
    parts = urlsplit(frame.url)
    return frame.name or f"{parts.netloc}{parts.path}"[:80]


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
    frames = _FRAMES.get(page, ({}, [page.main_frame]))[1][1:]
    for frame in frames:  # badges inside iframes are drawn by the iframe's own document
        with suppress(PlaywrightError):
            frame.evaluate(_MARKS_JS, True)
    try:
        return page.screenshot(type="jpeg", quality=JPEG_QUALITY)
    finally:
        for frame in [page.main_frame, *frames]:
            with suppress(PlaywrightError):
                frame.evaluate(_MARKS_JS, False)  # gone already if the page navigated mid-screenshot
