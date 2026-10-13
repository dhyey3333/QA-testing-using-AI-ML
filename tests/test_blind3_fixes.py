"""The fixes for what blind set 3 showed (research/part-c-results.md, 2026-10-13).

Each test is the smallest page that shows one cause: demo_shop/static/lab/reach.html, load-error.html and
load-error-start.html. The blind pages themselves are never used here; blind set 4 measures the fixes.
"""

import json

import httpx
import pytest

from nightshift.actions import Action, InvalidAction, execute, validate_action
from nightshift.checks import check_page
from nightshift.explore import explore
from nightshift.export import export_playwright
from nightshift.model import HttpModel, ModelConfig
from nightshift.observe import observe
from nightshift.outcome import ENV_ISSUE, TEST_OUTDATED, categorize
from nightshift.prompts import Context
from nightshift.recording import Recording, RecordedStep, RecordingStore
from nightshift.result import RunResult, Step
from nightshift.runner import RunOptions, run_spec
from nightshift.spec import Spec
from scripted import ScriptedModel


def _reach(base_url, steps=("x",), expect=("y",), max_steps=8):
    return Spec(name="reach", url=f"{base_url}/lab/reach.html", steps=steps, expect=expect, max_steps=max_steps)


def _open(browser, url):
    page = browser.new_page()
    page.goto(url)
    return page


def _by_label(observation, text):
    return next(e for e in observation.elements if text.lower() in e.label.lower())


# --- 1. what the selectors couldn't see ------------------------------------------------

