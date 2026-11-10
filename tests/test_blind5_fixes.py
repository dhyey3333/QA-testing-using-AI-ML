"""The fixes for what blind set 5 found (research/part-c-results.md, 2026-10-15).

The lab page is demo_shop/static/lab/set5-fixes.html. The blind pages themselves are never used here.
"""

import pytest

from nightshift.actions import Action, ActionFailed, execute
from nightshift.judge import verify
from nightshift.observe import observe
from nightshift.spec import Spec


@pytest.fixture
def lab(browser, base_url):
    page = browser.new_page()
    page.goto(f"{base_url}/lab/set5-fixes.html")
    yield page
    page.close()


def _field(page, label):
    return next(e for e in observe(page).elements if e.label == label)


def test_a_box_that_empties_a_pasted_value_is_typed_into_key_by_key(lab):
    note = execute(lab, Action("type", id=_field(lab, "Visit date").id, text="30/11/2026"), {})
    assert note == "typed it key by key: the field emptied itself when it was filled in all at once"
    execute(lab, Action("type", id=_field(lab, "Comment").id, text="Fake test booking"), {})  # focus moves on
    assert lab.input_value("#visit") == "30/11/2026"


def test_a_box_that_learns_from_keystrokes_keeps_a_filled_value(lab):
    # The clinic demo's kind: it only notices a keyup, and wipes what it didn't notice at the next click.
    assert execute(lab, Action("type", id=_field(lab, "Booking date").id, text="30/11/2026"), {}) == ""
    execute(lab, Action("click", id=_field(lab, "Comment").id), {})
    assert lab.input_value("#booking") == "30/11/2026"


def test_an_ordinary_field_is_left_as_typed_and_keeps_the_focus(lab):
    note = execute(lab, Action("type", id=_field(lab, "Search").id, text="kulhad"), {})
    assert note == ""
    execute(lab, Action("press", key="Enter"), {})  # still goes to the search box
    assert lab.inner_text("#searched") == "Searched for kulhad"


def test_a_read_only_or_disabled_box_says_so_at_once(lab):
    elements = observe(lab).elements
    readonly = next(e for e in elements if e.value == "The Color is Green")
    with pytest.raises(ActionFailed, match="the field is read-only"):
        execute(lab, Action("type", id=readonly.id, text="test"), {})


def test_field_lines_name_unlabelled_boxes_and_say_ticked_and_disabled(lab):
    lines = [line for line in observe(lab).text.splitlines() if line.startswith(("[field]", "[button]"))]
    assert "[field] Read-Only Text Field: The Color is Green (read-only)" in lines  # named by its table cell
    assert "[field] Disabled Text Field: empty (disabled)" in lines
    assert "[field] Pre-Check Box: checked" in lines  # named by the text before it
    assert not [line for line in lines if "Plain Box" in line]  # unticked is the default: no line
    assert "[button] Disabled: disabled" in lines and not [line for line in lines if "Enabled" in line]


def test_those_lines_prove_the_claims_set_5_could_not(lab):
    observation = observe(lab)
    for expected, quote in [("the Read-Only Text Field cannot be edited", "[field] Read-Only Text Field: The Color is Green (read-only)"),
                            ("the Pre-Check Box is ticked", "[field] Pre-Check Box: checked"),
                            ("the button labelled Disabled is disabled", "[button] Disabled: disabled")]:
        spec = Spec(name="t", url="https://app.test/", steps=("x",), expect=(expected,))
        raw = {"checks": [{"expected": expected, "evidence": [quote], "absent": [], "why": "w", "holds": True}]}
        checks, problems = verify(raw, spec, observation)
        assert checks[0].holds is True and problems == [], (expected, problems)
