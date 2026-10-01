"""The public-sites benchmark: its specs load, its flow names and its cost maths are right."""

from benchmark.public_sites import FLOWS, SPECS, USD_INR, cost_inr, flow_of, load_rows, render, row_for, save
from nightshift.spec import load_spec


def test_every_public_spec_loads_and_names_a_known_flow(monkeypatch):
    monkeypatch.setenv("NS_RUN", "20261001120000")
    paths = sorted(SPECS.glob("*.yaml"))
    specs = [load_spec(path) for path in paths]
    assert len(specs) >= 25
    for path, spec in zip(paths, specs):
        assert spec.name == path.stem  # the table groups by the file name's site and flow
        site, flow = flow_of(spec.name)
        assert flow != "other", spec.name
        assert site and site != spec.name
    signup = next(s for s in specs if s.name == "automationexercise-signup")
    assert signup.data["email"] == "ns20261001120000@example.com"  # a fresh account every run


def test_flow_names():
    assert flow_of("saucedemo-add-to-cart") == ("saucedemo", "add-to-cart")
    assert flow_of("the-internet-login-validation") == ("the-internet", "form validation")
    assert flow_of("expandtesting-signup-validation") == ("expandtesting", "form validation")
    assert flow_of("demoblaze-signup") == ("demoblaze", "signup")
    assert "login" in FLOWS and FLOWS.index("login-validation") < FLOWS.index("login")


def test_a_stopped_benchmark_keeps_what_it_measured(tmp_path):
    # Found on the first full run: the session ended 2 specs in, and nothing had been written.
    from nightshift.result import RunResult

    result = RunResult(spec="saucedemo-login", url="https://www.saucedemo.com/", model="m", out_dir=str(tmp_path / "x"))
    result.verdict, result.reason, result.duration_s = "pass", "every expected result is shown", 12.5
    result.model_calls, result.prompt_tokens, result.completion_tokens = 3, 6_000, 120
    report = save([row_for(result)], tmp_path, "qwen3-vl:4b")
    rows = load_rows(tmp_path)
    assert rows[0]["spec"] == "saucedemo-login" and rows[0]["flow"] == "login"
    assert "| saucedemo | login | pass | 12 | 3 | 6,120 |" in report
    assert render(rows, "m") == report.replace("qwen3-vl:4b", "m")


def test_reruns_get_their_own_table():
    row = {"spec": "saucedemo-login", "site": "saucedemo", "flow": "login", "verdict": "pass", "reason": "ok",
           "duration_s": 19.0, "model_calls": 5, "prompt_tokens": 9000, "completion_tokens": 300,
           "cost_inr_qwen3-vl-8b": 0.12, "cost_inr_qwen3-vl-235b": 0.25, "out_dir": "x",
           "rerun": {"verdict": "pass", "mode": "replay", "duration_s": 6.0, "model_calls": 0, "cost_inr_qwen3-vl-8b": 0.0}}
    report = render([row], "m")
    assert "| saucedemo | login | pass | pass | replay | 19 | 6 | 0 | 0.00 |" in report
    assert "Reruns: 1/1 pass, median 6s, 1 with no model call" in report


def test_cost_in_rupees():
    # 1M input tokens at $0.117 plus 1M output at $0.455 = $0.572
    assert abs(cost_inr(1_000_000, 1_000_000, "qwen3-vl-8b") - 0.572 * USD_INR) < 1e-9
    assert cost_inr(0, 0, "qwen3-vl-235b") == 0
