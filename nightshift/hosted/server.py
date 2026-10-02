"""`nightshift hosted serve`: the web app a QA agency's staff log in to.

Each of the agency's clients is a project: its tests, its saved paths, its test secrets,
a nightly schedule, and every run's client report. On the server it listens on 127.0.0.1
behind Caddy, which adds HTTPS (deploy/oracle/).

Guards, since it is on the internet and starts browsers:
  - a login cookie: HttpOnly, SameSite=Lax, Secure behind HTTPS; only its hash is stored;
  - every change must carry the header X-Nightshift: 1 (a form on another site can't send
    one) and, when the browser says where it comes from, this site's Origin;
  - five wrong passwords from one address lock that address out for 15 minutes;
  - run files are served to logged-in users only, and only from inside that run's folder;
  - secrets can be set and deleted, never read back.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .files import ProjectFiles
from .jobs import Busy, Runner
from .store import SESSION_DAYS, Store, StoreError

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "ns_session"
LOGIN_LIMIT = 5
LOGIN_WINDOW_S = 15 * 60
MAX_BODY = 300_000

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".jpg": "image/jpeg",
                 ".png": "image/png", ".svg": "image/svg+xml", ".webm": "video/webm", ".zip": "application/zip",
                 ".md": "text/plain; charset=utf-8", ".log": "text/plain; charset=utf-8",
                 ".csv": "text/csv; charset=utf-8", ".xml": "application/xml"}
# The app's own pages load nothing from anywhere else.
APP_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
# Run reports are generated pages with inline styles and scripts and embedded screenshots.
REPORT_CSP = ("default-src 'self' 'unsafe-inline' data: blob:; frame-ancestors 'self'; base-uri 'none'; "
              "form-action 'none'")


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class App:
    def __init__(self, store: Store, runner: Runner, public_url: str = "") -> None:
        self.store, self.runner = store, runner
        self.public_url = public_url.rstrip("/")
        self.secure = self.public_url.startswith("https://")
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # --- login rate limit ---------------------------------------------------------

    def _recent_failures(self, ip: str) -> list[float]:
        now = time.time()
        recent = [t for t in self._failures.get(ip, []) if now - t < LOGIN_WINDOW_S]
        self._failures[ip] = recent
        return recent

    def login(self, body: dict, ip: str) -> tuple[dict, str]:
        with self._lock:
            if len(self._recent_failures(ip)) >= LOGIN_LIMIT:
                raise HttpError(429, "too many wrong passwords from here; try again in 15 minutes")
        user = self.store.check_login(str(body.get("email", "")), str(body.get("password", "")))
        if user is None:
            with self._lock:
                self._failures.setdefault(ip, []).append(time.time())
            raise HttpError(401, "wrong email or password")
        return user, self.store.new_session(user["id"])

    def cookie(self, token: str, max_age: int = SESSION_DAYS * 86400) -> str:
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if self.secure else ""))

    # --- the API ------------------------------------------------------------------

    def api(self, method: str, path: str, body: dict, user: dict | None) -> tuple[int, object]:
        if user is None:
            raise HttpError(401, "log in first")
        route = f"{method} {path}"

        if route == "GET /api/me":
            return 200, {"user": user}
        if route == "PUT /api/me/password":
            if self.store.check_login(user["email"], str(body.get("current", ""))) is None:
                raise HttpError(400, "the current password is wrong")
            self.store.set_password(user["id"], str(body.get("new", "")))
            return 200, {"ok": True}

        if route == "GET /api/users":
            _admin(user)
            return 200, {"users": self.store.users()}
        if route == "POST /api/users":
            _admin(user)
            self.store.add_user(str(body.get("email", "")), str(body.get("name", "")), str(body.get("password", "")),
                                admin=bool(body.get("admin")))
            return 201, {"users": self.store.users()}
        if match := re.fullmatch(r"DELETE /api/users/(\d+)", route):
            _admin(user)
            if int(match[1]) == user["id"]:
                raise HttpError(400, "you can't delete yourself")
            self.store.delete_user(int(match[1]))
            return 200, {"users": self.store.users()}

        if route == "GET /api/projects":
            return 200, {"projects": [self._project_card(p) for p in self.store.projects()]}
        if route == "POST /api/projects":
            _admin(user)
            project = self.store.add_project(str(body.get("client", "")), str(body.get("base_url", "")),
                                             str(body.get("brand", "")), str(body.get("nightly", "")))
            ProjectFiles(self.runner.data, project["slug"])
            return 201, {"project": project}

        match = re.fullmatch(r"(GET|PUT|POST|DELETE) /api/projects/([a-z0-9-]+)(/.*)?", route)
        if not match:
            raise HttpError(404, "not found")
        project = self.store.project(match[2])
        if project is None:
            raise HttpError(404, "no such project")
        files = self.runner.files(project)
        rest = f"{method} {match[3] or ''}"

        if rest == "GET ":
            return 200, self._project_view(project, files)
        if rest == "PUT ":
            _admin(user)
            self.store.update_project(project["slug"], **{k: str(body[k]) for k in ("client", "base_url", "brand", "nightly")
                                                          if k in body})
            return 200, self._project_view(self.store.project(project["slug"]), files)
        if m := re.fullmatch(r"(GET|PUT|DELETE) /specs/([a-z0-9-]+)", rest):
            if m[1] == "GET":
                return 200, {"name": m[2], "text": files.read_spec(m[2]) if m[2] in files.spec_names() else ""}
            if m[1] == "PUT":
                files.save_spec(m[2], str(body.get("text", "")))
            else:
                files.delete_spec(m[2])
            return 200, {"specs": files.spec_names()}
        if m := re.fullmatch(r"(PUT|DELETE) /secrets/([A-Za-z0-9_]+)", rest):
            _admin(user)
            if m[1] == "PUT":
                files.set_secret(m[2], str(body.get("value", "")))
            else:
                files.delete_secret(m[2])
            return 200, {"secrets": sorted(files.secrets())}
        if rest == "POST /explore":
            url = str(body.get("url", "")).strip()
            if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
                raise HttpError(400, "give the address of the site, starting with http:// or https://")
            try:
                steps = int(body.get("steps") or 25)
            except ValueError:
                raise HttpError(400, "steps must be a number") from None
            if not 5 <= steps <= 60:
                raise HttpError(400, "steps must be between 5 and 60")
            params = {"url": url, "steps": steps, "focus": str(body.get("focus", "")).strip()[:200]}
            run_id = self.runner.enqueue(project, "explore", user["id"], params)
            return 201, {"run": self._run_view(project, files, self.store.run(run_id))}
        if rest == "POST /runs":
            run_id = self.runner.enqueue(project, "manual", user["id"])
            return 201, {"run": self._run_view(project, files, self.store.run(run_id))}
        if m := re.fullmatch(r"POST /runs/(\d+)/stop", rest):
            run = self.store.run(int(m[1]))
            if run is None or run["project_id"] != project["id"]:
                raise HttpError(404, "no such run")
            self.runner.stop(run["id"])
            return 200, {"run": self._run_view(project, files, self.store.run(run["id"]))}
        raise HttpError(404, "not found")

    def file(self, path: str, user: dict | None) -> Path:
        """/files/<project>/<run id>/<path inside that run's folder>"""
        if user is None:
            raise HttpError(401, "log in first")
        match = re.fullmatch(r"/files/([a-z0-9-]+)/(\d+)/(.+)", path)
        project = self.store.project(match[1]) if match else None
        run = self.store.run(int(match[2])) if match else None
        if project is None or run is None or run["project_id"] != project["id"]:
            raise HttpError(404, "not found")
        try:
            return self.runner.files(project).run_file(run["id"], match[3])
        except StoreError:
            raise HttpError(404, "not found") from None

    # --- views --------------------------------------------------------------------

    def _project_card(self, project: dict) -> dict:
        runs = self.store.runs(project["id"], limit=1)
        return {**project, "last_run": self._run_view(project, None, runs[0]) if runs else None}

    def _project_view(self, project: dict, files: ProjectFiles) -> dict:
        return {"project": project, "specs": files.spec_names(), "secrets": sorted(files.secrets()),
                "runs": [self._run_view(project, files, run) for run in self.store.runs(project["id"])],
                "parallel": self.runner.parallel}

    def _run_view(self, project: dict, files: ProjectFiles | None, run: dict) -> dict:
        files = files or self.runner.files(project)
        base = f"/files/{project['slug']}/{run['id']}"
        links = {}
        if (files.run_folder(run["id"]) / "output.log").exists():
            links["log"] = f"{base}/output.log"
        if run["run_dir"]:
            folder = files.run_folder(run["id"]) / run["run_dir"]
            for key, name in (("client_report", "client-report.html"), ("report", "index.html"),
                              ("report", "report.html"), ("findings", "findings.md")):
                if (folder / name).exists():
                    links[key] = f"{base}/{run['run_dir']}/{name}"
        keep = ("id", "trigger", "status", "queued", "started", "finished", "passed", "failed", "flaky", "errors", "message")
        target = json.loads(run["params"]).get("url", "") if run.get("params") else ""
        return {**{key: run[key] for key in keep}, "links": links, "target": target}


