"""The hosted app's records, in one SQLite file: workspaces, users, login sessions, invites,
projects and runs.

Each QA agency has a workspace of its own: its staff, and its clients as projects, each with
its own tests, saved paths, secrets, schedule and run history. Nobody sees another workspace's
projects. The owner (the first user) runs the deployment, makes a workspace for each agency and
invites its first admin; each workspace's admins invite their own staff. Files (specs,
recordings, reports) live on disk under the data folder; this file only indexes them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from ..totp import totp
from .vault import vault

SESSION_DAYS = 14
INVITE_DAYS = 7
RESET_HOURS = 24
AUDIT_DAYS = 365  # how long the activity log keeps an event
MIN_PASSWORD = 10
FIRST_WORKSPACE = 1  # the owner's own; everything made before workspaces existed belongs to it

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    admin INTEGER NOT NULL DEFAULT 0,      -- manages its workspace: clients, team, secrets
    created TEXT NOT NULL,
    workspace_id INTEGER NOT NULL DEFAULT 1,
    owner INTEGER NOT NULL DEFAULT 0       -- runs the deployment: makes workspaces for agencies
);
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,       -- like sessions, only the hash: the link is shown once
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    admin INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',         -- who it is for, as its maker wrote it
    created_by INTEGER,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    client TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    nightly TEXT NOT NULL DEFAULT '',      -- "HH:MM" in the server's time zone, or '' for no schedule
    last_nightly TEXT NOT NULL DEFAULT '', -- the date of the last nightly run, so it runs once a day
    created TEXT NOT NULL,
    targets TEXT NOT NULL DEFAULT 'chrome', -- where its tests run: chrome, firefox, safari, iphone, android
    workspace_id INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL,                 -- manual | nightly | explore
    status TEXT NOT NULL,                  -- queued | running | done | failed | stopped
    queued REAL NOT NULL,
    started REAL,
    finished REAL,
    run_dir TEXT NOT NULL DEFAULT '',      -- the `nightshift run` folder, relative to the project's folder
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    flaky INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    user_id INTEGER,
    params TEXT NOT NULL DEFAULT ''        -- JSON: an exploration's URL, steps and focus
);
CREATE TABLE IF NOT EXISTS resets (
    token_hash TEXT PRIMARY KEY,           -- a password reset link; like invites, only the hash
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    workspace_id INTEGER,                  -- whose activity log it is in; NULL for the owner's deployment events
    email TEXT NOT NULL DEFAULT '',        -- who did it (as typed, for a failed login)
    action TEXT NOT NULL,                  -- what, in words: "set secret SHOP_PASSWORD on acme". Never a value.
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_by_workspace ON audit (workspace_id, at);
"""

_NIGHTLY_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class StoreError(ValueError):
    """A request the store refuses, with a message fit to show the user."""


