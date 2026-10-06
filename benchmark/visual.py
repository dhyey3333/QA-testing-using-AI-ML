"""How well the visual check tells a visual bug from a harmless change.

    uv run python -m benchmark.visual             the page the check was tuned on
    uv run python -m benchmark.visual --holdout   a page it never saw: the honest number

The demo shop's lab page /lab/visual.html draws one product grid seven ways: the approved look
(the baseline), a legitimate content change (other products, a new offer) and five visual bugs
a text check can't see: overlapping cards, a price printed white on white, a missing image, a
collapsed layout, and buttons pushed off the screen. Each look is compared with the baseline and
judged by the model, exactly as in a test run. Correct means: the content change is not called a
bug, and every bug is.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path

from demo_shop.server import make_server
from nightshift.cli import configure_stdout
from nightshift.model import HttpModel, ModelConfig
from nightshift.runner import VIEWPORT, open_browser
from nightshift.visual import check_screen

LOOKS = {"content": False, "overlap": True, "invisible": True, "missing-image": True, "collapsed": True, "offscreen": True}
# A second page in another style, with other bugs, never used while tuning the check: its score is the honest one.
HOLDOUT = {"other-day": False, "cut": True, "ghost-button": True, "stacked-fields": True, "squashed": True}


def main(argv: list[str] | None = None) -> int:
    import sys

    holdout = "--holdout" in (argv if argv is not None else sys.argv[1:])
    looks, page_name = (HOLDOUT, "visual-form.html") if holdout else (LOOKS, "visual.html")
    configure_stdout()
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/lab/{page_name}"
    model = HttpModel(ModelConfig.from_env(), ModelConfig.judge_from_env(ModelConfig.from_env()))
    rows, right = [], 0
    try:
        with open_browser() as browser, tempfile.TemporaryDirectory() as tmp:
            out, baseline = Path(tmp), Path(tmp) / "baseline.png"
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            page.goto(f"{base}?look=approved")
            page.wait_for_timeout(300)
            check_screen(page, context, model, baseline=baseline, out_dir=out, spec_name="visual-lab", expectations=())
            for look, is_bug in looks.items():
                page.goto(f"{base}?look={look}")
                page.wait_for_timeout(300)
                started = time.perf_counter()
                check = check_screen(page, context, model, baseline=baseline, out_dir=out, spec_name="visual-lab",
                                     expectations=("the page shows the delivery form and the order summary",) if holdout
                                     else ("the product grid shows three products with prices and buttons",))
                called_bug = check.status == "visual bug"
                right += called_bug == is_bug
                rows.append(f"| {look} | {'bug' if is_bug else 'no bug'} | {check.status} | {check.changed:.1%} | "
                            f"{time.perf_counter() - started:.1f}s | {'yes' if called_bug == is_bug else '**no**'} | "
                            f"{check.what.replace('|', '/')} |")
                print(rows[-1], flush=True)
            context.close()
    finally:
        model.close()
        server.shutdown()
    print(f"\nmodel: {model.name}   page: {page_name}\ncorrect: {right} / {len(looks)}\n")
    print("| Look | Truth | Verdict | Changed | Time | Correct | What the model said |\n|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
