"""Filing defects in Jira, the way most QA teams track bugs.

    JIRA_URL=https://yourteam.atlassian.net   JIRA_PROJECT=SHOP
    JIRA_EMAIL=you@company.com  JIRA_API_TOKEN=...    Jira Cloud: your email and an API token
    JIRA_TOKEN=...                                    Jira Data Center / Server: a personal access token
    JIRA_ISSUE_TYPE=Bug                               optional, Bug by default

Each defect becomes one issue: the defect write-up as the description, labels
`nightshift` and its category, and the failing screenshot and bug report attached.

No duplicates: the defect's signature is stored as a label (ns-1a2b3c4d5e). When a
later run finds the same defect and its issue is still open, Nightshift comments
"seen again" on that issue instead of filing a new one. A closed issue that comes
back is filed again, as a regression.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .defects import Defect, defect_body

TIMEOUT = 30


class JiraError(RuntimeError):
    """Jira refused a request; the message carries its status and error text."""


@dataclass(frozen=True)
class JiraConfig:
    url: str
    project: str
    email: str = ""
    token: str = ""
    issue_type: str = "Bug"

    @classmethod
    def from_env(cls) -> JiraConfig | None:
        url, project = site_url(os.getenv("JIRA_URL", "")), _clean(os.getenv("JIRA_PROJECT", ""))
        token = _clean(os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN") or "")
        if not (url and project and token):
            return None
        return cls(url=url, project=project, email=_clean(os.getenv("JIRA_EMAIL", "")), token=token,
                   issue_type=(os.getenv("JIRA_ISSUE_TYPE") or "Bug").strip())

    def client(self) -> httpx.Client:
        # Cloud: basic auth with email + API token. Data Center: a bearer personal access token.
        if self.email:
            return httpx.Client(base_url=self.url, auth=(self.email, self.token), timeout=TIMEOUT)
        return httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {self.token}"}, timeout=TIMEOUT)


# Pages people copy a Jira address from. The REST API lives above them.
_UI_PATHS = ("/browse/", "/secure/", "/projects/", "/issues/", "/plugins/servlet/", "/rest/")


def site_url(raw: str) -> str:
    """The Jira base URL from whatever was pasted, a board's address included.

    Found on the first live run: the address bar shows
    https://team.atlassian.net/jira/software/projects/SCRUM/boards/1?filter=..., and API
    calls under that come back as a web page. Jira Cloud serves its API from the site
    root; Data Center may sit under a path like /jira, so only the page part is cut there.
    """
    raw = _clean(raw)
    parts = urlsplit(raw)
    if not (parts.scheme and parts.netloc):
        return raw.rstrip("/")
    path = parts.path
    if (parts.hostname or "").endswith(".atlassian.net"):
        path = ""
    else:
        hits = [path.find(p) for p in _UI_PATHS if p in path]
        path = path[:min(hits)] if hits else path
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _clean(value: str) -> str:
    """Drop whitespace and control characters: a terminal can turn Ctrl+V into one (^V)."""
    return re.sub(r"[\x00-\x20\x7f]+", "", value)


def signature_label(defect: Defect) -> str:
    return "ns-" + hashlib.sha1(defect.signature.encode("utf-8")).hexdigest()[:10]


def file_jira_issues(defects: list[Defect], run_dir: Path, config: JiraConfig,
                     client: httpx.Client | None = None) -> list[tuple[str, str, str]]:
    """File or update one issue per defect. Returns (defect id, "created" | "commented", issue key)."""
    own = client is None
    client = client or config.client()
    done = []
    try:
        for defect in defects:
            label = signature_label(defect)
            existing = _find_open(client, config, label)
            if existing:
                _post(client, f"/rest/api/2/issue/{existing}/comment",
                      {"body": f"Seen again by Nightshift in run {run_dir.name}, affecting "
                               f"{_affected(defect)}.\n\n{to_jira_markup(defect_body(defect, run_dir))}"})
                done.append((defect.id, "commented", existing))
                continue
            fields = {
                "project": {"key": config.project},
                "summary": f"[{defect.category}] {defect.title}"[:250],
                "description": to_jira_markup(defect_body(defect, run_dir)),
                "issuetype": {"name": config.issue_type},
                "labels": ["nightshift", re.sub(r"\W+", "-", defect.category), label],
            }
            key = _post(client, "/rest/api/2/issue", {"fields": fields})["key"]
            for path in _attachments(defect):
                _attach(client, key, path)
            done.append((defect.id, "created", key))
    finally:
        if own:
            client.close()
    return done


def to_jira_markup(markdown: str) -> str:
    """The defect write-up in Jira's wiki markup (what API v2 descriptions use)."""
    out, in_code, table_header = [], False, False
    for line in markdown.splitlines():
        if line.startswith("```"):
            out.append("{code}")  # Jira opens and closes a code block with the same tag
            in_code = not in_code
            continue
        if in_code:
            out.append(line)
            continue
        if re.fullmatch(r"\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?", line.strip()):
            table_header = False  # the |---|---| row: the line before it was the header
            continue
        if heading := re.match(r"^(#{1,6})\s+(.*)$", line):
            line = f"h{len(heading.group(1))}. {heading.group(2)}"
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"`([^`]+)`", r"{{\1}}", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"[\1|\2]", line)
        if line.startswith("|") and out and not out[-1].startswith("|") and not table_header:
            table_header = True
            line = line.replace("|", "||")  # the first row of a table is its header
        out.append(line)
    return "\n".join(out)


