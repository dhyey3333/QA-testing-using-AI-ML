"""`nightshift hosted serve`: the web app a QA agency's staff log in to.

Each of the agency's clients is a project: its tests, its saved paths, its test secrets,
a nightly schedule, and every run's client report. On the server it listens on 127.0.0.1
behind Caddy, which adds HTTPS (deploy/server/).

Guards, since it is on the internet and starts browsers:
  - each agency has its own workspace and sees only its own projects, people and run files: every
    project is looked up through the user's workspace, and another workspace's is "not found";
  - accounts are made only from an invite link: random, single use, a week long, stored hashed;
  - a login cookie: HttpOnly, SameSite=Lax, Secure behind HTTPS; only its hash is stored;
  - every change must carry the header X-Nightshift: 1 (a form on another site can't send
    one) and, when the browser says where it comes from, this site's Origin;
  - five wrong passwords from one address lock that address out for 15 minutes;
  - run files are served to logged-in users only, and only from inside that run's folder;
  - secrets can be set and deleted, never read back, and are encrypted on disk (vault.py);
  - two-factor login with an authenticator app, for anyone who turns it on;
  - forgotten passwords are reset with a link an admin makes: single use, a day long, stored hashed;
  - every change is written to the workspace's activity log (who, what, when, from where; never a
    value), which its admins can read.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from ..defects import analyse, load_results, record_issue
from ..generate import write_specs
from ..gherkin import GherkinError, parse_feature
from ..jira import JiraConfig, JiraError, file_jira_issues
from ..notify import post_slack, slack_payload
from ..visual import Baselines
from .files import ProjectFiles
from .jobs import Busy, Runner
from .store import SESSION_DAYS, Store, StoreError

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "ns_session"
LOGIN_LIMIT = 5
LOGIN_WINDOW_S = 15 * 60
MAX_BODY = 300_000
DRAIN_LIMIT = 5_000_000  # an oversized body up to this size is read and dropped, to answer 413 cleanly

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
    def __init__(self, status: int, message: str, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra or {}


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
        email = str(body.get("email", ""))
        user = self.store.check_login(email, str(body.get("password", "")))
        if user is None:
            self._failed(ip)
            if (workspace_id := self.store.workspace_of(email)) is not None:  # an unknown email logs nowhere
                self.store.log("login failed: wrong password", email=email.strip().lower(),
                               workspace_id=workspace_id, ip=ip)
            raise HttpError(401, "wrong email or password")
        if user["two_factor"]:
            # The password was right; the code from the authenticator app is the second step. A wrong
            # code counts as a wrong password for the lockout, so codes can't be guessed either.
            code = str(body.get("code", "")).strip()
            if not code:
                raise HttpError(401, "enter the 6-digit code from your authenticator app", {"need_code": True})
            if not self.store.check_code(user["id"], code):
                self._failed(ip)
                self.store.log("login failed: wrong two-factor code", user=user, ip=ip)
                raise HttpError(401, "that code is not right; use the newest one in your app", {"need_code": True})
        self.store.log("logged in", user=user, ip=ip)
        return user, self.store.new_session(user["id"])

    def _failed(self, ip: str) -> None:
        with self._lock:
            self._failures.setdefault(ip, []).append(time.time())

    def cookie(self, token: str, max_age: int = SESSION_DAYS * 86400, secure: bool = False) -> str:
        """Secure (HTTPS only) when this request came over HTTPS. Not whenever a public URL is set:
        the same app is also used on the laptop itself at http://127.0.0.1, where a Secure cookie
        may never come back and the user would be logged out at once."""
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if secure else ""))

    # --- the API ------------------------------------------------------------------

    def api(self, method: str, path: str, body: dict, user: dict | None) -> tuple[int, object]:
        if user is None:
            raise HttpError(401, "log in first")
        route = f"{method} {path}"

        if route == "GET /api/me":
            # public_url, so links made here (invites) point where people can open them, even when the
            # person making one uses the app at 127.0.0.1 on the laptop itself.
            return 200, {"user": user, "workspace": self.store.workspace(user["workspace_id"]),
                         "public_url": self.public_url}
        if route == "PUT /api/me/password":
            if self.store.check_login(user["email"], str(body.get("current", ""))) is None:
                raise HttpError(400, "the current password is wrong")
            self.store.set_password(user["id"], str(body.get("new", "")))
            return 200, {"ok": True}

        # Two-factor login with any authenticator app (Google Authenticator, Microsoft Authenticator...).
        if route == "POST /api/me/2fa":
            secret = self.store.start_two_factor(user["id"])
            label = quote(f"Nightshift QA:{user['email']}")
            return 200, {"secret": secret, "uri": f"otpauth://totp/{label}?secret={secret}&issuer=Nightshift%20QA"}
        if route == "POST /api/me/2fa/confirm":
            self.store.confirm_two_factor(user["id"], str(body.get("code", "")))
            return 200, {"ok": True}
        if route == "POST /api/me/2fa/off":
            if self.store.check_login(user["email"], str(body.get("password", ""))) is None:
                raise HttpError(400, "the password is wrong")
            self.store.stop_two_factor(user["id"])
            return 200, {"ok": True}

        workspace_id = user["workspace_id"]
        if route == "GET /api/audit":
            _admin(user)
            return 200, {"events": self.store.events(workspace_id)}
        if route == "GET /api/users":
            _admin(user)
            return 200, {"users": self.store.users(workspace_id)}
        if match := re.fullmatch(r"POST /api/users/(\d+)/(reset|2fa-off)", route):
            # Someone who forgot their password, or lost the phone with their codes, asks an admin.
            _admin(user)
            member = self._member(int(match[1]), workspace_id)
            if match[2] == "2fa-off":
                self.store.stop_two_factor(member["id"])
                return 200, {"users": self.store.users(workspace_id)}
            path = f"/#/reset/{self.store.new_reset(member['id'])}"
            return 201, {"path": path, "link": f"{self.public_url}{path}" if self.public_url else ""}
        if route == "POST /api/users":
            _admin(user)
            self.store.add_user(str(body.get("email", "")), str(body.get("name", "")), str(body.get("password", "")),
                                admin=bool(body.get("admin")), workspace_id=workspace_id)
            return 201, {"users": self.store.users(workspace_id)}
        if match := re.fullmatch(r"DELETE /api/users/(\d+)", route):
            _admin(user)
            if int(match[1]) == user["id"]:
                raise HttpError(400, "you can't delete yourself")
            self.store.delete_user(int(match[1]), workspace_id)
            return 200, {"users": self.store.users(workspace_id)}

        # The workspace's own name, and invite links into it.
        if route == "PUT /api/workspace":
            _admin(user)
            return 200, {"workspace": self.store.rename_workspace(workspace_id, str(body.get("name", "")))}
        if route == "GET /api/invites":
            _admin(user)
            return 200, {"invites": self.store.invites(workspace_id)}
        if route == "POST /api/invites":
            _admin(user)
            token = self.store.new_invite(workspace_id, admin=bool(body.get("admin")), note=str(body.get("note", "")),
                                          created_by=user["id"])
            return 201, self._invite_made(token, workspace_id)
        if match := re.fullmatch(r"DELETE /api/invites/(\d+)", route):
            _admin(user)
            self.store.revoke_invite(int(match[1]), workspace_id)
            return 200, {"invites": self.store.invites(workspace_id)}

        # The owner makes a workspace for each agency, and the invite for its first admin.
        if route == "GET /api/workspaces":
            _owner(user)
            return 200, {"workspaces": self.store.workspaces()}
        if route == "POST /api/workspaces":
            _owner(user)
            workspace = self.store.add_workspace(str(body.get("name", "")))
            token = self.store.new_invite(workspace["id"], admin=True, note=str(body.get("note", "")),
                                          created_by=user["id"])
            return 201, {**self._invite_made(token, workspace["id"]), "workspace": workspace,
                         "workspaces": self.store.workspaces()}
        if match := re.fullmatch(r"POST /api/workspaces/(\d+)/delete", route):
            # An agency that leaves: everything of theirs goes, on the owner's word and the typed name.
            _owner(user)
            gone = self.store.workspace(int(match[1]))
            if gone is None or str(body.get("confirm", "")).strip() != gone["name"]:
                raise HttpError(400, "type the workspace's name exactly to confirm")
            for slug in self.store.delete_workspace(gone["id"]):
                shutil.rmtree(self.runner.data / "projects" / slug, ignore_errors=True)
            return 200, {"workspaces": self.store.workspaces()}

        if route == "GET /api/projects":
            return 200, {"projects": [self._project_card(p) for p in self.store.projects(workspace_id)]}
        if route == "POST /api/projects":
            _admin(user)
            # Reports say "prepared by" the agency unless told otherwise: that's whose name the client knows.
            brand = str(body.get("brand", "")).strip() or self.store.workspace(workspace_id)["name"]
            project = self.store.add_project(str(body.get("client", "")), str(body.get("base_url", "")),
                                             brand, str(body.get("nightly", "")), workspace_id=workspace_id)
            ProjectFiles(self.runner.data, project["slug"])
            return 201, {"project": project}

        match = re.fullmatch(r"(GET|PUT|POST|DELETE) /api/projects/([a-z0-9-]+)(/.*)?", route)
        if not match:
            raise HttpError(404, "not found")
        project = self._project(match[2], user)
        files = self.runner.files(project)
        rest = f"{method} {match[3] or ''}"

        if rest == "GET ":
            return 200, self._project_view(project, files)
        if rest == "POST /delete":
            # A client that leaves, or asks for its data to be deleted: tests, secrets, saved paths, every
            # run and report. Only an admin, and only with the client's name typed back.
            _admin(user)
            if str(body.get("confirm", "")).strip() != project["client"]:
                raise HttpError(400, "type the client's name exactly to confirm")
            self.store.delete_project(project["slug"])
            shutil.rmtree(files.root, ignore_errors=True)
            return 200, {"deleted": project["slug"]}
        if rest == "PUT ":
            _admin(user)
            self.store.update_project(project["slug"], **{k: str(body[k]) for k in ("client", "base_url", "brand", "nightly",
                                                                                   "targets") if k in body})
            return 200, self._project_view(self.store.project(project["slug"]), files)
        if m := re.fullmatch(r"(GET|PUT|DELETE) /specs/([a-z0-9-]+)", rest):
            if m[1] == "GET":
                text = files.read_spec(m[2]) if m[2] in files.spec_names() else ""
                return 200, {"name": m[2], "text": text, "form": files.form_of(text) if text else None}
            if m[1] == "PUT":
                existing = files.read_spec(m[2]) if m[2] in files.spec_names() else ""
                text = files.yaml_of(m[2], body["form"], existing) if isinstance(body.get("form"), dict) \
                    else str(body.get("text", ""))
                files.save_spec(m[2], text)
            else:
                files.delete_spec(m[2])
            return 200, {"specs": files.spec_names()}
        if m := re.fullmatch(r"(GET|PUT|DELETE) /drafts/([a-z0-9-]+)", rest):
            if m[2] not in {d["name"] for d in files.drafts_list()}:
                raise HttpError(404, "no such draft")
            if m[1] == "GET":
                text = files.read_draft(m[2])
                return 200, {"name": m[2], "text": text, "form": files.form_of(text)}
            if m[1] == "PUT":
                text = files.yaml_of(m[2], body["form"], files.read_draft(m[2])) if isinstance(body.get("form"), dict) \
                    else str(body.get("text", ""))
                files.save_draft(m[2], text)
            else:
                files.delete_draft(m[2])
            return 200, self._project_view(project, files)
        if m := re.fullmatch(r"POST /drafts/([a-z0-9-]+)/accept", rest):
            files.accept_draft(m[1])
            return 200, self._project_view(project, files)
        if rest == "POST /drafts/accept-all":
            problems = []
            for draft in files.drafts_list():
                try:
                    files.accept_draft(draft["name"])
                except StoreError as exc:
                    problems.append(f"{draft['name']}: {exc}")
            return 200, {**self._project_view(project, files), "problems": problems}
        if rest == "POST /import-gherkin":
            text = str(body.get("text", ""))
            if not text.strip():
                raise HttpError(400, "paste the contents of a .feature file")
            url = str(body.get("url", "")).strip()
            if url and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
                raise HttpError(400, "the website must start with http:// or https://")
            try:
                specs = parse_feature(text, url=url)
            except GherkinError as exc:
                raise HttpError(400, str(exc)) from None
            written = write_specs(specs, files.drafts, url=url or "https://example.com/", source="a Cucumber feature")
            return 201, {**self._project_view(project, files), "imported": len(written)}
        if rest == "POST /generate":
            url = str(body.get("url", "")).strip()
            if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
                raise HttpError(400, "give the address of the site, starting with http:// or https://")
            try:
                # 0 explore actions is a real answer (write from the description alone), not "use the default".
                count = int(body["count"] if body.get("count") not in (None, "") else 5)
                steps = int(body["steps"] if body.get("steps") not in (None, "") else 20)
            except ValueError:
                raise HttpError(400, "the numbers must be numbers") from None
            if not (1 <= count <= 10 and 0 <= steps <= 40):
                raise HttpError(400, "write 1 to 10 tests, after exploring 0 to 40 actions")
            if steps == 0 and not str(body.get("about", "")).strip():
                raise HttpError(400, "say what the site is for, or let it explore the site first")
            params = {"url": url, "count": count, "steps": steps, "about": str(body.get("about", "")).strip()[:2000]}
            run_id = self.runner.enqueue(project, "generate", user["id"], params)
            return 201, {"run": self._run_view(project, files, self.store.run(run_id))}
        if m := re.fullmatch(r"(PUT|DELETE) /secrets/([A-Za-z0-9_]+)", rest):
            _admin(user)
            if m[1] == "PUT":
                files.set_secret(m[2], str(body.get("value", "")))
            else:
                files.delete_secret(m[2])
            return 200, {"secrets": files.secret_names()}
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
        if m := re.fullmatch(r"(GET|POST) /runs/(\d+)(/accept-visual|/jira|/slack)?", rest):
            run = self.store.run(int(m[2]))
            if run is None or run["project_id"] != project["id"]:
                raise HttpError(404, "no such run")
            return self._run_detail(project, files, run, m[1], m[3] or "", user)
        if m := re.fullmatch(r"POST /runs/(\d+)/stop", rest):
            run = self.store.run(int(m[1]))
            if run is None or run["project_id"] != project["id"]:
                raise HttpError(404, "no such run")
            self.runner.stop(run["id"])
            return 200, {"run": self._run_view(project, files, self.store.run(run["id"]))}
        raise HttpError(404, "not found")

    def _run_detail(self, project: dict, files: ProjectFiles, run: dict, method: str, action: str,
                    user: dict) -> tuple[int, object]:
        """A run's tests: result, probable cause, visual check; and what can be done with them."""
        folder = files.run_folder(run["id"]) / run["run_dir"] if run["run_dir"] else None
        has_results = folder is not None and (folder / "summary.json").exists()
        results = load_results(folder) if has_results else []
        secrets = files.secrets()
        jira = JiraConfig.from_mapping(secrets)
        if method == "POST" and action == "/accept-visual":
            baselines = Baselines(files.root / "visual")
            accepted = 0
            for result in results:
                if result.visual.get("status") in ("changed", "visual bug"):
                    current = Path(result.out_dir) / result.visual["files"]["current"]
                    baselines.accept(current, result.spec, result.browser, result.viewport)
                    accepted += 1
            return 200, {"accepted": accepted}
        if method == "POST" and action == "/jira":
            if jira is None:
                raise HttpError(400, "set JIRA_URL, JIRA_PROJECT, JIRA_EMAIL and JIRA_API_TOKEN under Settings → Secrets first")
            defects = analyse(results, files.runs)
            if not defects:
                return 200, {"filed": []}
            try:
                filed = file_jira_issues(defects, folder, jira)
            except (JiraError, httpx.HTTPError) as exc:
                raise HttpError(502, f"Jira refused it: {exc}") from None
            for defect_id, _, key in filed:
                record_issue(folder, defect_id, "jira", key, f"{jira.url}/browse/{key}")
            return 200, {"filed": [{"defect": d, "action": a, "key": k, "url": f"{jira.url}/browse/{k}"} for d, a, k in filed]}
        if method == "POST" and action == "/slack":
            webhook = secrets.get("SLACK_WEBHOOK_URL", "")
            if not webhook:
                raise HttpError(400, "set SLACK_WEBHOOK_URL under Settings → Secrets first")
            link = f"{self.public_url}/#/p/{project['slug']}/run/{run['id']}" if self.public_url else ""
            try:
                post_slack(webhook, slack_payload(results, folder, link))
            except httpx.HTTPError as exc:
                raise HttpError(502, f"Slack refused it ({type(exc).__name__})") from None
            return 200, {"posted": True}
        if method != "GET" or action:
            raise HttpError(404, "not found")
        base = f"/files/{project['slug']}/{run['id']}/{run['run_dir']}"
        issues = _issues(folder) if folder else {}
        tests = []
        for result in results:
            spec_dir = Path(result.out_dir).relative_to(folder).as_posix() if has_results else ""
            links = {"report": f"{base}/{spec_dir}/report.html"}
            if (Path(result.out_dir) / "bug.md").exists():
                links["bug"] = f"{base}/{spec_dir}/bug.md"
            visual = {k: result.visual.get(k) for k in ("status", "what", "changed")} if result.visual else {}
            if result.visual.get("files", {}).get("sides"):
                visual["picture"] = f"{base}/{spec_dir}/{result.visual['files']['sides']}"
            tests.append({"spec": result.spec, "verdict": result.verdict, "category": result.category,
                          "reason": result.reason, "cause": result.cause, "mode": result.mode,
                          "duration_s": result.duration_s, "visual": visual, "links": links})
        order = {"fail": 0, "error": 1, "flaky": 2, "pass": 3}
        tests.sort(key=lambda t: (order.get(t["verdict"], 4), t["spec"]))
        return 200, {"run": self._run_view(project, files, run), "tests": tests, "issues": list(issues.values()),
                     "jira": jira is not None, "slack": bool(secrets.get("SLACK_WEBHOOK_URL"))}

    def file(self, path: str, user: dict | None) -> Path:
        """/files/<project>/<run id>/<path inside that run's folder>"""
        if user is None:
            raise HttpError(401, "log in first")
        match = re.fullmatch(r"/files/([a-z0-9-]+)/(\d+)/(.+)", path)
        project = self._project(match[1], user) if match else None
        run = self.store.run(int(match[2])) if match else None
        if project is None or run is None or run["project_id"] != project["id"]:
            raise HttpError(404, "not found")
        try:
            return self.runner.files(project).run_file(run["id"], match[3])
        except StoreError:
            raise HttpError(404, "not found") from None

    def _project(self, slug: str, user: dict) -> dict:
        """A project, if it is in the user's workspace. Another workspace's is "no such project", not
        "forbidden": a guessed name shouldn't even confirm that it exists."""
        project = self.store.project(slug)
        if project is None or project["workspace_id"] != user["workspace_id"]:
            raise HttpError(404, "no such project")
        return project

    def _member(self, user_id: int, workspace_id: int) -> dict:
        member = next((u for u in self.store.users(workspace_id) if u["id"] == user_id), None)
        if member is None:
            raise HttpError(404, "no such user")
        return member

    def reset(self, token: str, method: str, body: dict, ip: str) -> dict:
        """A reset link, before any login: whose it is (GET), or the new password (POST)."""
        member = self.store.reset_user(token)
        if member is None:
            raise HttpError(404, "this reset link has expired or was already used; ask an admin for a new one")
        if method == "GET":
            return {"email": member["email"]}
        self.store.use_reset(token, str(body.get("password", "")))
        self.store.log("set a new password with a reset link", user=member, ip=ip)
        return {"ok": True}

    def health(self) -> tuple[int, dict]:
        """For an uptime monitor: is the app up and its database answering. Counts only, no names."""
        try:
            counts = self.store.counts()
        except Exception:  # noqa: BLE001 (any database failure is "not healthy")
            return 503, {"ok": False, "database": "not answering"}
        return 200, {"ok": True, "database": "ok", **counts, "last_backup": self.runner.last_backup}

    def describe(self, method: str, path: str, body: dict) -> str:
        """The activity-log line for a change, worked out BEFORE it runs (so a removed person is still
        named). Built from the route and a few named fields; never a password, a secret's value or a
        test's text."""
        words = _describe(method, path, body)
        emails = {u["id"]: u["email"] for u in self.store.users()}
        return re.sub(r"user #(\d+)", lambda m: emails.get(int(m[1]), m[0]), words)

    def _invite_made(self, token: str, workspace_id: int) -> dict:
        """A new invite: its link (shown once; only the token's hash is kept) and the open invites."""
        path = f"/#/join/{token}"
        return {"token": token, "link": f"{self.public_url}{path}" if self.public_url else "", "path": path,
                "invites": self.store.invites(workspace_id)}

    def join(self, token: str, method: str, body: dict) -> tuple[dict, str | None]:
        """The invite page, before any login: what the link is for (GET), or the new account (POST)."""
        invite = self.store.invite(token)
        if invite is None:
            raise HttpError(404, "this invite link has expired or was already used; ask for a new one")
        if method == "GET":
            return {"workspace": invite["workspace"], "admin": bool(invite["admin"])}, None
        user_id = self.store.accept_invite(token, str(body.get("email", "")), str(body.get("name", "")),
                                           str(body.get("password", "")))
        user = next(u for u in self.store.users() if u["id"] == user_id)
        return {"user": user}, self.store.new_session(user_id)

    # --- views --------------------------------------------------------------------

    def _project_card(self, project: dict) -> dict:
        runs = self.store.runs(project["id"], limit=10)
        # The last ten runs, oldest first, for the history strip on the client's card.
        history = [{key: run[key] for key in ("id", "trigger", "status", "passed", "failed", "flaky", "errors")}
                   for run in reversed(runs)]
        return {**project, "last_run": self._run_view(project, None, runs[0]) if runs else None, "history": history,
                "tests": len(self.runner.files(project).spec_names())}

    def _project_view(self, project: dict, files: ProjectFiles) -> dict:
        return {"project": project, "specs": files.spec_names(), "secrets": files.secret_names(),
                "drafts": files.drafts_list(),
                "missing": [{"test": test, "secret": secret} for test, secret in files.missing_secrets()],
                "runs": [self._run_view(project, files, run) for run in self.store.runs(project["id"])],
                "parallel": self.runner.parallel, "keep_runs": self.runner.keep_runs}

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


