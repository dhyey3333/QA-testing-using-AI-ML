"""The client report: one self-contained file a QA team can send its client."""

import json

from nightshift.client_report import write_client_report
from nightshift.defects import record_issue
from test_e2e import CHECKOUT, CHECKOUT_EVIDENCE


def _run_dir(tmp_path, *results):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(json.dumps([r.summary() for r in results]), encoding="utf-8")
    return run_dir


def test_a_run_with_a_defect_becomes_a_client_report(run, spec_for, tmp_path):
    passed = run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE)
    failed = run("checkout", CHECKOUT, bugs={"checkout-500"}, evidence=CHECKOUT_EVIDENCE)
    run_dir = _run_dir(tmp_path, passed, failed)
    record_issue(run_dir, "D1", "jira", "SCRUM-7", "https://team.atlassian.net/browse/SCRUM-7")

    path = write_client_report(run_dir, client="Acme Retail", brand="Your QA Co", specs={"checkout": spec_for("checkout")})
    html = path.read_text(encoding="utf-8")
    assert "Test report: Acme Retail" in html and "Your QA Co" in html
    assert "Not ready to release: 1 defect found, 1 of high severity" in html
    assert "HTTP 500 on POST /api/order" in html  # what the browser saw
    assert "Steps to reproduce" in html and "Click &quot;Place order&quot;" in html
    assert "href='https://team.atlassian.net/browse/SCRUM-7'" in html  # the ticket it was filed as
    assert "data:image/jpeg;base64," in html  # the screenshot travels inside the file
    assert "Nightshift" not in html  # white-label: the agency's name, not ours
    assert html.count("<span class='pill pass'>PASSED</span>") == 1


def test_a_clean_run_says_so_and_lists_requirements(run, spec_for, tmp_path):
    from dataclasses import replace

    passed = run("checkout", CHECKOUT, evidence=CHECKOUT_EVIDENCE)
    run_dir = _run_dir(tmp_path, passed)
    spec = replace(spec_for("checkout"), title="Customer can place a cash-on-delivery order", requirements=("R3",))
    html = write_client_report(run_dir, client="Acme", specs={"checkout": spec}).read_text(encoding="utf-8")
    assert "No defects found in this run" in html
    assert "<td>R3</td><td>Customer can place a cash-on-delivery order</td>" in html
    assert "Prepared with Nightshift QA." in html  # no brand given
