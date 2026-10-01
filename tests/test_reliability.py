"""Part C step 2, reliability: tester slips caught before they become bug reports, and every
non-pass labelled BUG / FLAKY / TEST_OUTDATED / ENV_ISSUE."""

from nightshift.outcome import BUG, ENV_ISSUE, FLAKY, TEST_OUTDATED, categorize, environment_problem
from nightshift.result import RunResult
from nightshift.runner import run_spec
from nightshift.spec import Spec
from scripted import ScriptedModel
from test_e2e import LOGIN


def _result(verdict, reason="", app_errors=(), final_text=""):
    result = RunResult(spec="s", url="https://x.test/", model="m", verdict=verdict, reason=reason)
    result.app_errors, result.final_text = list(app_errors), final_text
    return result


def test_failures_are_labelled_by_what_went_wrong():
    assert categorize(_result("pass")) == ""
    assert categorize(_result("flaky")) == FLAKY
    assert categorize(_result("fail", "not shown: the total is ₹240")) == BUG
    assert categorize(_result("fail", app_errors=["HTTP 500 on POST /api/order"])) == BUG
    assert categorize(_result("error", "ran out of steps (12) before the test finished")) == TEST_OUTDATED


def test_a_site_that_is_down_or_behind_a_bot_check_is_an_environment_issue():
    # Both seen on public demo sites, and both were reported as app failures.
    assert categorize(_result("fail", app_errors=["HTTP 522 on GET /login"])) == ENV_ISSUE
    assert categorize(_result("fail", final_text="demo.opencart.com\nPerforming security verification\n...")) == ENV_ISSUE
    assert categorize(_result("error", "browser error: Page.goto: net::ERR_NAME_NOT_RESOLVED")) == ENV_ISSUE
    assert categorize(_result("error", "model call failed: could not reach http://localhost:11434/v1")) == ENV_ISSUE
    assert "gateway" in environment_problem(_result("fail", app_errors=["HTTP 503 on GET /"]))
    assert environment_problem(_result("fail", app_errors=["HTTP 500 on GET /"])) == ""  # the app's own error


def test_an_unreachable_site_ends_as_an_environment_error_not_a_failure(browser, tmp_path):
    spec = Spec(name="down", url="http://127.0.0.1:9/", steps=("open the page",), expect=("the page loads",))
    result = run_spec(browser, spec, ScriptedModel(), out_dir=tmp_path / "down")
    assert result.verdict == "error" and result.category == ENV_ISSUE
    assert result.reason.startswith("environment:")


def test_a_login_failed_before_the_password_was_submitted_is_finished_first(run):
    # The shop benchmark: the model clicked "Log in" before typing the password, then failed the login.
    script = [("click", "a:Log in"), ("type", "Email", "{{email}}"), ("click", "button:Log in"),
              ("type", "Password", "{{password}}"), ("raw", {"action": "fail", "reason": "login fails"}),
              ("click", "button:Log in")]
    result = run("login", script, evidence=["Hi, Test Shopper"])
    assert result.verdict == "pass", result.reason
    assert any(step.outcome.startswith('not yet: you typed into "Password"') for step in result.steps)


def test_a_verdict_with_test_data_never_typed_gets_one_reminder(run):
    # The shop's checkout-validation: the 4B model typed only the pincode, then declared the result.
    script = [*LOGIN, ("click", "Add Masala Chai"), ("click", "Cart ("), ("click", "Proceed to checkout"),
              ("type", "Pincode", "{{bad_pincode}}"), ("click", "Place order"),
              ("raw", {"action": "pass", "reason": "the pincode error is shown"}),
              ("type", "Full name", "{{full_name}}"), ("type", "Address", "{{address}}"), ("type", "City", "{{city}}"),
              ("click", "Cash on delivery"), ("click", "Place order")]
    result = run("checkout-validation", script, evidence=["Pincode must be 6 digits."])
    assert result.verdict == "pass", result.reason
    reminder = next(step.outcome for step in result.steps if step.outcome.startswith("not yet"))
    assert "{{full_name}}" in reminder and "{{city}}" in reminder and "{{bad_pincode}}" not in reminder


def test_a_premature_pass_gets_a_second_look(browser, base_url, tmp_path):
    # lambdatest's signup: the model said pass with a box still unticked; the judge found nothing.
    spec = Spec(name="grid", url=f"{base_url}/lab/product-grid.html", steps=("add Blue Top to the cart",),
                expect=("the cart shows Blue Top",), max_steps=6)
    script = [("raw", {"action": "pass", "reason": "done"}), ("click", "Add to cart — Blue Top")]
    result = run_spec(browser, spec, ScriptedModel(script, evidence=["Cart: Blue Top"]), out_dir=tmp_path / "g")
    assert result.verdict == "pass", result.reason
    assert result.steps[0].outcome.startswith('not accepted yet: "the cart shows Blue Top" is not shown')


def test_a_real_bug_survives_the_second_look(run):
    script = [("click", "Add Masala Chai"), ("click", "Add Clay Kulhad"), ("click", "Cart (")]
    result = run("add-to-cart", script, bugs={"wrong-total"}, evidence=["Total: ₹600"])
    assert result.verdict == "fail" and result.category == BUG
    assert result.reason.startswith("not shown")


def test_a_button_covered_longer_than_one_timeout_is_clicked_on_the_retry(browser, base_url, tmp_path):
    spec = Spec(name="slow", url=f"{base_url}/lab/late-overlay.html", steps=("press Continue",),
                expect=("the page says all set",), max_steps=4)
    result = run_spec(browser, spec, ScriptedModel([("click", "Continue")], evidence=["All set."]), out_dir=tmp_path / "s")
    assert result.verdict == "pass", result.reason
    assert result.steps[0].outcome == "changed"


def test_a_submit_that_does_nothing_in_an_unfinished_form_is_not_a_dead_button(browser, base_url, tmp_path):
    # LambdaTest's playground: "Register" pressed four times with "Password Confirm" empty and the
    # privacy box unticked, then reported as a dead control.
    spec = Spec(name="register", url=f"{base_url}/lab/register.html", steps=("register an account",),
                expect=("a page says the account has been created",), data={"name": "Asha Rao"}, max_steps=12)
    script = [("type", "Name", "{{name}}"), ("click", "Register"), ("click", "Register"), ("click", "Register"),
              ("type", "Password Confirm", "secret-1"), ("click", "I agree"), ("click", "Register")]
    result = run_spec(browser, spec, ScriptedModel(script, evidence=["Your Account Has Been Created!"]),
                      out_dir=tmp_path / "r")
    assert result.verdict == "pass", result.reason
    hint = next(step.outcome for step in result.steps if "its form still has" in step.outcome)
    assert '"Password Confirm (empty)"' in hint and '"I agree to the Privacy Policy (unticked)"' in hint
    assert "Name" not in hint.split("its form still has")[1]  # filled fields aren't listed


def test_the_prompt_says_which_test_data_is_typed():
    from nightshift.actions import Action
    from nightshift.observe import Observation
    from nightshift.prompts import Context, agent_messages
    from nightshift.result import Step

    spec = Spec(name="s", url="https://x.test/", steps=("fill the form",), expect=("done",),
                data={"email": "a@b.test", "city": "Pune"})
    history = (Step(1, Action("type", id=3, text="{{email}}"), 'type [3] "Email" <- "{{email}}"', outcome="changed"),)
    _, text, _ = agent_messages(Context(spec, Observation("https://x.test/", "", "", ()), history, None))
    assert "{{email}} (typed), {{city}} (not typed yet)" in text
