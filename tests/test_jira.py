"""Filing defects in Jira, against a stand-in Jira server that speaks the same REST API."""

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nightshift.defects import analyse
from nightshift.jira import JiraConfig, file_jira_issues, signature_label, to_jira_markup
from test_e2e import CHECKOUT, CHECKOUT_EVIDENCE


class FakeJira(ThreadingHTTPServer):
    def __init__(self, cloud: bool):
        super().__init__(("127.0.0.1", 0), FakeJiraHandler)
        self.cloud = cloud
        self.issues: dict[str, dict] = {}
        self.comments: list[tuple[str, str]] = []
        self.attachments: list[tuple[str, str]] = []
        self.auth: list[str] = []


class FakeJiraHandler(BaseHTTPRequestHandler):
    server: FakeJira

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.auth.append(self.headers.get("Authorization", ""))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path = self.path
        if path == "/rest/api/3/search/jql" and not self.server.cloud:
            return self._send(404, {})
        if path in ("/rest/api/3/search/jql", "/rest/api/2/search"):
            label = re.search(r'labels = "([^"]+)"', json.loads(body)["jql"]).group(1)
            hits = [{"key": k} for k, issue in self.server.issues.items()
                    if label in issue["fields"]["labels"] and issue["status"] != "Done"]
            return self._send(200, {"issues": hits})
        if path == "/rest/api/2/issue":
            key = f"SHOP-{len(self.server.issues) + 1}"
            self.server.issues[key] = {"fields": json.loads(body)["fields"], "status": "To Do"}
            return self._send(201, {"key": key})
        if match := re.fullmatch(r"/rest/api/2/issue/([\w-]+)/attachments", path):
            assert self.headers.get("X-Atlassian-Token") == "no-check"
            name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
            self.server.attachments.append((match.group(1), name))
            return self._send(200, [{"filename": name}])
        if match := re.fullmatch(r"/rest/api/2/issue/([\w-]+)/comment", path):
            self.server.comments.append((match.group(1), json.loads(body)["body"]))
            return self._send(201, {"id": "1"})
        self._send(404, {})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def jira():
    servers = []

    def _start(cloud=True):
        server = FakeJira(cloud)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def server_error_defects(run):
    result = run("checkout", CHECKOUT, bugs={"checkout-500"}, evidence=CHECKOUT_EVIDENCE)
    return analyse([result]), Path(result.out_dir).parent


def test_a_defect_becomes_one_issue_with_its_evidence_attached(jira, server_error_defects):
    defects, run_dir = server_error_defects
    server = jira(cloud=True)
    config = JiraConfig(url=f"http://127.0.0.1:{server.server_port}", project="SHOP", email="qa@example.test", token="t0k")
    assert file_jira_issues(defects, run_dir, config) == [("D1", "created", "SHOP-1")]

    fields = server.issues["SHOP-1"]["fields"]
    assert fields["project"] == {"key": "SHOP"} and fields["issuetype"] == {"name": "Bug"}
    assert fields["summary"] == "[server error] Server error: POST /api/order returns HTTP 500"
    assert {"nightshift", "server-error", signature_label(defects[0])} <= set(fields["labels"])
    assert "*Analysis.*" in fields["description"]
    assert {name for _, name in server.attachments} >= {"bug.md"}
    assert any(name.endswith(".jpg") for _, name in server.attachments)
    assert server.auth[0] == "Basic " + base64.b64encode(b"qa@example.test:t0k").decode()


def test_the_same_open_defect_gets_a_comment_not_a_duplicate(jira, server_error_defects):
    defects, run_dir = server_error_defects
    server = jira(cloud=True)
    config = JiraConfig(url=f"http://127.0.0.1:{server.server_port}", project="SHOP", email="qa@example.test", token="t")
    file_jira_issues(defects, run_dir, config)
    assert file_jira_issues(defects, run_dir, config) == [("D1", "commented", "SHOP-1")]
    assert len(server.issues) == 1 and server.comments[0][0] == "SHOP-1"
    assert "Seen again by Nightshift" in server.comments[0][1]

    server.issues["SHOP-1"]["status"] = "Done"  # fixed and closed... then it comes back
    assert file_jira_issues(defects, run_dir, config) == [("D1", "created", "SHOP-2")]


def test_data_center_uses_a_token_and_the_older_search(jira, server_error_defects):
    defects, run_dir = server_error_defects
    server = jira(cloud=False)
    config = JiraConfig(url=f"http://127.0.0.1:{server.server_port}", project="SHOP", token="pat-123")
    file_jira_issues(defects, run_dir, config)
    assert file_jira_issues(defects, run_dir, config)[0][1] == "commented"  # found through /rest/api/2/search
    assert set(server.auth) == {"Bearer pat-123"}


def test_jira_is_off_until_it_is_configured(monkeypatch):
    for name in ("JIRA_URL", "JIRA_PROJECT", "JIRA_API_TOKEN", "JIRA_TOKEN", "JIRA_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    assert JiraConfig.from_env() is None
    monkeypatch.setenv("JIRA_URL", "https://team.atlassian.net/")
    monkeypatch.setenv("JIRA_PROJECT", "SHOP")
    monkeypatch.setenv("JIRA_API_TOKEN", "x")
    config = JiraConfig.from_env()
    assert config.url == "https://team.atlassian.net" and config.issue_type == "Bug"


def test_markdown_becomes_jira_markup():
    markdown = "## Steps\n\n| | |\n|---|---|\n| Category | server error |\n\n**Analysis.** see `trace.zip` and [report](r.html)\n\n```diff\n-Total: ₹600\n```"
    markup = to_jira_markup(markdown)
    assert "h2. Steps" in markup
    assert "|| || ||" in markup and "|---|" not in markup
    assert "*Analysis.* see {{trace.zip}} and [report|r.html]" in markup
    assert "{code}\n-Total: ₹600\n{code}" in markup
