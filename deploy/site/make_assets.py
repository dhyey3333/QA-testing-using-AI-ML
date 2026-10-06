"""Make the website's images from the real app: product screenshots, the link-preview image and the icon.

Needs the hosted app running with some projects and runs (fake data only):
    NS_EMAIL=... NS_PASSWORD=... uv run python deploy/site/make_assets.py --app http://localhost:8091
The login comes from the environment so it never lands in the repo or the shell history of a commit.
"""

import argparse
import base64
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[2] / "site" / "assets"
FAVICON = (ASSETS / "favicon.svg")

OG_HTML = """<!doctype html><html><head><style>
  body { margin: 0; width: 1200px; height: 630px; overflow: hidden; font-family: "Segoe UI", system-ui, sans-serif;
         background: radial-gradient(700px 420px at 10% 0%, #2d2d82 0%, transparent 62%),
                     radial-gradient(600px 400px at 100% 100%, #17306a 0%, transparent 60%), #0b0e1f; color: #fff; }
  .text { position: absolute; left: 72px; top: 92px; width: 560px; }
  .logo { display: flex; align-items: center; gap: 14px; font-weight: 750; font-size: 30px; }
  .logo img { width: 52px; height: 52px; }
  h1 { font-size: 58px; line-height: 1.08; letter-spacing: -2px; margin: 46px 0 0; }
  h1 em { font-style: normal; color: #a5a5ff; }
  p { font-size: 25px; color: #b9bfd8; margin: 26px 0 0; line-height: 1.4; }
  .shot { position: absolute; left: 680px; top: 110px; width: 760px; border-radius: 14px; border: 1px solid rgba(255,255,255,.14);
          box-shadow: 0 30px 80px rgba(0,0,0,.6); }
</style></head><body>
  <div class="text"><div class="logo"><img src="data:image/svg+xml;base64,{icon}">Nightshift QA</div>
    <h1>AI regression testing for <em>QA agencies</em></h1>
    <p>Plain-English tests, run every night in a real browser. Every pass proven.</p></div>
  <img class="shot" src="data:image/jpeg;base64,{shot}">
</body></html>"""


def close_up(page, url: str, selector: str, name: str) -> None:
    page.goto(url)
    page.wait_for_selector(selector)
    page.wait_for_timeout(1200)
    page.mouse.move(0, 0)
    box = page.locator(selector).bounding_box()
    pad = 16  # a little of the page around the panel, so its shadow isn't cut off
    clip = {"x": box["x"] - pad, "y": box["y"] - pad, "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad}
    page.screenshot(path=str(ASSETS / name), type="jpeg", quality=82, clip=clip, full_page=True)
    print(f"saved {name} {round(clip['width'])}x{round(clip['height'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", default="http://localhost:8091")
    parser.add_argument("--run", default="visual-demo/run/9", help="the run page for the hero picture")
    parser.add_argument("--runs", default="visual-demo/runs")
    parser.add_argument("--tests", default="academybugs-practice/tests")
    args = parser.parse_args()
    email, password = os.environ.get("NS_EMAIL"), os.environ.get("NS_PASSWORD")
    if not (email and password):
        sys.exit("Set NS_EMAIL and NS_PASSWORD to a login of the app.")
    icon = base64.b64encode(FAVICON.read_bytes()).decode()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("dark", "light"):
            context = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2, color_scheme=scheme)
            page = context.new_page()
            page.goto(f"{args.app}/#/login")
            page.fill("input[name=email]", email)
            page.fill("input[name=password]", password)
            page.click("button[type=submit]")
            page.wait_for_selector("a.client")

            def shoot(hash_: str, name: str) -> None:
                page.goto(f"{args.app}/#/p/{hash_}")
                page.wait_for_timeout(1500)
                page.mouse.move(0, 0)  # no hover effects in the picture
                page.screenshot(path=str(ASSETS / name), type="jpeg", quality=80)
                print("saved", name)

            if scheme == "dark":
                shoot(args.run, "shot-run-dark.jpg")
            else:
                # Close-ups for the "how it works" steps: one panel each, at a size where the text is readable.
                page.set_viewport_size({"width": 1000, "height": 800})
                close_up(page, f"{args.app}/#/p/{args.tests}", ".two", "shot-tests.jpg")
                page.set_viewport_size({"width": 1240, "height": 800})  # the runs table needs room to stay on one line
                close_up(page, f"{args.app}/#/p/{args.runs}", "#tab", "shot-runs.jpg")
                # The client report of the hero's run, as the agency's client sees it.
                slug, _, run_id = args.run.split("/")
                run = context.request.get(f"{args.app}/api/projects/{slug}/runs/{run_id}").json()  # shares the login cookie
                page.set_viewport_size({"width": 860, "height": 620})
                page.goto(args.app + run["run"]["links"]["client_report"])
                page.wait_for_timeout(800)
                page.screenshot(path=str(ASSETS / "shot-report.jpg"), type="jpeg", quality=82)
                print("saved shot-report.jpg 860x620")
            context.close()

        # The picture shown when the site is shared (WhatsApp, LinkedIn, Slack) and the phone icon.
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        shot = base64.b64encode((ASSETS / "shot-run-dark.jpg").read_bytes()).decode()
        page.set_content(OG_HTML.replace("{icon}", icon).replace("{shot}", shot))
        page.wait_for_timeout(300)
        page.screenshot(path=str(ASSETS / "og.png"))
        print("saved og.png")
        page = browser.new_page(viewport={"width": 180, "height": 180})
        page.set_content(f'<body style="margin:0"><img src="data:image/svg+xml;base64,{icon}" width="180" height="180"></body>')
        page.screenshot(path=str(ASSETS / "apple-touch-icon.png"))
        print("saved apple-touch-icon.png")
        browser.close()


if __name__ == "__main__":
    main()
