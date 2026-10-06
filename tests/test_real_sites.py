"""Phase 2, real sites: web components (shadow DOM), styled checkboxes, popups, one-time-code
boxes, fields that format as you type, JS errors as warnings, SMS codes, and staying logged in
between tests."""

import json

import pytest

from nightshift import actions
from nightshift.actions import Action, ActionFailed, execute
from nightshift.inbox import _same_recipient
from nightshift.locators import describe_target, resolve
from nightshift.observe import observe
from nightshift.runner import RunOptions, run_spec
from nightshift.sessions import Sessions
from nightshift.spec import Spec, SpecError, load_spec
from scripted import ScriptedModel
from test_e2e import LOGIN


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def by_label(observation, text):
    return next(e for e in observation.elements if text.lower() in e.label.lower())


# --- shadow DOM -------------------------------------------------------------------

SHADOW = """
<h1>Account</h1>
<sign-up></sign-up>
<script>
customElements.define('sign-up', class extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<form><label>Username <input name="user"></label>
      <button type="submit">Create account</button><p id="out"></p></form>`;
    root.querySelector('form').onsubmit = (event) => {
      event.preventDefault();
      root.getElementById('out').textContent = 'Welcome, ' + root.querySelector('input').value;
    };
  }
});
</script>"""


def test_controls_and_text_inside_a_shadow_root_are_seen_and_used(page):
    page.set_content(SHADOW)
    obs = observe(page)
    assert "Account" in obs.text and "Create account" in obs.text  # text from both sides of the shadow root
    execute(page, Action("type", id=by_label(obs, "Username").id, text="{{user}}"), {"user": "asha"})
    execute(page, Action("click", id=by_label(obs, "Create account").id), {})
    assert "Welcome, asha" in observe(page).text


def test_a_saved_path_finds_an_element_inside_a_shadow_root_again(page):
    page.set_content(SHADOW)
    button = by_label(observe(page), "Create account")
    candidates = describe_target(page, button.id, button.label)
    assert candidates[0]["by"] != "css"  # a verified way to find it, not only the CSS fallback
    page.set_content(SHADOW)  # a fresh page: the old numbers are gone
    assert resolve(page, candidates, timeout_s=1) is not None


# --- styled checkboxes, popups ------------------------------------------------------

STYLED = """
<style>.box input { position: absolute; opacity: 0; z-index: -1; } .box label::before { content: '[ ] '; }</style>
<form>
  <div class="box"><input type="checkbox" id="agree"><label for="agree">I agree to the Privacy Policy</label></div>
  <div class="box"><input type="radio" name="news" id="yes"><label for="yes">Yes</label>
    <input type="radio" name="news" id="no" checked><label for="no">No</label></div>
</form>"""


def test_a_styled_checkbox_is_listed_by_its_label_and_can_be_ticked(page):
    page.set_content(STYLED)
    box = by_label(observe(page), "I agree")
    assert (box.tag, box.type, box.checked) == ("input", "checkbox", False)
    execute(page, Action("click", id=box.id), {})
    assert by_label(observe(page), "I agree").checked is True
    execute(page, Action("select", id=by_label(observe(page), "Yes").id, value="Yes"), {})
    assert page.is_checked("#yes")


POPUP = """
<p>Shop</p><button id="buy">Buy now</button>
<div id="ad" style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
  <div class="modal" style="background:#fff;margin:80px auto;width:300px;padding:20px">
    <h3>THIS IS A MODAL WINDOW</h3>
    <div class="modal-footer"><p>Close</p></div>
  </div>
</div>
<script>
document.querySelector('.modal-footer p').onclick = () => document.getElementById('ad').remove();
document.getElementById('buy').onclick = () => { document.querySelector('p').textContent = 'Bought'; };
</script>"""


