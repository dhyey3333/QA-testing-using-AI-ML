"""The fixes for what blind set 4 found (research/part-c-results.md, 2026-10-13).

The lab page is demo_shop/static/lab/set4-fixes.html. The blind pages themselves are never used here.
"""

from nightshift.actions import Action, execute
from nightshift.explore import explore
from nightshift.observe import observe
from nightshift.outcome import TEST_OUTDATED
from nightshift.runner import run_spec
from nightshift.spec import Spec
from scripted import ScriptedModel


def _lab(base_url, steps=("x",), expect=("y",), max_steps=8):
    return Spec(name="set4", url=f"{base_url}/lab/set4-fixes.html", steps=steps, expect=expect, max_steps=max_steps)


def _open(browser, base_url):
    page = browser.new_page()
    page.goto(f"{base_url}/lab/set4-fixes.html")
    return page


# --- 1. text scrolled out of an inner panel --------------------------------------------------

def test_text_scrolled_out_of_an_inner_panel_is_still_on_the_page(browser, base_url):
    page = _open(browser, base_url)
    try:
        assert page.evaluate("document.getElementById('done').getBoundingClientRect().bottom") < 0  # above the screen
        text = observe(page).text
        assert "Mission done" in text  # a person can scroll the panel back up
        assert "Off the page" not in text  # moved off the page still isn't
    finally:
        page.close()


def test_a_message_in_a_scrolled_panel_can_prove_a_result(browser, base_url, tmp_path):
    spec = _lab(base_url, ("read the panel",), ("the panel says Mission done",))
    result = run_spec(browser, spec, ScriptedModel([], evidence=["Mission done"]), out_dir=tmp_path / "panel")
    assert result.verdict == "pass", result.reason


# --- 2. dragging an unnamed picture, and looking for text that isn't there -------------------------

def test_an_unnamed_draggable_picture_is_offered_by_its_file_name(browser, base_url):
    page = _open(browser, base_url)
    try:
        picture = next(e for e in observe(page).elements if e.role == "draggable")
        assert picture.label == "kulhad logo (picture)"
        execute(page, Action("drag", id=picture.id, to_text="Drop the logo here"), {})
        assert page.inner_text("#logo-zone") == "Logo dropped"
    finally:
        page.close()


def test_looking_for_text_that_isnt_there_is_not_a_bug(browser, base_url, tmp_path):
    spec = _lab(base_url, ("drag the logo onto the box",), ("the box says Logo dropped",))
    guess = {"action": "drag", "text": "Se", "to": "Drop the logo here"}
    model = ScriptedModel([("raw", guess)] * 3, evidence=["Logo dropped"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "guess")
    assert (result.verdict, result.category) == ("error", TEST_OUTDATED), result.reason
    assert result.reason.startswith("the tester couldn't do a step: it couldn't find what to act on")
    assert "use an id from ELEMENTS" in result.steps[0].outcome  # and the agent was told what to do instead


def test_exploring_doesnt_report_text_it_couldnt_find(browser, base_url, tmp_path):
    guess = {"action": "drag", "text": "Se", "to": "Drop the logo here"}
    model = ScriptedModel([("raw", guess)] * 3 + [("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/set4-fixes.html", model, out_dir=tmp_path / "explore", budget=6)
    assert not [f for f in result.findings if "Se" in f.title], [f.title for f in result.findings]


# --- 3. a field's state, as a line the judge can quote -------------------------------------------

def test_disabled_and_read_only_fields_have_lines_the_judge_can_quote(browser, base_url):
    page = _open(browser, base_url)
    try:
        lines = [line for line in observe(page).text.splitlines() if line.startswith("[field]")]
        assert "[field] Nickname: empty (disabled)" in lines
        assert "[field] Coupon: ABC-123 (read-only)" in lines
        assert "[field] Cabbage: not checked (disabled)" in lines
        assert not [line for line in lines if "Pumpkin" in line or "Send" in line]  # nothing to say about them
    finally:
        page.close()


def test_a_disabled_field_can_prove_a_result(browser, base_url, tmp_path):
    spec = _lab(base_url, ("read the form",), ("the Nickname box is disabled",))
    result = run_spec(browser, spec, ScriptedModel([], evidence=["[field] Nickname: empty (disabled)"]),
                      out_dir=tmp_path / "disabled")
    assert result.verdict == "pass", result.reason
