"""API tests against the real demo shop: no browser, no model, deterministic."""

from pathlib import Path

import pytest

from nightshift.api import ApiSpecError, _lookup, _matches, parse_requests
from nightshift.defects import analyse
from nightshift.runner import run_with_retries
from nightshift.spec import load_spec

API = Path(__file__).resolve().parent.parent / "specs" / "api"


@pytest.fixture
def api_run(shop, tmp_path):
    def _run(name, bugs=()):
        shop.bugs = set(bugs)
        spec = load_spec(API / f"{name}.yaml").with_base_url(f"http://127.0.0.1:{shop.server_port}")
        return run_with_retries(None, spec, model=type("NoModel", (), {"name": "none"})(), out_dir=tmp_path / name)

    yield _run
    shop.bugs = set()


@pytest.mark.parametrize("name", ["api-login", "api-order", "api-order-validation"])
def test_every_api_spec_passes_on_the_clean_shop(api_run, name):
    result = api_run(name)
    assert result.verdict == "pass", result.reason
    assert all(check.holds for check in result.checks)
    assert (Path(result.out_dir) / "report.html").exists()


@pytest.mark.parametrize("bug, spec, says", [
    ("order-total-mismatch", "api-order", "total is 480 (got 530)"),
    ("no-order-number", "api-order", "orderId matches"),
    ("checkout-500", "api-order", "HTTP 500 on POST /api/order"),
    ("login-rejects", "api-login", "status is 200 (got 401)"),
    ("login-case-sensitive", "api-login", "sign in with capitals in the email: status is 200 (got 401)"),
])
def test_backend_bugs_are_caught_with_the_exact_reason(api_run, bug, spec, says):
    result = api_run(spec, bugs={bug})
    assert result.verdict == "fail"
    assert says in result.reason


def test_a_server_error_from_the_api_is_classified_like_one_from_the_browser(api_run):
    [defect] = analyse([api_run("api-order", bugs={"checkout-500"})])
    assert defect.category == "server error" and defect.area == "backend"


def test_paths_and_matchers():
    data = {"user": {"name": "T"}, "items": [{"id": "a"}, {"id": "b"}]}
    assert _lookup(data, "user.name") == (True, "T")
    assert _lookup(data, "items[1].id") == (True, "b")
    assert _lookup(data, "items.length") == (True, 2)
    assert _lookup(data, "items[5].id") == (False, None)
    assert _matches(True, "KC-10042", "/^KC-\\d+$/")[0]
    assert _matches(True, 240, 240.0)[0] and not _matches(True, 241, 240)[0]
    assert _matches(False, None, "<missing>")[0] and not _matches(True, "x", "<missing>")[0]
    assert _matches(True, "anything", "*")[0]


def test_a_request_must_stay_on_the_spec_site():
    with pytest.raises(ApiSpecError, match="must start with /"):
        parse_requests([{"get": "https://evil.example.test/"}])
    with pytest.raises(ApiSpecError, match="exactly one of"):
        parse_requests([{"get": "/a", "post": "/b"}])
