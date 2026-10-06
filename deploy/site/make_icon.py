"""Make launch/nightshift.ico, the desktop icon, from the website's logo (site/assets/favicon.svg).

    uv run python deploy/site/make_icon.py

The browser draws the logo at each size Windows uses, and the images are packed into one .ico by hand
(an .ico is a small header plus the images), so no image library is needed. Sizes up to 128 px are
stored as classic bitmaps and only 256 px as PNG: older parts of Windows can't read small PNG icons.
"""

import base64
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "site" / "assets" / "favicon.svg"
ICO = ROOT / "launch" / "nightshift.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def pack_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, type 1 = icon, image count
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256 in the format
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def bitmap(size: int, rgba: bytes) -> bytes:
    """An icon image in the classic format: a bitmap header, the pixels bottom-up as BGRA, then a
    1-bit mask (all zeros: the alpha channel already says what's transparent)."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)  # height x2 counts the mask
    rows = [rgba[y * size * 4:(y + 1) * size * 4] for y in range(size)]
    pixels = b"".join(bytes(b for i in range(0, len(row), 4) for b in (row[i + 2], row[i + 1], row[i], row[i + 3]))
                      for row in reversed(rows))
    mask = b"\0" * (((size + 31) // 32) * 4 * size)
    return header + pixels + mask


DRAW = """async ({src, size}) => {
  const img = new Image(); img.src = src; await img.decode();
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0, size, size);
  const data = ctx.getImageData(0, 0, size, size).data; let out = '';
  for (let i = 0; i < data.length; i++) out += String.fromCharCode(data[i]);
  return btoa(out);
}"""


def main() -> None:
    src = "data:image/svg+xml;base64," + base64.b64encode(SVG.read_bytes()).decode()
    images = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for size in SIZES:
            rgba = base64.b64decode(page.evaluate(DRAW, {"src": src, "size": size}))
            if size < 256:
                images.append((size, bitmap(size, rgba)))
            else:
                page.set_viewport_size({"width": size, "height": size})
                page.set_content(f'<body style="margin:0"><img src="{src}" width="{size}" height="{size}"></body>')
                images.append((size, page.screenshot(omit_background=True)))
        browser.close()
    ICO.write_bytes(pack_ico(images))
    print(f"saved {ICO} ({ICO.stat().st_size // 1024} KB, sizes {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
