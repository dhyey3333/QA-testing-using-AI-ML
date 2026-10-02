"""Edge-case tests generated from one test that passes: the negative cases a tester writes next.

For each value the test types, a variant replaces it with what a careless or hostile user would
type: nothing, far too much, or the wrong format. Each variant expects the app to refuse it with an
error message about that value.

    nightshift edge-cases specs/signup.yaml          writes specs/edge-cases/signup--email-format.yaml ...
    nightshift run --url URL --goal "sign up" --data email=... --edge-cases

No model designs these: the rules below are fixed, so the same spec always gives the same cases.
They are drafts like any generated test. A form that accepts a 300-character name may be fine
for this app, so read a failing edge case before filing it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .spec import Spec
from .totp import SECRET_KEY

TOO_LONG = 300
_NEGATIVE = re.compile(r"\b(error|invalid|rejected?|not accepted|refused)\b", re.IGNORECASE)

# (pattern on the data key, the wrong-format value, what makes it wrong)
_FORMATS = (
    (r"e-?mail", "not-an-email", "not an email address"),
    (r"phone|mobile|tel", "12ab", "letters in a phone number"),
    (r"pin|zip|postal", "12", "too few digits"),
    (r"pass(word)?", "a", "one character"),
    (r"upi|vpa", "not-a-upi-id", "not a UPI ID"),
)


def variants(spec: Spec) -> list[dict]:
    """The edge-case specs for `spec`, as YAML-ready dicts. Values come from the spec's own data."""
    if not spec.data or not spec.expect:
        return []
    if any(_NEGATIVE.search(item) for item in spec.expect):
        return []  # already a negative test: its "success" is an error message, so it has no edge cases
    raw = spec.raw_data or spec.data
    cases = []
    for key in spec.data:
        if key == SECRET_KEY:
            continue
        human = key.replace("_", " ")
        kinds = [("empty", "", f"the {human} is empty")]
        fmt = next(((value, why) for pattern, value, why in _FORMATS if re.search(pattern, key, re.IGNORECASE)), None)
        if fmt:
            kinds.append(("format", fmt[0], f"the {human} is wrong: {fmt[1]}"))
        else:
            kinds.append(("too-long", "A" * TOO_LONG, f"the {human} is {TOO_LONG} characters long"))
        for kind, value, why in kinds:
            data = {**raw, key: value}
            cases.append({
                "name": f"{spec.name}--{key.replace('_', '-')}-{kind}",
                "title": f"Edge case: {why}",
                "technique": "edge case (generated)",
                "requirements": list(spec.requirements),
                "url": spec.url,
                "steps": [*spec.steps, f"(this test types a deliberately bad {{{{{key}}}}}: {why}; "
                                       "the app should refuse it with an error message)"],
                # One plain expectation. A second one, "this does not happen: <the original success>",
                # read to the 4B model on saucedemo as "expects no error", and a correct refusal was
                # reported as a bug. The quoted error message is the proof the input was refused.
                "expect": [f"an error message about the {human} is shown"],
                "data": data,
                "max_steps": spec.max_steps,
            })
    return cases


def write_variants(spec: Spec, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for case in variants(spec):
        path = folder / f"{case['name']}.yaml"
        header = f"# Generated from {spec.path or spec.name} by `nightshift edge-cases`. A draft: read it before trusting it.\n"
        path.write_text(header + yaml.safe_dump(case, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")
        paths.append(path)
    return paths
