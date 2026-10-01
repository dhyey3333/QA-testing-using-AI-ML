"""Can the harness physically operate the holdout app's idioms?

These use a scripted model, so they say nothing about how well a real model does
on the clinic (that is the benchmark's job, and nothing may be tuned to it).
They only prove the plumbing works: a <div role="button">, a date input, a
<select>, a checkbox, server-side form posts, and a native confirm() dialog.
"""

import threading
from pathlib import Path

import pytest

from holdout.server import make_server
from nightshift.runner import RunOptions, run_spec
from nightshift.spec import load_spec
from scripted import ScriptedModel

SPECS = Path(__file__).resolve().parent.parent / "holdout" / "specs"

BOOK = [
    ("click", "View profile"),  # the first doctor card is Dr. Asha Iyer
    ("click", "Book with Dr. Asha Iyer"),
    ("type", "Date", "{{appointment_date}}"),
    ("select", "Time", "10:30"),
    ("type", "Patient's full name", "{{patient_name}}"),
    ("type", "Mobile number", "{{mobile}}"),
    ("type", "Reason for visit", "{{reason}}"),
    ("click", "privacy policy"),
    ("click", "Review booking"),
    ("click", "Confirm booking"),
]
BOOKED = ["Appointment booked. Reference SC-1002", "Dr. Asha Iyer", "14 Mar 2031 at 10:30", "Fee ₹600"]


@pytest.fixture(scope="module")
def clinic():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def run_clinic(clinic, browser, tmp_path):
    def _run(name, script, evidence, bugs=()):
        clinic.bugs = set(bugs)
        clinic.reset()
        spec = load_spec(SPECS / f"{name}.yaml").with_base_url(f"http://127.0.0.1:{clinic.server_port}")
        return run_spec(browser, spec, ScriptedModel(script, evidence), out_dir=tmp_path / name, options=RunOptions())

    yield _run
    clinic.bugs = set()


def test_the_booking_wizard_can_be_driven(run_clinic):
    result = run_clinic("book-appointment", BOOK, BOOKED)
    assert result.verdict == "pass", result.reason
    kinds = {step.description.split(" ")[0] for step in result.steps}
    assert {"click", "type", "select"} <= kinds


def test_a_dead_div_button_is_caught(run_clinic):
    script = BOOK[:-2] + [("click", "Review booking")] * 3
    result = run_clinic("book-appointment", script, BOOKED, bugs={"continue-dead"})
    assert result.verdict == "fail" and "had no effect" in result.reason


def test_a_dropdown_option_is_found_from_a_partial_name(run_clinic):
    # The option reads "Dr. Rahul Menon (General physician)"; the model only wrote the name.
    script = [("select", "Doctor", "Dr. Rahul Menon"), ("type", "Date", "{{appointment_date}}"),
              ("select", "Time", "16:00"), ("type", "Patient's full name", "{{patient_name}}"),
              ("type", "Mobile number", "{{short_mobile}}"), ("click", "privacy policy"), ("click", "Review booking")]
    result = run_clinic("bad-phone", script, ["Enter a 10-digit mobile number."])
    assert result.verdict == "pass", result.reason
    assert all(not step.outcome.startswith("failed") for step in result.steps)


def test_a_native_confirm_dialog_is_accepted(run_clinic):
    result = run_clinic("cancel-appointment", [("click", "a:My appointments"), ("click", "Cancel SC-1001")],
                        ["Appointment SC-1001 cancelled."])
    assert result.verdict == "pass", result.reason
    assert any(w.startswith("confirm dialog accepted") for w in result.warnings)
