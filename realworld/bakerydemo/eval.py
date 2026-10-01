"""Nightshift on a real app: Wagtail's bakerydemo, with bugs planted in its real source code.

    uv run python -m realworld.bakerydemo.eval                 clean run of every spec, then each bug
    uv run python -m realworld.bakerydemo.eval --clean-only
    uv run python -m realworld.bakerydemo.eval --only js-crash,blog-tag-ignored --skip-clean

Needs a bakerydemo checkout (default: ../realapps/bakerydemo next to this repo, or
BAKERYDEMO_DIR), set up as its README says: a Python 3.12 virtualenv in .venv,
`manage.py migrate`, `manage.py load_initial_data`.

Every run starts from the same place: the checkout reset to pristine, one bug's
patch applied (bugs/*.patch), the database restored to its freshly loaded copy,
and a fresh dev server. The specs in specs/ were written from browsing the site,
before Nightshift ever ran against it, and nothing is tuned to this app.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from benchmark.__main__ import CLEAN, Row, render
from nightshift.cli import configure_stdout
from nightshift.model import HttpModel, ModelConfig
from nightshift.runner import RunOptions, open_browser, run_with_retries
from nightshift.spec import load_specs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PORT = 8000
DB_NAME = "bakerydemodb"


@dataclass(frozen=True)
class Bug:
    description: str
    specs: tuple[str, ...]


BUGS: dict[str, Bug] = {
    "search-drops-breads": Bug("A refactor drops bread pages from search results", ("search",)),
    "blog-tag-ignored": Bug("The tag filter's result is never assigned, so every post shows", ("blog-tag",)),
    "bread-origin-swapped": Bug("The Origin row prints the bread type (copy-paste slip)", ("browse-breads",)),
    "ingredients-missing-last": Bug("The ingredients list is capped at three, hiding the fourth", ("browse-breads",)),
    "no-results-message-missing": Bug('The "No results found" message was deleted', ("search-no-results",)),
    "js-crash": Bug("main.js reads a menu element that no longer exists and throws on every page", ("browse-breads",)),
    "heading-from-slug": Bug("Page headings come from the URL slug, so a renamed page keeps its old heading",
                             ("admin-edit-bread",)),
    "contact-form-500": Bug("A new submission hook reads the wrong field key: every contact form post is a 500",
                            ("contact-form",)),
}


class App:
    """The bakerydemo checkout: reset it, plant a bug, serve it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.python = path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.db = path / DB_NAME
        self.pristine_db = path / f"{DB_NAME}.pristine"
        self.server: subprocess.Popen | None = None
        self.log = path / "nightshift-server.log"

    def check(self) -> None:
        if not self.python.exists() or not self.db.exists():
            raise SystemExit(f"{self.path}: set up bakerydemo first (venv in .venv, migrate, load_initial_data)")
        changed = self._git("status", "--porcelain", "--untracked-files=no").strip()
        if changed:
            # Resetting would throw these edits away; refuse instead of guessing.
            raise SystemExit(f"{self.path} has uncommitted changes to tracked files:\n{changed}")
        if not self.pristine_db.exists():
            shutil.copy2(self.db, self.pristine_db)  # the freshly loaded data every run starts from

    def prepare(self, bugs: str | list[str] | None) -> None:
        """Pristine code plus the given bug(s), and the freshly loaded database."""
        self.stop()
        self._git("checkout", "--", ".")
        for bug in [bugs] if isinstance(bugs, str) else bugs or []:
            self._git("apply", str(HERE / "bugs" / f"{bug}.patch"))
        shutil.copy2(self.pristine_db, self.db)

    def start(self) -> None:
        # Something already answering on the port would be tested instead of this checkout,
        # with whatever bugs it has. Happened once: a run that looked dead was still going.
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError(f"port {PORT} is already in use: is another evaluation still running?")
        with self.log.open("w", encoding="utf-8") as log:
            self.server = subprocess.Popen([str(self.python), "manage.py", "runserver", "--noreload", f"127.0.0.1:{PORT}"],
                                           cwd=self.path, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
                return
            except OSError:
                if self.server.poll() is not None:
                    break
                time.sleep(0.5)
        raise RuntimeError(f"the dev server did not start; see {self.log}")

    def stop(self) -> None:
        if self.server and self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=15)
        self.server = None

    def restore(self) -> None:
        self.stop()
        self._git("checkout", "--", ".")
        shutil.copy2(self.pristine_db, self.db)

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.path, check=True, capture_output=True, text=True,
                              encoding="utf-8").stdout


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(prog="python -m realworld.bakerydemo.eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", type=Path,
                        default=Path(os.getenv("BAKERYDEMO_DIR") or ROOT.parent / "realapps" / "bakerydemo"))
    parser.add_argument("--only", default="", help="comma-separated bug names (default: every bug)")
    parser.add_argument("--clean-only", action="store_true")
    parser.add_argument("--skip-clean", action="store_true")
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)

    app = App(args.app.resolve())
    app.check()
    specs = {spec.name: spec for spec in load_specs([HERE / "specs"])}
    bugs = [] if args.clean_only else [b.strip() for b in args.only.split(",") if b.strip()] or list(BUGS)
    unknown = [b for b in bugs if b not in BUGS]
    if unknown:
        parser.error(f"unknown bug(s): {', '.join(unknown)}")
    plan = ([] if args.skip_clean else [(CLEAN, list(specs))]) + [(bug, list(BUGS[bug].specs)) for bug in bugs]
    total = sum(len(names) for _, names in plan)

    agent = ModelConfig.from_env()
    model = HttpModel(agent, ModelConfig.judge_from_env(agent))
    options = RunOptions(send_screenshot=not args.no_vision, retries=args.retries)
    out = args.out / f"real-bakerydemo-{datetime.now():%Y%m%d-%H%M%S}"
    print(f"app: bakerydemo at {app.path}   model: {model.name}   runs: {total}\nresults: {out}\n")

    rows: list[Row] = []
    try:
        with open_browser(headed=args.headed) as browser:
            for config, names in plan:
                for name in names:
                    app.prepare(None if config == CLEAN else config)
                    app.start()
                    print(f"[{len(rows) + 1}/{total}] {config} / {name} ... ", end="", flush=True)
                    result = run_with_retries(browser, specs[name], model, out_dir=out / config / name, options=options)
                    print(f"{result.verdict.upper()} ({len(result.steps)} steps, {result.duration_s:.0f}s): {result.reason}")
                    rows.append(Row(config, result))
    finally:
        app.restore()
        model.close()

    report = render(rows, BUGS, "bakerydemo (Wagtail, real app)", model.name, options)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
