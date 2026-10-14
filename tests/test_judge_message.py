"""The judge must quote a message that says what a "says ..." expectation states (judge.message_unsaid).

Found on the blind set 3 recheck (research/part-c-results.md): for "the page says your message could not be
sent" the judge quoted "Error: all fields are required", a real line about something else, and a broken
flow passed.
"""

import pytest

from nightshift.judge import message_unsaid, verify
from nightshift.observe import Observation
from nightshift.spec import Spec


@pytest.mark.parametrize("expected, said", [
    ("the page says your message could not be sent", "Your message could not be sent"),
    ("the page says your message could not be sent", "Sorry, we were unable to send your message"),
    ("a message says the order was placed", "Thank you! Your order has been placed."),
    ("an alert says validation failed", "alert: validation failed (closed with OK)"),
    ("the page says the item was added to your cart", "Filter Coffee was added to the cart"),
    ("the page says Welcome {{username}}", "Welcome {{username}}"),
    ("the header greets the logged-in user by name", "Hi, Test Shopper"),  # no "says": not a message claim
    # "Shows" states a value, not a message (found on the demo shop when it was included):
    ("the header's cart link shows 2 items", "Cart (2)"),
    ('the header shows a "Log in" link again', "Log in"),
])
def test_evidence_that_says_the_message_counts(expected, said):
    assert message_unsaid(expected, said) == ""


@pytest.mark.parametrize("expected, said, why", [
    ("the page says your message could not be sent", "Error: all fields are required", 'never says "message", "sent"'),
    ("the page says your message could not be sent", "Your message has been sent", "negative part"),  # the opposite
    ("the page says the payment failed", "Transaction declined", "never says"),  # too loose a test: reword it
])
def test_evidence_that_says_something_else_does_not(expected, said, why):
    assert why in message_unsaid(expected, said)


def test_a_negative_part_can_be_proven_by_absence():
    expected = "the list says buy milk and not walk the dog"
    assert message_unsaid(expected, "buy milk", proven_absent=True) == ""
    assert "negative part" in message_unsaid(expected, "buy milk")


def _verify(expected, text, evidence, absent=()):
    spec = Spec(name="t", url="https://app.test/", steps=("x",), expect=(expected,))
    observation = Observation(url="https://app.test/contact", title="t", text=text, elements=())
    raw = {"checks": [{"expected": expected, "evidence": list(evidence), "absent": list(absent), "why": "w",
                       "holds": True}]}
    return verify(raw, spec, observation)


def test_the_false_pass_is_caught_and_the_judge_hears_why():
    checks, problems = _verify("the page says your message could not be sent",
                               "Contact us\nError: all fields are required\nFirst Name", ["Error: all fields are required"])
    assert checks[0].holds is False
    assert problems and 'never says "message", "sent"' in problems[0] and "quote the text" in problems[0]


def test_a_quote_that_says_the_message_still_proves_it():
    checks, problems = _verify("the page says your message could not be sent",
                               "Contact us\nSorry, your message could not be sent.", ["your message could not be sent"])
    assert checks[0].holds is True and problems == []
