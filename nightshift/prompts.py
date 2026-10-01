"""Every prompt Nightshift sends, and the code that fills them in.

One file, so "what exactly does the model see?" has one answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .observe import Element, Observation
from .result import Step
from .spec import Spec

# Keeps the prompt inside a small model's context window (Ollama defaults to a few
# thousand tokens, and the screenshot alone costs about a thousand).
PROMPT_TEXT_LIMIT = 2_500
JUDGE_TEXT_LIMIT = 6_000
HISTORY_LIMIT = 15

TEST_PROMPT = """\
You are a QA tester. You drive a web browser through one test of a web app, one \
action at a time, and decide whether the app works.

Each turn you get the test (its steps and expected results), what you have done so \
far, and the current page: URL, visible text, and numbered interactive elements. A \
screenshot may be attached; the red numbers on it are the element ids.

Reply with exactly one JSON object and nothing else. One of:
{"thought": "<one short sentence>", "action": "click", "id": 12}
{"thought": "...", "action": "type", "id": 4, "text": "{{email}}"}
{"thought": "...", "action": "select", "id": 7, "value": "Pune"}
{"thought": "...", "action": "press", "key": "Enter"}
{"thought": "...", "action": "scroll", "direction": "down"}
{"thought": "...", "action": "back"}
{"thought": "...", "action": "goto", "url": "/admin/"}
{"thought": "...", "action": "wait"}
{"thought": "...", "action": "pass", "reason": "<what you see that proves each expected result>"}
{"thought": "...", "action": "fail", "reason": "<the bug, written like a bug report title>"}

Rules:
1. Use only ids from the current ELEMENTS list. They change from turn to turn.
2. To enter test data, type its placeholder exactly, e.g. {{email}}. The real value \
is filled in for you. Never make up values.
3. Do the steps in order. Do nothing the test does not ask for.
4. You are testing, not helping. Never work around a problem: if the app shows an \
error the test does not expect, shows wrong data, or an action has no effect, reply \
"fail" and describe the bug. (Some tests expect an error message; that one is fine.)
5. If your last action shows "no change", try once more at most. If it still does \
nothing, that is a bug: reply "fail".
6. Reply "pass" only when every step is done and every expected result is visible \
on the current page. Check numbers yourself, for example add up a total.
7. A dropdown (a select element, shown with options=[...]) needs the "select" action \
with one of its options; clicking it does nothing. A date or time field needs "type", \
e.g. 2031-01-31 or 14:30.
8. Use "goto" only when a step names a path on the site, like /admin/.
9. Lines starting with "[browser says]" are the browser's validation messages: the form \
was not sent because of that field. Pressing submit again changes nothing.
"""

JUDGE_PROMPT = """\
You are the judge in a QA test of a web app. The test's steps have been done. For \
each expected result, decide whether the CURRENT PAGE shows it.

Reply with exactly one JSON object:
{"checks": [{"expected": "<the expected result>", "evidence": ["<text copied exactly from VISIBLE TEXT>"], "absent": [], "why": "<one sentence>", "holds": true}]}

Rules:
1. One check per expected result, in the order given.
2. Evidence is short snippets copied character for character from VISIBLE TEXT. \
Never paraphrase, never write what the page should say. If nothing on the page \
shows the result, use [] and "holds": false.
3. When the expected result is that something is NOT shown ("X is no longer \
listed", "no error appears"), put the text that must not appear in "absent", e.g. \
"absent": ["X"]. It is checked against the page, so only claim what is truly missing.
4. Decide from the evidence alone. For anything with numbers, redo the arithmetic \
in "why" using the numbers in your evidence. Example: prices 250 and 99 should \
total 349; if the evidence says "Total: 250", "holds" is false.
5. "holds" is true only if the evidence (or the absence) clearly shows the expected result.
6. Lines starting with "[browser says]" are the browser's own validation messages on a \
form field (the tooltip it shows when it blocks a submit). They are error messages the \
page shows, and they can be quoted as evidence.
"""

EXPLORE_PROMPT = """\
You are an exploratory QA tester with a limited number of actions. Your job is to \
find bugs in a web app by using it the way real users do, including the ways they \
get it wrong.

Each turn you get the pages visited so far, your recent actions, and the current \
page: URL, visible text, and numbered interactive elements. A screenshot may be \
attached; the red numbers on it are the element ids.

Priorities:
1. Reach pages and features you have not seen yet (see PAGES VISITED).
2. Try forms with bad input: leave required fields empty, use the wrong format, \
very long text, or special characters like <b>'"&. Check the app responds with a \
clear message instead of breaking or silently accepting it.
3. When something is broken (an error, wrong numbers, missing content, a control \
that does nothing), report it, then keep exploring.