def test_a_popup_is_named_when_it_blocks_a_click_and_its_plain_close_text_can_be_clicked(page, monkeypatch):
    monkeypatch.setattr(actions, "ACTION_TIMEOUT_MS", 800)
    monkeypatch.setattr(actions, "RETRY_PAUSE_MS", 100)
    page.set_content(POPUP)
    obs = observe(page)
    with pytest.raises(ActionFailed, match="another element is covering it: <div"):
        execute(page, Action("click", id=by_label(obs, "Buy now").id), {})
    close = by_label(obs, "Close")
    assert close.role == "button"
    execute(page, Action("click", id=close.id), {})
    execute(page, Action("click", id=by_label(observe(page), "Buy now").id), {})
    assert "Bought" in observe(page).text


# --- typing: code boxes, fields that format as you type --------------------------------

OTP = ("<form><fieldset><legend>Enter OTP</legend>"
       + "".join(f'<input class="d" maxlength="1" aria-label="digit {i}">' for i in range(1, 7))
       + """</fieldset></form>
<script>
const d = [...document.querySelectorAll('.d')];
d.forEach((box, i) => box.oninput = () => { box.value = box.value.slice(-1); if (box.value && d[i + 1]) d[i + 1].focus(); });
</script>""")


def test_a_code_typed_into_one_time_code_boxes_goes_one_digit_per_box(page):
    page.set_content(OTP)
    first = by_label(observe(page), "box 1 of 6")
    assert "type the whole code here" in first.label
    execute(page, Action("type", id=first.id, text="{{code}}"), {"code": "407193"})
    assert page.eval_on_selector_all(".d", "els => els.map((e) => e.value).join('')") == "407193"


MASKED = """<label>Card number <input id="card"></label>
<script>
// Formats as you type and, like many masks, throws away a value pasted in all at once.
const card = document.getElementById('card');
let last = '';
card.addEventListener('input', () => {
  const digits = card.value.replace(/\\D/g, '');
  if (digits.length - last.replace(/\\D/g, '').length > 1) { card.value = last; return; }
  card.value = digits.replace(/(\\d{4})(?=\\d)/g, '$1 ');
  last = card.value;
});
</script>"""


def test_a_field_that_formats_as_you_type_gets_the_value_typed_key_by_key(page):
    page.set_content(MASKED)
    field = by_label(observe(page), "Card number")
    execute(page, Action("type", id=field.id, text="{{card}}"), {"card": "4111111111111111"})
    assert page.input_value("#card") == "4111 1111 1111 1111"


# --- JS errors, SMS codes -------------------------------------------------------------

def test_js_errors_can_be_warnings_instead_of_failures(run):
    # The js-error bug: the cart renders fine, and a background analytics call throws.
    script = [("click", "Add Masala Chai"), ("click", "Add Clay Kulhad"), ("click", "Cart (")]
    evidence = ["Masala Chai", "Clay Kulhad", "₹180", "₹420", "Total: ₹600", "Cart (2)"]
    result = run("add-to-cart", script, bugs={"js-error"}, evidence=evidence, js_errors="warn")
    assert result.verdict == "pass", result.reason
    assert any(warning.startswith("uncaught JS error") for warning in result.warnings)
    assert run("add-to-cart", script, bugs={"js-error"}, evidence=evidence).verdict == "fail"  # the default


PHONE = [("click", "a:Log in"), ("click", "Log in with your mobile number"), ("type", "Mobile number", "{{phone}}"),
         ("click", "Send OTP"), ("type", "box 1 of 6", "{{sms_code}}"), ("click", "Verify and log in")]


def test_logging_in_with_a_mobile_number_and_an_otp_by_sms(run, shop):
    result = run("login-with-phone", PHONE, evidence=["Hi, Test Shopper"])
    assert result.verdict == "pass", result.reason
    code = shop.sms[-1]["Text"].split()[0]
    record = json.dumps(result.to_json())
    assert "{{sms_code}}" in record and code not in record  # the model and the record only see the placeholder


def test_a_phone_box_whose_formatting_its_own_server_rejects_is_a_bug(run):
    result = run("login-with-phone", [*PHONE[:4], ("raw", {"action": "fail", "reason": "Send OTP says the number is invalid"})],
                 bugs={"phone-spaces-rejected"}, evidence=["Hi, Test Shopper"])
    assert result.verdict == "fail" and result.category == "BUG", result.reason