def hash_password(password: str) -> str:
    """scrypt, from the standard library: slow on purpose, so a stolen database is slow to crack."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        again = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                               dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(again.hex(), digest)


# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "project"


TARGET_NAMES = ("chrome", "firefox", "safari", "iphone", "android")


def check_targets(value: str) -> str:
    chosen = [t.strip().lower() for t in str(value).split(",") if t.strip()]
    unknown = [t for t in chosen if t not in TARGET_NAMES]
    if unknown or not chosen:
        raise StoreError(f"pick where the tests run from: {', '.join(TARGET_NAMES)}")
    return ",".join(t for t in TARGET_NAMES if t in chosen)  # one order, whatever order they came in


def check_nightly(value: str) -> str:
    value = (value or "").strip()
    if value and not _NIGHTLY_RE.fullmatch(value):
        raise StoreError('the nightly time must look like 02:30 (24-hour), or be empty for no schedule')
    return value


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            # Databases made before explore runs existed get the new column.
            columns = [row[1] for row in self._db.execute("PRAGMA table_info(runs)")]
            if "params" not in columns:
                self._db.execute("ALTER TABLE runs ADD COLUMN params TEXT NOT NULL DEFAULT ''")
            if "targets" not in [row[1] for row in self._db.execute("PRAGMA table_info(projects)")]:
                self._db.execute("ALTER TABLE projects ADD COLUMN targets TEXT NOT NULL DEFAULT 'chrome'")
            # Databases made before workspaces: everyone and everything joins the first workspace, and
            # the first admin becomes the owner.
            if "workspace_id" not in [row[1] for row in self._db.execute("PRAGMA table_info(projects)")]:
                self._db.execute("ALTER TABLE projects ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1")
            user_columns = [row[1] for row in self._db.execute("PRAGMA table_info(users)")]
            if "workspace_id" not in user_columns:
                self._db.execute("ALTER TABLE users ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1")
            if "owner" not in user_columns:
                self._db.execute("ALTER TABLE users ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
            # Two-factor login: the authenticator secret (encrypted, vault.py), one being set up, and the
            # last code's time step, so a code can't be used twice.
            for column, kind in (("totp_secret", "TEXT NOT NULL DEFAULT ''"), ("totp_pending", "TEXT NOT NULL DEFAULT ''"),
                                 ("totp_last", "INTEGER NOT NULL DEFAULT 0")):
                if column not in user_columns:
                    self._db.execute(f"ALTER TABLE users ADD COLUMN {column} {kind}")
            self._db.execute("DELETE FROM audit WHERE at < ?", (time.time() - AUDIT_DAYS * 86400,))
            if not self._db.execute("SELECT id FROM workspaces WHERE id = ?", (FIRST_WORKSPACE,)).fetchone():
                self._db.execute("INSERT INTO workspaces (id, name, created) VALUES (?, 'My agency', ?)",
                                 (FIRST_WORKSPACE, _now()))
            if not self._db.execute("SELECT id FROM users WHERE owner = 1").fetchone():
                self._db.execute("UPDATE users SET owner = 1 WHERE id = (SELECT MIN(id) FROM users WHERE admin = 1)")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    def _write(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, args).lastrowid

    # --- users and sessions -------------------------------------------------------

    def add_user(self, email: str, name: str, password: str, admin: bool = False,
                 workspace_id: int = FIRST_WORKSPACE) -> int:
        email = _check_new_user(self, email, password)
        # The very first user sets the deployment up, so they own it and administer the first workspace.
        first = self._one("SELECT id FROM users LIMIT 1") is None
        try:
            return self._write("INSERT INTO users (email, name, password_hash, admin, created, workspace_id, owner) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)", (email, name.strip(), hash_password(password),
                                                                int(admin or first), _now(), workspace_id, int(first)))
        except sqlite3.IntegrityError:  # the same email, added a moment ago by another request
            raise StoreError("a user with that email already exists") from None

    def check_login(self, email: str, password: str) -> dict | None:
        user = self._one("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
        ok = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
        return _public_user(user) if user and ok else None

    def set_password(self, user_id: int, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise StoreError(f"the password must be at least {MIN_PASSWORD} characters")
        self._write("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        self._write("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # log out everywhere else

    def users(self, workspace_id: int | None = None) -> list[dict]:
        if workspace_id is None:
            return [_public_user(u) for u in self._rows("SELECT * FROM users ORDER BY email")]
        return [_public_user(u) for u in self._rows("SELECT * FROM users WHERE workspace_id = ? ORDER BY email",
                                                    (workspace_id,))]

    def delete_user(self, user_id: int, workspace_id: int | None = None) -> None:
        user = self._one("SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None or (workspace_id is not None and user["workspace_id"] != workspace_id):
            raise StoreError("no such user")
        if user["owner"]:
            raise StoreError("the owner can't be removed")
        admins = self._rows("SELECT id FROM users WHERE admin = 1 AND workspace_id = ?", (user["workspace_id"],))
        if [a["id"] for a in admins] == [user_id]:
            raise StoreError("that is the only admin; make someone else an admin first")
        self._write("DELETE FROM users WHERE id = ?", (user_id,))

    # --- workspaces and invites -----------------------------------------------------

    def add_workspace(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise StoreError("give the agency's name")
        workspace_id = self._write("INSERT INTO workspaces (name, created) VALUES (?, ?)", (name[:80], _now()))
        return self.workspace(workspace_id)

    def workspace(self, workspace_id: int) -> dict | None:
        return self._one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))

    def workspaces(self) -> list[dict]:
        """Every workspace with how big it is. Names and counts only: the owner doesn't see inside."""
        return self._rows("SELECT workspaces.*, "
                          "(SELECT COUNT(*) FROM users WHERE users.workspace_id = workspaces.id) AS members, "
                          "(SELECT COUNT(*) FROM projects WHERE projects.workspace_id = workspaces.id) AS clients, "
                          "(SELECT COUNT(*) FROM invites WHERE invites.workspace_id = workspaces.id AND invites.expires > ?) "
                          "AS pending FROM workspaces ORDER BY id", (time.time(),))

    def rename_workspace(self, workspace_id: int, name: str) -> dict:
        if not name.strip():
            raise StoreError("give the workspace a name")
        self._write("UPDATE workspaces SET name = ? WHERE id = ?", (name.strip()[:80], workspace_id))
        return self.workspace(workspace_id)

    def new_invite(self, workspace_id: int, admin: bool = False, note: str = "", created_by: int | None = None,
                   days: float = INVITE_DAYS) -> str:
        """A link token for joining a workspace: random, single use, gone in a week. Only its hash is
        kept, so the link can be copied when it is made and never again."""
        if self.workspace(workspace_id) is None:
            raise StoreError("no such workspace")
        token = secrets.token_urlsafe(32)
        self._write("DELETE FROM invites WHERE expires < ?", (time.time(),))
        self._write("INSERT INTO invites (token_hash, workspace_id, admin, note, created_by, expires) VALUES (?, ?, ?, ?, ?, ?)",
                    (_token_hash(token), workspace_id, int(admin), note.strip()[:120], created_by,
                     time.time() + days * 86400))
        return token

    def invite(self, token: str) -> dict | None:
        """The live invite behind a link, with its workspace's name; None if unknown, used or expired."""
        if not token:
            return None
        return self._one("SELECT invites.id, invites.workspace_id, invites.admin, invites.note, invites.expires, "
                         "workspaces.name AS workspace FROM invites JOIN workspaces ON workspaces.id = invites.workspace_id "
                         "WHERE invites.token_hash = ? AND invites.expires > ?", (_token_hash(token), time.time()))

    def invites(self, workspace_id: int) -> list[dict]:
        return self._rows("SELECT id, admin, note, expires FROM invites WHERE workspace_id = ? AND expires > ? ORDER BY id",
                          (workspace_id, time.time()))

    def revoke_invite(self, invite_id: int, workspace_id: int) -> None:
        self._write("DELETE FROM invites WHERE id = ? AND workspace_id = ?", (invite_id, workspace_id))

    def accept_invite(self, token: str, email: str, name: str, password: str) -> int:
        """Join with an invite: the account goes in the invite's workspace, and the invite is spent."""
        email = _check_new_user(self, email, password)
        with self._lock:
            # Taken and deleted in one step, so a link opened twice at once makes one account.
            row = self._db.execute("SELECT * FROM invites WHERE token_hash = ? AND expires > ?",
                                   (_token_hash(token), time.time())).fetchone()
            if row is None or self._db.execute("DELETE FROM invites WHERE id = ?", (row["id"],)).rowcount != 1:
                raise StoreError("this invite link has expired or was already used; ask for a new one")
        return self.add_user(email, name, password, admin=bool(row["admin"]), workspace_id=row["workspace_id"])

    def new_session(self, user_id: int) -> str:
        """A random token for the cookie. Only its hash is stored, so the database alone can't log anyone in."""
        token = secrets.token_urlsafe(32)
        self._write("DELETE FROM sessions WHERE expires < ?", (time.time(),))  # expired logins don't pile up
        self._write("INSERT INTO sessions (token_hash, user_id, expires) VALUES (?, ?, ?)",
                    (_token_hash(token), user_id, time.time() + SESSION_DAYS * 86400))
        return token

    def user_for(self, token: str) -> dict | None:
        if not token:
            return None
        row = self._one("SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                        "WHERE sessions.token_hash = ? AND sessions.expires > ?", (_token_hash(token), time.time()))
        return _public_user(row) if row else None

    def end_session(self, token: str) -> None:
        self._write("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    # --- password reset links -------------------------------------------------------

    def new_reset(self, user_id: int) -> str:
        """A link for setting a new password, made by an admin for someone who forgot theirs (there is no
        email server to send one). Random, single use, a day long; only its hash is kept. A new link
        replaces that person's older ones."""
        if self._one("SELECT id FROM users WHERE id = ?", (user_id,)) is None:
            raise StoreError("no such user")
        token = secrets.token_urlsafe(32)
        self._write("DELETE FROM resets WHERE user_id = ? OR expires < ?", (user_id, time.time()))
        self._write("INSERT INTO resets (token_hash, user_id, expires) VALUES (?, ?, ?)",
                    (_token_hash(token), user_id, time.time() + RESET_HOURS * 3600))
        return token

    def reset_user(self, token: str) -> dict | None:
        """Whose password a live reset link is for; None if unknown, used or expired."""
        if not token:
            return None
        row = self._one("SELECT users.* FROM resets JOIN users ON users.id = resets.user_id "
                        "WHERE resets.token_hash = ? AND resets.expires > ?", (_token_hash(token), time.time()))
        return _public_user(row) if row else None

    def use_reset(self, token: str, password: str) -> dict:
        """Set the new password and spend the link. Every login of that person ends."""
        if len(password) < MIN_PASSWORD:
            raise StoreError(f"the password must be at least {MIN_PASSWORD} characters")
        with self._lock:
            row = self._db.execute("SELECT * FROM resets WHERE token_hash = ? AND expires > ?",
                                   (_token_hash(token), time.time())).fetchone()
            if row is None or self._db.execute("DELETE FROM resets WHERE token_hash = ?",
                                               (row["token_hash"],)).rowcount != 1:
                raise StoreError("this reset link has expired or was already used; ask an admin for a new one")
        self.set_password(row["user_id"], password)
        return _public_user(self._one("SELECT * FROM users WHERE id = ?", (row["user_id"],)))

    # --- two-factor login -------------------------------------------------------------

    def start_two_factor(self, user_id: int) -> str:
        """A new authenticator secret (base32), kept as pending until a code from it is confirmed."""
        secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
        self._write("UPDATE users SET totp_pending = ? WHERE id = ?", (vault().seal(secret), user_id))
        return secret

    def confirm_two_factor(self, user_id: int, code: str) -> None:
        row = self._one("SELECT totp_pending FROM users WHERE id = ?", (user_id,))
        if not row or not row["totp_pending"]:
            raise StoreError("start setting up two-factor login first")
        step = _code_step(vault().open(row["totp_pending"]), code, 0)
        if step is None:
            raise StoreError("that code is not right; check the time on your phone and try the newest code")
        self._write("UPDATE users SET totp_secret = totp_pending, totp_pending = '', totp_last = ? WHERE id = ?",
                    (step, user_id))

    def check_code(self, user_id: int, code: str) -> bool:
        """A code from the user's authenticator, each time step usable once (a seen code can't be replayed)."""
        with self._lock:
            row = self._db.execute("SELECT totp_secret, totp_last FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not row["totp_secret"]:
                return False
            step = _code_step(vault().open(row["totp_secret"]), code, row["totp_last"])
            if step is None:
                return False
            self._db.execute("UPDATE users SET totp_last = ? WHERE id = ?", (step, user_id))
            return True

    def two_factor_on(self, user_id: int) -> bool:
        row = self._one("SELECT totp_secret FROM users WHERE id = ?", (user_id,))
        return bool(row and row["totp_secret"])

    def stop_two_factor(self, user_id: int) -> None:
        self._write("UPDATE users SET totp_secret = '', totp_pending = '', totp_last = 0 WHERE id = ?", (user_id,))

    # --- the activity log ---------------------------------------------------------------

    def log(self, action: str, *, user: dict | None = None, email: str = "", workspace_id: int | None = None,
            ip: str = "") -> None:
        """One line in a workspace's activity log. Callers pass words, never a secret's value or a password."""
        if user is not None:
            email, workspace_id = user["email"], user["workspace_id"] if workspace_id is None else workspace_id
        self._write("INSERT INTO audit (at, workspace_id, email, action, ip) VALUES (?, ?, ?, ?, ?)",
                    (time.time(), workspace_id, email[:200], action[:300], ip[:64]))

    def events(self, workspace_id: int, limit: int = 300) -> list[dict]:
        return self._rows("SELECT at, email, action, ip FROM audit WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                          (workspace_id, limit))

    def workspace_of(self, email: str) -> int | None:
        row = self._one("SELECT workspace_id FROM users WHERE email = ?", (email.strip().lower(),))
        return row["workspace_id"] if row else None

    # --- projects -----------------------------------------------------------------

    def add_project(self, client: str, base_url: str = "", brand: str = "", nightly: str = "",
                    workspace_id: int = FIRST_WORKSPACE) -> dict:
        client = client.strip()
        if not client:
            raise StoreError("give the client's name")
        base_url = _check_url(base_url)
        nightly = check_nightly(nightly)
        slug, n = slugify(client), 2
        while self._one("SELECT id FROM projects WHERE slug = ?", (slug,)):
            slug, n = f"{slugify(client)}-{n}", n + 1
        self._write("INSERT INTO projects (slug, client, base_url, brand, nightly, last_nightly, created, workspace_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (slug, client, base_url, brand.strip(), nightly,
                                                        _skip_today(nightly), _now(), workspace_id))
        return self.project(slug)

    def projects(self, workspace_id: int | None = None) -> list[dict]:
        """One workspace's projects; all of them for the scheduler, which runs everyone's nightly tests."""
        if workspace_id is None:
            return self._rows("SELECT * FROM projects ORDER BY client")
        return self._rows("SELECT * FROM projects WHERE workspace_id = ? ORDER BY client", (workspace_id,))

    def project(self, slug: str) -> dict | None:
        return self._one("SELECT * FROM projects WHERE slug = ?", (slug,))

    def project_by_id(self, project_id: int) -> dict | None:
        return self._one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def update_project(self, slug: str, *, client: str | None = None, base_url: str | None = None,
                       brand: str | None = None, nightly: str | None = None, targets: str | None = None) -> dict:
        project = self.project(slug)
        if project is None:
            raise StoreError("no such project")
        fields = {}
        if client is not None:
            if not client.strip():
                raise StoreError("give the client's name")
            fields["client"] = client.strip()
        if base_url is not None:
            fields["base_url"] = _check_url(base_url)
        if brand is not None:
            fields["brand"] = brand.strip()
        if targets is not None:
            fields["targets"] = check_targets(targets)
        if nightly is not None and check_nightly(nightly) != project["nightly"]:
            fields["nightly"] = check_nightly(nightly)
            fields["last_nightly"] = _skip_today(fields["nightly"])
        if fields:
            sets = ", ".join(f"{key} = ?" for key in fields)
            self._write(f"UPDATE projects SET {sets} WHERE slug = ?", (*fields.values(), slug))
        return self.project(slug)

    def mark_nightly(self, project_id: int, day: str) -> None:
        self._write("UPDATE projects SET last_nightly = ? WHERE id = ?", (day, project_id))

    def delete_project(self, slug: str) -> None:
        """The project's rows; its runs go with it (ON DELETE CASCADE). Its files are the caller's."""
        project = self.project(slug)
        if project is None:
            raise StoreError("no such project")
        if self.active_run(project["id"]):
            raise StoreError("a run of this client is queued or running; stop it first")
        self._write("DELETE FROM projects WHERE id = ?", (project["id"],))

    def delete_workspace(self, workspace_id: int) -> list[str]:
        """A whole agency: its projects (and runs), people, invites and logins. Returns the projects'
        slugs, whose folders the caller removes. The owner's own workspace can't be deleted."""
        if workspace_id == FIRST_WORKSPACE or self.workspace(workspace_id) is None:
            raise StoreError("that workspace can't be deleted")
        slugs = [p["slug"] for p in self.projects(workspace_id)]
        if any(self.active_run(p["id"]) for p in self.projects(workspace_id)):
            raise StoreError("one of its clients has a run queued or running; stop it first")
        with self._lock:
            self._db.execute("DELETE FROM projects WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM users WHERE workspace_id = ?", (workspace_id,))  # sessions and resets cascade
            self._db.execute("DELETE FROM invites WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM audit WHERE workspace_id = ?", (workspace_id,))
            self._db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        return slugs

    def counts(self) -> dict:
        """For the health check: how much is waiting, nothing about whose."""
        row = self._one("SELECT SUM(status = 'queued') AS queued, SUM(status = 'running') AS running FROM runs")
        return {"queued": int(row["queued"] or 0), "running": int(row["running"] or 0)}

    # --- runs ---------------------------------------------------------------------

    def add_run(self, project_id: int, trigger: str, user_id: int | None = None, params: dict | None = None) -> int:
        return self._write("INSERT INTO runs (project_id, trigger, status, queued, user_id, params) "
                           "VALUES (?, ?, 'queued', ?, ?, ?)",
                           (project_id, trigger, time.time(), user_id, json.dumps(params) if params else ""))

    def run(self, run_id: int) -> dict | None:
        return self._one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def runs(self, project_id: int, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM runs WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit))

    def active_run(self, project_id: int) -> dict | None:
        return self._one("SELECT * FROM runs WHERE project_id = ? AND status IN ('queued', 'running') ORDER BY id",
                         (project_id,))

    def next_queued(self) -> dict | None:
        """The oldest queued run, marked running in the same step so two workers never take it."""
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE runs SET status = 'running', started = ? WHERE id = ?", (time.time(), row["id"]))
            return {**dict(row), "status": "running"}

    def update_run(self, run_id: int, **fields) -> None:
        allowed = {"status", "finished", "run_dir", "passed", "failed", "flaky", "errors", "message"}
        if not fields or set(fields) - allowed:
            raise StoreError(f"cannot update {sorted(set(fields) - allowed)}")
        sets = ", ".join(f"{key} = ?" for key in fields)
        self._write(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))

    def interrupted(self) -> None:
        """Runs the server was in the middle of when it stopped: they will never finish."""
        self._write("UPDATE runs SET status = 'failed', message = 'the server restarted during this run', "
                    "finished = ? WHERE status = 'running'", (time.time(),))


def _public_user(row: dict | None) -> dict | None:
    return None if row is None else {"id": row["id"], "email": row["email"], "name": row["name"],
                                     "admin": bool(row["admin"]), "owner": bool(row["owner"]),
                                     "workspace_id": row["workspace_id"],
                                     "two_factor": bool(row.get("totp_secret"))}


def _code_step(secret: str, code: str, last_step: int) -> int | None:
    """The time step a 6-digit code belongs to (now, or one step either side for a slow phone clock),
    if it is newer than the last one used; None otherwise."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        return None
    now = int(time.time() // 30)
    for step in (now, now - 1, now + 1):
        if step > last_step and hmac.compare_digest(totp(secret, at=step * 30), code):
            return step
    return None


def _check_new_user(store: Store, email: str, password: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise StoreError("that is not an email address")
    if len(password) < MIN_PASSWORD:
        raise StoreError(f"the password must be at least {MIN_PASSWORD} characters")
    if store._one("SELECT id FROM users WHERE email = ?", (email,)):
        raise StoreError("a user with that email already exists")
    return email


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
        raise StoreError("the base URL must start with http:// or https://")
    return url


def _skip_today(nightly: str) -> str:
    """A schedule set after today's time starts tomorrow, instead of running the moment it is saved."""
    if nightly and datetime.now().strftime("%H:%M") >= nightly:
        return datetime.now().strftime("%Y-%m-%d")
    return ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
