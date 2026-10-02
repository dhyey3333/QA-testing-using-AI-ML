"""What kind of result a run is, for the people who have to act on it.

    BUG            the app is wrong: a proven wrong or missing result, a dead control,
                   an uncaught JS error, an HTTP 500 from the app
    FLAKY          failed, then passed when a fresh agent tried again
    TEST_OUTDATED  the test could not be carried out as written: its steps no longer
                   fit the app (a saved path broke and could not be healed), or the
                   tester could not follow them
    ENV_ISSUE      nothing was tested: the site or a gateway in front of it was down,
                   a bot check stood in the way, or the model could not be reached

Found on public demo sites: a Cloudflare 522 (the site's own server never answered)
and a bot-check page were both reported as app failures. An environment problem is
now an `error` with ENV_ISSUE, not a `fail`: a failure must mean the app is wrong.
"""

from __future__ import annotations

import re

from .result import RunResult

BUG, FLAKY, TEST_OUTDATED, ENV_ISSUE = "BUG", "FLAKY", "TEST_OUTDATED", "ENV_ISSUE"

# Gateways and CDNs answer these when the app behind them is down or unreachable. A 500
# is the app's own error, so it stays a bug.
_GATEWAY_STATUS = re.compile(r"^HTTP (502|503|504|52\d) ")
# Pages a browser lands on instead of the app: a CDN's error page, a bot check, a dead host.
_ENV_PAGE = re.compile(
    r"error code:? ?52\d|performing security verification|checking your browser|verify you are human|"
    r"attention required!? ?\| ?cloudflare|just a moment\.\.\.|502 bad gateway|503 service (temporarily )?unavailable|"
    r"504 gateway time-?out|this site can.t be reached|"
    # Bot firewalls' block pages. Found on a real platform's public demo (Spree, on Vercel): "This
    # request was blocked | 403 FORBIDDEN" was reported as a bug in the app. Only the firewalls' own
    # wording, so an app's "Access denied" in a permissions test still counts as the app's.
    r"this request was blocked|sorry, you have been blocked|access to this page has been denied|"
    r"pardon our interruption|request unsuccessful\. incapsula",
    re.IGNORECASE,
)
_ENV_REASON = re.compile(r"net::ERR_|page\.goto:|model call failed|judge call failed", re.IGNORECASE)


def environment_problem(result: RunResult) -> str:
    """Why nothing could really be tested, or "" when the environment was fine."""
    for error in result.app_errors:
        if _GATEWAY_STATUS.match(error):
            return f"{error} (a gateway or CDN error: the site was down or unreachable)"
    if match := _ENV_PAGE.search(result.final_text[:3_000]):
        return f'the browser landed on "{match.group(0)}" instead of the app'
    if _ENV_REASON.search(result.reason):
        return result.reason
    return ""


def categorize(result: RunResult) -> str:
    if result.verdict == "pass":
        return ""
    if result.verdict == "flaky":
        return FLAKY
    if environment_problem(result):
        return ENV_ISSUE
    if result.verdict == "error":
        return TEST_OUTDATED
    return BUG
