"""The judge: a second, narrower look at the final page before a pass counts.

The agent that ran the steps is a poor witness for whether they worked: it knows
what the page *should* say, and a small model will report exactly that. On the
first benchmark the 4B model looked at a cart whose total read ₹180 and passed it
as "₹600, the sum of the line totals".

So a pass needs a separate call that has to quote the page for every expected
result, and code checks each quote is really on the page. A made-up quote gets
one chance to be corrected; after that the check counts as not shown.
"""

from __future__ import annotations

import re
import unicodedata

from .actions import InvalidAction
from .model import Model, ModelError
from .observe import Observation
from .prompts import JudgeContext, mask
from .result import Check
from .spec import Spec

_EDGES = "\"'` .,;:"
_ELLIPSIS_RE = re.compile(r"\.{3,}|…")
# Pages use typographic quotes and dashes; models type plain ones. Found on a real site:
# the judge quoted "didn't" correctly, the page said "didn’t", and a true claim was thrown out.
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-",
                             "−": "-"})

# Values an expected result names: amounts, stand-alone numbers, capitalised names, quoted text.
# Codes like KC-12345 or "10-digit" are left out on purpose: "looks like KC-12345" names a shape.
_AMOUNT = r"[₹$€£]\s?\d[\d,]*(?:\.\d+)?"
_NUMBER = r"(?<![\w-])\d+(?:[.,:]\d+)*(?![\w-])"
_NAME = r"(?<![\w-])[A-Z][a-zA-Z]+"
_QUOTED = r"\"([^\"]+)\"|“([^”]+)”"
_TERM_RE = re.compile(f"{_QUOTED}|({_AMOUNT})|({_NUMBER})|({_NAME})")
_NEGATED_RE = re.compile(r"\b(no longer|not|no|never|without|nothing|none|isn't|aren't|doesn't|don't|didn't)\b",
                         re.IGNORECASE)
_NOT_NAMES = {"The", "A", "An", "It", "Its", "This", "That", "These", "Those", "No", "Not", "Every", "Each",
              "All", "Some", "Only", "When", "If", "There", "They", "Their", "You", "Your", "We", "Our", "One",
              "Both", "And", "Or", "After", "Before", "Then"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY).casefold()
    return re.sub(r"\s+", " ", text).strip().strip(_EDGES)


_REPORTING_VERB_RE = re.compile(r"\b(says|say|reads|read|shows|show|displays|display|states)\b", re.IGNORECASE)


def is_absence_claim(expected: str) -> bool:
    """ "SC-1001 is no longer listed" is about absence; "a message says no results were found"
    is about a message, whose words happen to include "no". Only negation before the
    reporting verb counts."""
    verb = _REPORTING_VERB_RE.search(expected)
    return bool(_NEGATED_RE.search(expected[: verb.start()] if verb else expected))


def key_terms(expected: str) -> list[str]:
    """The specific values an expected result names, which its evidence must mention.

    Found on a real site: for "the origin is France" the judge quoted "Origin" and
    "Yeast bread" (the page had a bug) and still said the claim held. Evidence that
    never mentions France can't prove anything about France.
    """
    terms = []
    for match in _TERM_RE.finditer(expected):
        quoted = match.group(1) or match.group(2)
        term = quoted or match.group(3) or match.group(4) or match.group(5)
        if term and not (match.group(5) and term in _NOT_NAMES):
            terms.append(term.strip())
    return list(dict.fromkeys(t for t in terms if t))


def is_on_page(quote: str, page_text: str) -> bool:
    """True if the quote appears on the page, ignoring case, spacing and line breaks.

    A quote with "..." in it counts if each part appears: models elide long lines.
    """
    page = normalize(page_text)
    parts = [normalize(part) for part in _ELLIPSIS_RE.split(quote)]
    parts = [part for part in parts if part]
    return bool(parts) and all(len(part) >= 2 and part in page for part in parts)