def _issues(folder: Path) -> dict:
    try:
        return json.loads((folder / "issues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# How each kind of change reads in the activity log. Only names from the path or the body (a client's,
# a test's, a secret's, a person's email); the body's other fields never get in.
_ACTIONS = [
    (r"PUT /api/me/password", lambda m, b: "changed their password"),
    (r"POST /api/me/2fa", lambda m, b: "began setting up two-factor login"),
    (r"POST /api/me/2fa/confirm", lambda m, b: "turned on two-factor login"),
    (r"POST /api/me/2fa/off", lambda m, b: "turned off two-factor login"),
    (r"POST /api/users", lambda m, b: f"added {b.get('email', 'a user')}"),
    (r"DELETE /api/users/(\d+)", lambda m, b: f"removed user #{m[1]}"),
    (r"POST /api/users/(\d+)/reset", lambda m, b: f"made a password reset link for user #{m[1]}"),
    (r"POST /api/users/(\d+)/2fa-off", lambda m, b: f"turned off two-factor login for user #{m[1]}"),
    (r"PUT /api/workspace", lambda m, b: f"renamed the workspace to {b.get('name', '')}"),
    (r"POST /api/invites", lambda m, b: "made an " + ("admin " if b.get("admin") else "") + "invite link"
     + (f" for {b['note']}" if b.get("note") else "")),
    (r"DELETE /api/invites/(\d+)", lambda m, b: "cancelled an invite link"),
    (r"POST /api/workspaces", lambda m, b: f"made the workspace {b.get('name', '')}"),
    (r"POST /api/workspaces/(\d+)/delete", lambda m, b: f"deleted the workspace {b.get('confirm', '')}"),
    (r"POST /api/projects", lambda m, b: f"added the client {b.get('client', '')}"),
    (r"POST /api/projects/([a-z0-9-]+)/delete", lambda m, b: f"deleted the client {m[1]} and all its data"),
    (r"PUT /api/projects/([a-z0-9-]+)", lambda m, b: f"changed the settings of {m[1]}"),
    (r"PUT /api/projects/([a-z0-9-]+)/specs/([a-z0-9-]+)", lambda m, b: f"saved the test {m[2]} in {m[1]}"),
    (r"DELETE /api/projects/([a-z0-9-]+)/specs/([a-z0-9-]+)", lambda m, b: f"deleted the test {m[2]} from {m[1]}"),
    (r"PUT /api/projects/([a-z0-9-]+)/secrets/(\w+)", lambda m, b: f"set the secret {m[2]} on {m[1]}"),
    (r"DELETE /api/projects/([a-z0-9-]+)/secrets/(\w+)", lambda m, b: f"deleted the secret {m[2]} from {m[1]}"),
    (r"POST /api/projects/([a-z0-9-]+)/runs", lambda m, b: f"started a run of {m[1]}"),
    (r"POST /api/projects/([a-z0-9-]+)/runs/(\d+)/stop", lambda m, b: f"stopped run {m[2]} of {m[1]}"),
    (r"POST /api/projects/([a-z0-9-]+)/explore", lambda m, b: f"started exploring {b.get('url', '')} for {m[1]}"),
    (r"POST /api/projects/([a-z0-9-]+)/generate", lambda m, b: f"asked the AI to write tests for {m[1]}"),
    (r"POST /api/projects/([a-z0-9-]+)/runs/(\d+)/(accept-visual|jira|slack)",
     lambda m, b: {"accept-visual": "accepted the new look", "jira": "filed Jira issues",
                   "slack": "posted to Slack"}[m[3]] + f" from run {m[2]} of {m[1]}"),
]


def _describe(method: str, path: str, body: dict) -> str:
    route = f"{method} {path}"
    for pattern, words in _ACTIONS:
        if match := re.fullmatch(pattern, route):
            return words(match, body)
    return route  # a change with no wording yet: the route, which holds no values


def _admin(user: dict) -> None:
    if not user.get("admin"):
        raise HttpError(403, "only an admin can do that")


def _owner(user: dict) -> None:
    if not user.get("owner"):
        raise HttpError(403, "only the owner can do that")


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
                    self._json(200, {"user": user}, {"Set-Cookie": app.cookie(token, secure=self._https())})
                elif path == "/healthz" and method == "GET":
                    self._json(*app.health())
                elif join := re.fullmatch(r"/api/join/([A-Za-z0-9_-]{20,100})", path):
                    if method not in ("GET", "POST"):
                        raise HttpError(404, "not found")
                    payload, new_token = app.join(join[1], method, body)
                    headers = {"Set-Cookie": app.cookie(new_token, secure=self._https())} if new_token else None
                    if new_token:
                        app.store.log("joined with an invite link", user=payload["user"], ip=self._ip())
                    self._json(201 if new_token else 200, payload, headers)
                elif reset := re.fullmatch(r"/api/reset/([A-Za-z0-9_-]{20,100})", path):
                    if method not in ("GET", "POST"):
                        raise HttpError(404, "not found")
                    self._json(200, app.reset(reset[1], method, body, self._ip()))
                elif path == "/api/logout" and method == "POST":
                    if user is not None:
                        app.store.log("logged out", user=user, ip=self._ip())
                    app.store.end_session(token)
                    self._json(200, {"ok": True}, {"Set-Cookie": app.cookie("", max_age=0, secure=self._https())})
                elif path.startswith("/api/"):
                    words = app.describe(method, path, body) if method != "GET" and user else ""
                    status, payload = app.api(method, path, body, user)
                    if words:  # logged only once the change went through
                        app.store.log(words, user=user, ip=self._ip())
                    self._json(status, payload)
                elif path.startswith("/files/") and method == "GET":
                    target = app.file(path, user)
                    self._raw(200, target.read_bytes(), CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
                              REPORT_CSP)
                elif method == "GET":
                    self._static(path)
                else:
                    raise HttpError(404, "not found")
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message, **exc.extra})
            except Busy as exc:
                self._json(409, {"error": str(exc)})
            except StoreError as exc:
                self._json(400, {"error": str(exc)})

        def _check_change(self) -> None:
            if self.headers.get("X-Nightshift") != "1":
                raise HttpError(403, "missing the X-Nightshift header")
            origin = (self.headers.get("Origin") or "").rstrip("/")
            if origin:
                host = self.headers.get("Host", "")
                # This site is whatever host the browser asked for, over http or https (behind an HTTPS
                # tunnel the browser says https://<tunnel host> while the app speaks plain http), and
                # the public URL. Both found in use: with a tunnel every change was refused, and with
                # --public-url set, so was every change made on the laptop itself at 127.0.0.1.
                allowed = {f"http://{host}", f"https://{host}", *([app.public_url] if app.public_url else [])}
                if origin not in allowed:
                    raise HttpError(403, "this request came from another site")

        def _https(self) -> bool:
            # Caddy and Cloudflare's tunnel say so when the browser used HTTPS; the app itself speaks http.
            if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
                return True
            return app.secure and self.headers.get("Host", "") == urlsplit(app.public_url).netloc

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
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise HttpError(400, "bad Content-Length") from None
            if length > MAX_BODY:
                # Read (and drop) a moderately oversized body so the client gets the 413 instead of a
                # reset connection; past DRAIN_LIMIT, just close: nobody legitimately sends that much.
                self.close_connection = True
                if length <= DRAIN_LIMIT:
                    while length > 0:
                        length -= len(self.rfile.read(min(length, 65_536)) or b"x" * length)
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
