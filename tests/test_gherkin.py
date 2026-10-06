"""Cucumber features in and out."""

import pytest

from nightshift.gherkin import GherkinError, parse_feature, spec_to_feature
from nightshift.spec import Spec

FEATURE = '''
# The shop's checkout, as the team already wrote it for Cucumber.
@checkout
Feature: Checkout
  People buy tea and coffee.

  Background:
    Given I am on "https://shop.example.test/"
    And I am logged in as the test shopper

  @REQ-12
  Scenario: Pay cash on delivery
    When I add Filter Coffee to the cart
    And I check out with these details:
      | city | Pune   |
      | pin  | 411001 |
    Then the order is placed
    But no card is charged

  Scenario Outline: Wrong pincode
    When I check out with pincode <pin>
    Then an error says "<message>"

    Examples:
      | pin   | message                 |
      | 4110  | Pincode must be 6 digits |
      | abcde | Pincode must be 6 digits |

  Scenario: Leave a note
    When I add this note:
      """
      Ring the bell twice
      """
'''


def test_scenarios_become_specs_with_background_steps_first():
    specs = parse_feature(FEATURE)
    assert [s["name"] for s in specs] == ["pay-cash-on-delivery", "wrong-pincode-1", "wrong-pincode-2", "leave-a-note"]
    cash = specs[0]
    assert cash["url"] == "https://shop.example.test/"
    assert cash["steps"] == ['I am on "https://shop.example.test/"', "I am logged in as the test shopper",
                             "I add Filter Coffee to the cart", "I check out with these details: (city, Pune) (pin, 411001)"]
    assert cash["expect"] == ["the order is placed", "no card is charged"]  # But stays an expected result
    assert cash["requirements"] == ["REQ-12"] and cash["review"] == []


def test_an_outline_becomes_one_spec_per_example_row():
    first, second = parse_feature(FEATURE)[1:3]
    assert first["steps"][-1] == "I check out with pincode 4110" and second["steps"][-1] == "I check out with pincode abcde"
    assert first["expect"] == ['an error says "Pincode must be 6 digits"']
    assert first["title"] == "Wrong pincode (pin=4110, message=Pincode must be 6 digits)"


def test_what_a_feature_leaves_out_is_flagged_for_review():
    note = parse_feature(FEATURE)[3]
    assert note["steps"][-1] == 'I add this note: (text: "Ring the bell twice")'
    assert note["review"] == ["the scenario has no Then: say what should happen"]
    bare = parse_feature("Feature: X\n  Scenario: Y\n    When I search\n    Then results show\n")[0]
    assert bare["url"] == "https://example.com/" and "set the website" in bare["review"][0]
    assert parse_feature("Feature: X\n  Scenario: Y\n    When I search\n    Then results show\n",
                         url="https://staging.test/")[0]["url"] == "https://staging.test/"
    with pytest.raises(GherkinError):
        parse_feature("just some notes, not a feature")


def test_a_spec_exports_as_a_scenario():
    spec = Spec(name="checkout", url="https://shop.test/", steps=("add coffee", "check out"), expect=("order placed",),
                title="Checkout works")
    assert spec_to_feature(spec) == ('Feature: Checkout works\n\n  Scenario: checkout\n    Given I open "https://shop.test/"\n'
                                     "    When add coffee\n    And check out\n    Then order placed\n")
    round_trip = parse_feature(spec_to_feature(spec))[0]
    assert round_trip["steps"][1:] == ["add coffee", "check out"] and round_trip["expect"] == ["order placed"]