Reply with exactly one JSON object and nothing else. One of:
{"thought": "<one short sentence>", "action": "click", "id": 12}
{"thought": "...", "action": "type", "id": 4, "text": "..."}
{"thought": "...", "action": "select", "id": 7, "value": "..."}
{"thought": "...", "action": "press", "key": "Enter"}
{"thought": "...", "action": "scroll", "direction": "down"}
{"thought": "...", "action": "back"}
{"thought": "...", "action": "report", "title": "<the bug in one line>", "details": "<what you did, what you expected, what happened>"}
{"thought": "...", "action": "done", "reason": "<why more exploring is pointless>"}

Rules:
1. Use only ids from the current ELEMENTS list.
2. To enter test data, type its placeholder, e.g. {{email}}.
3. Never click anything on the AVOID list.
4. Don't report the same problem twice (see FINDINGS SO FAR).
5. A dropdown (a select element, shown with options=[...]) needs the "select" action \
with one of its options. A date or time field needs "type", e.g. 2031-01-31 or 14:30.
"""

GENERATE_PROMPT = """\
You are a senior QA engineer writing regression tests for a web app. Each test is \
one user flow in plain English, which an agent will later run in a real browser.

Reply with exactly one JSON object:
{"specs": [{"name": "kebab-case-name", "steps": ["..."], "expect": ["..."], "data": ["email", "password"]}]}

Rules:
1. Cover what matters most first: sign-in, the main purchase or booking flow, \
search, key forms. Then edge cases: wrong input, empty required fields.
2. Steps are short imperative sentences a person could follow, naming buttons and \
fields the way the app labels them.
3. Every expected result must be checkable by reading the final page: name the \
text, number or state to look for. Never "works correctly".
4. "data" lists the test data the steps need (email, password, a pincode...). In \
steps, refer to it as "the test account" or "the test data", never a made-up value.
5. Only use pages and features that appear in what you are given. Never invent features.
6. Every test starts in a fresh browser on the app's start page: logged out, empty \
cart, nothing saved. Begin with the steps that set up what the test needs (log in, \
add the items).
7. The explored pages may themselves contain bugs, so never copy a number or \
message from them as the expected result. Derive expected values from the \
requirement and from prices or labels that you work out yourself (e.g. two items at \
₹100 and ₹50 should total ₹150).
"""


DESIGN_PROMPT = """\
You are a senior QA engineer designing test cases for ONE requirement of a web app, \
the way a careful tester would. Another agent will run each case in a real browser.

Reply with exactly one JSON object:
{"cases": [{"title": "...", "technique": "positive", "priority": "high", "start": "/", "steps": ["..."], "expect": ["..."], "data": {"name": "value"}}]}

