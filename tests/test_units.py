import json
from pathlib import Path

import httpx
import pytest

from nightshift.actions import InvalidAction, fill_placeholders, validate_action
from nightshift.export import evidence_assertion, value_ts
from nightshift.generate import generate_specs, write_specs
from nightshift.judge import is_on_page, judge_page, verify
from nightshift.model import parse_json_object
from nightshift.notify import post_slack, slack_payload
from nightshift.observe import Element, Observation
from nightshift.prompts import mask
from nightshift.result import RunResult
from nightshift.runner import _combine
from nightshift.spec import Spec, SpecError, load_spec, load_specs

SPECS = Path(__file__).resolve().parent.parent / "specs"

PAGE = Observation(
    url="http://shop.test/",
    title="Shop",
    text="Your cart\nMasala Chai ₹180\nClay Kulhad ₹420\nTotal: ₹180",
    elements=(Element(id=1, tag="button", label="Go"), Element(id=2, tag="input", label="Email", type="email"),
              Element(id=3, tag="button", label="Delete account")),
)
DATA = {"email": "shopper@example.test", "pin": "12"}
SPEC = Spec(name="t", url="http://shop.test/", steps=("x",), expect=("total is the sum",), data=DATA)


# --- parsing and validating model replies ------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "click", "id": 1}',
        '```json\n{"action": "click", "id": 1}\n```',
        'Sure! Here is my answer: {"action": "click", "id": 1} Hope that helps.',
        '{"thought": "the {button} is there", "action": "click", "id": 1}',
    ],
)
def test_parse_json_object_finds_the_object(raw):
    assert parse_json_object(raw)["action"] == "click"


def test_parse_json_object_rejects_prose():
    with pytest.raises(InvalidAction, match="not a JSON object"):
        parse_json_object("I would click the button.")


def test_validate_accepts_aliases_small_models_use():
    action = validate_action({"action": "Click", "element_id": "1"}, PAGE, DATA)
    assert (action.kind, action.id) == ("click", 1)
    assert validate_action({"action": "pass", "summary": "ok"}, PAGE, DATA).reason == "ok"


def test_validate_rejects_an_id_not_on_the_page():
    with pytest.raises(InvalidAction, match=r"no element \[9\]"):
        validate_action({"action": "click", "id": 9}, PAGE, DATA)


def test_validate_rejects_unknown_test_data():
    with pytest.raises(InvalidAction, match=r"\{\{password\}\}"):
        validate_action({"action": "type", "id": 2, "text": "{{password}}"}, PAGE, DATA)


def test_validate_refuses_the_avoid_list():
    with pytest.raises(InvalidAction, match="AVOID"):
        validate_action({"action": "click", "id": 3}, PAGE, DATA, avoid=("delete",))


FORM = Observation(
    url="http://clinic.test/book", title="Book", text="",
    elements=(Element(id=1, tag="select", label="Doctor", value="Choose", options=("Choose", "Dr. A (Skin)")),
              Element(id=2, tag="input", label="Date", type="date"),
              Element(id=3, tag="button", label="Review")),
)


def test_clicking_a_dropdown_is_refused_with_the_right_action():
    with pytest.raises(InvalidAction, match=r'"select", "id": 1.*"Dr. A \(Skin\)"'):
        validate_action({"action": "click", "id": 1}, FORM, {})


def test_clicking_a_date_field_is_refused_with_the_format():
    with pytest.raises(InvalidAction, match=r'"type", "id": 2, "text": "2031-01-31"'):
        validate_action({"action": "click", "id": 2}, FORM, {})


def test_select_on_a_button_is_refused():
    with pytest.raises(InvalidAction, match="not a dropdown"):
        validate_action({"action": "select", "id": 3, "value": "x"}, FORM, {})


def test_explore_actions_are_not_allowed_in_a_test():
    with pytest.raises(InvalidAction, match="unknown action"):
        validate_action({"action": "report", "title": "x"}, PAGE, DATA)


def test_placeholders_are_filled_only_at_typing_time():
    assert fill_placeholders("{{email}} / {{ pin }}", DATA) == "shopper@example.test / 12"


def test_mask_hides_data_values_but_not_short_ones():
    # "12" is too short to mask safely: it would mangle every 12 on the page.
    assert mask("Hi shopper@example.test, 12 items", DATA) == "Hi {{email}}, 12 items"


# --- the judge ----------------------------------------------------------------

def test_quotes_are_matched_loosely_but_must_exist():
    assert is_on_page("total: ₹180", PAGE.text)
    assert is_on_page("Masala Chai ₹180 ... Total: ₹180", PAGE.text)
    assert not is_on_page("Total: ₹600", PAGE.text)


def test_verify_flags_a_pass_with_made_up_evidence():
    raw = {"checks": [{"expected": "total", "evidence": ["Total: ₹600"], "why": "180+420=600", "holds": True}]}
    checks, problems = verify(raw, SPEC, PAGE)
    assert problems and "not on the page" in problems[0]
    assert checks[0].evidence == [] and not checks[0].holds