def _find_open(client: httpx.Client, config: JiraConfig, label: str) -> str | None:
    """The key of an open issue carrying this defect's label, if there is one."""
    jql = f'project = "{config.project}" AND labels = "{label}" AND statusCategory != Done ORDER BY created DESC'
    # Jira Cloud moved JQL search to /rest/api/3/search/jql; Data Center still has /rest/api/2/search.
    for path in ("/rest/api/3/search/jql", "/rest/api/2/search"):
        response = client.post(path, json={"jql": jql, "fields": ["summary"], "maxResults": 1})
        if response.status_code in (404, 405, 410):
            continue
        _raise_for(response, "search")
        issues = _data(response).get("issues") or []
        return issues[0]["key"] if issues else None
    raise JiraError("this Jira has no JQL search endpoint Nightshift knows")


def _post(client: httpx.Client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    _raise_for(response, path)
    return _data(response)


def _raise_for(response: httpx.Response, what: str) -> None:
    if response.status_code in (401, 403):
        raise JiraError(f"Jira refused the login (HTTP {response.status_code}): check JIRA_EMAIL and the "
                        "API token, and that this account can create issues in the project")
    if response.status_code >= 400:
        raise JiraError(f"Jira refused {what}: HTTP {response.status_code} {response.text[:300]}")


def _data(response: httpx.Response) -> dict:
    """Jira's JSON answer. A web page instead means JIRA_URL points at a page, not at Jira's API."""
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        raise JiraError(f"Jira answered {response.request.url} with a web page, not data. JIRA_URL should "
                        "be just the site, like https://yourteam.atlassian.net") from None


def _attach(client: httpx.Client, key: str, path: Path) -> None:
    mime = "image/jpeg" if path.suffix == ".jpg" else "text/markdown"
    response = client.post(f"/rest/api/2/issue/{key}/attachments", headers={"X-Atlassian-Token": "no-check"},
                           files={"file": (path.name, path.read_bytes(), mime)})
    _raise_for(response, f"the attachment {path.name}")


def _attachments(defect: Defect) -> list[Path]:
    """The failing step's screenshot and the bug report of the first failing test."""
    lead = defect.failures[0]
    out = Path(lead.result.out_dir)
    files = []
    shot = next((s.screenshot for s in lead.result.steps if s.index == lead.step and s.screenshot), None)
    if shot and (out / shot).exists():
        files.append(out / shot)
    if (out / "bug.md").exists():
        files.append(out / "bug.md")
    return files


def _affected(defect: Defect) -> str:
    return ", ".join(defect.specs)
