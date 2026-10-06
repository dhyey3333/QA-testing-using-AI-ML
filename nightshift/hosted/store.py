"""The hosted app's records, in one SQLite file: users, login sessions, projects and runs.

One deployment serves one QA agency. Its staff log in; each client of the agency is a
project with its own tests, saved paths, secrets, schedule and run history. Files (specs,
recordings, reports) live on disk under the data folder; this file only indexes them.
"""

from __future__ import annotations

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

SESSION_DAYS = 14
MIN_PASSWORD = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    admin INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
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
    targets TEXT NOT NULL DEFAULT 'chrome' -- where its tests run: chrome, firefox, safari, iphone, android
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

    def add_user(self, email: str, name: str, password: str, admin: bool = False) -> int:
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise StoreError("that is not an email address")
        if len(password) < MIN_PASSWORD:
            raise StoreError(f"the password must be at least {MIN_PASSWORD} characters")
        if self._one("SELECT id FROM users WHERE email = ?", (email,)):
            raise StoreError("a user with that email already exists")
        return self._write("INSERT INTO users (email, name, password_hash, admin, created) VALUES (?, ?, ?, ?, ?)",
                           (email, name.strip(), hash_password(password), int(admin), _now()))

    def check_login(self, email: str, password: str) -> dict | None:
        user = self._one("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
        ok = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
        return _public_user(user) if user and ok else None

    def set_password(self, user_id: int, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise StoreError(f"the password must be at least {MIN_PASSWORD} characters")
        self._write("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        self._write("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # log out everywhere else

    def users(self) -> list[dict]:
        return [_public_user(u) for u in self._rows("SELECT * FROM users ORDER BY email")]

    def delete_user(self, user_id: int) -> None:
        admins = self._rows("SELECT id FROM users WHERE admin = 1")
        if [a["id"] for a in admins] == [user_id]:
            raise StoreError("that is the only admin; make someone else an admin first")
        self._write("DELETE FROM users WHERE id = ?", (user_id,))

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

    # --- projects -----------------------------------------------------------------

    def add_project(self, client: str, base_url: str = "", brand: str = "", nightly: str = "") -> dict:
        client = client.strip()
        if not client:
            raise StoreError("give the client's name")
        base_url = _check_url(base_url)
        nightly = check_nightly(nightly)
        slug, n = slugify(client), 2
        while self._one("SELECT id FROM projects WHERE slug = ?", (slug,)):
            slug, n = f"{slugify(client)}-{n}", n + 1
        self._write("INSERT INTO projects (slug, client, base_url, brand, nightly, last_nightly, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (slug, client, base_url, brand.strip(), nightly,
                                                     _skip_today(nightly), _now()))
        return self.project(slug)

    def projects(self) -> list[dict]:
        return self._rows("SELECT * FROM projects ORDER BY client")

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
                                     "admin": bool(row["admin"])}


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