def test_one_real_quote_cannot_carry_a_made_up_one():
    raw = {"checks": [{"expected": "t", "evidence": ["Masala Chai ₹180", "Total: ₹600"], "why": "", "holds": True}]}
    checks, problems = verify(raw, SPEC, PAGE)
    assert problems and not checks[0].holds


def test_absence_is_checked_against_the_page():
    gone = {"checks": [{"expected": "Filter Coffee is not in the cart", "evidence": [], "absent": ["Filter Coffee"],
                        "why": "", "holds": True}]}
    checks, problems = verify(gone, SPEC, PAGE)
    assert not problems and checks[0].holds

    still_there = {"checks": [{"expected": "Masala Chai is not in the cart", "evidence": [], "absent": ["Masala Chai"],
                               "why": "", "holds": True}]}
    checks, problems = verify(still_there, SPEC, PAGE)
    assert any("IS on the page" in p for p in problems) and not checks[0].holds


def test_evidence_must_mention_the_values_the_claim_names():
    # Found on a real site: "origin is France" passed on evidence that only said "Origin", "Yeast bread".
    page = Observation(url="u", title="t", text="Origin\nYeast bread\nType\nYeast bread", elements=())
    spec = Spec(name="t", url="u", steps=("x",), expect=("it says the origin is France",))
    raw = {"checks": [{"expected": "e", "evidence": ["Origin", "Yeast bread"], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert not checks[0].holds and 'never mentions "France"' in problems[0]


def test_curly_and_straight_quotes_match():
    assert is_on_page("We didn't get it", "Sorry, we didn’t get it.")
    assert is_on_page("You searched for \"bread\"", "You searched for “bread”, 8 results found.")


def test_a_paraphrased_label_is_dropped_but_real_evidence_still_proves_the_claim():
    # Found on a real admin dashboard: five real quotes plus one embellished label.
    page = Observation(url="u", title="t", text="Site summary\n34 Pages\n39 Images", elements=())
    spec = Spec(name="t", url="u", steps=("x",), expect=("the dashboard shows a site summary",))
    raw = {"checks": [{"expected": "e", "evidence": ["Site summary", "34 Pages", "Welcome to your dashboard"],
                       "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert checks[0].holds and not problems
    assert checks[0].evidence == ["Site summary", "34 Pages"]


def test_absence_claims_are_told_apart_from_messages_that_say_no():
    from nightshift.judge import is_absence_claim

    assert is_absence_claim("SC-1001 is no longer in the appointments list")
    assert is_absence_claim("Masala Chai is not shown")
    assert not is_absence_claim('a message says no results were found for "zzzz"')
    assert not is_absence_claim("an error says the username and password did not match")


def test_gone_is_proven_by_what_shows_instead():
    page = Observation(url="u", title="t", text="Appointment SC-1001 cancelled.\nYou have no appointments.", elements=())
    spec = Spec(name="t", url="u", steps=("x",), expect=("SC-1001 is no longer in the appointments list",))
    raw = {"checks": [{"expected": "e", "evidence": ["You have no appointments."], "why": "", "holds": True}]}
    checks, problems = verify(raw, spec, page)
    assert checks[0].holds and not problems


def test_a_quote_may_use_the_real_test_value_it_saw_in_the_screenshot():
    # The text has {{bad_email}}; the screenshot shows not-an-email. Both quotes are true.
    page = Observation(url="u", title="t", text="'{{bad_email}}' is missing an '@'.", elements=())
    spec = Spec(name="t", url="u", steps=("x",), expect=("the email is rejected",), data={"bad_email": "not-an-email"})
    for quote in ("'not-an-email' is missing an '@'", "'{{bad_email}}' is missing an '@'"):
        raw = {"checks": [{"expected": "e", "evidence": [quote], "why": "", "holds": True}]}
        checks, problems = verify(raw, spec, page)
        assert checks[0].holds and not problems, quote


def test_key_terms_skip_shapes_and_filler():
    from nightshift.judge import key_terms

    assert key_terms("it shows an order number that looks like KC-12345") == ["KC"]
    assert key_terms("an error says a 10-digit mobile number is needed") == []
    assert key_terms("The booking is with Dr. Asha Iyer at 10:30 for ₹600") == ["Dr", "Asha", "Iyer", "10:30", "₹600"]
    assert key_terms('the post "Tracking Wild Yeast" is listed') == ["Tracking Wild Yeast"]


class _Judge:
    name = "judge"

    def __init__(self, *replies):
        self.replies = list(replies)

    def judge(self, context):
        return self.replies.pop(0)


def test_judge_gives_one_second_chance_then_fails_the_claim():
    lie = {"checks": [{"expected": "t", "evidence": ["Total: ₹600"], "why": "", "holds": True}]}
    verdict, reason, checks = judge_page(_Judge(lie, lie), SPEC, PAGE, None)
    assert verdict == "fail" and "could not point to it" in reason


def test_judge_passes_when_the_second_answer_is_grounded():
    lie = {"checks": [{"expected": "t", "evidence": ["Total: ₹600"], "why": "", "holds": True}]}
    honest = {"checks": [{"expected": "t", "evidence": ["Total: ₹180"], "why": "180 + 420 = 600 ≠ 180", "holds": False}]}
    verdict, reason, _ = judge_page(_Judge(lie, honest), SPEC, PAGE, None)
    assert verdict == "fail" and "600" in reason


# --- retries --------------------------------------------------------------------

def _result(verdict, mode="agent", reason="r"):
    return RunResult(spec="s", url="u", model="m", verdict=verdict, mode=mode, reason=reason)


def test_fail_then_pass_is_flaky():
    assert _combine([_result("fail"), _result("pass")]).verdict == "flaky"


def test_a_stale_recording_that_a_fresh_agent_passes_is_a_pass():
    combined = _combine([_result("fail", mode="replay"), _result("pass")])
    assert combined.verdict == "pass" and "re-recorded" in combined.warnings[0]


def test_failing_every_time_is_a_confirmed_fail():
    combined = _combine([_result("fail", reason="boom"), _result("fail", reason="boom")])
    assert combined.verdict == "fail" and combined.reason == "boom (failed 2 of 2 runs)"


# --- export -----------------------------------------------------------------------

def test_long_numbers_in_evidence_become_patterns():
    assert evidence_assertion("Order number: KC-10001") == \
        'await expect(page.locator("body")).toContainText(/Order number: KC-\\d+/);'
    assert evidence_assertion("Total: ₹180") == 'await expect(page.locator("body")).toContainText("Total: ₹180");'


def test_typed_values_read_placeholders_from_data():
    assert value_ts("{{email}}") == "data.email"
    assert value_ts("Dear {{name}}!") == "`Dear ${data.name}!`"
    assert value_ts("plain") == '"plain"'


# --- generate ---------------------------------------------------------------------

class _Writer:
    name = "writer"

    def ask(self, system, user, max_tokens=1500):
        assert "REQUIREMENT TO TEST" in user
        return {"specs": [
            {"name": "Reset Password!", "steps": ["open login", "click forgot password"],
             "expect": ["a reset email is sent"], "data": ["email"]},
            {"name": "broken", "steps": [], "expect": ["x"]},  # dropped: no steps
        ]}


def test_generated_specs_are_written_and_loadable(tmp_path, monkeypatch):
    specs = generate_specs(_Writer(), url="http://app.test/", story="Users can reset their password")
    assert [s["name"] for s in specs] == ["reset-password"]
    paths = write_specs(specs, tmp_path, url="http://app.test/")
    text = paths[0].read_text(encoding="utf-8")
    assert "${NS_EMAIL}" in text and "Draft written by" in text
    monkeypatch.setenv("NS_EMAIL", "someone@example.test")
    assert load_spec(paths[0]).data == {"email": "someone@example.test"}


# --- notify ------------------------------------------------------------------------

def test_slack_payload_lists_only_what_needs_attention(tmp_path):
    results = [_result("pass"), _result("fail", reason="HTTP 500 on POST /api/order")]
    payload = slack_payload(results, tmp_path, "https://ci.example.test/run/1")
    assert "1 pass, 1 fail" in payload["text"] and "HTTP 500" in payload["text"]
    assert "https://ci.example.test/run/1" in payload["text"]

    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(json.loads(request.content)) or httpx.Response(200))
    post_slack("https://hooks.example.test/x", payload, client=httpx.Client(transport=transport))
    assert sent == [payload]


# --- specs --------------------------------------------------------------------------

def test_the_demo_specs_load():
    names = {spec.name for spec in load_specs([SPECS])}
    assert {"add-to-cart", "checkout", "login", "search", "guest-checkout"} <= names


def test_every_planted_bug_names_real_specs():
    from demo_shop.server import BUGS

    names = {spec.name for spec in load_specs([SPECS])}
    assert len(BUGS) == 20
    for bug in BUGS.values():
        assert set(bug.specs) <= names


def test_base_url_override_keeps_the_path(tmp_path):
    spec_file = tmp_path / "s.yaml"
    spec_file.write_text("url: http://localhost:5180/shop?x=1\nsteps: [a]\nexpect: [b]\n", encoding="utf-8")
    spec = load_spec(spec_file).with_base_url("https://staging.example.test")
    assert spec.url == "https://staging.example.test/shop?x=1"
    assert spec.name == "s"


def test_data_reads_environment_variables(tmp_path, monkeypatch):
    spec_file = tmp_path / "s.yaml"
    spec_file.write_text("url: http://x.test/\nsteps: [a]\nexpect: [b]\ndata: {password: '${SHOP_PW}'}\n",
                         encoding="utf-8")
    monkeypatch.setenv("SHOP_PW", "from-env")
    spec = load_spec(spec_file)
    assert spec.data == {"password": "from-env"}
    assert spec.raw_data == {"password": "${SHOP_PW}"}
    monkeypatch.delenv("SHOP_PW")
    with pytest.raises(SpecError, match="SHOP_PW is not set"):
        load_spec(spec_file)


def test_a_spec_without_steps_is_rejected(tmp_path):
    spec_file = tmp_path / "s.yaml"
    spec_file.write_text("url: http://x.test/\nexpect: [b]\n", encoding="utf-8")
    with pytest.raises(SpecError, match="steps"):
        load_spec(spec_file)
