"""The fixes for the false alarms found by the 2026-10-09 blind test (research/part-c-results.md).

Each test is the smallest page that shows one cause: the lab pages are demo_shop/static/lab/
blind-fixes.html, repeat-login.html and late-content.html. The blind pages themselves are never
used here; they measure the fixes, they don't shape them.
"""

from nightshift.actions import Action, execute
from nightshift.explore import explore
from nightshift.judge import bounds_in, verify
from nightshift.observe import DIALOG_HEADING, Observation, observe, record_dialog
from nightshift.prompts import JudgeContext, _truncate, judge_messages
from nightshift.result import Step
from nightshift.runner import refused_lines, run_spec
from nightshift.spec import Spec
from scripted import ScriptedModel


def _lab(base_url, steps, expect):
    return Spec(name="lab", url=f"{base_url}/lab/blind-fixes.html", steps=steps, expect=expect, max_steps=6)


def _page(text):
    return Observation(url="u", title="t", text=text, elements=())


def _spec(expect, data=None):
    return Spec(name="t", url="u", steps=("x",), expect=(expect,), data=data or {})


# --- 1. dialogs -----------------------------------------------------------------

def test_an_alert_is_shown_to_the_judge_and_counts_as_a_change(browser, base_url, tmp_path):
    spec = _lab(base_url, ("press Save",), ("an alert says Saved!",))
    model = ScriptedModel([("click", "button:Save")], evidence=["alert: Saved! (closed with OK)"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "alert")
    assert result.verdict == "pass", result.reason
    assert result.steps[0].outcome == "changed"
    # 7. The page's ad script was aborted by Nightshift; Chrome's "Failed to load resource" for it is ours.
    assert not [w for w in result.warnings if w.startswith("console:")], result.warnings


def test_the_same_dialog_again_changes_nothing():
    class Page:  # record_dialog only needs a hashable, weakly referenced key
        pass

    page = Page()
    record_dialog(page, "alert", "Saved!", "closed with OK")
    record_dialog(page, "alert", "Saved!", "closed with OK")
    record_dialog(page, "confirm", "Today is Friday.\nDo you agree?", "answered OK")
    from nightshift.observe import _DIALOGS

    assert _DIALOGS[page] == ["alert: Saved! (closed with OK)", "confirm: Today is Friday. Do you agree? (answered OK)"]


def test_a_long_page_keeps_its_dialogs_when_cut():
    text = "x" * 50 + f"\n{DIALOG_HEADING}\nalert: Saved! (closed with OK)"
    cut = _truncate(text, 10)
    assert cut.startswith("x" * 10 + "\n...(truncated)") and cut.endswith("alert: Saved! (closed with OK)")


# --- 2. the key-term rule: bounds and "does not contain" ------------------------------

def test_a_number_inside_a_range_proves_it_and_one_outside_does_not():
    spec = _spec("the progress bar stopped between 70% and 80%")
    page = _page("Start Stop\n79%\nResult: 4, duration: 26828")
    ok = {"checks": [{"expected": "e", "evidence": ["79%"], "why": "", "holds": True}]}
    checks, problems = verify(ok, spec, page)
    assert checks[0].holds and not problems

    page = _page("Start Stop\n25%\nResult: -50, duration: 400")
    lie = {"checks": [{"expected": "e", "evidence": ["25%"], "why": "", "holds": True}]}
    checks, problems = verify(lie, spec, page)
    assert not checks[0].holds and "25 in its evidence is not between 70% and 80%" in problems[0]


def test_every_amount_in_the_evidence_must_be_inside_the_bound():
    spec = _spec("the total is under ₹500")
    page = _page("Subtotal ₹300\nTotal ₹600")
    raw = {"checks": [{"expected": "e", "evidence": ["Subtotal ₹300", "Total ₹600"], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert not checks[0].holds and "600" in problems[0]


def test_bounds_are_read_from_the_wording():
    [b] = bounds_in("stopped between 70% and 80%")
    assert (b.low, b.high, b.unit) == (70, 80, "%") and b.holds_for(75) and not b.holds_for(81)
    [b] = bounds_in("at least 3 results are listed")
    assert b.holds_for(3) and not b.holds_for(2)
    [b] = bounds_in("the total is less than ₹1,000")
    assert b.unit == "₹" and b.holds_for(999) and not b.holds_for(1000)


def test_absent_text_about_a_quoted_line_is_checked_on_that_line_only():
    # The page's own instructions mention 'spin'; the status label does not.
    spec = _spec("the status label shows the Moving Target's class, and the class does not contain spin")
    page = _page("Make sure its class does not contain 'spin'.\nStart Animation Moving Target\n"
                 "Moving Target clicked. It's class name is 'btn btn-primary'")
    raw = {"checks": [{"expected": "e", "evidence": ["Moving Target clicked. It's class name is 'btn btn-primary'"],
                       "absent": ["spin"], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert checks[0].holds and not problems

    spinning = _page("Moving Target clicked. It's class name is 'btn btn-primary spin'")
    raw["checks"][0]["evidence"] = ["Moving Target clicked. It's class name is 'btn btn-primary"]
    checks, problems = verify(raw, spec, spinning)
    assert not checks[0].holds and "IS on the page" in problems[0]


def test_a_claim_that_something_is_gone_still_checks_the_whole_page():
    spec = _spec("Masala Chai is no longer in the cart")
    page = _page("Your cart\nFilter Coffee\nMasala Chai")
    raw = {"checks": [{"expected": "e", "evidence": ["Filter Coffee"], "absent": ["Masala Chai"], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert not checks[0].holds


# --- 3. text nobody can see; 4. links with no href --------------------------------------

def test_invisible_text_is_left_out_and_script_made_links_are_offered(browser, base_url):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(f"{base_url}/lab/blind-fixes.html")
        observation = observe(page)
        assert "Shown text" in observation.text and "Dimmed button" in observation.text
        assert "Ghost" not in observation.text, observation.text
        assert any(e.label == "Script link" for e in observation.elements)
        # The page is left as it was found.
        assert page.evaluate("document.querySelectorAll('[data-ns-unseen]').length") == 0
        assert page.evaluate("document.adoptedStyleSheets.length") == 0
    finally:
        context.close()


# --- 5. a field half under another element --------------------------------------------

def test_typing_into_a_half_covered_field_scrolls_it_clear_first(browser, base_url):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(f"{base_url}/lab/blind-fixes.html")
        name = next(e for e in observe(page).elements if e.label == "Name")
        execute(page, Action(kind="type", id=name.id, text="{{name}}"), {"name": "Asha"})
        assert page.locator("#name").input_value() == "Asha"
    finally:
        context.close()


# --- 6. what the browser refused --------------------------------------------------------

def test_refused_clicks_become_lines_the_judge_can_quote():
    click = Action(kind="click", id=4)
    steps = [Step(1, click, 'click [4] "Button"', outcome="changed"),
             Step(2, click, 'click [4] "Button"', outcome="failed: another element is covering it: <div>. If a popup "
                                                           "is open, close it first")]
    lines = refused_lines(steps)
    assert lines == ('[refused] click "Button": another element is covering it: <div>',)

    spec = _spec("after the first press the green button is covered, so it cannot be pressed again")
    page = _page("Button")
    raw = {"checks": [{"expected": "e", "evidence": ["another element is covering it"], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page, lines)
    assert checks[0].holds and not problems
    assert not verify(raw, spec, page)[0][0].holds  # without the browser's word, there is no proof
    _, user, _ = judge_messages(JudgeContext(spec, page, None, refused=lines))
    assert "REFUSED BY THE BROWSER DURING THE TEST:\n[refused] click" in user


# --- 8. exploring -----------------------------------------------------------------------

def test_explore_does_not_call_a_button_dead_after_it_worked_and_moves_on(browser, base_url, tmp_path):
    explorer = ScriptedModel([("click", "Sign in page"), *[("click", "button:Login")] * 6])
    result = explore(browser, f"{base_url}/lab/blind-fixes.html", explorer, out_dir=tmp_path / "explore", budget=12)
    assert not [f for f in result.findings if "Login" in f.title], [f.title for f in result.findings]
    assert result.moved_on == ["/lab/repeat-login.html"]
    assert len([s for s in result.steps if s.description.startswith('click') and "Login" in s.description]) == 4


def test_explore_does_not_report_a_page_that_was_still_loading(browser, base_url, tmp_path):
    explorer = ScriptedModel([
        ("raw", {"action": "report", "title": "The page is blank", "details": "nothing on it"}),
        ("raw", {"action": "done", "reason": "seen enough"}),
    ])
    result = explore(browser, f"{base_url}/lab/late-content.html", explorer, out_dir=tmp_path / "explore", budget=4)
    assert not [f for f in result.findings if f.severity == "suspected"]
    assert result.steps[0].outcome.startswith("not reported: the page was still loading")
