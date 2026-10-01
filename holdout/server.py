"""Sehat Clinic: the holdout app. Appointment booking, with planted bugs.

It is built in idioms the demo shop never uses, on purpose: server-rendered pages
and real form posts instead of a hash-routed SPA, a <div role="button"> instead
of <button>, <select> dropdowns, a date input, a checkbox, a review-then-confirm
wizard, and a native confirm() dialog.

HOLDOUT RULE: never tune a prompt, a threshold or the agent against this app.
Its score is only honest while nothing has been fitted to it. Report it as is.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

DEFAULT_PORT = 5190


@dataclass(frozen=True)
class Bug:
    description: str
    specs: tuple[str, ...]


BUGS: dict[str, Bug] = {
    "continue-dead": Bug('The "Review booking" control does nothing', ("book-appointment",)),
    "wrong-doctor": Bug("The booking is saved with the next doctor in the list", ("book-appointment",)),
    "fee-wrong": Bug("The confirmation shows a tenth of the real fee", ("book-appointment",)),
    "booking-500": Bug("Confirming a booking returns HTTP 500", ("book-appointment",)),
    "past-date-accepted": Bug("A date in the past is accepted", ("past-date",)),
    "phone-validation-missing": Bug("A 5-digit mobile number is accepted", ("bad-phone",)),
    "consent-ignored": Bug("Booking goes through without agreeing to the privacy policy", ("consent-required",)),
    "cancel-does-nothing": Bug("Cancel says it worked but the appointment stays", ("cancel-appointment",)),
}

DOCTORS = [
    {"id": "iyer", "name": "Dr. Asha Iyer", "specialty": "Dermatology", "fee": 600, "hours": "Mon to Sat, 9:00 to 13:00"},
    {"id": "menon", "name": "Dr. Rahul Menon", "specialty": "General physician", "fee": 400, "hours": "Mon to Fri, 16:00 to 20:00"},
    {"id": "khan", "name": "Dr. Sana Khan", "specialty": "Paediatrics", "fee": 500, "hours": "Tue to Sun, 10:00 to 14:00"},
]
TIMES = ["09:00", "09:30", "10:00", "10:30", "11:00", "16:00", "16:30", "17:00"]
SEED = [{"ref": "SC-1001", "doctor": "menon", "date": "2031-05-02", "time": "11:00", "patient": "Test Patient",
         "mobile": "9000000001"}]


class ClinicServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], bugs: Iterable[str] = (), verbose: bool = False) -> None:
        super().__init__(address, ClinicHandler)
        self.bugs: set[str] = set(bugs)
        self.variants: set[str] = set()
        self.verbose = verbose
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Back to the seeded appointments, so every benchmark run starts from the same state."""
        with self.lock:
            self.appointments = [dict(a) for a in SEED]
            self.next_ref = 1002


def make_server(port: int = DEFAULT_PORT, bugs: Iterable[str] = (), verbose: bool = False) -> ClinicServer:
    return ClinicServer(("127.0.0.1", port), bugs=bugs, verbose=verbose)


def doctor(doctor_id: str) -> dict | None:
    return next((d for d in DOCTORS if d["id"] == doctor_id), None)


def pretty_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%a %d %b %Y")
    except ValueError:
        return iso


