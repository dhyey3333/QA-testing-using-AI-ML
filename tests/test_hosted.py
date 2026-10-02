"""Phase 3, the hosted product: logins, a project per client, nightly runs, parallel runs,
and a client report for every run. Plus the model client's patience with a busy free plan."""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from nightshift import cli
from nightshift import model as model_module
from nightshift.hosted.files import ProjectFiles
from nightshift.hosted.jobs import Runner
from nightshift.hosted.server import App, serve
from nightshift.hosted.store import Store, StoreError
from nightshift.model import HttpModel, ModelConfig

ROOT = Path(__file__).resolve().parent.parent
ADMIN = ("admin@agency.test", "test-admin-password")  # fake, for these tests only
STAFF = ("staff@agency.test", "test-staff-password")
SPEC = "name: smoke\nurl: https://shop.example.test/\nsteps:\n  - open the shop\nexpect:\n  - the shop is shown\n"


@pytest.fixture
def hosted(tmp_path):
    store = Store(tmp_path / "nightshift.db")
    store.add_user(*ADMIN[:1], "Admin", ADMIN[1], admin=True)
    store.add_user(*STAFF[:1], "Staff", STAFF[1])
    runner = Runner(store, tmp_path, max_runs=1, parallel=1)
    server = serve(App(store, runner), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(base=f"http://127.0.0.1:{server.server_port}", store=store, runner=runner, data=tmp_path)
    runner.close()
    server.shutdown()
    server.server_close()
    store.close()


def client(hosted, who=None) -> httpx.Client:
    session = httpx.Client(base_url=hosted.base, headers={"X-Nightshift": "1"}, timeout=30)
    if who:
        response = session.post("/api/login", json={"email": who[0], "password": who[1]})
        assert response.status_code == 200, response.text
    return session


def test_passwords_are_stored_as_slow_hashes_and_a_new_one_logs_out_everywhere(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    user_id = store.add_user("a@agency.test", "A", "first-password-1")
    assert store.check_login("a@agency.test", "wrong-password-1") is None
    assert store.check_login("A@Agency.test", "first-password-1")["id"] == user_id
    token = store.new_session(user_id)
    assert store.user_for(token)["email"] == "a@agency.test"
    store.set_password(user_id, "second-password-2")
    assert store.user_for(token) is None
    store.close()
    raw = (tmp_path / "db.sqlite").read_bytes()
    assert b"first-password-1" not in raw and b"second-password-2" not in raw and token.encode() not in raw
    with pytest.raises(StoreError, match="at least 10"):
        Store(tmp_path / "db.sqlite").add_user("b@agency.test", "B", "short")


def test_everything_needs_a_login(hosted):
    anonymous = client(hosted)
    assert anonymous.get("/api/projects").status_code == 401
    assert anonymous.get("/files/acme/1/output.log").status_code == 401
    assert anonymous.get("/").status_code == 200  # the login page itself


def test_changes_need_the_header_and_this_sites_origin(hosted):
    admin = client(hosted, ADMIN)
    bare = httpx.post(f"{hosted.base}/api/projects", json={"client": "Acme"}, cookies=admin.cookies)
    assert bare.status_code == 403  # a form on another site can't send X-Nightshift
    forged = admin.post("/api/projects", json={"client": "Acme"}, headers={"Origin": "https://evil.example"})
    assert forged.status_code == 403
    assert admin.post("/api/projects", json={"client": "Acme"}).status_code == 201


def test_wrong_passwords_lock_an_address_out(hosted):
    guest = client(hosted)
    for _ in range(5):
        assert guest.post("/api/login", json={"email": ADMIN[0], "password": "not-the-password"}).status_code == 401
    assert guest.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 429


def test_staff_run_tests_but_only_admins_manage_projects_users_and_secrets(hosted):
    admin, staff = client(hosted, ADMIN), client(hosted, STAFF)
    slug = admin.post("/api/projects", json={"client": "Acme Retail"}).json()["project"]["slug"]
    assert staff.post("/api/projects", json={"client": "Other"}).status_code == 403
    assert staff.put(f"/api/projects/{slug}", json={"nightly": "02:00"}).status_code == 403
    assert staff.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "x"}).status_code == 403
    assert staff.get("/api/users").status_code == 403
    assert staff.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC}).status_code == 200


def test_secrets_are_write_only(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC})
    assert admin.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "s3cret-value"}).status_code == 200
    view = admin.get(f"/api/projects/{slug}").text
    assert "SHOP_PASSWORD" in view and "s3cret-value" not in view
    assert admin.put(f"/api/projects/{slug}/secrets/bad-name", json={"value": "x"}).status_code == 404
    secrets_file = ProjectFiles(hosted.data, slug).secrets_file
    if os.name != "nt":
        assert secrets_file.stat().st_mode & 0o077 == 0  # readable by the app's user only


def test_a_spec_must_load_before_it_replaces_anything(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    bad = admin.put(f"/api/projects/{slug}/specs/broken", json={"text": "url: not-a-url\nsteps: [a]\nexpect: [b]\n"})
    assert bad.status_code == 400 and "url must be" in bad.json()["error"]
    assert admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC}).status_code == 200
    twin = admin.put(f"/api/projects/{slug}/specs/smoke-copy", json={"text": SPEC})
    assert twin.status_code == 400 and "already has the name" in twin.json()["error"]
    assert admin.get(f"/api/projects/{slug}").json()["specs"] == ["smoke"]


