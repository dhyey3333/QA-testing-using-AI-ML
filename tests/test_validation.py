"""Requirements in, test cases designed, the app validated, defects analysed."""

import csv
import json
from pathlib import Path

from nightshift.actions import Action
from nightshift.defects import analyse, classify, load_results, write_defects
from nightshift.generate import Requirement, design_tests, parse_requirements, write_specs
from nightshift.result import Check, RunResult, Step
from nightshift.spec import Spec, load_spec
from nightshift.traceability import build_matrix, write_test_cases, write_traceability

REQS = """
# Contact

Some prose that is not a requirement.

- Visitors can send a message through the Contact Us form.
- REQ-7: The form rejects an email address without an @.
1. Search shows matching breads.
R9. Blog posts can be filtered by tag.
"""


def test_requirements_are_read_from_bullets_numbers_and_ids():
    reqs = parse_requirements(REQS)
    assert [r.id for r in reqs] == ["R1", "REQ-7", "R2", "R9"]
    assert reqs[1].text == "The form rejects an email address without an @."


class _Designer:
    name = "designer"

    def ask(self, system, user, max_tokens=1500):
        assert "REQUIREMENT REQ-7" in user
        return {"cases": [
            {"title": "Valid email is accepted", "technique": "positive", "priority": "high",
             "steps": ["open Contact Us", "fill the form with email"], "expect": ["a thank-you message"],
             "data": {"email": "visitor@example.test"}},
            {"title": "Email without @ is rejected", "technique": "negative", "priority": "high",
             "steps": ["open Contact Us", "type the bad_email into Your Email", "submit"],
             "expect": ["the email field is flagged as invalid"], "data": {"bad_email": "not-an-email", "password": "x"}},
            {"title": "no steps", "steps": [], "expect": ["x"]},
        ]}


def test_designed_cases_carry_their_requirement_technique_and_priority(tmp_path):
    cases = design_tests(_Designer(), Requirement("REQ-7", "The form rejects an email address without an @."),
                         url="http://app.test/")
    assert [c["technique"] for c in cases] == ["positive", "negative"]
    paths = write_specs(cases, tmp_path, url="http://app.test/", known_data={"password": "${NS_PASSWORD}"})
    raw = paths[1].read_text(encoding="utf-8")
    assert "${NS_PASSWORD}" in raw and "password: x" not in raw  # a credential never lands in the file
    assert "bad_email: not-an-email" in raw


def test_designed_case_loads_with_its_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("NS_PASSWORD", "secret")
    cases = design_tests(_Designer(), Requirement("REQ-7", "x"), url="http://app.test/")
    paths = write_specs(cases, tmp_path, url="http://app.test/", known_data={"password": "${NS_PASSWORD}"})
    spec = load_spec(paths[1])
    assert spec.requirements == ("REQ-7",) and spec.technique == "negative" and spec.priority == "high"
    assert spec.title == "Email without @ is rejected"


def _spec(name, reqs, technique="positive"):
    return Spec(name=name, url="http://app.test/", steps=("s",), expect=("e",), requirements=tuple(reqs),
                technique=technique, priority="high")


def _result(spec, verdict, reason="", out_dir="", **extra):
    return RunResult(spec=spec, url="http://app.test/", model="m", verdict=verdict, reason=reason,
                     out_dir=out_dir, **extra)


def test_the_matrix_says_which_requirements_pass_fail_or_are_untested(tmp_path):
    reqs = [Requirement("R1", "a"), Requirement("R2", "b"), Requirement("R3", "c"), Requirement("R4", "d")]
    specs = [_spec("t1", ["R1"]), _spec("t2", ["R2"]), _spec("t3", ["R2"], "negative"), _spec("t4", ["R3"])]
    results = [_result("t1", "pass", out_dir=str(tmp_path / "t1")), _result("t2", "pass", out_dir=str(tmp_path / "t2")),
               _result("t3", "fail", "boom", out_dir=str(tmp_path / "t3")), _result("t4", "error", out_dir=str(tmp_path / "t4"))]
    rows = build_matrix(reqs, specs, results, [])
    assert [row.status for row in rows] == ["pass", "fail", "blocked", "untested"]
    write_traceability(rows, tmp_path, results)
    with (tmp_path / "traceability.csv").open(encoding="utf-8-sig") as handle:
        table = list(csv.DictReader(handle))
    assert [r["Requirement"] for r in table] == ["R1", "R2", "R2", "R3", "R4"]
    assert (tmp_path / "traceability.html").exists() and (tmp_path / "traceability.md").exists()