def verify(raw: dict, spec: Spec, observation: Observation) -> tuple[list[Check], list[str]]:
    """Read the judge's reply against the real page. Returns the checks and what was wrong with them.

    A claim is demoted to "does not hold" right here when it isn't proven: no real
    evidence left, a made-up quote with a number in it (one real quote can't carry
    an invented "Total: ₹600"), text claimed absent that is on the page, or a value
    the claim names that its evidence never mentions. The problems list says why,
    and judge_page gives the model one chance to answer again.
    """
    # What the judge was shown: the URL and the page text, masked as it saw them. Not the tab
    # title: on a real site a heading bug showed "Baguette" while the tab still said "Baguette
    # Parisienne", and a quote from the tab "proved" the heading. Claims are proven from the page.
    page_text = mask(f"{observation.url}\n{observation.text}", spec.data)
    items = raw.get("checks")
    if not isinstance(items, list) or not items:
        return [], ['the reply needs a "checks" list with one entry per expected result']

    problems: list[str] = []
    if len(items) != len(spec.expect):
        problems.append(f"expected {len(spec.expect)} checks, got {len(items)}")

    checks: list[Check] = []
    for index, expected in enumerate(spec.expect):
        item = items[index] if index < len(items) and isinstance(items[index], dict) else {}
        evidence = _quotes(item.get("evidence"))
        absent = _quotes(item.get("absent"))
        holds = item.get("holds") is True
        why = str(item.get("why") or "").strip()

        # The text the judge reads has test data masked ({{invalid_email}}); its screenshot shows
        # the real value (not-an-email). A quote may use either, so mask quotes the same way.
        missing = [quote for quote in evidence if not is_on_page(mask(quote, spec.data), page_text)]
        present = [quote for quote in absent if is_on_page(mask(quote, spec.data), page_text)]
        grounded = [quote for quote in evidence if quote not in missing]
        # A made-up number sinks the claim: that is where hallucinations do harm ("Total: ₹600").
        # A made-up label is dropped, and the claim stands if the real evidence left proves it.
        invented_numbers = [quote for quote in missing if re.search(r"\d", quote)]
        cited = normalize(" ".join(grounded + [q for q in absent if q not in present]))
        # "X is no longer listed" is proven by what the page shows instead ("You have no
        # appointments"), so only claims that something IS there must name their values.
        needs_terms = not is_absence_claim(expected)
        unmentioned = [t for t in key_terms(expected) if normalize(t) not in cited] if needs_terms else []
        proven = grounded or [q for q in absent if q not in present]
        if holds and (invented_numbers or present or unmentioned or not proven):
            if not proven and not missing and not needs_terms:
                # An absence claim with nothing to check. Small models leave "absent" empty even
                # after "no evidence given"; telling them exactly what to put there works better.
                hint = _absence_hint(expected)
                problems.append(f'check {index + 1} says something is NOT shown: put the words that would appear '
                                f'if it were there into "absent"' + (f', e.g. "absent": ["{hint}"]' if hint else ""))
            elif invented_numbers or not proven:
                shown = "; ".join(f'"{quote}"' for quote in missing) or "(no evidence given)"
                problems.append(f"check {index + 1} says it holds but its evidence is not on the page: {shown}")
            if present:
                shown = "; ".join(f'"{quote}"' for quote in present)
                problems.append(f"check {index + 1} says this is absent but it IS on the page: {shown}")
            if unmentioned and not invented_numbers and not present:
                shown = ", ".join(f'"{term}"' for term in unmentioned)
                problems.append(f"check {index + 1} says it holds but its evidence never mentions {shown}; "
                                "quote the text that shows it")
            holds = False
            why = "the judge could not point to it on the page" + (f" ({why})" if why else "")
        checks.append(Check(expected=expected, evidence=[q for q in evidence if q not in missing], why=why,
                            holds=holds, absent=[q for q in absent if q not in present]))
    return checks, problems


def _absence_hint(expected: str) -> str:
    """The first real word after the negation: "no thank-you message is shown" -> "thank"."""
    match = _NEGATED_RE.search(expected)
    if not match:
        return ""
    for word in re.findall(r"[A-Za-z][A-Za-z'’]+", expected[match.end():]):
        stem = word.split("-")[0]
        if len(stem) >= 4 and stem.lower() not in {"longer", "error", "message", "shown", "visible", "page", "listed"}:
            return stem
    return ""


def _quotes(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(quote) for quote in value if str(quote).strip()]


def judge_page(
    model: Model, spec: Spec, observation: Observation, screenshot: bytes | None
) -> tuple[str, str, list[Check]]:
    """Returns (verdict, reason, checks). The verdict is pass, fail, or error if the judge never answers usefully."""
    feedback = ""
    checks: list[Check] = []
    problems: list[str] = []
    for _ in range(2):
        try:
            raw = model.judge(JudgeContext(spec, observation, screenshot, feedback))
        except InvalidAction as exc:
            checks, problems = [], [str(exc)]
        except ModelError as exc:
            return "error", f"judge call failed: {exc}", []
        else:
            checks, problems = verify(raw, spec, observation)
        if not problems:
            break
        feedback = "; ".join(problems)

    if not checks:
        return "error", f"the judge gave no usable answer ({feedback})", []

    failing = [check for check in checks if not check.holds]
    if not failing:
        return "pass", "every expected result is shown on the page", checks
    first = failing[0]
    reason = f"not shown: {first.expected}" + (f" ({first.why})" if first.why else "")
    if len(failing) > 1:
        reason += ". Also not shown: " + "; ".join(check.expected for check in failing[1:])
    return "fail", reason, checks