class ClinicHandler(BaseHTTPRequestHandler):
    server: ClinicServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)

    # --- routing ------------------------------------------------------------

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path == "/":
            self._page("Our doctors", self._home())
        elif parts.path.startswith("/doctors/"):
            found = doctor(parts.path.removeprefix("/doctors/"))
            if found is None:
                self._page("Not found", "<h1>Doctor not found</h1>", status=404)
            else:
                self._page(found["name"], self._profile(found))
        elif parts.path == "/book":
            self._page("Book an appointment", self._book_form({"doctor": query.get("doctor", "")}, []))
        elif parts.path == "/appointments":
            self._page("My appointments", self._appointments(query))
        else:
            self._page("Not found", "<h1>Page not found</h1>", status=404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}
        if path == "/book/review":
            errors = self._validate(form)
            if errors:
                self._page("Book an appointment", self._book_form(form, errors), status=422)
            else:
                self._page("Review your booking", self._review(form))
        elif path == "/book/confirm":
            self._confirm(form)
        elif path == "/appointments/cancel":
            ref = form.get("ref", "")
            if "cancel-does-nothing" not in self.server.bugs:
                with self.server.lock:
                    self.server.appointments = [a for a in self.server.appointments if a["ref"] != ref]
            self._redirect(f"/appointments?cancelled={quote(ref)}")
        else:
            self._page("Not found", "<h1>Page not found</h1>", status=404)

    # --- pages ----------------------------------------------------------------

    def _home(self) -> str:
        cards = "".join(
            f'<article class="card"><h2>{escape(d["name"])}</h2><p>{escape(d["specialty"])}</p>'
            f'<a href="/doctors/{d["id"]}">View profile</a></article>'
            for d in DOCTORS
        )
        return f"<h1>Our doctors</h1><div class=\"cards\">{cards}</div>"

    def _profile(self, d: dict) -> str:
        return (f"<h1>{escape(d['name'])}</h1><p class=\"lead\">{escape(d['specialty'])}</p>"
                f"<dl><dt>Consultation fee</dt><dd>₹{d['fee']}</dd><dt>Hours</dt><dd>{escape(d['hours'])}</dd></dl>"
                f'<a class="btn-link" href="/book?doctor={d["id"]}">Book with {escape(d["name"])}</a>')

    def _book_form(self, form: dict, errors: list[str]) -> str:
        def option(value: str, label: str, selected: str) -> str:
            return f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(label)}</option>'

        doctors = option("", "Choose a doctor", form.get("doctor", "")) + "".join(
            option(d["id"], f'{d["name"]} ({d["specialty"]})', form.get("doctor", "")) for d in DOCTORS)
        times = option("", "Choose a time", form.get("time", "")) + "".join(
            option(t, t, form.get("time", "")) for t in TIMES)
        error_list = ("<div class=\"errors\" role=\"alert\"><p>Please fix the following:</p><ul>"
                      + "".join(f"<li>{escape(e)}</li>" for e in errors) + "</ul></div>") if errors else ""
        checked = " checked" if form.get("consent") else ""
        return f"""
<h1>Book an appointment</h1>
{error_list}
<form id="booking" method="post" action="/book/review">
  <div class="field"><label for="doctor">Doctor</label><select id="doctor" name="doctor">{doctors}</select></div>
  <div class="row">
    <div class="field"><label for="date">Date</label><input id="date" name="date" type="date" value="{escape(form.get('date', ''))}"></div>
    <div class="field"><label for="time">Time</label><select id="time" name="time">{times}</select></div>
  </div>
  <div class="field"><label for="patient">Patient's full name</label><input id="patient" name="patient" value="{escape(form.get('patient', ''))}"></div>
  <div class="field"><label for="mobile">Mobile number</label><input id="mobile" name="mobile" type="tel" value="{escape(form.get('mobile', ''))}"></div>
  <div class="field"><label for="reason">Reason for visit</label><textarea id="reason" name="reason" rows="3">{escape(form.get('reason', ''))}</textarea></div>
  <div class="check"><input id="consent" name="consent" type="checkbox" value="yes"{checked}><label for="consent">I agree to the clinic's privacy policy</label></div>
  <div class="btn" role="button" tabindex="0" id="review">Review booking</div>
</form>
<script>
  // The control is a div, as in many real apps. BUG continue-dead: its handler is never attached.
  if (!window.BUGS.includes("continue-dead")) {{
    const review = document.getElementById("review");
    const go = () => document.getElementById("booking").submit();
    review.addEventListener("click", go);
    review.addEventListener("keydown", (e) => {{ if (e.key === "Enter" || e.key === " ") {{ e.preventDefault(); go(); }} }});
  }}
</script>"""

    def _validate(self, form: dict) -> list[str]:
        errors = []
        if doctor(form.get("doctor", "")) is None:
            errors.append("Choose a doctor.")
        try:
            day = date.fromisoformat(form.get("date", ""))
            if day < date.today() and "past-date-accepted" not in self.server.bugs:
                errors.append("Choose a date from today onwards.")
        except ValueError:
            errors.append("Choose a date.")
        if form.get("time") not in TIMES:
            errors.append("Choose a time.")
        if not form.get("patient", "").strip():
            errors.append("Enter the patient's name.")
        mobile = form.get("mobile", "").replace(" ", "")
        valid_mobile = mobile.isdigit() and (len(mobile) == 10 or "phone-validation-missing" in self.server.bugs)
        if not valid_mobile:
            errors.append("Enter a 10-digit mobile number.")
        if not form.get("consent") and "consent-ignored" not in self.server.bugs:
            errors.append("Please agree to the privacy policy to book.")
        return errors

    def _review(self, form: dict) -> str:
        d = doctor(form["doctor"])
        hidden = "".join(f'<input type="hidden" name="{k}" value="{escape(form.get(k, ""))}">'
                         for k in ("doctor", "date", "time", "patient", "mobile", "reason"))
        return f"""
<h1>Review your booking</h1>
<dl class="summary">
  <dt>Doctor</dt><dd>{escape(d["name"])}, {escape(d["specialty"])}</dd>
  <dt>When</dt><dd>{pretty_date(form["date"])} at {escape(form["time"])}</dd>
  <dt>Patient</dt><dd>{escape(form["patient"])}</dd>
  <dt>Mobile</dt><dd>{escape(form["mobile"])}</dd>
  <dt>Fee</dt><dd>₹{d["fee"]}, pay at the clinic</dd>
</dl>
<form method="post" action="/book/confirm">{hidden}<input class="btn" type="submit" value="Confirm booking"></form>
<p><a href="/book?doctor={escape(form["doctor"])}">Change something</a></p>"""

    def _confirm(self, form: dict) -> None:
        if "booking-500" in self.server.bugs:
            self._page("Server error", "<h1>Internal Server Error</h1><p>Something went wrong.</p>", status=500)
            return
        chosen = form.get("doctor", "")
        if "wrong-doctor" in self.server.bugs:
            ids = [d["id"] for d in DOCTORS]
            chosen = ids[(ids.index(chosen) + 1) % len(ids)] if chosen in ids else chosen
        with self.server.lock:
            ref = f"SC-{self.server.next_ref}"
            self.server.next_ref += 1
            self.server.appointments.append({"ref": ref, "doctor": chosen, "date": form.get("date", ""),
                                             "time": form.get("time", ""), "patient": form.get("patient", ""),
                                             "mobile": form.get("mobile", "")})
        self._redirect(f"/appointments?booked={ref}")

    def _appointments(self, query: dict) -> str:
        banner = ""
        booked = next((a for a in self.server.appointments if a["ref"] == query.get("booked")), None)
        if booked:
            d = doctor(booked["doctor"])
            fee = d["fee"] // 10 if "fee-wrong" in self.server.bugs else d["fee"]
            banner = (f'<div class="banner ok" role="status">Appointment booked. Reference {booked["ref"]}: '
                      f'{escape(d["name"])}, {pretty_date(booked["date"])} at {escape(booked["time"])}. '
                      f"Fee ₹{fee}, pay at the clinic.</div>")
        elif query.get("cancelled"):
            banner = f'<div class="banner ok" role="status">Appointment {escape(query["cancelled"])} cancelled.</div>'
        rows = "".join(
            f"<tr><td>{a['ref']}</td><td>{escape(doctor(a['doctor'])['name'])}</td><td>{pretty_date(a['date'])}</td>"
            f"<td>{escape(a['time'])}</td><td>{escape(a['patient'])}</td>"
            f'<td><form method="post" action="/appointments/cancel" '
            f"onsubmit=\"return confirm('Cancel appointment {a['ref']}?')\">"
            f'<input type="hidden" name="ref" value="{a["ref"]}"><button type="submit">Cancel {a["ref"]}</button></form></td></tr>'
            for a in self.server.appointments
        )
        table = (f"<table><thead><tr><th>Reference</th><th>Doctor</th><th>Date</th><th>Time</th><th>Patient</th><th></th></tr>"
                 f"</thead><tbody>{rows}</tbody></table>") if rows else "<p>You have no appointments.</p>"
        return f"{banner}<h1>My appointments</h1>{table}"

    # --- plumbing -------------------------------------------------------------

    def _page(self, title: str, body: str, status: int = 200) -> None:
        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | Sehat Clinic</title>
