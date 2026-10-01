"""End to end: the real loop, a real browser and the real demo shop, with a scripted
model standing in for the LLM (see scripted.py)."""

import xml.etree.ElementTree as ET
from pathlib import Path

from nightshift.explore import explore
from nightshift.export import export_playwright
from nightshift.recording import RecordingStore
from nightshift.report import write_junit
from nightshift.runner import run_spec
from nightshift.spec import Spec
from scripted import ScriptedModel

LOGIN = [
    ("click", "a:Log in"),
    ("type", "Email", "{{email}}"),
    ("type", "Password", "{{password}}"),
    ("click", "button:Log in"),
]
DELIVERY = [
    ("type", "Full name", "{{full_name}}"),
    ("type", "Address", "{{address}}"),
    ("type", "City", "{{city}}"),
    ("type", "Pincode", "{{pincode}}"),
    ("click", "Cash on delivery"),
    ("click", "Place order"),
]
CHECKOUT = [*LOGIN, ("click", "Add Filter Coffee"), ("click", "Cart ("), ("click", "Proceed to checkout"), *DELIVERY]
CHECKOUT_EVIDENCE = ["Order placed!", "Order number: KC-", "Pay ₹240"]


def test_checkout_passes_on_the_clean_shop(run):
    result = run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE)
    assert result.verdict == "pass", result.reason
    assert [c.holds for c in result.checks] == [True, True, True]
    out = Path(result.out_dir)
    for name in ("trace.zip", "result.json", "report.html", "step-01.jpg"):
        assert (out / name).exists(), name


def test_the_page_is_read_only_after_the_request_a_click_set_off_returns(run):
    # The server takes 1.2 s to answer the login. Reading the page earlier shows the old
    # form, the agent sees "no change" and clicks again into a re-rendering page.
    result = run("login", LOGIN, variants={"slow-api"}, evidence=["Hi, Test Shopper"])
    assert result.verdict == "pass", result.reason
    assert result.steps[3].description.startswith("click") and result.steps[3].outcome == "changed"


def test_test_data_never_appears_in_the_step_log(run):
    result = run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE)
    log = "\n".join(step.history_line() for step in result.steps)
    assert "{{password}}" in log
    assert "chai-time-42" not in log


def test_a_500_fails_the_run_even_though_the_model_never_said_so(run):
    result = run("checkout", CHECKOUT, bugs={"checkout-500"}, evidence=CHECKOUT_EVIDENCE)
    assert result.verdict == "fail"
    assert result.reason == "HTTP 500 on POST /api/order"


def test_the_judge_fails_a_missing_order_number(run):
    result = run("checkout", CHECKOUT, bugs={"no-order-number"}, evidence=CHECKOUT_EVIDENCE)
    assert result.verdict == "fail"
    assert result.reason.startswith("not shown")


def test_a_lying_judge_cannot_pass_a_wrong_total(run):
    # The model claims the page says ₹600. It says ₹180. Code checks the quote, not the claim.
    liar = ScriptedModel([("click", "Add Masala Chai"), ("click", "Add Clay Kulhad"), ("click", "Cart (")],
                         evidence=["Total: ₹600"], holds=True)
    result = run("add-to-cart", bugs={"wrong-total"}, model=liar)
    assert result.verdict == "fail"
    assert liar.judgements == 2  # rejected once, given a second chance, still unproven
    assert not any(check.holds for check in result.checks)


def test_an_agent_fail_is_overruled_when_every_expected_result_is_proven(run):
    # A negative test: the error message IS the expected result, and small models call any error a bug.
    script = [*LOGIN, ("click", "Add Masala Chai"), ("click", "Cart ("), ("click", "Proceed to checkout"),
              ("type", "Full name", "{{full_name}}"), ("type", "Address", "{{address}}"), ("type", "City", "{{city}}"),
              ("type", "Pincode", "{{bad_pincode}}"), ("click", "Cash on delivery"), ("click", "Place order"),
              ("raw", {"action": "fail", "reason": "the order was rejected"})]
    result = run("checkout-validation", script, evidence=["Pincode must be 6 digits."])
    assert result.verdict == "pass", result.reason
    assert "agent reported a bug (the order was rejected)" in result.warnings[0]


def test_an_agent_fail_stands_when_the_expected_results_are_missing(run):
    result = run("add-to-cart", [("raw", {"action": "fail", "reason": "the shop looks broken"})], evidence=["Total"])
    assert result.verdict == "fail" and result.reason == "the shop looks broken"


def test_an_uncaught_js_error_fails_a_page_that_looks_fine(run):
    result = run("add-to-cart", [("click", "Add Masala Chai"), ("click", "Cart (")],
                 bugs={"js-error"}, evidence=["Total"])
    assert result.verdict == "fail"
    assert result.reason.startswith("uncaught JS error")


def test_a_dead_button_is_caught_when_the_model_keeps_retrying_it(run):
    result = run("add-to-cart", [("click", "Add Clay Kulhad")] * 3, bugs={"dead-add-button"})
    assert result.verdict == "fail"
    assert "had no effect 3 times" in result.reason
    assert [step.outcome for step in result.steps] == ["no change"] * 3


def test_unusable_replies_end_the_run_as_a_tester_error_not_a_bug(run):
    result = run("login", [("raw", {"action": "click", "id": 999})] * 3)
    assert result.verdict == "error"
    assert all(step.outcome.startswith("invalid") for step in result.steps)