def test_the_test_case_document_has_one_row_per_case(tmp_path):
    specs = [_spec("t1", ["R1"]), _spec("t2", ["R2", "R3"], "boundary")]
    path = write_test_cases(specs, tmp_path / "cases.csv", {"t1": _result("t1", "pass", "fine")})
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Last result"] == "pass: fine" and rows[1]["Requirements"] == "R2, R3"
    assert path.with_suffix(".md").exists()


# --- defect analysis --------------------------------------------------------------

def _failed(spec, reason, *, app_errors=(), checks=(), final_text="", label="Place order"):
    step = Step(3, Action("click", id=5), f'click [5] "{label}"', outcome="changed", target_label=label)
    return _result(spec, "fail", reason, app_errors=list(app_errors), checks=list(checks), steps=[step],
                   final_url="http://app.test/cart", final_text=final_text)


def test_failures_are_classified():
    assert classify(_failed("a", "HTTP 500 on POST /api/order", app_errors=["HTTP 500 on POST /api/order"]))[0] == "server error"
    assert classify(_failed("a", "x", app_errors=["uncaught JS error: boom"]))[:2] == ("frontend crash", "js:boom")
    assert classify(_failed("a", 'click [5] "Place order" had no effect 3 times in a row'))[0] == "dead control"
    wrong = _failed("a", "not shown: total", checks=[Check("the total is ₹240", [], "it is ₹180", False)])
    assert classify(wrong)[0] == "wrong or missing result"


def test_one_root_cause_across_tests_is_one_defect():
    crash = ["uncaught JS error: Cannot read properties of null"]
    defects = analyse([_failed("browse", "x", app_errors=crash), _failed("search", "x", app_errors=crash),
                       _failed("checkout", "HTTP 500 on POST /api/order", app_errors=["HTTP 500 on POST /api/order"]),
                       _result("login", "pass")])
    assert len(defects) == 2
    crash_defect = next(d for d in defects if d.category == "frontend crash")
    assert crash_defect.specs == ["browse", "search"] and crash_defect.area == "frontend"


def test_what_changed_since_the_last_pass_is_shown(tmp_path):
    runs = tmp_path / "runs"
    passed = runs / "20260101-000000" / "cart"
    passed.mkdir(parents=True)
    (passed / "result.json").write_text(json.dumps({"final_text": "Your cart\nTotal: ₹600\nCheckout"}), encoding="utf-8")
    (runs / "20260101-000000" / "summary.json").write_text(
        json.dumps([{"spec": "cart", "verdict": "pass", "out_dir": str(passed)}]), encoding="utf-8")

    now = _failed("cart", "not shown: total", final_text="Your cart\nTotal: ₹180\nCheckout",
                  checks=[Check("the total is ₹600", [], "it is ₹180", False)])
    now.out_dir = str(runs / "20260102-000000" / "cart")
    defects = analyse([now], runs)
    assert defects[0].failures[0].changes == ["-Total: ₹600", "+Total: ₹180"]
    write_defects(defects, runs / "20260102-000000")
    text = (runs / "20260102-000000" / "defects.md").read_text(encoding="utf-8")
    assert "What changed since it last passed" in text and "+Total: ₹180" in text


def test_a_planted_bug_breaking_two_tests_is_one_defect_end_to_end(run, tmp_path):
    # Real browser, real shop: the js-error bug crashes the cart page, which two specs visit.
    first = run("add-to-cart", [("click", "Add Masala Chai"), ("click", "Cart (")], bugs={"js-error"}, evidence=["Total"])
    second = run("remove-from-cart", [("click", "Add Masala Chai"), ("click", "Cart (")], bugs={"js-error"},
                 evidence=["Total"])
    defects = analyse([first, second])
    assert len(defects) == 1 and defects[0].category == "frontend crash"
    assert defects[0].specs == ["add-to-cart", "remove-from-cart"]
    assert "Cart" in first.final_text and first.final_url.endswith("#/cart")


