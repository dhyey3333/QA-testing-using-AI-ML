"""The dashboard: its API, its guards, and its job runner (with a stand-in command, no model)."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nightshift.dashboard.server import Dashboard, serve

SPEC = """name: login
url: http://localhost:5180/
steps:
  - log in
expect:
  - the header greets the user
data:
  password: ${NS_TEST_PASSWORD}
"""


@pytest.fixture
def dash(tmp_path):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "login.yaml").write_text(SPEC, encoding="utf-8")
    run = tmp_path / "runs" / "20261001-120000"
    (run / "login").mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps([{"spec": "login", "verdict": "fail", "reason": "HTTP 500",
                                                   "out_dir": str(run / "login")}]), encoding="utf-8")
    (run / "defects.json").write_text(json.dumps([{"id": "D1", "title": "Server error", "category": "server error",
                                                   "area": "backend", "severity": "High", "specs": ["login"]}]),
                                      encoding="utf-8")
    (run / "index.html").write_text("<h1>run</h1>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the dashboard", encoding="utf-8")

    def fake(kind, params):
        report = (run / "index.html").as_posix()
        code = (f"import time; print('> login  http://localhost:5180/'); time.sleep({params.get('sleep', 0.2)}); "
                f"print('report: {report}')")
        return [sys.executable, "-c", code], f"fake {kind}"

    dashboard = Dashboard(tmp_path, [Path("specs")], Path("runs"), command_for=fake)
    server = serve(dashboard, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dashboard.base = f"http://127.0.0.1:{server.server_port}"
    yield dashboard
    dashboard.close()
    server.shutdown()
    server.server_close()


def call(dash, method, path, body=None, token=True, host=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Nightshift-Token"] = dash.token
    if host:
        headers["Host"] = host
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(dash.base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_the_page_carries_its_token(dash):
    status, page = call(dash, "GET", "/")
    assert status == 200 and dash.token.encode() in page


def test_changes_need_the_token(dash):
    status, body = call(dash, "POST", "/api/job", {"kind": "run"}, token=False)
    assert status == 403 and "token" in body["error"]


def test_only_localhost_may_ask(dash):
    status, body = call(dash, "GET", "/api/state", host="evil.example.test")
    assert status == 403


def test_specs_are_listed_without_needing_their_secrets(dash):
    status, specs = call(dash, "GET", "/api/specs")
    assert status == 200 and specs[0]["name"] == "login"
    assert specs[0]["needs_env"] == ["NS_TEST_PASSWORD"] and specs[0]["last"]["verdict"] == "fail"


def test_a_broken_spec_is_refused_with_the_reason(dash):
    status, body = call(dash, "PUT", "/api/spec", {"path": "specs/login.yaml", "text": "url: nope\nsteps: [a]\nexpect: [b]\n"})
    assert status == 400 and "url must be" in body["error"]
    assert "http://localhost:5180/" in (dash.workspace / "specs" / "login.yaml").read_text(encoding="utf-8")


def test_nothing_outside_the_workspace_can_be_read_or_written(dash):
    assert call(dash, "GET", "/api/spec?path=../secret.yaml")[0] == 400
    assert call(dash, "PUT", "/api/spec", {"path": "secret.txt", "text": "x"})[0] == 400
    assert call(dash, "GET", "/files/../secret.txt")[0] in (400, 404)
    assert call(dash, "GET", "/api/text?path=../outside.md")[0] == 400


def test_a_new_test_case_is_written_as_a_valid_spec(dash):
    status, created = call(dash, "POST", "/api/spec", {"title": "Search for coffee", "url": "http://localhost:5180/",
                                                       "steps": "search for coffee", "expect": "Filter Coffee is shown",
                                                       "requirements": "R2, R3", "technique": "positive", "folder": "specs"})
    assert status == 200 and created["requirements"] == ["R2", "R3"] and created["path"] == "specs/search-for-coffee.yaml"


def test_runs_are_listed_with_their_defects(dash):
    status, runs = call(dash, "GET", "/api/runs")
    assert status == 200 and runs[0]["id"] == "20261001-120000"
    assert runs[0]["defects"][0]["severity"] == "High" and "index.html" in runs[0]["files"]
    assert call(dash, "GET", "/files/20261001-120000/index.html")[1] == b"<h1>run</h1>"


def test_one_job_at_a_time_and_its_log_is_followed(dash):
    status, view = call(dash, "POST", "/api/job", {"kind": "run", "params": {"sleep": 1.5}})
    assert status == 200 and view["status"] == "running"
    assert call(dash, "POST", "/api/job", {"kind": "run"})[0] == 409  # one GPU, one model, one job
    deadline = time.time() + 15
    while time.time() < deadline:
        view = call(dash, "GET", "/api/job?since=0")[1]
        if view["status"] != "running":
            break
        time.sleep(0.2)
    assert view["status"] == "finished" and view["exit_code"] == 0
    assert view["current"] == "login" and view["links"]["report"] == "20261001-120000/index.html"


def test_a_running_job_can_be_stopped(dash):
    call(dash, "POST", "/api/job", {"kind": "run", "params": {"sleep": 30}})
    call(dash, "POST", "/api/job/stop", {})
    view = call(dash, "GET", "/api/job?since=0")[1]
    assert view["status"] == "stopped"
