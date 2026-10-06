"""Why a test failed, in one plain sentence a developer can act on.

Built only from what the browser recorded, never guessed by a model: the server error, the
script that crashed, the element on top of a button, the value the judge found instead of the
expected one, an API call the app refused. That keeps it as trustworthy as the verdict itself.
The defect analysis (defects.py) groups failures; this explains each one.
"""

from __future__ import annotations

import re

from .outcome import environment_problem
from .result import RunResult

_RUNS = re.compile(r"(\s*\((failed \d+ of \d+ runs|reproduced after going back)\))+$")  # the runner's notes
_COVER = re.compile(r"covering it: (<[^>]+>)")


def probable_cause(result: RunResult) -> str:
    if result.verdict == "pass":
        if result.visual.get("status") == "visual bug":
            return f"It works, but looks broken: {result.visual.get('what')}"
        return ""
    reason = _RUNS.sub("", result.reason)
    api_errors = [w.removeprefix("api error: ") for w in result.warnings if w.startswith("api error: ")]
    also = f" Before that, the app's API refused {api_errors[0]}." if api_errors else ""

    if result.category == "ENV_ISSUE" or (problem := environment_problem(result)):
        problem = _plain_environment((environment_problem(result) or reason).removeprefix("environment: "))
        return f"Not the app: {problem}. Nothing was tested; run it again when the site is reachable."
    if result.app_errors:
        first = result.app_errors[0]
        if first.startswith("HTTP 5"):
            status, _, request = first.removeprefix("HTTP ").partition(" on ")
            return (f"The server crashed on {request} (HTTP {status}). The cause is in the server's own logs for "
                    f"that request; the request itself is in the trace.{also}")
        if first.startswith("uncaught JS error"):
            return (f"The page's own JavaScript crashed: “{first.removeprefix('uncaught JS error: ')}”. "
                    f"Look at recent changes to the scripts on {_page(result)}.{also}")
    if reason.startswith("visual bug: "):
        return f"It works, but looks broken: {reason.removeprefix('visual bug: ')}"

    last = next((s for s in reversed(result.steps) if s.action is not None and s.action.kind in ("click", "type", "select")),
                None)
    label = last.target_label if last and last.target_label else "the control"
    if last and (cover := _COVER.search(last.outcome)):
        return f"“{label}” can't be used: {cover.group(1)} sits on top of it, so clicks never reach it.{also}"
    if "had no effect" in reason or "no change" in reason:
        return (f"“{label}” does nothing: using it changed nothing on the page.{also or ' No error was shown.'} "
                "Check its click handler and whether it is disabled or hidden.")

    if result.verdict == "error":
        if "ran out of steps" in reason:
            return ("The tester couldn't finish within the step limit. Usually the test's steps no longer match the app "
                    "(a renamed button, a new page in the flow): the last screenshots show where it got stuck.")
        if "could not start logged in" in reason:
            return f"This test never ran: {reason.removeprefix('could not start logged in: ')}."
        return reason

    failing = next((c for c in result.checks if not c.holds), None)
    if failing is not None and reason.startswith("not shown"):
        # The judge failed it: say what was expected and what it found instead.
        found = failing.why.rstrip(".") if failing.why and failing.why != "scripted" else "it isn't on the page"
        return f"Expected “{failing.expected}”. What the judge found: {found}.{also}"
    # The agent failed it in its own words ("Adding Clay Kulhad to the cart does nothing"), and the
    # judge confirmed the expected result is missing: the agent's words are the sharper cause.
    return reason.rstrip(".") + "." + also


# Browser errors a developer would recognise, said the way a tester would.
_ENVIRONMENT = (("Timeout", "the site didn't load within 30 seconds"),
                ("ERR_NAME_NOT_RESOLVED", "the site's address doesn't exist (DNS)"),
                ("ERR_CONNECTION_REFUSED", "nothing answered at the site's address"),
                ("ERR_CONNECTION", "the connection to the site failed"),
                ("model call failed", "the AI model couldn't be reached"))


def _plain_environment(problem: str) -> str:
    if problem.startswith(("browser error:", "model call failed")):
        problem = next((plain for needle, plain in _ENVIRONMENT if needle in problem), problem)
    return problem.rstrip(".")


def _page(result: RunResult) -> str:
    from urllib.parse import urlsplit

    return urlsplit(result.final_url or result.url).path or "/"