def test_a_finished_run_can_be_read_back_for_triage(tmp_path):
    out = tmp_path / "run" / "checkout"
    out.mkdir(parents=True)
    failed = _failed("checkout", "HTTP 500 on POST /api/order", app_errors=["HTTP 500 on POST /api/order"])
    failed.out_dir = str(out)
    (out / "result.json").write_text(json.dumps(failed.to_json(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "run" / "summary.json").write_text(json.dumps([failed.summary()]), encoding="utf-8")
    [loaded] = load_results(tmp_path / "run")
    assert loaded.app_errors == ["HTTP 500 on POST /api/order"] and loaded.steps[0].action.kind == "click"
    assert Path(loaded.out_dir) == out


# --- design review, goto, re-select ------------------------------------------------

def test_the_design_review_sends_back_missing_examples_and_made_up_wording():
    from nightshift.generate import review_design

    req = Requirement("R4", 'The Yeast tag shows "Tracking Wild Yeast" but not "Desserts with Benefits".')
    vague = [{"expect": ["The page displays only blog posts tagged 'Yeast'"], "steps": ["open the Blog"]}]
    problems = review_design(req, vague)
    assert any("Tracking Wild Yeast" in p and "Desserts with Benefits" in p for p in problems)

    guessed = [{"expect": ["a message says 'Thank you for your message! We will reply soon.'"],
                "steps": ["open the Yeast tag", "Tracking Wild Yeast", "Desserts with Benefits"]}]
    assert any("not in the requirement" in p for p in review_design(req, guessed))


class _Reviser:
    name = "reviser"

    def __init__(self):
        self.calls = []

    def ask(self, system, user, max_tokens=1500):
        self.calls.append(user)
        if len(self.calls) == 1:
            return {"cases": [{"title": "vague", "steps": ["open the Blog"], "expect": ["only Yeast posts show"]}]}
        assert "YOUR LAST DESIGN HAD PROBLEMS" in user and "Desserts with Benefits" in user
        return {"cases": [{"title": "example", "start": "/blog/", "steps": ["click the Yeast tag", "type the password"],
                           "expect": ["Tracking Wild Yeast is listed", "Desserts with Benefits is not listed"],
                           "data": {"password": ""}}]}


def test_a_flawed_design_gets_one_revision_with_the_findings():
    model = _Reviser()
    req = Requirement("R4", 'The Yeast tag shows "Tracking Wild Yeast" but not "Desserts with Benefits".')
    [case] = design_tests(model, req, url="http://app.test/", data_keys=("password",))
    assert "TEST DATA AVAILABLE" in model.calls[0] and "password" in model.calls[0]
    assert case["title"] == "example" and case["url"] == "http://app.test/blog/"


def test_a_test_may_start_on_a_path_but_never_on_another_host():
    class _Hop:
        name = "hop"

        def ask(self, system, user, max_tokens=1500):
            return {"cases": [{"title": "t", "start": "//evil.test/x", "steps": ["s"], "expect": ["e"]}]}

    [case] = design_tests(_Hop(), Requirement("R1", "e"), url="http://app.test/")
    assert case["url"] == "http://app.test/"


def test_goto_takes_only_a_path_on_this_site():
    from nightshift.actions import InvalidAction, validate_action
    from nightshift.observe import Observation

    page = Observation(url="http://app.test/", title="", text="", elements=())
    assert validate_action({"action": "goto", "url": "/admin/"}, page, {}).value == "/admin/"
    for bad in ("https://evil.test/", "//evil.test/", "admin"):
        try:
            validate_action({"action": "goto", "url": bad}, page, {})
        except InvalidAction:
            continue
        raise AssertionError(f"{bad} was accepted")


def test_reselecting_the_current_option_is_refused():
    from nightshift.actions import InvalidAction, validate_action
    from nightshift.observe import Element, Observation

    page = Observation(url="u", title="", text="", elements=(
        Element(id=16, tag="select", label="Purpose", value="Comment", options=("Comment", "Question")),))
    try:
        validate_action({"action": "select", "id": 16, "value": "comment"}, page, {})
    except InvalidAction as exc:
        assert "already shows" in str(exc)
    else:
        raise AssertionError("re-selecting was accepted")


def test_goto_works_in_a_real_browser(browser, base_url, tmp_path):
    from nightshift.runner import run_spec
    from scripted import ScriptedModel

    spec = Spec(name="lab", url=f"{base_url}/", steps=("go to /lab/opened.html",), expect=("the details page",))
    model = ScriptedModel([("raw", {"action": "goto", "url": "/lab/opened.html"})], evidence=["Details page"])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "goto")
    assert result.verdict == "pass", result.reason and result.final_url.endswith("/lab/opened.html")