def _admin(user: dict) -> None:
    if not user.get("admin"):
        raise HttpError(403, "only an admin can do that")


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Nightshift"
        sys_version = ""

        def log_message(self, format: str, *args) -> None:  # noqa: A002 (the base class's name)
            pass  # request lines can hold report paths; the server keeps no access log

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            path = unquote(urlsplit(self.path).path)
            try:
                # The body is read before anything is refused: closing a connection with the body
                # still unread resets it, and the client sees a dropped connection, not the 403.
                body = self._body() if method in ("POST", "PUT") else {}
                if method != "GET":
                    self._check_change()
                token = self._token()
                user = app.store.user_for(token)
                if path == "/api/login" and method == "POST":
                    user, token = app.login(body, self._ip())
                    self._json(200, {"user": user}, {"Set-Cookie": app.cookie(token)})
                elif path == "/api/logout" and method == "POST":
                    app.store.end_session(token)
                    self._json(200, {"ok": True}, {"Set-Cookie": app.cookie("", max_age=0)})
                elif path.startswith("/api/"):
                    self._json(*app.api(method, path, body, user))
                elif path.startswith("/files/") and method == "GET":
                    target = app.file(path, user)
                    self._raw(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
                              REPORT_CSP)
                elif method == "GET":
                    self._static(path)
                else:
                    raise HttpError(404, "not found")
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message})
            except Busy as exc:
                self._json(409, {"error": str(exc)})
            except StoreError as exc:
                self._json(400, {"error": str(exc)})

        def _check_change(self) -> None:
            if self.headers.get("X-Nightshift") != "1":
                raise HttpError(403, "missing the X-Nightshift header")
            origin = self.headers.get("Origin")
            if origin:
                expected = app.public_url or f"http://{self.headers.get('Host', '')}"
                if origin.rstrip("/") != expected:
                    raise HttpError(403, "this request came from another site")

        def _token(self) -> str:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:  # noqa: BLE001 (a malformed cookie header is simply no login)
                return ""
            return cookie[COOKIE].value if COOKIE in cookie else ""

        def _ip(self) -> str:
            peer = self.client_address[0]
            forwarded = self.headers.get("X-Forwarded-For", "")
            # Behind Caddy on the same machine, the client is the last address Caddy added.
            if peer in ("127.0.0.1", "::1") and forwarded:
                return forwarded.split(",")[-1].strip()
            return peer

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise HttpError(413, "too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                raise HttpError(400, "the body must be JSON") from None
            if not isinstance(body, dict):
                raise HttpError(400, "the body must be a JSON object")
            return body

        def _static(self, path: str) -> None:
            name = path.removeprefix("/static/") if path.startswith("/static/") else "index.html"
            target = (STATIC / name).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                raise HttpError(404, "not found")
            self._raw(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"), APP_CSP)

        def _json(self, status: int, payload: object, headers: dict | None = None) -> None:
            self._raw(status, json.dumps(payload).encode("utf-8"), "application/json", APP_CSP, headers)

        def _raw(self, status: int, body: bytes, content_type: str, csp: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", csp)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(app: App, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
