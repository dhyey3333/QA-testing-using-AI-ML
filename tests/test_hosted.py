"""Phase 3, the hosted product: logins, a project per client, nightly runs, parallel runs,
and a client report for every run. Plus the model client's patience with a busy free plan."""

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

from nightshift import cli
from nightshift import model as model_module
from nightshift.hosted.files import ProjectFiles
from nightshift.hosted.jobs import Runner
from nightshift.hosted.server import App, serve
from nightshift.hosted.store import Store, StoreError
from nightshift.model import HttpModel, ModelConfig
from nightshift.spec import load_spec

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


FAKE_EXPLORE = (
    "import json, pathlib, sys; p = pathlib.Path(sys.argv[1]) / 'explore-1'; p.mkdir(parents=True); "
    "(p / 'explore.json').write_text(json.dumps({'findings': [{'severity': 'bug'}, {'severity': 'suspected'}, "
    "{'severity': 'warning'}, {'severity': 'warning'}]})); (p / 'report.html').write_text('<h1>explored</h1>'); "
    "(p / 'findings.md').write_text('- a bug')"
)


def test_exploring_a_website_from_the_web_app_needs_no_tests(hosted):
    seen = {}

    def fake_explore(params, out):  # stands in for `nightshift explore`, which needs a model
        seen.update(params)
        return [sys.executable, "-c", FAKE_EXPLORE, str(out)]

    hosted.runner.explore_command = fake_explore
    staff = client(hosted, STAFF)
    slug = client(hosted, ADMIN).post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    assert staff.post(f"/api/projects/{slug}/explore", json={"url": "ftp://academybugs.com"}).status_code == 400
    assert staff.post(f"/api/projects/{slug}/explore", json={"url": "https://academybugs.com/", "steps": 99}).status_code == 400
    started = staff.post(f"/api/projects/{slug}/explore",
                         json={"url": "https://academybugs.com/", "steps": "20", "focus": "the cart"})
    assert started.status_code == 201, started.text
    hosted.runner.start()
    deadline = time.time() + 60
    while (run := staff.get(f"/api/projects/{slug}").json()["runs"][0])["status"] in ("queued", "running"):
        assert time.time() < deadline
        time.sleep(0.5)
    assert seen == {"url": "https://academybugs.com/", "steps": 20, "focus": "the cart"}
    assert (run["status"], run["trigger"], run["target"]) == ("done", "explore", "https://academybugs.com/")
    assert run["message"] == "1 bug proven, 1 suspected, 2 warnings"
    assert staff.get(run["links"]["report"]).text == "<h1>explored</h1>" and "findings" in run["links"]


def test_a_database_from_before_explore_runs_gets_the_new_column(tmp_path):
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, project_id INTEGER, trigger TEXT, status TEXT, queued REAL,"
                " started REAL, finished REAL, run_dir TEXT DEFAULT '', passed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,"
                " flaky INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, message TEXT DEFAULT '', user_id INTEGER)")
    old.commit()
    old.close()
    store = Store(tmp_path / "old.db")
    project = store.add_project("Acme")
    run_id = store.add_run(project["id"], "explore", params={"url": "https://x.test/"})
    assert json.loads(store.run(run_id)["params"]) == {"url": "https://x.test/"}
    store.close()


# --- no-YAML tests: the form, and drafts the AI writes ----------------------------------

FORM = {"url": "https://shop.example.test/", "steps": "log in with the test account\n- add the Blue Top to the cart",
        "expect": "the cart lists the Blue Top", "data": "email = qa@example.test\npassword: ${SHOP_PASSWORD}",
        "max_steps": "20", "js_errors_warn": True}


