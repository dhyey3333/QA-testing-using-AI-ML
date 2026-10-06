"""Visual checks: a page can say all the right things and still look broken."""

from pathlib import Path

from nightshift.recording import RecordingStore
from nightshift.report import spec_report_html
from nightshift.runner import RunOptions, run_spec
from nightshift.spec import Spec
from nightshift.visual import compare
from scripted import ScriptedModel


def lab(base_url, look, visual=""):
    # Every look of the lab page says the same words; only how it looks changes.
    return Spec(name="product-grid-look", url=f"{base_url}/lab/visual.html?look={look}", steps=("look at the products",),
                expect=("three products are listed with prices",), max_steps=3, visual=visual)


def run(browser, spec, model, tmp_path, name, **options):
    store = RecordingStore(tmp_path / "recordings")
    return run_spec(browser, spec, model, out_dir=tmp_path / name, options=RunOptions(recordings=store, **options))


def test_the_first_pass_is_the_approved_look_and_an_unchanged_screen_needs_no_model(browser, base_url, tmp_path):
    first = run(browser, lab(base_url, "approved"), ScriptedModel(evidence=["Masala Chai"]), tmp_path, "one")
    assert first.verdict == "pass" and first.visual["status"] == "baseline"
    assert (tmp_path / "visual" / "product-grid-look@chromium-1280x800.png").exists()
    model = ScriptedModel(evidence=["Masala Chai"], visual={"visual_bug": True, "what": "x"})
    again = run(browser, lab(base_url, "approved"), model, tmp_path, "two")
    assert again.visual["status"] == "same" and model.looks == 0


def test_a_visual_bug_the_text_check_cannot_see_is_reported_and_can_fail_the_test(browser, base_url, tmp_path):
    run(browser, lab(base_url, "approved"), ScriptedModel(evidence=["Masala Chai"]), tmp_path, "baseline")
    broken = {"visual_bug": True, "what": "the prices are missing"}
    # The price is printed white on white: the words are all on the page, so the judge passes it.
    warned = run(browser, lab(base_url, "invisible"), ScriptedModel(evidence=["Masala Chai"], visual=broken), tmp_path, "warn")
    assert warned.verdict == "pass" and warned.visual["status"] == "visual bug"
    assert warned.warnings[0].startswith("visual bug: the prices are missing")
    assert (Path(warned.out_dir) / "visual-sides.jpg").exists() and "Visual check" in spec_report_html(warned, lab(base_url, "invisible"))
    failed = run(browser, lab(base_url, "invisible", visual="fail"), ScriptedModel(evidence=["Masala Chai"], visual=broken),
                 tmp_path, "fail")
    assert (failed.verdict, failed.category, failed.reason) == ("fail", "BUG", "visual bug: the prices are missing")


def test_a_harmless_change_is_noted_and_a_new_look_is_approved_only_on_request(browser, base_url, tmp_path):
    run(browser, lab(base_url, "approved"), ScriptedModel(evidence=["Kulhad"]), tmp_path, "baseline")
    fine = {"visual_bug": False, "what": "other products are shown"}
    changed = run(browser, lab(base_url, "content"), ScriptedModel(evidence=["Kulhad"], visual=fine), tmp_path, "changed")
    assert changed.verdict == "pass" and changed.visual["status"] == "changed"
    assert "accept it as the new look" in changed.warnings[0]
    run(browser, lab(base_url, "content"), ScriptedModel(evidence=["Kulhad"]), tmp_path, "approve", update_visual=True)
    model = ScriptedModel(evidence=["Kulhad"], visual=fine)
    after = run(browser, lab(base_url, "content"), model, tmp_path, "after")
    assert after.visual["status"] == "same" and model.looks == 0


def test_comparing_screens_finds_a_small_concentrated_change(browser):
    context = browser.new_context(viewport={"width": 400, "height": 300})
    page = context.new_page()
    page.set_content('<p style="font:20px sans-serif">Total: <b id="t">₹240</b></p>')
    before = page.screenshot(type="png")
    page.eval_on_selector("#t", "el => el.style.color = 'white'")
    after = page.screenshot(type="png")
    found = compare(context, before, after)
    same = compare(context, before, before)
    context.close()
    assert same["changed"] == 0 and found["max_cell"] > 0.2 and "across" in found["where"]


# --- why it failed ---------------------------------------------------------------------

from nightshift.actions import Action  # noqa: E402
from nightshift.cause import probable_cause  # noqa: E402
from nightshift.result import Check, RunResult, Step  # noqa: E402


def failed(**fields):
    return RunResult(spec="checkout", url="https://shop.test/", model="m", **{"verdict": "fail", "category": "BUG", **fields})


def test_the_cause_names_what_the_browser_recorded():
    server = failed(reason="HTTP 500 on POST /api/order", app_errors=["HTTP 500 on POST /api/order"],
                    warnings=["api error: HTTP 422 on POST /api/address"])
    assert probable_cause(server).startswith("The server crashed on POST /api/order (HTTP 500)")
    assert "refused HTTP 422 on POST /api/address" in probable_cause(server)
    crash = failed(reason="uncaught JS error: x is undefined", app_errors=["uncaught JS error: x is undefined"])
    assert probable_cause(crash).startswith("The page's own JavaScript crashed: “x is undefined”")
    covered = failed(reason="click [3] \"Place order\" failed 3 times in a row", steps=[Step(
        1, Action("click", id=3), 'click [3] "Place order"', target_label="Place order",
        outcome='failed: another element is covering it: <div class="promo-layer">. If a popup...')])
    assert probable_cause(covered) == ("“Place order” can't be used: <div class=\"promo-layer\"> sits on top of it, "
                                       "so clicks never reach it.")


def test_the_cause_prefers_the_agents_own_words_and_else_says_what_the_judge_found():
    judge = failed(reason="not shown: the amount to pay is ₹240 (the judge could not point to it)",
                   checks=[Check("the amount to pay is ₹240", [], "The page shows ₹290.", False)])
    assert probable_cause(judge) == "Expected “the amount to pay is ₹240”. What the judge found: The page shows ₹290."
    agent = failed(reason="Adding Clay Kulhad to the cart does nothing (reproduced after going back) (failed 2 of 2 runs)")
    assert probable_cause(agent) == "Adding Clay Kulhad to the cart does nothing."
    blocked = failed(verdict="error", category="ENV_ISSUE", reason="environment: x",
                     final_text="This request was blocked\n403 FORBIDDEN")
    assert probable_cause(blocked).startswith("Not the app: the browser landed on \"This request was blocked\"")
    assert probable_cause(RunResult(spec="s", url="u", model="m", verdict="pass")) == ""


def test_an_unreachable_site_is_said_plainly():
    down = failed(verdict="error", category="ENV_ISSUE", reason="environment: browser error: Page.goto: Timeout 30000ms exceeded.")
    assert probable_cause(down) == ("Not the app: the site didn't load within 30 seconds. Nothing was tested; "
                                    "run it again when the site is reachable.")


def test_a_read_only_run_compares_but_never_writes_a_new_approved_look(browser, base_url, tmp_path):
    # Found in the bug hunt: a --no-record replay (how CI runs) wrote screenshots into ci/visual/.
    result = run(browser, lab(base_url, "approved"), ScriptedModel(evidence=["Masala Chai"]), tmp_path, "ro", record=False)
    assert result.verdict == "pass" and result.visual["status"] == "skipped"
    assert not (tmp_path / "visual").exists()