def test_a_phone_number_matches_however_it_is_written():
    assert _same_recipient("98765 43210", ("+919876543210",))
    assert not _same_recipient("9876543211", ("+919876543210",))
    assert _same_recipient("Shopper@Kulhad.test", ("shopper@kulhad.test",))


def test_the_spec_settings_for_real_sites_are_checked(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("url: https://x.test/\nsteps: [a]\nexpect: [b]\njs_errors: maybe\n", encoding="utf-8")
    with pytest.raises(SpecError, match="js_errors"):
        load_spec(path)
    path.write_text("url: https://x.test/\nsteps: [a]\nexpect: [b]\njs_errors: warn\nsession_from: login\n"
                    "sms_inbox: https://x.test/sms\n", encoding="utf-8")
    spec = load_spec(path)
    assert (spec.js_errors, spec.session_from) == ("warn", "login")
    assert spec.with_base_url("https://staging.x.test").sms_inbox == "https://staging.x.test/sms"


# --- staying logged in ----------------------------------------------------------------

def _logged_in_spec(base_url):
    return Spec(name="cart-logged-in", url=f"{base_url}/", steps=("look at the header",),
                expect=("the header greets the logged-in user by name",), session_from="login", max_steps=5)


def test_a_test_can_start_logged_in_from_the_login_test(browser, shop, base_url, spec_for, tmp_path):
    shop.bugs = set()
    sessions = Sessions([spec_for("login"), _logged_in_spec(base_url)])
    options = RunOptions(sessions=sessions)
    first = run_spec(browser, _logged_in_spec(base_url), ScriptedModel(LOGIN, evidence=["Hi, Test Shopper"]),
                     out_dir=tmp_path / "first", options=options)
    assert first.verdict == "pass", first.reason
    assert (tmp_path / "_session-login" / "result.json").exists()  # the login test ran first, for its session

    model = ScriptedModel([], evidence=["Hi, Test Shopper"])
    again = run_spec(browser, _logged_in_spec(base_url), model, out_dir=tmp_path / "again", options=options)
    assert again.verdict == "pass", again.reason
    assert model.decisions == 1  # the session was reused: the login test did not run again

    # A session is a live credential: it stays in memory and never lands in the run's files.
    written = [p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json")]
    assert written and not any('"localStorage"' in text or '"cookies"' in text for text in written)


def test_when_the_login_test_fails_the_tests_that_need_its_session_are_errors(browser, shop, base_url, spec_for, tmp_path):
    shop.bugs = {"login-rejects"}
    try:
        sessions = Sessions([spec_for("login"), _logged_in_spec(base_url)])
        result = run_spec(browser, _logged_in_spec(base_url), ScriptedModel(LOGIN, evidence=["Hi, Test Shopper"]),
                          out_dir=tmp_path / "x", options=RunOptions(sessions=sessions))
    finally:
        shop.bugs = set()
    assert result.verdict == "error" and "could not start logged in" in result.reason


def test_ad_networks_never_load_in_a_test_browser(browser):
    from nightshift.observe import block_ads

    context = browser.new_context()
    block_ads(context)
    page = context.new_page()
    failed = []
    page.on("requestfailed", lambda request: failed.append(request.url))
    # Blocked before any network traffic, so this needs no internet connection.
    page.set_content('<img src="https://pagead2.googlesyndication.com/pagead/ad.gif">'
                     '<img src="https://securepubads.g.doubleclick.net/x.gif">')
    page.wait_for_timeout(300)
    context.close()
    assert sorted(failed) == ["https://pagead2.googlesyndication.com/pagead/ad.gif",
                              "https://securepubads.g.doubleclick.net/x.gif"]


def test_what_a_form_field_holds_can_be_quoted_but_passwords_never(page):
    # Found on a practice shop: "the quantity field shows 2" was true and could not be proven.
    page.set_content('<label>Quantity <input value="2"></label><label>Password <input type="password" value="hunter2-x"></label>'
                     '<select aria-label="Size"><option>S</option><option selected>M</option></select>')
    text = observe(page).text
    assert "[field] Quantity: 2" in text and "[field] Size: M" in text
    assert "hunter2-x" not in text and "Password" not in text.split("[field]", 1)[-1]