def test_a_passing_run_is_saved_then_replayed_with_no_agent_calls(run, spec_for, tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    first = run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE, recordings=store)
    assert first.verdict == "pass" and first.mode == "agent"
    assert store.path(spec_for("checkout")).exists()

    replayer = ScriptedModel(evidence=CHECKOUT_EVIDENCE, forbid_agent=True)
    second = run("checkout", model=replayer, recordings=store)
    assert second.verdict == "pass", second.reason
    assert second.mode == "replay"
    assert all(step.outcome == "replayed" for step in second.steps)
    assert replayer.judgements == 1  # the judge still checks the result


def test_replay_heals_itself_after_a_redesign(run, spec_for, tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    assert run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE, recordings=store).verdict == "pass"

    # "Proceed to checkout" is now "Go to checkout" with a new id: replay can't find it at step 7.
    rest = [("click", "Go to checkout"), *DELIVERY]
    healed = run("checkout", rest, variants={"redesign"}, evidence=CHECKOUT_EVIDENCE, recordings=store)
    assert healed.verdict == "pass", healed.reason
    assert healed.mode == "healed" and healed.healed_at == 7

    recording = store.load(spec_for("checkout"))
    names = [c.get("name") for step in recording.steps for c in step.locators]
    assert "Go to checkout" in names and "Proceed to checkout" not in names


def test_a_saved_run_exports_to_a_playwright_test(run, spec_for, tmp_path):
    store = RecordingStore(tmp_path / "recordings")
    run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE, recordings=store)
    spec = spec_for("checkout")
    code = export_playwright(spec, store.load(spec))
    assert 'page.getByRole("link", { name: "Log in", exact: true }).click();' in code
    assert ".fill(data.email);" in code
    assert 'toContainText("Order placed!")' in code
    assert "chai-time-42" in code  # literal test data stays literal; ${ENV} values become env reads


def test_a_failure_leaves_a_bug_report_and_junit(run, tmp_path):
    result = run("checkout", CHECKOUT, bugs={"checkout-500"}, evidence=CHECKOUT_EVIDENCE)
    bug = (Path(result.out_dir) / "bug.md").read_text(encoding="utf-8")
    assert "## Steps to reproduce" in bug
    assert 'Click "Place order"' in bug
    assert "High: the app crashed or the server failed" in bug

    junit = tmp_path / "junit.xml"
    write_junit([result], junit)
    case = ET.parse(junit).getroot().find("testcase")
    assert case.find("failure").get("message") == "HTTP 500 on POST /api/order"


def _lab(base_url, page, steps, expect):
    return Spec(name="lab", url=f"{base_url}/lab/{page}", steps=steps, expect=expect, max_steps=6)


def test_the_browsers_own_validation_message_is_readable(browser, base_url, tmp_path):
    # A bad email in a type=email field: the browser blocks the submit with a tooltip
    # that isn't in the page. Nightshift reads it from the field and adds it as text.
    spec = _lab(base_url, "email-form.html", ("type a bad email and send",), ("the email is rejected",))
    # The button is <input type="submit"> with no value: the browser shows "Submit".
    model = ScriptedModel([("type", "Email", "not-an-email"), ("click", "Submit")],
                          evidence=["[browser says] Email: Please include an '@'"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "validation")
    assert result.verdict == "pass", result.reason
    assert result.steps[1].outcome == "changed"  # the complaint counts as a visible change


def test_pressing_a_blocked_submit_again_and_again_is_judged_not_assumed_a_bug(browser, base_url, tmp_path):
    # A negative test: the browser blocking the form IS the expected result. Small models
    # keep pressing Submit; the no-effect rule must ask the judge before calling it a bug.
    spec = _lab(base_url, "email-form.html", ("type a bad email and send",), ("the email is rejected",))
    model = ScriptedModel([("type", "Email", "not-an-email")] + [("click", "Submit")] * 4,
                          evidence=["[browser says] Email: Please include an '@'"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "blocked")
    assert result.verdict == "pass", result.reason
    assert "had no effect 3 times" in result.warnings[0]


def test_a_link_that_opens_a_new_tab_is_followed(browser, base_url, tmp_path):
    spec = _lab(base_url, "new-tab.html", ("open the details",), ("the details page is shown",))
    model = ScriptedModel([("click", "Open the details")], evidence=["Details page"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "new-tab")
    assert result.verdict == "pass", result.reason
    assert result.steps[0].outcome == "changed"


def test_explore_turns_browser_errors_into_findings(shop, browser, base_url, tmp_path):
    shop.bugs = {"js-error"}
    try:
        explorer = ScriptedModel([
            ("click", "Add Masala Chai"),
            ("click", "Cart ("),
            ("raw", {"action": "report", "title": "Total looks odd", "details": "scripted"}),
            ("raw", {"action": "done", "reason": "seen enough"}),
        ])
        result = explore(browser, base_url + "/", explorer, out_dir=tmp_path / "explore", budget=10)
    finally:
        shop.bugs = set()
    proven = [f for f in result.findings if f.severity == "bug"]
    assert any(f.title.startswith("uncaught JS error") and f.step == 2 for f in proven)
    assert any(f.severity == "suspected" and f.title == "Total looks odd" for f in result.findings)
    assert len(result.pages) >= 2
    for name in ("discovered.json", "findings.md", "report.html"):
        assert (tmp_path / "explore" / name).exists(), name