How to design:
1. First the main positive case: the requirement working as intended. When the \
requirement gives an example, this case tests exactly that example with its exact \
values: the example is the expected result.
2. Then, only where the requirement states a rule (a required field, a format, a \
limit, a permission), add a negative case (invalid or missing input) or a boundary \
case (just outside a limit). Never invent rules the requirement does not state.
3. Every case starts in a fresh browser, logged out, nothing saved, at "start": a path \
on the site ("/" for the home page, "/admin/" for an admin login). Include the steps \
that set up what it needs.
4. Steps are short imperative sentences naming buttons and fields as the app labels \
them (see the pages below).
5. Expected results must be checkable by reading the final page. Use the \
requirement's own words and values. Never invent exact wording or numbers the \
requirement does not give: write "a thank-you message is shown", not a made-up \
message. Never take expected values from the explored pages; they may contain bugs.
6. "data" is the test data the steps use, as name: value, with realistic fake values \
(an invalid email is "not-an-email"). In steps, refer to data by its name ("type the \
bad_email into Email"), never by its value. Data listed under TEST DATA AVAILABLE \
already exists: write it as "" and refer to it by name ("type the password"), and \
use each one only for the field it is named for. A negative case gives its invalid \
value its own name ("wrong_password": "not-the-password"); never reuse a real one.
7. "technique" is positive, negative or boundary. "priority" is high when the \
requirement is core to the business (sign-in, buying, booking, contact), medium \
otherwise, low for cosmetic details.
"""


@dataclass(frozen=True)
class Context:
    """Everything the model gets to choose one action."""

    spec: Spec
    observation: Observation
    history: tuple[Step, ...]
    screenshot: bytes | None  # JPEG with ids drawn on; None in --no-vision mode
    mode: str = "test"  # "test" or "explore"
    notes: str = ""  # explore mode: pages visited, avoid list, findings so far


@dataclass(frozen=True)
class JudgeContext:
    spec: Spec
    observation: Observation
    screenshot: bytes | None
    feedback: str = ""  # why the previous answer was rejected, on a second try


def agent_messages(context: Context) -> tuple[str, str, bytes | None]:
    """(system prompt, user text, image) for choosing the next action."""
    spec, data = context.spec, context.spec.data
    history = "\n".join(mask(step.history_line(), data) for step in context.history[-HISTORY_LIMIT:])
    keys = ", ".join("{{" + key + "}}" for key in data) or "(none)"
    closing = (
        "A screenshot is attached; the red numbers on it are the element ids."
        if context.screenshot is not None
        else "No screenshot this turn; work from the text and elements above."
    )

    if context.mode == "explore":
        header = [f"FOCUS: {spec.steps[0] if spec.steps else 'the whole app'}", context.notes]
    else:
        steps = "\n".join(f"{i}. {step}" for i, step in enumerate(spec.steps, 1))
        expect = "\n".join(f"- {item}" for item in spec.expect)
        header = [f"TEST: {spec.name}", f"STEPS:\n{steps}", f"EXPECTED RESULTS:\n{expect}"]

    parts = [
        *header,
        f"TEST DATA (type the placeholder): {keys}",
        f"WHAT YOU HAVE DONE:\n{history or '(nothing yet)'}",
        *_page_sections(context.observation, data, PROMPT_TEXT_LIMIT),
        closing + " Reply with one JSON object.",
    ]
    system = EXPLORE_PROMPT if context.mode == "explore" else TEST_PROMPT
    return system, "\n\n".join(part for part in parts if part), context.screenshot


def judge_messages(context: JudgeContext) -> tuple[str, str, bytes | None]:
    spec = context.spec
    expect = "\n".join(f"{i}. {item}" for i, item in enumerate(spec.expect, 1))
    obs = context.observation
    text = mask(obs.text, spec.data)
    if len(text) > JUDGE_TEXT_LIMIT:
        text = text[:JUDGE_TEXT_LIMIT] + "\n...(truncated)"
    parts = [
        f"TEST: {spec.name}",
        f"EXPECTED RESULTS:\n{expect}",
        f"CURRENT PAGE: {obs.url}",  # no tab title: claims must be proven from the page itself
        f"VISIBLE TEXT:\n{text or '(empty)'}",
    ]
    if context.feedback:
        parts.append(f"YOUR LAST ANSWER WAS REJECTED: {context.feedback}. Copy evidence exactly from VISIBLE TEXT.")
    parts.append("Reply with one JSON object.")
    return JUDGE_PROMPT, "\n\n".join(parts), context.screenshot


def _page_sections(observation: Observation, data: dict[str, str], limit: int) -> list[str]:
    text = mask(observation.text, data)
    if len(text) > limit:
        text = text[:limit] + "\n...(truncated)"
    elements = "\n".join(format_element(e, data) for e in observation.elements)
    page = f"CURRENT PAGE: {observation.url}" + (f' "{observation.title}"' if observation.title else "")
    return [page, f"VISIBLE TEXT:\n{text or '(empty)'}", f"ELEMENTS:\n{elements or '(none)'}"]


def format_element(element: Element, data: dict[str, str]) -> str:
    kind = element.role or (f"{element.tag}:{element.type}" if element.type else element.tag)
    parts = [f"[{element.id}] {kind}"]
    if element.label:
        parts.append(json.dumps(mask(element.label, data), ensure_ascii=False))
    if element.value is not None:
        parts.append("value=" + json.dumps(mask(element.value, data), ensure_ascii=False))
    if element.options:
        parts.append("options=" + json.dumps(list(element.options), ensure_ascii=False))
    if element.checked is not None:
        parts.append("checked" if element.checked else "unchecked")
    if element.disabled:
        parts.append("disabled")
    if element.invalid:
        parts.append("invalid=" + json.dumps(element.invalid, ensure_ascii=False))
    if not element.in_view:
        parts.append("(off screen)")
    return " ".join(parts)


def mask(text: str, data: dict[str, str]) -> str:
    """Show test data values as their {{placeholder}}.

    This keeps credentials out of the prompt text and the logs, and it teaches
    the model to type the placeholder instead of copying a value, which small
    models often get wrong one character at a time.
    """
    for key, value in sorted(data.items(), key=lambda item: -len(item[1])):
        if len(value) >= 4:  # masking "12" would mangle every number on the page
            text = text.replace(value, "{{" + key + "}}")
    return text