def test_a_test_written_in_the_form_becomes_a_spec_and_reads_back(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    assert admin.put(f"/api/projects/{slug}/specs/checkout", json={"form": FORM}).status_code == 200
    spec = load_spec(ProjectFiles(hosted.data, slug).specs / "checkout.yaml", expand_env=False)
    assert spec.steps == ("log in with the test account", "add the Blue Top to the cart")
    assert spec.expect == ("the cart lists the Blue Top",) and spec.js_errors == "warn" and spec.max_steps == 20
    assert spec.data == {"email": "qa@example.test", "password": "${SHOP_PASSWORD}"}
    form = admin.get(f"/api/projects/{slug}/specs/checkout").json()["form"]
    assert form["steps"] == "log in with the test account\nadd the Blue Top to the cart"
    assert form["data"] == "email = qa@example.test\npassword = ${SHOP_PASSWORD}" and form["js_errors_warn"] is True


def test_the_form_keeps_what_it_does_not_edit_and_turns_one_sentence_into_a_goal(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    files = ProjectFiles(hosted.data, slug)
    files.save_spec("signup", SPEC + "session_from: login\n")
    one_line = {"url": "https://shop.example.test/", "steps": "subscribe to the newsletter", "expect": ""}
    assert admin.put(f"/api/projects/{slug}/specs/signup", json={"form": one_line}).status_code == 200
    spec = load_spec(files.specs / "signup.yaml", expand_env=False)
    assert spec.session_from == "login" and spec.expect == ("the page shows this was done: subscribe to the newsletter",)
    two_lines = {**one_line, "steps": "open the shop\nsubscribe"}
    response = admin.put(f"/api/projects/{slug}/specs/signup", json={"form": two_lines})
    assert response.status_code == 400 and "what should happen" in response.json()["error"]
    bad_data = {**FORM, "data": "just a sentence"}
    assert "name = value" in admin.put(f"/api/projects/{slug}/specs/x", json={"form": bad_data}).json()["error"]


def test_a_run_that_would_stop_on_a_missing_secret_is_refused_with_the_reason(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    admin.put(f"/api/projects/{slug}/specs/checkout", json={"form": FORM})
    refused = admin.post(f"/api/projects/{slug}/runs")
    assert refused.status_code == 400 and "checkout needs SHOP_PASSWORD" in refused.json()["error"]
    assert admin.get(f"/api/projects/{slug}").json()["missing"] == [{"test": "checkout", "secret": "SHOP_PASSWORD"}]
    admin.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "x"})
    assert admin.get(f"/api/projects/{slug}").json()["missing"] == []


def test_ai_written_tests_wait_as_drafts_until_a_person_accepts_them(hosted):
    def fake_generate(params, files, out):  # stands in for `nightshift generate`, which needs a model
        draft = files.drafts / "buy-a-top.yaml"
        text = "# Review: the price may differ\n" + SPEC.replace("name: smoke", "name: buy-a-top") + \
            "data:\n  password: ${NS_PASSWORD}\n"
        return [sys.executable, "-c", f"import pathlib; pathlib.Path(r'{draft}').write_text({text!r})"]

    hosted.runner.generate_command = fake_generate
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    assert admin.post(f"/api/projects/{slug}/generate", json={"url": "https://x.test/", "steps": 0}).status_code == 400
    assert admin.post(f"/api/projects/{slug}/generate", json={"url": "https://x.test/", "count": 3}).status_code == 201
    hosted.runner.start()
    deadline = time.time() + 60
    while (run := admin.get(f"/api/projects/{slug}").json()["runs"][0])["status"] in ("queued", "running"):
        assert time.time() < deadline
        time.sleep(0.5)
    assert (run["status"], run["message"]) == ("done", "wrote 1 draft test: review them under Tests"), run
    view = admin.get(f"/api/projects/{slug}").json()
    assert view["specs"] == [] and view["drafts"] == [
        {"name": "buy-a-top", "review": ["the price may differ"], "needs": ["NS_PASSWORD"]}]
    edited = {"url": "https://x.test/", "steps": "buy a top", "expect": "the order is placed"}
    assert admin.put(f"/api/projects/{slug}/drafts/buy-a-top", json={"form": edited}).status_code == 200
    assert admin.post(f"/api/projects/{slug}/drafts/buy-a-top/accept").status_code == 200
    view = admin.get(f"/api/projects/{slug}").json()
    assert view["specs"] == ["buy-a-top"] and view["drafts"] == []
    files = ProjectFiles(hosted.data, slug)
    files.save_draft("buy-a-top", SPEC.replace("name: smoke", "name: buy-a-top"))
    clash = admin.post(f"/api/projects/{slug}/drafts/buy-a-top/accept")
    assert clash.status_code == 400 and "already a test" in clash.json()["error"]


# --- a run's details: why each test failed, the visual check, Jira and Slack ----------------

def _fake_run(hosted, slug, visual_status="changed"):
    """A finished run on disk with one passing test whose screen changed."""
    project = hosted.store.project(slug)
    files = ProjectFiles(hosted.data, slug)
    run_id = hosted.store.add_run(project["id"], "manual")
    folder = files.run_folder(run_id) / "20310105-020000"
    test_dir = folder / "checkout"
    test_dir.mkdir(parents=True)
    (test_dir / "visual-current.png").write_bytes(b"\x89PNG today")
    result = {"spec": "checkout", "url": "https://shop.test/", "model": "m", "verdict": "pass", "out_dir": str(test_dir),
              "browser": "chromium", "viewport": "1280x800", "steps": [], "checks": [],
              "visual": {"status": visual_status, "what": "the prices are missing", "changed": 0.01,
                         "files": {"current": "visual-current.png", "sides": "visual-sides.jpg"}}}
    (test_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps([{"spec": "checkout", "verdict": "pass", "out_dir": str(test_dir)}]),
                                         encoding="utf-8")
    hosted.store.update_run(run_id, status="done", run_dir="20310105-020000", passed=1)
    return run_id, files


def test_a_runs_page_lists_each_test_with_its_visual_check_and_a_new_look_can_be_approved(hosted):
    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
    run_id, files = _fake_run(hosted, slug, "visual bug")
    detail = admin.get(f"/api/projects/{slug}/runs/{run_id}").json()
    test = detail["tests"][0]
    assert (test["spec"], test["verdict"], test["visual"]["status"]) == ("checkout", "pass", "visual bug")
    assert test["visual"]["picture"].endswith("/checkout/visual-sides.jpg") and detail["jira"] is False
    assert admin.post(f"/api/projects/{slug}/runs/{run_id}/accept-visual").json() == {"accepted": 1}
    assert (files.root / "visual" / "checkout@chromium-1280x800.png").read_bytes() == b"\x89PNG today"


def test_jira_and_slack_use_the_projects_secrets(hosted):
    received = []

    class Webhook(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server's name)
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    hook = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
    threading.Thread(target=hook.serve_forever, daemon=True).start()
    try:
        admin = client(hosted, ADMIN)
        slug = admin.post("/api/projects", json={"client": "Acme"}).json()["project"]["slug"]
        run_id, _ = _fake_run(hosted, slug)
        refused = admin.post(f"/api/projects/{slug}/runs/{run_id}/jira")
        assert refused.status_code == 400 and "JIRA_URL" in refused.json()["error"]
        assert admin.post(f"/api/projects/{slug}/runs/{run_id}/slack").status_code == 400
        admin.put(f"/api/projects/{slug}/secrets/SLACK_WEBHOOK_URL", json={"value": f"http://127.0.0.1:{hook.server_port}/hook"})
        assert admin.post(f"/api/projects/{slug}/runs/{run_id}/slack").status_code == 200
        assert received and "1 pass" in received[0]["text"]
    finally:
        hook.shutdown()
        hook.server_close()


# --- found in the bug hunt -----------------------------------------------------------------

def test_behind_an_https_tunnel_changes_work_without_a_public_url(hosted):
    # A quick tunnel serves https://<host> while the app speaks http: every change used to be refused.
    admin = client(hosted, ADMIN)
    host = hosted.base.removeprefix("http://")
    assert admin.post("/api/projects", json={"client": "Tunnel Co"}, headers={"Origin": f"https://{host}"}).status_code == 201
    assert admin.post("/api/projects", json={"client": "Evil Co"},
                      headers={"Origin": "https://evil.example"}).status_code == 403


def test_an_oversized_upload_gets_a_clean_413_not_a_dropped_connection(hosted):
    admin = client(hosted, ADMIN)
    response = admin.post("/api/projects", content=b"x" * 400_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"] == "too large"


def test_a_double_click_on_run_now_starts_one_run(hosted):
    import concurrent.futures

    admin = client(hosted, ADMIN)
    slug = admin.post("/api/projects", json={"client": "Race Co"}).json()["project"]["slug"]
    admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC})
    cookies = admin.cookies

    def start(_):
        return httpx.post(f"{hosted.base}/api/projects/{slug}/runs", headers={"X-Nightshift": "1"}, cookies=cookies).status_code

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        codes = sorted(pool.map(start, range(8)))
    assert codes.count(201) == 1 and codes.count(409) == 7, codes


def test_only_the_newest_runs_keep_their_files_so_the_disk_never_fills(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    runner = Runner(store, tmp_path, keep_runs=2)
    project = store.add_project("Acme")
    files = ProjectFiles(tmp_path, project["slug"])
    ids = []
    for _ in range(4):
        run_id = store.add_run(project["id"], "manual")
        files.run_folder(run_id).mkdir(parents=True)
        (files.run_folder(run_id) / "output.log").write_text("x", encoding="utf-8")
        store.update_run(run_id, status="done", run_dir="r")
        ids.append(run_id)
    running = store.add_run(project["id"], "manual")  # never touched, however old the others are
    files.run_folder(running).mkdir(parents=True)
    store.update_run(running, status="running")
    (files.recordings / "login.json").write_text("{}", encoding="utf-8")

    assert runner.prune(project) == 2
    assert [files.run_folder(i).exists() for i in ids] == [False, False, True, True]
    assert files.run_folder(running).exists() and (files.recordings / "login.json").exists()
    old = store.run(ids[0])
    assert old["run_dir"] == "" and "files removed to save space" in old["message"]
    assert Runner(store, tmp_path, keep_runs=0).prune(project) == 0  # 0 keeps everything
    store.close()



def test_with_a_public_url_the_laptop_itself_can_still_make_changes(tmp_path):
    # The one-click start sets --public-url to the tunnel; the browser on the laptop uses 127.0.0.1.
    store = Store(tmp_path / "db.sqlite")
    store.add_user(*ADMIN[:1], "Admin", ADMIN[1], admin=True)
    server = serve(App(store, Runner(store, tmp_path), public_url="https://abc.trycloudflare.com"), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        admin = httpx.Client(base_url=base, headers={"X-Nightshift": "1"})
        admin.post("/api/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        for origin in (base, "https://abc.trycloudflare.com"):
            assert admin.post("/api/projects", json={"client": f"Co {origin[-5:]}"}, headers={"Origin": origin}).status_code == 201
        assert admin.post("/api/projects", json={"client": "X"}, headers={"Origin": "https://evil.example"}).status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        store.close()
