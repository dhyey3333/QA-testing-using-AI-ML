"""Visual checks: did the page look right, not only say the right things.

Text checks can't see a price printed white on white, two sections drawn on top of each other,
or a broken image: the words are all still there. So when a test passes, its final screen is
compared with the approved one from an earlier pass (the baseline):

  - no baseline yet: this screen becomes it;
  - no real change: the same. Real means 0.05% of the screen, or one small area that changed a
    lot (a price, a button); scattered pixels from anti-aliasing or a blinking caret are not;
  - otherwise the model sees both screens side by side, is told where they differ, and says
    whether a person would call it broken or whether it is just different content (a new order
    number, another product, a rotating banner).

Comparing happens in the test's own browser, on a canvas, so it needs no image library. A
changed screen is never approved by itself: a person accepts it as the new baseline
(`--update-visual`, or "Accept the new look" in the web app), so a slow drift can't creep in.
"""

from __future__ import annotations

import base64
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from .actions import InvalidAction
from .model import ModelError

CHANGED_RATIO = 0.0005  # this share of changed pixels is a real change...
CELL_SHARE = 0.2  # ...and so is one 24 px cell that is this much changed (a price turned invisible: 0.1% overall)
TOLERANCE = 32  # a pixel is changed when a colour channel moves more than this (0-255)

VISUAL_PROMPT = """\
You compare two screenshots of the same web page, taken at the end of the same test on two \
different days, one under the label APPROVED (the approved look) and one under TODAY. The \
message says where on the screen the pixels changed. When the change is in one part of the \
screen, both pictures are zoomed in on that part. Compare them piece by piece: look for text, \
prices, labels, buttons or images that are on one and missing or unreadable on the other.

Decide whether RIGHT shows a visual bug: something a user would see as broken. Bugs: text that \
overlaps other text or is cut off, text you can't read (same colour as its background), a \
missing or broken image, a button, menu or section that is missing or in the wrong place, a \
layout that collapsed or spills off the page, one element covering another.

Not bugs: different data (another product, price, date, name or order number), a rotating \
banner or ad, an extra or missing list item, small shifts that keep the layout intact.

Reply with exactly one JSON object:
{"visual_bug": true or false, "what": "<one sentence: what changed on the page>"}
"""


@dataclass
class VisualCheck:
    status: str  # baseline | same | changed | visual bug | skipped
    changed: float = 0.0  # share of pixels that differ
    what: str = ""  # the model's one-line description, when it looked
    files: dict = field(default_factory=dict)  # baseline / current / diff / sides: file names in the run folder
    where: str = ""  # where on the screen it changed, in words

    def to_json(self) -> dict:
        return asdict(self)


