"""Running projects' tests in the background: a queue, a few workers, and the nightly schedule.

Each run is a `nightshift run` subprocess over the project's specs, with its saved paths
(so a nightly run of a stable suite replays with no model calls), its secrets as environment
variables, `--parallel` specs at a time, and `--client`/`--brand` so every run ends with the
client report. Up to `max_runs` runs go at once, one per project at most.

Sizing for the Oracle free server (2 OCPU, 12 GB): two runs of two browsers each. A browser
takes 300 to 500 MB; the model runs elsewhere (Ollama Cloud).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from .files import ProjectFiles
from .store import Store, StoreError

SCHEDULE_EVERY_S = 30


class Busy(StoreError):
    """The project already has a run queued or running."""


class Runner:
    def __init__(self, store: Store, data: Path, *, max_runs: int = 2, parallel: int = 2, command=None,
                 explore_command=None) -> None:
        # Absolute: each run starts inside its project's folder, where a relative --data path
        # (`--data hosted-data`) would point nowhere. Found on the first real use.
        self.store, self.data = store, data.resolve()
        self.max_runs, self.parallel = max(1, max_runs), max(1, parallel)
        self.command = command or self._command
        self.explore_command = explore_command or self._explore_command
        self.generate_command = self._generate_command
        self._wake = threading.Condition()
        self._procs: dict[int, subprocess.Popen] = {}
        self._closing = False
        self._enqueue_lock = threading.Lock()

    def files(self, project: dict) -> ProjectFiles:
        return ProjectFiles(self.data, project["slug"])

    def start(self) -> None:
        self.store.interrupted()
        for n in range(self.max_runs):
            threading.Thread(target=self._work, name=f"run-worker-{n}", daemon=True).start()
        threading.Thread(target=self._schedule, name="nightly", daemon=True).start()

    def close(self) -> None:
        self._closing = True
        with self._wake:
            self._wake.notify_all()
        for run_id in list(self._procs):
            self.stop(run_id)

    def enqueue(self, project: dict, trigger: str, user_id: int | None = None, params: dict | None = None) -> int:
        # One check-and-add at a time: a double-click on Run now (two requests at once) must not
        # slip two runs of one project past the "already running" check.
        with self._enqueue_lock:
            return self._enqueue(project, trigger, user_id, params)

    def _enqueue(self, project: dict, trigger: str, user_id: int | None, params: dict | None) -> int:
        if self.store.active_run(project["id"]):
            raise Busy("a run of this project is already queued or running")
        files = self.files(project)
        if trigger in ("manual", "nightly"):
            if not files.spec_names():
                raise StoreError("this project has no tests yet")
            if missing := files.missing_secrets():
                # `nightshift run` stops on the first ${SECRET} it can't fill, so say which before starting.
                listed = ", ".join(f"{test} needs {secret}" for test, secret in missing[:5])
                raise StoreError(f"set these secrets under Settings first: {listed}")
        run_id = self.store.add_run(project["id"], trigger, user_id, params)
        with self._wake:
            self._wake.notify()
        return run_id

    def stop(self, run_id: int) -> None:
        run = self.store.run(run_id)
        if run is None or run["status"] not in ("queued", "running"):
            return
        self.store.update_run(run_id, status="stopped", finished=time.time(), message="stopped by a user")
        if (process := self._procs.get(run_id)) is not None:
            _kill_tree(process)

    # --- the nightly schedule ---------------------------------------------------------

    def tick(self, now: datetime) -> list[int]:
        """Queue each project's nightly run once a day, at or after its time (in the server's time zone)."""
        day, clock = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        queued = []
        for project in self.store.projects():
            if not project["nightly"] or clock < project["nightly"] or project["last_nightly"] == day:
                continue
            self.store.mark_nightly(project["id"], day)
            try:
                queued.append(self.enqueue(project, "nightly"))
            except StoreError as exc:  # still running from earlier, or no tests yet: skip tonight
                print(f"nightly run of {project['slug']} skipped: {exc}", file=sys.stderr, flush=True)
        return queued

    def _schedule(self) -> None:
        while not self._closing:
            try:
                self.tick(datetime.now())
            except Exception:  # noqa: BLE001 (the scheduler must outlive one bad tick)
                traceback.print_exc()
            time.sleep(SCHEDULE_EVERY_S)

    # --- running -----------------------------------------------------------------

    def _work(self) -> None:
        while not self._closing:
            run = self.store.next_queued()
            if run is None:
                with self._wake:
                    self._wake.wait(timeout=5)
                continue
            try:
                self.execute(run)
            except Exception as exc:  # noqa: BLE001 (record it and keep the worker alive)
                traceback.print_exc()
                self.store.update_run(run["id"], status="failed", finished=time.time(),
                                      message=f"the runner failed: {type(exc).__name__}")

    def execute(self, run: dict) -> None:
        project = self.store.project_by_id(run["project_id"])
        files = self.files(project)
        out = files.run_folder(run["id"])
        out.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **files.secrets(), "PYTHONIOENCODING": "utf-8"}
        exploring, generating = run["trigger"] == "explore", run["trigger"] == "generate"
        params = json.loads(run["params"] or "{}")
        drafts_before = {path.name for path in files.drafts.glob("*.yaml")}
        if exploring:
            command = self.explore_command(params, out)
        elif generating:
            command = self.generate_command(params, files, out)
        else:
            command = self.command(project, files, out)
        with open(out / "output.log", "w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, cwd=files.root, start_new_session=os.name != "nt")
            self._procs[run["id"]] = process
            try:
                process.wait()
            finally:
                self._procs.pop(run["id"], None)

        if self.store.run(run["id"])["status"] == "stopped":
            return
        if exploring:
            self._finish_exploration(run, out, process.returncode)
            return
        if generating:
            written = len({path.name for path in files.drafts.glob("*.yaml")} - drafts_before)
            explored = sorted(out.glob("explore-*/report.html"))
            if process.returncode == 0 and written:
                self.store.update_run(run["id"], status="done", finished=time.time(),
                                      run_dir=explored[-1].parent.relative_to(out).as_posix() if explored else "",
                                      message=f"wrote {_n(written, 'draft test')}: review them under Tests")
            else:
                tail = (out / "output.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
                self.store.update_run(run["id"], status="failed", finished=time.time(),
                                      message=" / ".join(tail)[-500:] or "no tests were written")
            return
        folder = _run_folder(out)
        if folder is None:
            tail = (out / "output.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            self.store.update_run(run["id"], status="failed", finished=time.time(),
                                  message=" / ".join(tail)[-500:] or f"nightshift run exited with {process.returncode}")
            return
        verdicts = [row.get("verdict") for row in json.loads((folder / "summary.json").read_text(encoding="utf-8"))]
        self.store.update_run(run["id"], status="done", finished=time.time(), run_dir=folder.relative_to(out).as_posix(),
                              passed=verdicts.count("pass"), failed=verdicts.count("fail"),
                              flaky=verdicts.count("flaky"), errors=verdicts.count("error"))

    def _finish_exploration(self, run: dict, out: Path, code: int) -> None:
        found = sorted(out.glob("explore-*/explore.json"))
        if not found:
            tail = (out / "output.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            self.store.update_run(run["id"], status="failed", finished=time.time(),
                                  message=" / ".join(tail)[-500:] or f"nightshift explore exited with {code}")
            return
        findings = json.loads(found[-1].read_text(encoding="utf-8")).get("findings") or []
        counts = {severity: sum(f.get("severity") == severity for f in findings) for severity in ("bug", "suspected", "warning")}
        self.store.update_run(run["id"], status="done", finished=time.time(),
                              run_dir=found[-1].parent.relative_to(out).as_posix(),
                              message=f"{_n(counts['bug'], 'bug')} proven, {counts['suspected']} suspected, "
                                      f"{_n(counts['warning'], 'warning')}")

    def _explore_command(self, params: dict, out: Path) -> list[str]:
        command = [sys.executable, "-m", "nightshift", "explore", str(params["url"]), "--steps", str(params["steps"]),
                   "--out", str(out), "--quiet"]
        if params.get("focus"):
            command += ["--focus", str(params["focus"])]
        return command

    def _generate_command(self, params: dict, files: ProjectFiles, out: Path) -> list[str]:
        """Explore the site to learn it, then draft tests into the project's drafts folder."""
        command = [sys.executable, "-m", "nightshift", "generate", "--url", str(params["url"]),
                   "--explore-steps", str(params["steps"]), "--count", str(params["count"]),
                   "--specs-out", str(files.drafts), "--out", str(out), "--quiet"]
        if params.get("about"):
            command += ["--story", str(params["about"])]
        return command

    def _command(self, project: dict, files: ProjectFiles, out: Path) -> list[str]:
        command = [sys.executable, "-m", "nightshift", "run", str(files.specs), "--out", str(out),
                   "--recordings", str(files.recordings), "--parallel", str(self.parallel), "--quiet",
                   "--client", project["client"]]
        if project["brand"]:
            command += ["--brand", project["brand"]]
        if project["base_url"]:
            command += ["--base-url", project["base_url"]]
        return command


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _run_folder(out: Path) -> Path | None:
    """The folder `nightshift run` wrote (it names it by the time), once it has a summary."""
    found = sorted(path.parent for path in out.glob("*/summary.json"))
    return found[-1] if found else None


def _kill_tree(process: subprocess.Popen) -> None:
    """The run and the browsers it started."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
