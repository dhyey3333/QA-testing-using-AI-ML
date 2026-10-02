"""Part C step 3, hard flows: a payment widget in a cross-origin iframe, authenticator-app codes,
and edge cases generated from one passing test."""

from test_e2e import LOGIN

PAY = [*LOGIN, ("click", "Add Filter Coffee"), ("click", "Cart ("), ("click", "Proceed to checkout"),
       ("type", "Full name", "{{full_name}}"), ("type", "Address", "{{address}}"), ("type", "City", "{{city}}"),
       ("type", "Pincode", "{{pincode}}"), ("click", "Pay online now"), ("click", "Place order"),
       ("type", "UPI ID", "{{upi_id}}"), ("click", "Pay now")]


def test_paying_inside_a_cross_origin_gateway_iframe(run):
    # The gateway is served from another origin (localhost vs 127.0.0.1), like Razorpay's checkout.
    result = run("pay-online", PAY, evidence=["Order placed!", "Paid ₹240 online."])
    assert result.verdict == "pass", result.reason
    upi = next(step for step in result.steps if step.target_label == "UPI ID")
    assert upi.outcome == "changed"  # typed into a field inside the iframe


def test_a_declined_payment_is_reported_and_no_order_is_placed(run):
    result = run("pay-online-failure", PAY, evidence=["Payment failed. Your order was not placed."])
    assert result.verdict == "pass", result.reason


def test_an_order_placed_after_a_declined_payment_is_a_bug(run):
    result = run("pay-online-failure", PAY, bugs={"payment-failure-ignored"},
                 evidence=["Payment failed. Your order was not placed."])
    assert result.verdict == "fail" and result.category == "BUG"


def test_totp_matches_the_rfc_6238_test_vectors():
    from nightshift.totp import totp

    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890", from the RFC
    assert totp(secret, at=59, digits=8) == "94287082"
    assert totp(secret, at=1111111109, digits=8) == "07081804"
    assert totp(secret, at=20000000000, digits=8) == "65353130"


def test_an_authenticator_code_gets_past_two_factor_sign_in(browser, base_url, tmp_path):
    from nightshift.runner import run_spec
    from nightshift.spec import Spec
    from scripted import ScriptedModel

    spec = Spec(name="2fa", url=f"{base_url}/lab/two-factor.html", steps=("verify with the authenticator code",),
                expect=("the two-factor check passed",), data={"totp_secret": "JBSWY3DPEHPK3PXP"}, max_steps=6)
    script = [("type", "Authenticator code", "{{totp_code}}"), ("click", "Verify")]
    result = run_spec(browser, spec, ScriptedModel(script, evidence=["Two-factor check passed."]), out_dir=tmp_path / "t")
    assert result.verdict == "pass", result.reason
    log = "\n".join(step.history_line() for step in result.steps)
    assert "JBSWY3DPEHPK3PXP" not in log  # the secret never shows up in the run's record


def test_the_authenticator_secret_itself_is_never_typed():
    import pytest

    from nightshift.actions import InvalidAction, validate_action
    from nightshift.observe import Element, Observation

    obs = Observation(url="https://x.test/", title="", text="", elements=(Element(1, "input", "Code", type="text"),))
    with pytest.raises(InvalidAction, match="never typed"):
        validate_action({"action": "type", "id": 1, "text": "{{totp_secret}}"}, obs, {"totp_secret": "JBSW"})


def test_edge_cases_are_generated_from_the_values_a_test_types(spec_for, tmp_path):
    from nightshift.edgecases import variants, write_variants
    from nightshift.spec import load_spec

    spec = spec_for("checkout")  # email, password, full_name, address, city, pincode
    cases = {case["name"]: case for case in variants(spec)}
    assert len(cases) == 2 * len(spec.data)  # empty, plus a wrong format or a too-long value, per value
    pincode = cases["checkout--pincode-format"]
    assert pincode["data"]["pincode"] == "12" and pincode["data"]["email"] == spec.raw_data["email"]
    assert pincode["expect"] == ["an error message about the pincode is shown"]
    assert cases["checkout--email-format"]["data"]["email"] == "not-an-email"
    assert len(cases["checkout--full-name-too-long"]["data"]["full_name"]) == 300
    assert "full_name" in cases["checkout--full-name-empty"]["data"]
    # Written specs load like any other, and the same spec always gives the same cases.
    paths = write_variants(spec, tmp_path)
    assert all(load_spec(path).technique == "edge case (generated)" for path in paths)
    assert [c["name"] for c in variants(spec)] == list(cases)


def test_a_generated_edge_case_runs_like_any_spec(browser, base_url, spec_for, tmp_path):
    from nightshift.edgecases import write_variants
    from nightshift.runner import run_spec
    from nightshift.spec import load_spec
    from scripted import ScriptedModel

    # checkout's generated variants: run "city left empty" through the real loop.
    assert write_variants(spec_for("checkout-validation"), tmp_path / "none") == []  # already a negative test
    write_variants(spec_for("checkout"), tmp_path)
    spec = load_spec(tmp_path / "checkout--city-empty.yaml").with_base_url(base_url)
    assert spec.data["city"] == ""
    script = [*LOGIN, ("click", "Add Filter Coffee"), ("click", "Cart ("), ("click", "Proceed to checkout"),
              ("type", "Full name", "{{full_name}}"), ("type", "Address", "{{address}}"), ("type", "City", "{{city}}"),
              ("type", "Pincode", "{{pincode}}"), ("click", "Cash on delivery"), ("click", "Place order")]
    model = ScriptedModel(script, evidence=["Please fill in your name, address and city."])
    result = run_spec(browser, spec, model, out_dir=tmp_path / "run")
    assert result.verdict == "pass", result.reason
