"""The fixes for the false alarms found by the 2026-10-09 blind test (research/part-c-results.md).

Each test is the smallest page that shows one cause: the lab pages are demo_shop/static/lab/
blind-fixes.html, repeat-login.html and late-content.html. The blind pages themselves are never
used here; they measure the fixes, they don't shape them.
"""

import pytest

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


# --- found exploring Buggy Cars: another site's errors, and logging in to explore -----------

def test_same_site_ignores_www_and_subdomains_but_not_other_hosts():
    from nightshift.checks import same_site

    assert same_site("https://www.example.com/a", "https://example.com/")
    assert same_site("https://app.example.com/a", "https://www.example.com/")
    assert not same_site("https://www.facebook.com/x", "https://buggy.justtestit.org/")
    assert same_site("about:blank", "https://example.com/")


def test_errors_on_another_site_a_link_led_to_are_not_the_apps(browser, base_url, tmp_path):
    spec = Spec(name="lab", url=f"{base_url}/lab/offsite-start.html", steps=("open the other site",),
                expect=("the noisy page is shown",), max_steps=4)
    model = ScriptedModel([("click", "Other site")], evidence=["Noisy page"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "offsite")
    assert result.verdict == "pass", result.reason  # its uncaught exception would have failed the run
    assert not [w for w in result.warnings if "noisy" in w], result.warnings


def test_explore_data_fills_secrets_from_the_environment(monkeypatch):
    from nightshift.cli import explore_data
    from nightshift.spec import SpecError

    monkeypatch.setenv("SHOP_PASSWORD", "fake-pass-1")
    assert explore_data(["username=tester1", "password=${SHOP_PASSWORD}"]) == {
        "username": "tester1", "password": "fake-pass-1"}
    monkeypatch.delenv("SHOP_PASSWORD")
    with pytest.raises(SpecError, match="SHOP_PASSWORD is not set"):
        explore_data(["password=${SHOP_PASSWORD}"])


def test_the_web_apps_explore_data_keeps_passwords_as_secret_names(tmp_path):
    from nightshift.hosted.files import ProjectFiles
    from nightshift.hosted.jobs import Runner
    from nightshift.hosted.store import Store, StoreError

    files = ProjectFiles(tmp_path, "buggy")
    files.set_secret("BUGGY_PASS", "fake-pass-1")
    assert files.run_data("username: tester1\npassword: ${BUGGY_PASS}\npincode: 560001\n") == [
        ["username", "tester1"], ["password", "${BUGGY_PASS}"], ["pincode", "560001"]]
    with pytest.raises(StoreError, match="under Settings → Secrets"):
        files.run_data("password: fake-pass-1")
    with pytest.raises(StoreError, match="set the secret OTHER_PASS"):
        files.run_data("password: ${OTHER_PASS}")
    with pytest.raises(StoreError, match="one \"name: value\" per line"):
        files.run_data("just some words")
    store = Store(tmp_path / "db.sqlite")
    try:
        command = Runner(store, tmp_path)._explore_command(
            {"url": "https://buggy.example.test/", "steps": 20, "focus": "", "data": [["password", "${BUGGY_PASS}"]]},
            tmp_path / "out")
    finally:
        store.close()
    assert command[-2:] == ["--data", "password=${BUGGY_PASS}"] and "fake-pass-1" not in " ".join(command)


# --- found exploring Buggy Cars logged in: framework crashes, and an "it saved" that wasn't -----

def test_a_framework_caught_crash_is_one_bug_in_exploring_and_a_warning_in_a_test(browser, base_url, tmp_path):
    explorer = ScriptedModel([("click", "Open broken page"), ("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/framework-error.html", explorer, out_dir=tmp_path / "explore", budget=4)
    crashes = [f.title for f in result.findings if f.severity == "bug"]
    assert crashes == ["app error: Cannot match any routes: 'broken'"], [f.title for f in result.findings]
    assert not [f for f in result.findings if "STACKTRACE" in f.title]

    spec = Spec(name="lab", url=f"{base_url}/lab/framework-error.html", steps=("open the broken page",),
                expect=("the cars page is shown",), max_steps=4)
    run = run_spec(browser, spec, ScriptedModel([("click", "Open broken page")], evidence=["Cars"]),
                   out_dir=tmp_path / "run")
    assert run.verdict == "pass", run.reason  # a test's verdict doesn't change
    assert "app error: Cannot match any routes: 'broken'" in run.warnings


def test_an_it_saved_report_goes_back_once_when_an_error_box_just_appeared(browser, base_url, tmp_path):
    explorer = ScriptedModel([
        ("type", "Age", "not-a-number"), ("click", "Save"),
        ("raw", {"action": "report", "title": "Age field accepts and saves non-numeric input", "details": "scripted"}),
        ("raw", {"action": "report", "title": "A non-numeric age is refused with the message Get a candy ;)",
                 "details": "instead of saying the age must be a number"}),
        ("raw", {"action": "done", "reason": "seen enough"}),
    ])
    result = explore(browser, f"{base_url}/lab/profile-form.html", explorer, out_dir=tmp_path / "explore", budget=8)
    reports = [s for s in result.steps if s.description.startswith("REPORT")]
    assert reports[0].outcome.startswith('not reported yet: right after your last action the page showed "Get a candy ;)"')
    assert [f.title for f in result.findings if f.severity == "suspected"] == [
        "A non-numeric age is refused with the message Get a candy ;)"]


def test_the_page_reports_its_alert_boxes(browser, base_url):
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(f"{base_url}/lab/profile-form.html")
        assert observe(page).messages == ()
        page.click("#save")
        assert observe(page).messages == ("Get a candy ;)",)
    finally:
        context.close()


def test_a_query_routed_store_has_one_page_per_route_not_one_page_in_all():
    # Found exploring TutorialsNinja (OpenCart): every page is /index.php?route=...
    from nightshift.checks import page_key

    store = "https://tutorialsninja.com/demo/index.php"
    assert page_key(f"{store}?route=checkout/cart") != page_key(f"{store}?route=information/contact")
    assert page_key(f"{store}?route=product/product&product_id=40") == page_key(f"{store}?product_id=43&route=product/product")
    assert page_key(f"{store}?route=product/search&search=%3Cb%3E") == page_key(f"{store}?route=product/search&search=mac")
    assert page_key("https://x.test/shop?utm_source=mail") == "/shop"
    assert page_key("https://x.test/app#/orders?id=4") == "/app#/orders"


def test_a_success_message_does_not_send_an_it_saved_report_back(browser, base_url, tmp_path):
    explorer = ScriptedModel([
        ("type", "Age", "42"), ("click", "Save"),
        ("raw", {"action": "report", "title": "Age is saved without asking to confirm", "details": "scripted"}),
        ("raw", {"action": "done", "reason": "seen enough"}),
    ])
    result = explore(browser, f"{base_url}/lab/profile-form.html", explorer, out_dir=tmp_path / "explore", budget=6)
    report = next(s for s in result.steps if s.description.startswith("REPORT"))
    assert report.outcome == "reported"  # "Profile saved" agrees with the claim


# --- found exploring Conduit and Buggy Cars: no login, so nothing past the sign-up page ---------

def test_a_throwaway_account_is_made_up_fresh_and_reaches_nobody():
    from nightshift.explore import throwaway_account

    first, second = throwaway_account(), throwaway_account()
    assert first["username"] != second["username"] and first["password"] != second["password"]
    assert first["email"] == f"{first['username']}@example.com"
    assert len(first["password"]) >= 12 and any(c.isupper() for c in first["password"])
    assert any(c.isdigit() for c in first["password"]) and "!" in first["password"]


def test_exploring_with_sign_up_makes_an_account_first_and_explores_as_it(browser, base_url, tmp_path):
    seen = {}

    class Watching(ScriptedModel):
        def decide(self, context):
            seen["focus"] = context.spec.steps[0]
            seen["data"] = dict(context.spec.data)
            seen["text"] = context.observation.text
            return super().decide(context)

    explorer = Watching([("type", "Username", "{{username}}"), ("type", "Email", "{{email}}"),
                         ("type", "Password", "{{password}}"), ("click", "Create account"),
                         ("raw", {"action": "done", "reason": "seen the member area"})])
    result = explore(browser, f"{base_url}/lab/signup.html", explorer, out_dir=tmp_path / "explore", budget=8,
                     sign_up=True, focus="the settings")
    username = result.test_account["username"]
    assert seen["focus"].startswith("first create a new account") and seen["focus"].endswith("Then focus on: the settings")
    assert seen["data"]["username"] == username and seen["data"]["email"] == f"{username}@example.com"
    assert f"Welcome, {username}" in seen["text"]  # it got in as the made-up account
    from nightshift.prompts import mask
    assert "Welcome, {{username}}" in mask(seen["text"], seen["data"])  # and the model sees only the placeholder
    findings = (tmp_path / "explore" / "findings.md").read_text(encoding="utf-8")
    assert f"Signed up a throwaway account to explore as: {username}" in findings
    assert seen["data"]["password"] not in findings and seen["data"]["password"] not in (
        tmp_path / "explore" / "explore.json").read_text(encoding="utf-8")


def test_a_login_in_the_test_data_wins_over_a_made_up_one(browser, base_url, tmp_path):
    explorer = ScriptedModel([("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/signup.html", explorer, out_dir=tmp_path / "explore", budget=2,
                     sign_up=True, data={"username": "agency-tester", "password": "fake-pass-1"})
    assert result.test_account["username"] == "agency-tester"


def test_the_web_app_passes_sign_up_to_the_exploration(tmp_path):
    from nightshift.hosted.jobs import Runner
    from nightshift.hosted.store import Store

    store = Store(tmp_path / "db.sqlite")
    try:
        command = Runner(store, tmp_path)._explore_command(
            {"url": "https://practice.example.test/", "steps": 20, "focus": "", "data": [], "sign_up": True},
            tmp_path / "out")
    finally:
        store.close()
    assert "--sign-up" in command


def test_a_failure_reported_right_after_typing_goes_back_to_submit_first(browser, base_url, tmp_path):
    # Found exploring Conduit: Post on an empty comment, type a comment, report "unable to post" without
    # pressing Post again.
    explorer = ScriptedModel([
        ("click", "Post Comment"), ("type", "Write a comment", "A real comment"),
        ("raw", {"action": "report", "title": "Unable to post comment despite providing text", "details": "scripted"}),
        ("click", "Post Comment"),
        ("raw", {"action": "done", "reason": "seen enough"}),
    ])
    result = explore(browser, f"{base_url}/lab/comment.html", explorer, out_dir=tmp_path / "explore", budget=8)
    report = next(s for s in result.steps if s.description.startswith("REPORT"))
    assert report.outcome.startswith('not reported yet: your last action typed into "Write a comment"')
    assert not [f for f in result.findings if f.severity == "suspected"]


# --- found exploring Conduit: the menu vanished after a settings save, and nobody said -------------

def test_a_page_that_loses_its_menu_is_flagged(browser, base_url, tmp_path):
    explorer = ScriptedModel([("click", "Save settings"), ("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/menu-home.html", explorer, out_dir=tmp_path / "explore", budget=4)
    flagged = [f for f in result.findings if f.severity == "suspected"]
    assert [f.title for f in flagged] == [
        "the site's menu disappeared (Home, Profile, Settings no longer shown on /lab/menu-home.html)"]
    assert flagged[0].details.startswith('right after: click [') and "only Shop" in flagged[0].details


def test_a_page_that_never_had_a_menu_is_not_flagged(browser, base_url, tmp_path):
    explorer = ScriptedModel([("click", "Go to checkout"), ("raw", {"action": "done", "reason": "seen enough"})])
    result = explore(browser, f"{base_url}/lab/menu-home.html", explorer, out_dir=tmp_path / "explore", budget=4)
    assert not [f for f in result.findings if "menu" in f.title]