<style>
body {{ margin:0; font:16px/1.5 "Segoe UI", system-ui, sans-serif; color:#15302f; background:#f3f8f7; }}
nav {{ display:flex; gap:20px; align-items:center; padding:12px 24px; background:#0f766e; }}
nav a {{ color:#fff; text-decoration:none; }} nav .brand {{ font-weight:700; margin-right:auto; }}
main {{ max-width:760px; margin:0 auto; padding:24px 16px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }}
.card {{ background:#fff; border-radius:8px; padding:12px 16px; border:1px solid #cfe3e0; }}
.field {{ display:grid; gap:4px; margin-bottom:12px; }} .row {{ display:flex; gap:16px; }}
input, select, textarea {{ font:inherit; padding:8px; border:1px solid #9cc3bd; border-radius:6px; }}
.check {{ display:flex; gap:8px; align-items:center; margin:8px 0 16px; }}
.btn, .btn-link {{ display:inline-block; background:#0f766e; color:#fff; padding:10px 18px; border-radius:6px; border:0;
  cursor:pointer; font:inherit; text-decoration:none; }}
.errors {{ background:#fdecec; border:1px solid #e5a3a3; padding:8px 16px; border-radius:6px; }}
.banner {{ background:#e1f5ee; border:1px solid #93d3bd; padding:12px 16px; border-radius:6px; margin-bottom:16px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; }} td, th {{ padding:8px; border-bottom:1px solid #d7e7e4; text-align:left; }}
dl {{ display:grid; grid-template-columns:max-content 1fr; gap:6px 16px; }} dt {{ font-weight:600; }}
</style>
<script>window.BUGS = {json.dumps(sorted(self.server.bugs))};</script>
</head><body>
<nav><a class="brand" href="/">Sehat Clinic</a><a href="/">Doctors</a><a href="/book">Book appointment</a><a href="/appointments">My appointments</a></nav>
<main>{body}</main>
</body></html>"""
        encoded = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str) -> None:
        # 303: after a form post, the browser GETs the result page (post/redirect/get).
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="python -m holdout.server", description="Sehat Clinic, the holdout app.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bugs", default="", help='comma-separated bug names, or "all"')
    args = parser.parse_args()
    names = set(BUGS) if args.bugs == "all" else {b.strip() for b in args.bugs.split(",") if b.strip()}
    server = make_server(port=args.port, bugs=names)
    print(f"Sehat Clinic on http://localhost:{server.server_port}   bugs on: {', '.join(sorted(names)) or 'none'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
