"""`nightshift serve`: a local dashboard over everything the command line does.

It listens on 127.0.0.1 only, and it can start browsers that click through
websites, so two rules guard it:
  - every request must name this machine in its Host header, which stops a web
    page from reaching it through a DNS name it controls (DNS rebinding);
  - every request that changes something must carry a token that is only in the
    dashboard page itself, so no other site open in the browser can start a run.

Jobs (a test run, a validation, an exploration) run one at a time, because one GPU
runs one model at a time, each as a `nightshift` subprocess. The page polls for
progress: the log, the current test, and the screenshot the agent last saw.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from ..generate import slugify
from ..model import ModelConfig
from ..spec import SpecError, load_spec

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765
DEMO_PORT = 5180
MAX_BODY = 2_000_000
MAX_LOG_LINES = 5_000
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".nightshift"}

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".jpg": "image/jpeg",
                 ".png": "image/png", ".webm": "video/webm", ".zip": "application/zip",
                 ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                 ".xml": "application/xml"}


class Busy(Exception):
    """A job is already running."""


class BadRequest(Exception):
    """The request names something it may not, or is malformed."""


@dataclass
class Job:
    id: str
    kind: str
    title: str
    command: list[str]
    started: float
    status: str = "running"  # running | finished | stopped
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)  # report / defects / traceability / findings, as paths
    process: subprocess.Popen | None = None
    finished: float | None = None


class Dashboard:
    def __init__(self, workspace: Path, spec_dirs: list[Path], runs: Path, command_for=None) -> None:
        self.workspace = workspace.resolve()
        self.spec_dirs = [self._inside(d if d.is_absolute() else self.workspace / d) for d in spec_dirs]
        self.runs = self._inside(runs if runs.is_absolute() else self.workspace / runs)
        self.token = secrets.token_urlsafe(24)
        self.job: Job | None = None
        self.lock = threading.Lock()
        self.demo: subprocess.Popen | None = None
        self.demo_bugs: list[str] = []
        self.command_for = command_for or self._command_for
        self._model_status: tuple[float, dict] | None = None

    # --- paths ------------------------------------------------------------------

    def _inside(self, path: Path) -> Path:
        """Resolve a path and refuse anything outside the workspace."""
        resolved = path.resolve()
        if resolved != self.workspace and not resolved.is_relative_to(self.workspace):
            raise BadRequest(f"{path} is outside the workspace")
        return resolved

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.workspace).as_posix()

    def _spec_path(self, value: str) -> Path:
        path = self._inside(self.workspace / value)
        if path.suffix not in (".yaml", ".yml") or not any(path.is_relative_to(d) for d in self.spec_dirs):
            raise BadRequest("specs live in the spec folders and end in .yaml")
        return path

    # --- state ------------------------------------------------------------------

    def state(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "spec_dirs": [self.rel(d) for d in self.spec_dirs],
            "folders": self.folders(),
            "model": self.model_status(),
            "job": self.job_view(0),
            "demo": self.demo_view(),
            "jira": self.jira_view(),
        }

    def jira_view(self) -> dict:
        from ..jira import JiraConfig

        config = JiraConfig.from_env()
        return {"configured": config is not None, "url": config.url if config else "",
                "project": config.project if config else ""}

    def model_status(self) -> dict:
        """Is the model endpoint answering? Cached for 15 s: the page asks every few seconds."""
        if self._model_status and time.monotonic() - self._model_status[0] < 15:
            return self._model_status[1]
        config = ModelConfig.from_env()
        status = {"name": config.model, "base_url": config.base_url, "ok": False}
        try:
            request = urllib.request.Request(f"{config.base_url}/models")
            if config.api_key:
                request.add_header("Authorization", f"Bearer {config.api_key}")
            with urllib.request.urlopen(request, timeout=2) as response:
                status["ok"] = response.status < 400
        except Exception:  # noqa: BLE001 (any failure means "not reachable")
            pass
        self._model_status = (time.monotonic(), status)
        return status

    def folders(self) -> list[str]:
        found = []
        for root in self.spec_dirs:
            if not root.exists():
                found.append(self.rel(root))
                continue
            for folder in [root, *sorted(p for p in root.rglob("*") if p.is_dir() and not SKIP_DIRS & set(p.parts))]:
                if folder == root or any(folder.glob("*.y*ml")):
                    found.append(self.rel(folder))
        return list(dict.fromkeys(found))

    # --- specs ------------------------------------------------------------------

    def list_specs(self, folder: str = "") -> list[dict]:
        roots = [self._inside(self.workspace / folder)] if folder else self.spec_dirs
        last = self.last_results()
        items = []
        for root in roots:
            if not root.exists():
                continue
            files = sorted(root.glob("*.y*ml")) if folder else sorted(root.rglob("*.y*ml"))
            for path in files:
                items.append(self.describe_spec(path, last))
        return items

    def describe_spec(self, path: Path, last: dict | None = None) -> dict:
        text = path.read_text(encoding="utf-8")
        item = {"path": self.rel(path), "folder": self.rel(path.parent), "name": path.stem, "problem": "",
                "review": [line.removeprefix("# Review:").strip() for line in text.splitlines()
                           if line.startswith("# Review:")]}
        try:
            spec = load_spec(path, expand_env=False)
        except SpecError as exc:
            item["problem"] = str(exc).replace(str(path), path.name)
            return item
        needs = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text)) - set(os.environ))
        item.update(name=spec.name, title=spec.title, url=spec.url, steps=list(spec.steps), expect=list(spec.expect),
                    data=sorted(spec.data), requirements=list(spec.requirements), technique=spec.technique,
                    priority=spec.priority, needs_env=needs)
        result = (last or {}).get(spec.name)
        if result:
            item["last"] = result
        return item

    def read_spec(self, value: str) -> dict:
        path = self._spec_path(value)
        return {"path": self.rel(path), "text": path.read_text(encoding="utf-8")}

    def save_spec(self, value: str, text: str, create: bool = False) -> dict:
        path = self._spec_path(value)
        if create and path.exists():
            raise BadRequest(f"{self.rel(path)} already exists")
        check = path.with_name(f".{path.stem}.check.yaml")
        check.write_text(text, encoding="utf-8")
        try:
            load_spec(check, expand_env=False)
        except SpecError as exc:
            raise BadRequest(str(exc).replace(str(check), path.name)) from None
        finally:
            check.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return self.describe_spec(path)

    def new_spec(self, form: dict) -> dict:
        folder = self._inside(self.workspace / str(form.get("folder") or self.rel(self.spec_dirs[0])))
        name = slugify(str(form.get("name") or form.get("title") or "new-test"))
        lines = lambda key: [s.strip() for s in str(form.get(key) or "").splitlines() if s.strip()]  # noqa: E731
        doc: dict = {"name": name}
        if form.get("title"):
            doc["title"] = str(form["title"]).strip()
        if lines("requirements"):
            doc["requirements"] = [r for line in lines("requirements") for r in re.split(r"[,\s]+", line) if r]
        for key in ("technique", "priority"):
            if form.get(key):
                doc[key] = str(form[key])
        doc.update({"url": str(form.get("url") or "").strip(), "steps": lines("steps"), "expect": lines("expect")})
        data = {}
        for line in lines("data"):
            key, sep, value = line.partition("=")
            if not sep:
                raise BadRequest(f"test data takes name=value, got {line!r}")
            data[key.strip()] = value.strip()
        if data:
            doc["data"] = data
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
        return self.save_spec(self.rel(folder / f"{name}.yaml"), text, create=True)

    def delete_spec(self, value: str) -> None:
        self._spec_path(value).unlink()

    # --- runs ---------------------------------------------------------------------

    def last_results(self) -> dict[str, dict]:
        """Each spec's newest verdict, from the runs' summary.json files."""
        latest: dict[str, dict] = {}
        for summary in sorted(self.runs.glob("*/summary.json"), reverse=True):
            try:
                rows = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for row in rows:
                latest.setdefault(row["spec"], {"verdict": row["verdict"], "reason": row.get("reason", ""),
                                                "run": summary.parent.name})
        return latest

    def list_runs(self, limit: int = 60) -> list[dict]:
        runs = []
        if not self.runs.exists():
            return runs
        # Newest first by the time in the name: "explore-20260930-..." must not sort above "20261001-...".
        folders = [p for p in self.runs.iterdir() if p.is_dir()]
        for folder in sorted(folders, key=lambda p: re.sub(r"\D", "", p.name)[:14], reverse=True):
            item = {"id": folder.name, "time": _when(folder.name)}
            if (folder / "summary.json").exists():
                try:
                    rows = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                counts: dict[str, int] = {}
                for row in rows:
                    counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
                item.update(kind="run", counts=counts, total=len(rows), specs=[r["spec"] for r in rows])
                if (folder / "defects.json").exists():
                    item["defects"] = json.loads((folder / "defects.json").read_text(encoding="utf-8"))
                elif counts.get("fail") or counts.get("flaky"):
                    item["defects"] = None  # failures, but from before defect analysis existed
                item["files"] = [n for n in ("index.html", "client-report.html", "defects.html", "traceability.html", "test-cases.csv")
                                 if (folder / n).exists()]
            elif (folder / "explore.json").exists():
                data = json.loads((folder / "explore.json").read_text(encoding="utf-8"))
                findings = data.get("findings", [])
                item.update(kind="explore", url=data.get("url", ""), pages=len(data.get("pages", {})),
                            bugs=sum(f["severity"] == "bug" for f in findings),
                            suspected=sum(f["severity"] == "suspected" for f in findings), files=["report.html"])
            else:
                continue
            runs.append(item)
            if len(runs) >= limit:
                break
        return runs

    def run_file(self, value: str) -> Path:
        path = (self.runs / unquote(value)).resolve()
        if not path.is_relative_to(self.runs) or not path.is_file():
            raise BadRequest("no such file")
        return path

    # --- text files (requirements) ----------------------------------------------

    def read_text(self, value: str) -> dict:
        path = self._text_path(value)
        return {"path": self.rel(path), "text": path.read_text(encoding="utf-8") if path.exists() else ""}

    def save_text(self, value: str, text: str) -> dict:
        path = self._text_path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return {"path": self.rel(path)}

    def _text_path(self, value: str) -> Path:
        path = self._inside(self.workspace / value)
        if path.suffix not in (".md", ".txt") or SKIP_DIRS & set(path.parts) or path.is_relative_to(self.runs):
            raise BadRequest("requirements are .md or .txt files in the workspace")
        return path

    # --- jobs -------------------------------------------------------------------

    def start_job(self, kind: str, params: dict) -> Job:
        with self.lock:
            if self.job and self.job.status == "running":
                raise Busy(f"{self.job.title} is still running")
            command, title = self.command_for(kind, params)
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            job = Job(id=f"{int(time.time())}", kind=kind, title=title, command=command, started=time.time())
            job.process = subprocess.Popen(
                command, cwd=self.workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                # Its own process group, so stopping it also stops the browser it started.
                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}),
            )
            self.job = job
        threading.Thread(target=self._follow, args=(job,), daemon=True).start()
        return job

    def _follow(self, job: Job) -> None:
        for line in job.process.stdout:
            line = line.rstrip("\n")
            job.lines.append(line)
            if len(job.lines) > MAX_LOG_LINES:
                del job.lines[: len(job.lines) - MAX_LOG_LINES]
            for key, label in (("report", "report: "), ("defects", "defects: "),
                               ("traceability", "traceability matrix: "), ("findings", "findings: ")):
                if line.startswith(label):
                    job.results[key] = line.removeprefix(label).strip()
        job.exit_code = job.process.wait()
        if job.status == "running":
            job.status = "finished"
        job.finished = time.time()

    def stop_job(self) -> None:
        job = self.job
        if not job or job.status != "running" or not job.process:
            return
        job.status = "stopped"
        _kill_tree(job.process)

    def job_view(self, since: int) -> dict | None:
        job = self.job
        if not job:
            return None
        live = self._live_dir(job)
        shot = None
        if live:
            shots = sorted(live.rglob("step-*.jpg"), key=lambda p: p.stat().st_mtime)
            if shots:
                shot = shots[-1].relative_to(self.runs).as_posix()
        current = next((line[2:].split("  ")[0] for line in reversed(job.lines) if line.startswith("> ")), "")
        links = {}
        for key, value in job.results.items():
            try:
                links[key] = Path(value).resolve().relative_to(self.runs).as_posix()
            except ValueError:
                continue
        return {"id": job.id, "kind": job.kind, "title": job.title, "status": job.status, "exit_code": job.exit_code,
                "started": job.started, "finished": job.finished, "current": current, "screenshot": shot,
                "links": links, "offset": len(job.lines), "lines": job.lines[since:] if since >= 0 else []}

    def _live_dir(self, job: Job) -> Path | None:
        if not self.runs.exists():
            return None
        fresh = [p for p in self.runs.iterdir() if p.is_dir() and p.stat().st_ctime >= job.started - 2]
        return max(fresh, key=lambda p: p.stat().st_ctime) if fresh else None

    def _command_for(self, kind: str, p: dict) -> tuple[list[str], str]:
        base = [sys.executable, "-m", "nightshift"]
        runs = str(self.runs)
        data = [arg for line in str(p.get("data") or "").splitlines() if "=" in line
                for arg in ("--data", line.strip())]
        if kind == "run":
            paths = [self.rel(self._inside(self.workspace / x)) for x in (p.get("paths") or [])] or \
                    [self.rel(d) for d in self.spec_dirs if d.exists()]
            args = ["run", *paths, "--out", runs, "--retries", str(int(p.get("retries", 1)))]
            if p.get("base_url"):
                args += ["--base-url", str(p["base_url"])]
            for flag, key in (("--headed", "headed"), ("--no-replay", "fresh"), ("--no-vision", "no_vision")):
                if p.get(key):
                    args.append(flag)
            count = len(p.get("paths") or [])
            return base + args, f"Run {count} test case(s)" if count else f"Run {', '.join(paths)}"
        if kind == "validate":
            requirements = self.rel(self._text_path(str(p.get("requirements") or "requirements.md")))
            specs_out = self.rel(self._inside(self.workspace / str(p.get("specs_out") or "specs/requirements")))
            args = ["validate", requirements, "--url", _url(p.get("url")), "--cases", str(int(p.get("cases", 2))),
                    "--specs-out", specs_out, "--out", runs, *data]
            if int(p.get("explore_steps") or 0):
                args += ["--explore-steps", str(int(p["explore_steps"]))]
            if p.get("design_only"):
                args.append("--design-only")
            if p.get("redesign"):
                args += ["--redesign", str(p["redesign"])]
            if p.get("headed"):
                args.append("--headed")
            return base + args, ("Design test cases for " if p.get("design_only") else "Validate against ") + requirements
        if kind == "explore":
            args = ["explore", _url(p.get("url")), "--steps", str(int(p.get("steps", 30))), "--out", runs, *data]
            if p.get("focus"):
                args += ["--focus", str(p["focus"])]
            if p.get("headed"):
                args.append("--headed")
            return base + args, f"Explore {p.get('url')}"
        if kind == "cases":
            paths = [self.rel(self._inside(self.workspace / x)) for x in (p.get("paths") or [])] or \
                    [self.rel(d) for d in self.spec_dirs if d.exists()]
            target = self.runs / "test-cases.csv"
            return base + ["cases", *paths, "--to", str(target)], "Write the test-case document"
        if kind == "triage":
            run = (self.runs / str(p.get("run") or "")).resolve()
            if not run.is_relative_to(self.runs) or not (run / "summary.json").exists():
                raise BadRequest("no such run")
            args = ["triage", str(run)] + (["--file-jira"] if p.get("jira") else [])
            return base + args, ("File the defects of " if p.get("jira") else "Defect analysis of ") + run.name
        raise BadRequest(f"unknown job kind {kind!r}")

    # --- the demo shop ------------------------------------------------------------

    def demo_view(self) -> dict:
        available = importlib.util.find_spec("demo_shop") is not None
        view = {"available": available, "running": bool(self.demo and self.demo.poll() is None),
                "bugs": self.demo_bugs, "url": f"http://localhost:{DEMO_PORT}/"}
        if available:
            from demo_shop.server import BUGS

            view["all_bugs"] = [{"name": n, "description": b.description} for n, b in BUGS.items()]
        return view

    def start_demo(self, bugs: list[str]) -> dict:
        self.stop_demo()
        if _port_busy(DEMO_PORT):
            raise BadRequest(f"port {DEMO_PORT} is already in use; stop whatever runs there first")
        args = [sys.executable, "-m", "demo_shop", "--port", str(DEMO_PORT)]
        if bugs:
            args += ["--bugs", ",".join(bugs)]
        self.demo = subprocess.Popen(args, cwd=self.workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.demo_bugs = list(bugs)
        for _ in range(40):
            if _port_busy(DEMO_PORT):
                break
            time.sleep(0.25)
        return self.demo_view()

    def stop_demo(self) -> None:
        if self.demo and self.demo.poll() is None:
            _kill_tree(self.demo)
        self.demo, self.demo_bugs = None, []

    def close(self) -> None:
        self.stop_job()
        self.stop_demo()


def make_handler(dashboard: Dashboard, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            if method in ("POST", "PUT"):
                # Read the body before any refusal: on Windows, closing a connection with unread
                # data resets it, and the client sees a dropped connection instead of the 403.
                length = int(self.headers.get("Content-Length") or 0)
                self._raw_body = self.rfile.read(length) if 0 < length <= MAX_BODY else b""
                if length > MAX_BODY:
                    return self._send(413, {"error": "request too large"})
            if self.headers.get("Host", "") not in allowed_hosts:
                return self._send(403, {"error": "this dashboard only answers to localhost"})
            if method != "GET" and not secrets.compare_digest(self.headers.get("X-Nightshift-Token", ""), dashboard.token):
                return self._send(403, {"error": "missing or wrong dashboard token"})
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            try:
                body = self._body() if method in ("POST", "PUT") else {}
                self._route(method, parts.path, query, body)
            except Busy as exc:
                self._send(409, {"error": str(exc)})
            except (BadRequest, ValueError, KeyError) as exc:
                self._send(400, {"error": str(exc)})
            except FileNotFoundError:
                self._send(404, {"error": "not found"})

        def _route(self, method: str, path: str, query: dict, body: dict) -> None:
            d = dashboard
            match method, path:
                case "GET", "/":
                    page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", d.token)
                    return self._raw(200, page.encode("utf-8"), CONTENT_TYPES[".html"])
                case "GET", _ if path.startswith("/static/"):
                    file = (STATIC / path.removeprefix("/static/")).resolve()
                    if not file.is_relative_to(STATIC) or not file.is_file():
                        raise FileNotFoundError(path)
                    return self._raw(200, file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))
                case "GET", _ if path.startswith("/files/"):
                    file = d.run_file(path.removeprefix("/files/"))
                    return self._raw(200, file.read_bytes(), CONTENT_TYPES.get(file.suffix, "application/octet-stream"))
                case "GET", "/api/state":
                    return self._send(200, d.state())
                case "GET", "/api/specs":
                    return self._send(200, d.list_specs(query.get("folder", "")))
                case "GET", "/api/spec":
                    return self._send(200, d.read_spec(query["path"]))
                case "PUT", "/api/spec":
                    return self._send(200, d.save_spec(str(body["path"]), str(body["text"])))
                case "POST", "/api/spec":
                    return self._send(200, d.new_spec(body))
                case "DELETE", "/api/spec":
                    d.delete_spec(query["path"])
                    return self._send(200, {"ok": True})
                case "GET", "/api/runs":
                    return self._send(200, d.list_runs())
                case "GET", "/api/text":
                    return self._send(200, d.read_text(query["path"]))
                case "PUT", "/api/text":
                    return self._send(200, d.save_text(str(body["path"]), str(body["text"])))
                case "GET", "/api/job":
                    return self._send(200, d.job_view(int(query.get("since", 0))) or {})
                case "POST", "/api/job":
                    job = d.start_job(str(body.get("kind")), body.get("params") or {})
                    return self._send(200, d.job_view(0) | {"id": job.id})
                case "POST", "/api/job/stop":
                    d.stop_job()
                    return self._send(200, {"ok": True})
                case "POST", "/api/demo":
                    if body.get("action") == "stop":
                        d.stop_demo()
                        return self._send(200, d.demo_view())
                    return self._send(200, d.start_demo([str(b) for b in body.get("bugs") or []]))
            raise FileNotFoundError(path)

        def _body(self) -> dict:
            value = json.loads(getattr(self, "_raw_body", b"") or b"{}")  # read in _handle, before any check
            if not isinstance(value, dict):
                raise BadRequest("expected a JSON object")
            return value

        def _send(self, status: int, payload) -> None:
            self._raw(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")

        def _raw(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(dashboard: Dashboard, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(dashboard, port))
    # With port 0 the OS picks one; the Host check must use the port actually bound.
    server.RequestHandlerClass = make_handler(dashboard, server.server_port)
    server.daemon_threads = True
    return server


def _when(name: str) -> str:
    match = re.search(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", name)
    return f"{match[1]}-{match[2]}-{match[3]} {match[4]}:{match[5]}" if match else ""


def _url(value) -> str:
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://")):
        raise BadRequest("the app URL must start with http:// or https://")
    return url


def _port_busy(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _kill_tree(process: subprocess.Popen) -> None:
    """Stop a process and everything it started (the browser, the Playwright driver)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        else:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