def test_plain_elements_that_act_like_controls_are_offered(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        elements = observe(page).elements
        labels = {e.label: e for e in elements}
        assert labels["Add to basket"].role == "button"  # a <div> with a pointer cursor
        assert labels["Open the popup"].role == "button"  # a <span data-toggle>
        assert labels["Drag this card"].role == "draggable"  # draggable="true"
        assert labels["Slide me"].role == "draggable"  # a class that says draggable, the jQuery UI way
        # See-through tick boxes: one named by aria-label (shared, so with its row), one by its row alone.
        ticks = [e for e in elements if e.type == "checkbox"]
        assert [t.label for t in ticks] == ["Toggle — buy milk", "walk the dog"]
        assert not [e for e in elements if e.label == "buy milk"]  # the text line itself is not a control
    finally:
        page.close()


def test_a_see_through_tick_box_can_be_ticked(browser, base_url, tmp_path):
    spec = _reach(base_url, ("tick buy milk",), ("the counter says 1 item left",))
    model = ScriptedModel([("click", "Toggle — buy milk")], evidence=["1 item left"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "tick")
    assert result.verdict == "pass", result.reason


def test_a_pointer_div_and_a_toggle_span_can_be_clicked(browser, base_url, tmp_path):
    spec = _reach(base_url, ("add to basket", "open the popup"), ("it says Added to basket and Hello from the popup",))
    model = ScriptedModel([("click", "Add to basket"), ("click", "Open the popup")],
                          evidence=["Added to basket", "Hello from the popup"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "plain")
    assert result.verdict == "pass", result.reason


# --- 2. double-click and drag, by id or by the text shown ------------------------------------

def test_double_click_and_drag_work_by_id_and_by_text(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        execute(page, Action("double_click", text="Double-click this box"), {})
        assert page.inner_text("#dbl-said") == "Box was double-clicked"
        card = _by_label(observe(page), "Drag this card")
        execute(page, Action("drag", id=card.id, to_text="Drop the card here"), {})  # HTML5 drag and drop
        assert page.inner_text("#zone") == "Card dropped"
        execute(page, Action("drag", text="Slide me", to_text="Slide target"), {})  # mouse-driven dragging
        assert page.inner_text("#slide-target") == "Slid onto the target"
    finally:
        page.close()


def test_double_click_and_drag_replies_are_checked(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        observation = observe(page)
        card = _by_label(observation, "Drag this card")
        zone_text = "Drop the card here"
        assert validate_action({"action": "double-click", "text": "Double-click this box"}, observation, {}).kind == "double_click"
        drag = validate_action({"action": "drag", "id": card.id, "to": zone_text}, observation, {})
        assert (drag.id, drag.to_text, drag.to_id) == (card.id, zone_text, None)
        assert validate_action({"action": "drag", "text": "Slide me", "to": str(card.id)}, observation, {}).to_id == card.id
        with pytest.raises(InvalidAction, match='"drag" needs "to"'):
            validate_action({"action": "drag", "id": card.id}, observation, {})
        with pytest.raises(InvalidAction, match='"double_click" needs an "id"'):
            validate_action({"action": "double_click"}, observation, {})
        assert drag.describe("Drag this card") == f'drag [{card.id}] "Drag this card" onto "{zone_text}"'
    finally:
        page.close()


def test_nothing_visible_with_that_text_is_a_plain_refusal(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        from nightshift.actions import ActionFailed

        with pytest.raises(ActionFailed, match="nothing visible on the page shows the text"):
            execute(page, Action("double_click", text="No such box"), {})
    finally:
        page.close()


def test_a_drag_onto_a_numbered_element_is_not_saved_for_replay(tmp_path):
    spec = Spec(name="d", url="http://x.test/", steps=("drag",), expect=("dropped",))
    result = RunResult(spec="d", url=spec.url, model="m", out_dir=str(tmp_path), verdict="pass")
    result.steps = [Step(1, Action("drag", id=3, to_id=4), "drag", outcome="changed", locators=[{"css": "#a"}])]
    assert RecordingStore(tmp_path).save(spec, result) is None
    result.steps = [Step(1, Action("drag", id=3, to_text="Drop here"), "drag", outcome="changed", locators=[{"css": "#a"}])]
    assert RecordingStore(tmp_path).save(spec, result) is not None


def test_double_click_and_drag_export_to_playwright():
    spec = Spec(name="d", url="http://x.test/", steps=("s",), expect=("e",))
    recording = Recording(spec="d", fingerprint="f", model="m", recorded_at="now", steps=[
        RecordedStep(Action("double_click", text="Double-click this box"), "", "", []),
        RecordedStep(Action("drag", text="Slide me", to_text="Slide target"), "", "", []),
    ], evidence=[])
    code = export_playwright(spec, recording)
    assert 'await page.getByText("Double-click this box").first().dblclick();' in code
    assert 'await page.getByText("Slide me").first().dragTo(page.getByText("Slide target").first());' in code


# --- 3. searchable dropdowns ------------------------------------------------------------------

def test_typing_into_a_searchable_dropdown_picks_the_option_typed(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        field = _by_label(observe(page), "City")
        note = execute(page, Action("type", id=field.id, text="pune"), {})
        assert note == 'picked "Pune" from the list the field opened'
        assert page.inner_text("#chosen") == "Chosen city: Pune"
        # Several left and none is what was typed: nothing is picked, and the agent hears what is there.
        field = _by_label(observe(page), "City")
        note = execute(page, Action("type", id=field.id, text="P"), {})
        assert note == 'the field opened a list of choices ("Pune", "Patna"); click the one you mean'
        assert page.inner_text("#chosen") == "Chosen city: Pune"
        assert {"Pune", "Patna"} <= {e.label for e in observe(page).elements}  # the choices are offered
    finally:
        page.close()


def test_an_ordinary_field_is_not_treated_as_a_dropdown(browser, base_url):
    page = _open(browser, f"{base_url}/lab/reach.html")
    try:
        field = _by_label(observe(page), "Password")
        assert execute(page, Action("type", id=field.id, text="Pune"), {}) == ""
    finally:
        page.close()


# --- 4. made-up addresses, and steps the tester can't do ---------------------------------------

def test_a_made_up_address_is_refused_in_a_test(browser, base_url, tmp_path):
    spec = _reach(base_url, ("add to basket",), ("it says Added to basket",))
    model = ScriptedModel([("raw", {"action": "goto", "url": "/login/"}), ("click", "Add to basket")],
                          evidence=["Added to basket"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "goto")
    assert result.verdict == "pass", result.reason
    assert result.steps[0].outcome.startswith("invalid: no step names the address /login/")
    assert not [w for w in result.warnings if "/login/" in w]  # it never went there


def test_an_address_a_step_names_is_allowed(browser, base_url, tmp_path):
    spec = _reach(base_url, ("open /lab/load-error-start.html",), ("it says Start here",))
    model = ScriptedModel([("raw", {"action": "goto", "url": "/lab/load-error-start.html"})], evidence=["Start here"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "named")
    assert result.verdict == "pass", result.reason


def test_a_step_the_tester_cant_do_is_not_a_bug(browser, base_url, tmp_path):
    spec = _reach(base_url, ("right-click the box",), ("a menu opens",))
    stuck = {"action": "stuck", "reason": "there is no right-click action"}
    model = ScriptedModel([("raw", stuck), ("raw", stuck)], evidence=["a menu"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "stuck")
    assert (result.verdict, result.category) == ("error", TEST_OUTDATED), result.reason
    assert result.reason == "the tester couldn't do a step: there is no right-click action"
    assert result.steps[0].outcome.startswith("not yet: before giving up")  # asked to look again once


def test_a_fail_that_blames_the_elements_list_is_not_a_bug(browser, base_url, tmp_path):
    spec = _reach(base_url, ("right-click the box",), ("a menu opens",))
    blame = {"action": "fail", "reason": "The box is visible but missing from the interactive ELEMENTS list"}
    model = ScriptedModel([("raw", blame), ("raw", blame)], evidence=["a menu"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "blame")
    assert (result.verdict, result.category) == ("error", TEST_OUTDATED), result.reason


def test_unusable_model_replies_are_the_testers_problem_not_the_tests():
    result = RunResult(spec="s", url="u", model="m", out_dir="o", verdict="error",
                       reason="the model gave 3 unusable replies in a row")
    assert categorize(result) == ENV_ISSUE


# --- 5. a reply cut off at the length limit -----------------------------------------------------

def _reply(content, finish):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {}}
    return httpx.Response(200, json=body)


def test_a_cut_off_reply_is_asked_for_again_with_more_room(monkeypatch):
    model = HttpModel(ModelConfig(base_url="http://model.test/v1", model="m", api_key="", timeout=5))
    sent, replies = [], [_reply('{"thought": "I need to click the button under Modal Popup. Looking at', "length"),
                         _reply('{"thought": "Click it.", "action": "click", "id": 2}', "stop")]
    monkeypatch.setattr(model, "_post", lambda config, body: (sent.append(body), replies.pop(0))[1])
    reply = model._ask(model.config, "system", "user", None, max_tokens=600)
    assert reply == {"thought": "Click it.", "action": "click", "id": 2}
    assert sent[1]["max_tokens"] == 1200 and "CUT OFF" in sent[1]["messages"][1]["content"][0]["text"]

    replies[:] = [_reply('{"thought": "and so', "length"), _reply('{"thought": "and so on', "length")]
    with pytest.raises(InvalidAction, match="cut off because it was too long"):
        model._ask(model.config, "system", "user", None, max_tokens=600)


# --- 6. JavaScript errors: reported while a page loads, a failure after an action -----------------

def test_an_error_while_the_page_loads_is_reported_and_the_test_carries_on(browser, base_url, tmp_path):
    spec = Spec(name="load", url=f"{base_url}/lab/load-error.html", steps=("press Say hello",),
                expect=("it says Hello!",))
    result = run_spec(browser, spec, ScriptedModel([("click", "Say hello")], evidence=["Hello!"]),
                      out_dir=tmp_path / "load")
    assert result.verdict == "pass", result.reason
    assert result.warnings[0] == ("uncaught JS error while the page loaded: notLoadedLibrary is not defined "
                                  "(reported; the test carried on)")
    assert result.cause == "It works, but the page's JavaScript threw “notLoadedLibrary is not defined” while it loaded."
    # js_errors: fail keeps the old rule.
    strict = run_spec(browser, spec, ScriptedModel([("click", "Say hello")], evidence=["Hello!"]),
                      out_dir=tmp_path / "strict", options=RunOptions(js_errors="fail"))
    assert strict.verdict == "fail" and strict.reason.startswith("uncaught JS error"), strict.reason


def test_an_error_right_after_an_action_still_fails(browser, base_url, tmp_path):
    spec = Spec(name="break", url=f"{base_url}/lab/load-error.html", steps=("press Break",), expect=("nothing breaks",))
    result = run_spec(browser, spec, ScriptedModel([("click", "Break")], evidence=["Break"]), out_dir=tmp_path / "break")
    assert result.verdict == "fail" and result.category == "BUG", result.reason
    assert result.reason == "uncaught JS error: Cannot read properties of undefined (reading 'call')"


def test_an_error_on_a_page_a_link_opened_counts_as_loading(browser, base_url, tmp_path):
    spec = Spec(name="link", url=f"{base_url}/lab/load-error-start.html",
                steps=("open the broken page", "press Say hello"), expect=("it says Hello!",))
    model = ScriptedModel([("click", "Open the broken page"), ("click", "Say hello")], evidence=["Hello!"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "link")
    assert result.verdict == "pass", result.reason
    assert result.warnings[0].startswith("uncaught JS error while the page loaded:")


# --- 7. exploring ----------------------------------------------------------------------------

class _Watcher(ScriptedModel):
    """Plays its script and keeps the elements it was offered on each turn."""

    def __init__(self, script):
        super().__init__(script)
        self.offered = []

    def decide(self, context: Context):
        self.offered.append([e.label for e in context.observation.elements])
        return super().decide(context)


def test_exploring_never_offers_what_the_avoid_list_covers(browser, base_url, tmp_path):
    model = _Watcher([("raw", {"action": "done", "reason": "seen enough"})])
    explore(browser, f"{base_url}/lab/reach.html", model, out_dir=tmp_path / "avoid", budget=3)
    assert model.offered and not [label for label in model.offered[0] if "delete" in label.lower()]
    assert "Add to basket" in model.offered[0]


def test_a_password_the_agent_makes_up_is_never_written_down(browser, base_url, tmp_path):
    model = _Watcher([("type", "Password", "Made-Up-Pass-9"), ("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/reach.html", model, out_dir=tmp_path / "pw", budget=3)
    step = result.steps[0]
    assert step.description.endswith('<- "********"') and step.action.text == "********"
    files = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "pw").rglob("*") if p.suffix in (".md", ".json", ".html"))
    assert "Made-Up-Pass-9" not in files


def test_no_page_checks_on_a_blank_page(browser):
    page = browser.new_page()
    try:
        sink = RunResult(spec="s", url="u", model="m", out_dir="o")
        check_page(page, sink, set())  # about:blank: no title, no lang, and none of it is the app's
        assert sink.warnings == []
    finally:
        page.close()