def test_run_files_never_leave_their_run_folder(tmp_path):
    files = ProjectFiles(tmp_path, "acme")
    (files.root / "secrets.env").write_text("X=1\n", encoding="utf-8")
    files.run_folder(1).mkdir(parents=True)
    (files.run_folder(1) / "output.log").write_text("ok", encoding="utf-8")
    assert files.run_file(1, "output.log").read_text(encoding="utf-8") == "ok"
    for escape in ("../../secrets.env", "../1/../../secrets.env", "/etc/passwd"):
        with pytest.raises(StoreError):
            files.run_file(1, escape)


def test_nightly_runs_are_queued_once_a_day_at_their_time(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    runner = Runner(store, tmp_path)
    project = store.add_project("Acme", nightly="02:30")
    store.mark_nightly(project["id"], "")
    (ProjectFiles(tmp_path, project["slug"]).specs / "smoke.yaml").write_text(SPEC, encoding="utf-8")
    assert runner.tick(datetime(2031, 1, 5, 2, 29)) == []
    assert len(runner.tick(datetime(2031, 1, 5, 2, 31))) == 1
    assert runner.tick(datetime(2031, 1, 5, 23, 0)) == []  # once that day
    store.update_run(store.runs(project["id"])[0]["id"], status="done")
    assert len(runner.tick(datetime(2031, 1, 6, 2, 30))) == 1  # and again the next night
    store.close()


def test_a_run_from_the_web_app_ends_with_a_client_report(hosted, shop, base_url):
    admin = client(hosted, ADMIN)
    project = admin.post("/api/projects", json={"client": "Kulhad Test Client", "base_url": base_url,
                                                 "brand": "Test QA Co"}).json()["project"]
    slug = project["slug"]
    spec = (ROOT / "specs" / "api" / "api-order.yaml").read_text(encoding="utf-8")
    assert admin.put(f"/api/projects/{slug}/specs/api-order", json={"text": spec}).status_code == 200
    hosted.runner.start()
    started = admin.post(f"/api/projects/{slug}/runs")
    assert started.status_code == 201, started.text
    assert admin.post(f"/api/projects/{slug}/runs").status_code == 409  # one run per project at a time

    deadline = time.time() + 180
    while (run := admin.get(f"/api/projects/{slug}").json()["runs"][0])["status"] in ("queued", "running"):
        assert time.time() < deadline, "the run did not finish"
        time.sleep(1)
    assert (run["status"], run["passed"], run["failed"]) == ("done", 1, 0), run
    report = admin.get(run["links"]["client_report"])
    assert report.status_code == 200 and "Kulhad Test Client" in report.text and "Test QA Co" in report.text
    assert client(hosted).get(run["links"]["client_report"]).status_code == 401


def test_parallel_runs_replay_saved_paths_in_separate_browsers(shop, base_url, tmp_path, monkeypatch, capsys):
    # A model address where nothing listens: any model call fails, so a pass here used none.
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:9/v1")
    specs = [str(ROOT / "specs" / f"{name}.yaml") for name in ("login", "add-to-cart", "search", "checkout")]
    code = cli.main(["run", *specs, "--recordings", str(ROOT / "ci" / "recordings"), "--no-record", "--retries", "0",
                     "--parallel", "3", "--base-url", base_url, "--out", str(tmp_path), "--quiet"])
    assert code == 0, capsys.readouterr().out
    summary = json.loads(next(tmp_path.glob("*/summary.json")).read_text(encoding="utf-8"))
    assert sorted(row["spec"] for row in summary) == ["add-to-cart", "checkout", "login", "search"]
    assert all(row["verdict"] == "pass" and row["model_calls"] == 0 for row in summary)


def test_a_relative_data_folder_still_works_from_inside_a_project(tmp_path, monkeypatch):
    # Found on first use: `--data hosted-data`, and every run said "specs: no such file or folder".
    monkeypatch.chdir(tmp_path)
    store = Store(Path("hosted-data") / "nightshift.db")
    runner = Runner(store, Path("hosted-data"))
    project = store.add_project("Acme")
    files = runner.files(project)
    command = runner.command(project, files, files.run_folder(1))
    assert files.specs.is_absolute() and str(files.specs) in command
    store.close()


def test_a_busy_model_is_asked_again_instead_of_failing_the_run(monkeypatch):
    answers = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(503),
                    httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})])
    model = HttpModel(ModelConfig(base_url="http://model.test/v1", model="m"))
    model._client = httpx.Client(transport=httpx.MockTransport(lambda request: next(answers)))
    waits = []
    monkeypatch.setattr(model_module.time, "sleep", waits.append)
    assert model.ask("system", "user") == {"ok": 1}
    assert waits == [1.0, 4.0]  # the server's Retry-After, then a growing back-off


def test_the_database_has_no_plain_session_tokens(hosted):
    admin = client(hosted, ADMIN)
    token = admin.cookies.get("ns_session")
    rows = sqlite3.connect(hosted.data / "nightshift.db").execute("SELECT token_hash FROM sessions").fetchall()
    assert token and all(token not in row[0] for row in rows)
