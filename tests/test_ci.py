"""CI: the pull-request comment, and the action and workflow that post it."""

from pathlib import Path

import yaml

from nightshift.report import PR_COMMENT_MARKER, pr_comment
from nightshift.result import RunResult

ROOT = Path(__file__).resolve().parent.parent


def _result(spec, verdict, reason="", category="", mode="agent", calls=3):
    result = RunResult(spec=spec, url="http://localhost:5180/", model="m", verdict=verdict, reason=reason,
                       category=category, mode=mode)
    result.model_calls = calls
    return result


def test_the_comment_leads_with_what_needs_attention():
    results = [_result("login", "pass", mode="replay", calls=0), _result("search", "pass", mode="replay", calls=0),
               _result("site-down", "error", "environment: net::ERR_NAME_NOT_RESOLVED", "ENV_ISSUE", calls=0),
               _result("checkout", "fail", "HTTP 500 on POST /api/order", "BUG")]
    comment = pr_comment(results, run_url="https://github.com/o/r/actions/runs/1")
    assert comment.startswith(PR_COMMENT_MARKER)  # how the workflow finds it to update, not repeat
    assert "## Nightshift: ✅ 2 passed · ❌ 1 failed (bug) · ⚪ 1 not tested" in comment
    rows = [line for line in comment.splitlines() if line.startswith("| ❌") or line.startswith("| ⚪")]
    assert rows[0].startswith("| ❌ | `checkout` | BUG | HTTP 500")  # bugs first
    assert "`login`" not in comment  # passing tests are counted, not listed
    assert "2 replayed from saved paths with no model call · 3 model calls in all" in comment
    assert "(https://github.com/o/r/actions/runs/1)" in comment


def test_a_clean_run_says_so():
    assert "Every expected result was found on the page and checked." in pr_comment([_result("login", "pass")])


def test_the_action_comments_once_per_pull_request_and_needs_no_model():
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert not any(spec.get("required") for spec in action["inputs"].values())  # saved paths need no model
    steps = {step.get("name"): step for step in action["runs"]["steps"]}
    comment = steps["Comment on the pull request"]
    assert "always()" in comment["if"] and "pull_request" in comment["if"]
    assert PR_COMMENT_MARKER in comment["run"] and "PATCH" in comment["run"]  # updates its own comment

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "nightshift.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"]["pull-requests"] == "write"
    use = workflow["jobs"]["qa"]["steps"][-1]
    assert use["uses"] == "./" and use["with"]["recordings"] == "ci/recordings"
