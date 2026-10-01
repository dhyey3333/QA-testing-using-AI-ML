"""Telling the team: a Slack message and the GitHub Actions job summary.

Both are opt-in. Slack only posts when a webhook is configured
(--slack-webhook or SLACK_WEBHOOK_URL); the job summary only when running inside
GitHub Actions (GITHUB_STEP_SUMMARY is set).
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .report import markdown_summary
from .result import RunResult

ICON = {"pass": ":white_check_mark:", "fail": ":x:", "flaky": ":warning:", "error": ":white_circle:"}


def slack_payload(results: list[RunResult], run_dir: Path, run_url: str = "") -> dict:
    failing = [r for r in results if r.verdict != "pass"]
    counts = {v: sum(r.verdict == v for r in results) for v in ICON}
    headline = ", ".join(f"{n} {v}" for v, n in counts.items() if n)
    lines = [f"*Nightshift*: {headline}"]
    for result in failing[:10]:
        lines.append(f"{ICON[result.verdict]} *{result.spec}*: {result.reason}")
    if len(failing) > 10:
        lines.append(f"...and {len(failing) - 10} more")
    lines.append(f"Report: {run_url}" if run_url else f"Report: {run_dir / 'index.html'}")
    return {"text": "\n".join(lines)}


def post_slack(webhook: str, payload: dict, client: httpx.Client | None = None) -> None:
    close = client is None
    client = client or httpx.Client(timeout=10)
    try:
        response = client.post(webhook, json=payload)
        response.raise_for_status()
    finally:
        if close:
            client.close()


def append_github_summary(results: list[RunResult]) -> bool:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown_summary(results))
    return True


def github_run_url() -> str:
    server, repo, run_id = (os.getenv(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""
