"""Application validation on a real app, end to end: requirements in, verdicts and defects out.

    uv run python -m realworld.bakerydemo.validate_demo

1. On the untouched bakerydemo: explore the site briefly, design test cases for every
   requirement in requirements.md, run them, write the traceability matrix.
2. Plant three bugs at once in its source (search drops breads, blog tag filter
   ignored, contact form crashes) and validate again with the same test cases. The
   traceability matrix should fail exactly the requirements those bugs break, and
   defect analysis should report three defects, each diffed against the clean run.
"""

from __future__ import annotations

import sys

from nightshift.cli import main as nightshift

from .eval import ROOT, App

PLANTED = ["search-drops-breads", "blog-tag-ignored", "contact-form-500"]
HERE = ROOT / "realworld" / "bakerydemo"


def validate(app: App, bugs: list[str], explore_steps: int) -> int:
    app.prepare(bugs)
    app.start()
    try:
        return nightshift([
            "validate", str(HERE / "requirements.md"), "--url", "http://127.0.0.1:8000/",
            "--data", "username=admin", "--data", "password=changeme",
            "--specs-out", str(HERE / "designed"), "--cases", "2", "--explore-steps", str(explore_steps),
            "--no-record", "--no-replay", "--quiet", "--out", str(ROOT / "runs" / "validate-bakerydemo"),
        ])
    finally:
        app.stop()


def run() -> int:
    app = App((ROOT.parent / "realapps" / "bakerydemo").resolve())
    app.check()
    try:
        print("=" * 30, "1. the untouched site", "=" * 30)
        validate(app, [], explore_steps=15)
        print("\n" + "=" * 30, f"2. with planted bugs: {', '.join(PLANTED)}", "=" * 30)
        validate(app, PLANTED, explore_steps=0)
    finally:
        app.restore()
    return 0


if __name__ == "__main__":
    sys.exit(run())