class Baselines:
    """Approved screens, one per spec, browser and screen size, next to the saved paths."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, spec_name: str, browser: str, viewport: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9@._-]+", "-", f"{spec_name}@{browser}-{viewport}")
        return self.root / f"{safe}.png"

    def accept(self, current: Path, spec_name: str, browser: str, viewport: str) -> Path:
        target = self.path(spec_name, browser, viewport)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current, target)
        return target


# Runs in a blank page of the test's browser. Returns how much changed and where, today's screen
# with the changes painted red (for people), both screens side by side with the changed areas boxed
# (for people), and the same pair without boxes (for the model: in testing, it took the red boxes
# for part of the page and called a harmless change a bug).
_COMPARE_JS = r"""
async ({ before, after, tolerance }) => {
  const load = (src) => new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
  const [a, b] = await Promise.all([load(before), load(after)]);
  const w = b.width, h = b.height;
  const read = (img) => {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, w, h);
  };
  const pa = read(a).data, after_ = read(b), pb = after_.data;
  const CELL = 24, cols = Math.ceil(w / CELL), rows = Math.ceil(h / CELL), cells = new Uint32Array(cols * rows);
  let changed = a.width !== w || a.height !== h ? w * h : 0;
  if (!changed) {
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (Math.abs(pa[i] - pb[i]) > tolerance || Math.abs(pa[i + 1] - pb[i + 1]) > tolerance
          || Math.abs(pa[i + 2] - pb[i + 2]) > tolerance) {
        changed++; cells[Math.floor(y / CELL) * cols + Math.floor(x / CELL)]++;
        pb[i] = 255; pb[i + 1] = Math.round(pb[i + 1] * 0.3); pb[i + 2] = Math.round(pb[i + 2] * 0.3);
      }
    }
  }
  let maxCell = 0, x0 = cols, y0 = rows, x1 = -1, y1 = -1;
  const busy = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const share = cells[r * cols + c] / (CELL * CELL);
    maxCell = Math.max(maxCell, share);
    if (share > 0.05) { busy.push([c, r]); x0 = Math.min(x0, c); y0 = Math.min(y0, r); x1 = Math.max(x1, c); y1 = Math.max(y1, r); }
  }
  const diff = document.createElement('canvas'); diff.width = w; diff.height = h;
  diff.getContext('2d').putImageData(after_, 0, 0);

  // Both screens side by side: the whole screen at half size, or, when the change is in one part
  // of it, that part zoomed in. Found in testing: at half size a price printed white on white was
  // too small to see, and the model said nothing had changed.
  const pad = 96, small = x1 >= 0 && (x1 - x0 + 1) * (y1 - y0 + 1) * CELL * CELL < 0.4 * w * h;
  const zx0 = small ? Math.max(0, x0 * CELL - pad) : 0, zy0 = small ? Math.max(0, y0 * CELL - pad) : 0;
  const zx1 = small ? Math.min(w, (x1 + 1) * CELL + pad) : w, zy1 = small ? Math.min(h, (y1 + 1) * CELL + pad) : h;
  // A wide, short strip (a row of prices) goes one above the other, so neither gets shrunk to
  // nothing; anything else goes side by side.
  const pair = (boxes) => {
    const cw = zx1 - zx0, ch = zy1 - zy0, stacked = cw > 2 * ch;
    const s = Math.min(1, (stacked ? 1000 : 640) / cw), gap = 16, top = 28;
    const sw = Math.round(cw * s), sh = Math.round(ch * s);
    const c = document.createElement('canvas');
    c.width = stacked ? sw : sw * 2 + gap; c.height = stacked ? 2 * (sh + top) : sh + top;
    const g = c.getContext('2d'); g.fillStyle = '#555'; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = '#fff'; g.font = 'bold 16px sans-serif';
    const zoom = small ? ' (zoomed in)' : '';
    const [tx, ty] = stacked ? [0, sh + top] : [sw + gap, 0];
    g.fillText('APPROVED' + zoom, 8, 20); g.fillText('TODAY' + zoom, tx + 8, ty + 20);
    g.drawImage(a, zx0, zy0, cw, ch, 0, top, sw, sh); g.drawImage(b, zx0, zy0, cw, ch, tx, ty + top, sw, sh);
    if (boxes) {
      g.strokeStyle = 'rgba(225, 29, 72, 0.9)'; g.lineWidth = 2;
      for (const [cc, rr] of busy) g.strokeRect(tx + (cc * CELL - zx0) * s, ty + top + (rr * CELL - zy0) * s, CELL * s, CELL * s);
    }
    return c.toDataURL('image/jpeg', 0.85);
  };
  const pct = (v, total) => Math.round(100 * v / total);
  const where = x1 < 0 ? 'in many small places' : `from ${pct(x0 * CELL, w)}% to ${pct(Math.min(w, (x1 + 1) * CELL), w)}% across `
    + `and ${pct(y0 * CELL, h)}% to ${pct(Math.min(h, (y1 + 1) * CELL), h)}% down the screen`;
  return { changed: changed / (w * h), maxCell, where, diff: diff.toDataURL('image/png'), marked: pair(true), clean: pair(false) };
}
"""


def compare(context: BrowserContext, before: bytes, after: bytes) -> dict:
    """How two screens differ: changed (share of pixels), max_cell (the most-changed 24 px cell),
    where (in words), and pictures: diff (today, changes in red), marked and clean (side by side)."""
    page = context.new_page()
    try:
        raw = page.evaluate(_COMPARE_JS, {"before": _data_url(before), "after": _data_url(after), "tolerance": TOLERANCE})
    finally:
        page.close()
    return {"changed": float(raw["changed"]), "max_cell": float(raw["maxCell"]), "where": raw["where"],
            "diff": _decode(raw["diff"]), "marked": _decode(raw["marked"]), "clean": _decode(raw["clean"])}


def check_screen(page: Page, context: BrowserContext, model, *, baseline: Path, out_dir: Path,
                 spec_name: str, expectations: tuple[str, ...]) -> VisualCheck:
    """Compare the page as it looks now with its baseline (or make this the baseline)."""
    current = page.screenshot(type="png")
    (out_dir / "visual-current.png").write_bytes(current)
    files = {"current": "visual-current.png"}
    if not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(current)
        return VisualCheck("baseline", files=files)
    before = baseline.read_bytes()
    (out_dir / "visual-baseline.png").write_bytes(before)
    files["baseline"] = "visual-baseline.png"
    found = compare(context, before, current)
    changed = found["changed"]
    if changed < CHANGED_RATIO and found["max_cell"] < CELL_SHARE:
        return VisualCheck("same", changed, files=files)
    (out_dir / "visual-diff.png").write_bytes(found["diff"])
    (out_dir / "visual-sides.jpg").write_bytes(found["marked"])
    files.update(diff="visual-diff.png", sides="visual-sides.jpg")
    text = (f"Test: {spec_name}\nWhat the test checks: " + ("; ".join(expectations) or "(nothing listed)")
            + f"\nChanged pixels: {changed:.1%} of the screen, {found['where']}.")
    try:
        answer = model.look(VISUAL_PROMPT, text, found["clean"])
    except (ModelError, InvalidAction, AttributeError) as exc:
        # No verdict without a look: report the change for a person instead of guessing.
        return VisualCheck("changed", changed, f"the screen changed; the model could not judge it ({type(exc).__name__})",
                           files, found["where"])
    bug = answer.get("visual_bug") is True
    return VisualCheck("visual bug" if bug else "changed", changed, str(answer.get("what") or "")[:300], files,
                       found["where"])


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _decode(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[1])
