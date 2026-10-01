"""Kulhad & Co.: a tiny fake shop with switchable, planted bugs.

It exists to measure Nightshift. Run the specs against the clean shop and every
failure is a false alarm; switch on a planted bug and every pass is a missed bug.
Standard library only: static files plus three JSON endpoints.

Bugs are defects. Variants are legitimate UI changes (a redesign), used to test
that replay heals itself instead of reporting a bug.
"""

from __future__ import annotations

import json
import secrets
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
        elif path == "/mail/api/v1/messages":
            with self.server.lock:
                newest = list(reversed(self.server.outbox))
            summaries = [{k: m[k] for k in ("ID", "To", "Subject", "Created")} for m in newest]
            self._json(200, {"total": len(summaries), "messages": summaries})
        elif path.startswith("/mail/api/v1/message/"):
            wanted = path.removeprefix("/mail/api/v1/message/")
            found = next((m for m in self.server.outbox if m["ID"] == wanted), None)
            if found is None:
                self._json(404, {"error": "no such message"})
            else:
                self._json(200, {**found, "HTML": "", "Date": found["Created"]})
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
        elif path == "/api/order":
            self._order(body)
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
