"""Part C step 1, any website: fixes for what broke on public demo sites (research/my-tool-vs-market.md, B2)."""

import pytest

from nightshift.actions import Action, InvalidAction, validate_action
from nightshift.cli import _goal_spec
from nightshift.observe import Element, Observation, observe
from nightshift.prompts import _repeat_note, mask
from nightshift.result import Step
from nightshift.spec import goal_expectation, load_spec


def test_identical_buttons_carry_the_name_of_their_item(browser, base_url):
    # automationexercise and GreenKart: every product's button says "Add to cart", and the
    # model clicked the wrong one or none at all.
    page = browser.new_page()
    try:
        page.goto(f"{base_url}/lab/product-grid.html")
        labels = [e.label for e in observe(page).elements]
    finally:
        page.close()
    assert "Add to cart — Blue Top" in labels  # the price heading "Rs. 500" is skipped for the name
    assert "Add to cart — Men Tshirt" in labels
    assert "Edit — Asha Rao" in labels and "Edit — Vikram Shah" in labels  # table rows
    assert "ADD TO CART — Cucumber - 1 Kg" in labels  # labels are compared ignoring case
    assert "Checkout" in labels  # a unique label is left alone


def test_masking_hides_whole_values_only():
    # lambdatest's playground: first name "Test" turned "Web Automation Testing" into "...{{first_name}}ing".
    data = {"first_name": "Test", "email": "ns1@example.com"}
    assert mask("Web Automation Testing", data) == "Web Automation Testing"
    assert mask("Hi Test, mail ns1@example.com.", data) == "Hi {{first_name}}, mail {{email}}."


def test_a_wrong_element_id_is_answered_with_the_real_controls():
    # globalsqa: the model asked for [9] three times after "there is no element [9]".
    obs = Observation(url="https://x.test/", title="", text="", elements=(
        Element(1, "a", "Home"), Element(2, "input", "Amount", type="number"), Element(3, "button", "Withdraw")))
    with pytest.raises(InvalidAction) as info:
        validate_action({"action": "click", "id": 9}, obs, {})
    assert "ids go from 1 to 3" in str(info.value) and '[3] "Withdraw"' in str(info.value)


def test_the_same_action_three_times_gets_a_nudge():
    click = Action("click", id=11)
    same = tuple(Step(i, click, 'click [11] "Search"', outcome="changed") for i in (1, 2, 3))
    assert "last 3 actions were the same" in _repeat_note(same)
    assert _repeat_note(same[:2]) == ""
    scrolls = tuple(Step(i, Action("scroll", direction="down"), "scroll down", outcome="changed") for i in (1, 2, 3))
    assert _repeat_note(scrolls) == ""  # scrolling down a long page is fine


def test_a_url_and_a_goal_make_a_spec(tmp_path):
    path = _goal_spec("https://www.saucedemo.com/", "add the Sauce Labs Backpack to the cart", tmp_path)
    spec = load_spec(path)
    assert spec.steps == ("add the Sauce Labs Backpack to the cart",)
    assert spec.expect == (goal_expectation("add the Sauce Labs Backpack to the cart"),)
    assert spec.name == "add-the-sauce-labs-backpack-to-the-cart"
    # The same goal again reuses the file, so its saved path is replayed next time.
    assert _goal_spec("https://www.saucedemo.com/", "add the Sauce Labs Backpack to the cart", tmp_path) == path
