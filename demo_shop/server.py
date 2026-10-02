"""Kulhad & Co.: a tiny fake shop with switchable, planted bugs.

It exists to measure Nightshift. Run the specs against the clean shop and every
failure is a false alarm; switch on a planted bug and every pass is a missed bug.
Standard library only: static files plus three JSON endpoints.

Bugs are defects. Variants are legitimate UI changes (a redesign), used to test
that replay heals itself instead of reporting a bug.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .seed import PRODUCTS, TEST_USER

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 5180


@dataclass(frozen=True)
class Bug:
    description: str
    specs: tuple[str, ...]  # the specs that should catch it; the benchmark runs only these


# Server-side bugs are checked in the handler below; client-side ones reach the
# page as window.BUGS (see /bugs.js) and are checked in static/app.js.
BUGS: dict[str, Bug] = {
    # server
    "login-rejects": Bug("The server rejects the correct password", ("login",)),
    "login-case-sensitive": Bug("Login fails when the email has capital letters", ("login-email-case",)),
    "checkout-500": Bug("Placing an order returns HTTP 500", ("checkout",)),
    "no-order-number": Bug('The confirmation says "undefined" instead of an order number', ("checkout",)),
    "order-total-mismatch": Bug("The order charges ₹50 more than the cart total", ("checkout",)),
    # client
    "dead-add-button": Bug('"Add to cart" does nothing for the Clay Kulhad', ("add-to-cart",)),
    "wrong-total": Bug("The cart total leaves out the last line", ("add-to-cart",)),
    "js-error": Bug("The cart page throws an uncaught TypeError after it renders", ("add-to-cart",)),
    "qty-double": Bug("One click on Add to cart adds two", ("add-to-cart",)),
    "cart-count-stale": Bug("The header's cart count never updates", ("add-to-cart",)),
    "price-mismatch": Bug("The cart charges ₹480 for a Clay Kulhad listed at ₹420", ("add-to-cart",)),
    "cart-link-404": Bug('The header "Cart" link goes to a page that does not exist', ("add-to-cart",)),
    "add-wrong-product": Bug("Add to cart on Filter Coffee adds Masala Chai", ("checkout",)),
    "place-order-covered": Bug('An invisible layer covers "Place order", so clicks never reach it', ("checkout",)),
    "remove-wrong-item": Bug("Remove deletes the last item in the cart, not the one clicked", ("remove-from-cart",)),
    "logout-broken": Bug('"Log out" says goodbye but leaves you logged in', ("logout",)),
    "pincode-accepts-5": Bug("Checkout accepts a 5-digit pincode", ("checkout-validation",)),
    "validation-message-missing": Bug("A bad pincode blocks the order but shows no message", ("checkout-validation",)),
    "search-broken": Bug("Search never finds anything", ("search",)),
    "guest-cart-lost": Bug("Logging in during checkout empties the cart", ("guest-checkout",)),
    # server, added with the test inbox
    "otp-wrong-code": Bug("The emailed sign-in code is never accepted", ("login-with-code",)),
    "phone-spaces-rejected": Bug('The mobile number box shows "98765 43210" and the server rejects the space',
                                 ("login-with-phone",)),
    "payment-failure-ignored": Bug("A declined online payment still places the order", ("pay-online-failure",)),
}

VARIANTS: dict[str, str] = {
    "redesign": 'The cart\'s "Proceed to checkout" button becomes "Go to checkout", with a new id',
}


def parse_names(text: str, known: dict) -> set[str]:
    if text.strip() == "all":
        return set(known)
    names = {name.strip() for name in text.split(",") if name.strip()}
    unknown = names - known.keys()
    if unknown:
        raise ValueError(f"unknown name(s): {', '.join(sorted(unknown))}")
    return names


def parse_bugs(text: str) -> set[str]:
    return parse_names(text, BUGS)


class ShopServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], bugs: Iterable[str] = (), variants: Iterable[str] = (),
                 verbose: bool = False) -> None:
        super().__init__(address, ShopHandler)
        # Mutable on purpose: the benchmark and tests flip these between runs without a restart.
        self.bugs: set[str] = set(bugs)
        self.variants: set[str] = set(variants)
        self.verbose = verbose
        self.orders = 0
        # Emails the shop "sends", readable through a Mailpit-style API at /mail: a test inbox.
        self.outbox: list[dict] = []
        self.codes: dict[str, str] = {}
        # Text messages, the same way at /sms: the test phone's inbox.
        self.sms: list[dict] = []
        self.phone_codes: dict[str, str] = {}
        self.lock = threading.Lock()


def make_server(port: int = DEFAULT_PORT, bugs: Iterable[str] = (), variants: Iterable[str] = (),
                verbose: bool = False) -> ShopServer:
    return ShopServer(("127.0.0.1", port), bugs=bugs, variants=variants, verbose=verbose)


class ShopHandler(SimpleHTTPRequestHandler):
    server: ShopServer
    # Windows' registry can map .js to text/plain; say what these files are.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # switches must apply on the next load
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (the base class's name)
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/bugs.js":
            script = (f"window.BUGS = {json.dumps(sorted(self.server.bugs))};\n"
                      f"window.VARIANTS = {json.dumps(sorted(self.server.variants))};\n")
            self._send(200, "text/javascript; charset=utf-8", script.encode())
        elif path == "/api/products":
            self._json(200, PRODUCTS)
        elif path.startswith(("/mail/api/v1/", "/sms/api/v1/")):
            self._messages(self.server.outbox if path.startswith("/mail/") else self.server.sms,
                           path.split("/api/v1/", 1)[1])
        else:
            super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        body = self._read_json()
        if "slow-api" in self.server.variants:
            time.sleep(1.2)
        if path == "/api/login":
            self._login(body)
        elif path == "/api/login-code":
            self._send_code(body)
        elif path == "/api/login-code/verify":
            self._verify_code(body)
        elif path == "/api/phone-code":
            self._send_phone_code(body)
        elif path == "/api/phone-code/verify":
            self._verify_phone_code(body)
        elif path == "/api/order":
            self._order(body)
        elif path == "/api/two-factor":
            self._two_factor(body)
        else:
            self._json(404, {"error": "Not found."})

    def _login(self, body: dict) -> None:
        email = str(body.get("email", "")).strip()
        if "login-case-sensitive" not in self.server.bugs:
            email = email.lower()  # emails are case-insensitive; the bug forgets that
        correct = email == TEST_USER["email"] and body.get("password") == TEST_USER["password"]
        if not correct or "login-rejects" in self.server.bugs:
            self._json(401, {"error": "Wrong email or password."})
            return
        self._json(200, {"user": {"name": TEST_USER["name"], "email": TEST_USER["email"]}})

    def _send_code(self, body: dict) -> None:
        """Email a 6-digit sign-in code. Unknown addresses get the same answer and no email."""
        email = str(body.get("email", "")).strip().lower()
        if email == TEST_USER["email"]:
            code = f"{secrets.randbelow(900_000) + 100_000}"
            with self.server.lock:
                self.server.codes[email] = code
                self.server.outbox.append({
                    "ID": secrets.token_hex(8),
                    "To": [{"Name": TEST_USER["name"], "Address": email}],
                    "Subject": "Your Kulhad & Co. sign-in code",
                    "Text": f"Hi {TEST_USER['name']},\n\nYour sign-in code is {code}. It expires in 10 minutes.\n\n"
                            "If you did not ask for it, you can ignore this email.",
                    "Created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
        self._json(200, {"sent": True})

    def _verify_code(self, body: dict) -> None:
        email = str(body.get("email", "")).strip().lower()
        expected = self.server.codes.get(email)
        if expected and "otp-wrong-code" in self.server.bugs:
            expected = expected[::-1]  # checks against the code reversed: the emailed one never works
        if not expected or str(body.get("code", "")).strip() != expected:
            self._json(401, {"error": "That code is not right."})
            return
        self._json(200, {"user": {"name": TEST_USER["name"], "email": TEST_USER["email"]}})

    def _messages(self, box: list[dict], rest: str) -> None:
        """A Mailpit-style API over a list of messages: GET messages, GET message/{ID}."""
        if rest == "messages":
            with self.server.lock:
                newest = list(reversed(box))
            summaries = [{k: m[k] for k in ("ID", "To", "Subject", "Created")} for m in newest]
            self._json(200, {"total": len(summaries), "messages": summaries})
            return
        found = next((m for m in box if m["ID"] == rest.removeprefix("message/")), None)
        if found is None:
            self._json(404, {"error": "no such message"})
        else:
            self._json(200, {**found, "HTML": "", "Date": found["Created"]})

    def _phone(self, body: dict) -> str:
        """The 10-digit mobile number, or "" if it isn't one. The box formats it as "98765 43210"."""
        raw = str(body.get("phone", "")).strip()
        # BUG phone-spaces-rejected: the server takes the number as typed, so the box's own space breaks it.
        digits = raw if "phone-spaces-rejected" in self.server.bugs else re.sub(r"\D", "", raw)
        return digits if re.fullmatch(r"\d{10}", digits) else ""

    def _send_phone_code(self, body: dict) -> None:
        """Text a 6-digit OTP. Unknown numbers get the same answer and no message."""
        phone = self._phone(body)
        if not phone:
            self._json(400, {"error": "Enter a valid 10-digit mobile number."})
            return
        if phone == TEST_USER["phone"]:
            code = f"{secrets.randbelow(900_000) + 100_000}"
            with self.server.lock:
                self.server.phone_codes[phone] = code
                self.server.sms.append({
                    "ID": secrets.token_hex(8),
                    "To": [{"Name": "", "Address": f"+91{phone}"}],
                    "Subject": "SMS from KULHAD",
                    "Text": f"{code} is your Kulhad & Co. login OTP. It is valid for 10 minutes. Do not share it.",
                    "Created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
        self._json(200, {"sent": True})

    def _verify_phone_code(self, body: dict) -> None:
        phone = self._phone(body)
        expected = self.server.phone_codes.get(phone)
        if not expected or str(body.get("code", "")).strip() != expected:
            self._json(401, {"error": "That OTP is not right."})
            return
        self._json(200, {"user": {"name": TEST_USER["name"], "email": TEST_USER["email"]}})

    def _two_factor(self, body: dict) -> None:
        """The lab's two-factor page: the code from an authenticator app holding LAB_TOTP_SECRET."""
        code = str(body.get("code", "")).strip()
        now = time.time()
        if code and code in (_totp(LAB_TOTP_SECRET, now), _totp(LAB_TOTP_SECRET, now - 30)):
            self._json(200, {"ok": True})
        else:
            self._json(401, {"error": "That code is not right."})

    def _order(self, body: dict) -> None:
        if "checkout-500" in self.server.bugs:
            self._json(500, {"error": "Internal Server Error"})
            return

        items = body.get("items")
        if not isinstance(items, list) or not items:
            self._json(400, {"error": "Your cart is empty."})
            return
        prices = {product["id"]: product["price"] for product in PRODUCTS}
        total = 0
        for line in items:
            product_id, qty = line.get("id"), line.get("qty")
            if product_id not in prices or not isinstance(qty, int) or not 1 <= qty <= 99:
                self._json(400, {"error": "Invalid cart."})
                return
            total += prices[product_id] * qty
        for field in ("name", "address", "city", "pincode", "payment"):
            if not str(body.get(field, "")).strip():
                self._json(400, {"error": f"Missing {field}."})
                return
        if "order-total-mismatch" in self.server.bugs:
            total += 50  # a "handling fee" nobody told the customer about

        with self.server.lock:
            self.server.orders += 1
            number = 10000 + self.server.orders
        order = {"total": total, "payment": body["payment"]}
        if "no-order-number" not in self.server.bugs:
            order["orderId"] = f"KC-{number}"
        self._json(201, order)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _json(self, status: int, payload: object) -> None:
        self._send(status, "application/json", json.dumps(payload).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# A well-known example secret, for the lab's two-factor page only. Checked here with its own TOTP
# code (RFC 6238), separate from Nightshift's, so the test doesn't check Nightshift against itself.
LAB_TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _totp(secret: str, at: float) -> str:
    key = base64.b32decode(secret)
    mac = hmac.new(key, struct.pack(">Q", int(at // 30)), hashlib.sha1).digest()
    start = mac[19] & 15
    return str((int.from_bytes(mac[start:start + 4], "big") & 0x7FFFFFFF) % 1_000_000).zfill(6)
