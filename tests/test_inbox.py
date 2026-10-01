"""The test inbox: reading codes and links from email, over Mailpit's API and IMAP."""

import imaplib
import time
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

import httpx
import pytest

from nightshift import inbox as inbox_module
from nightshift.inbox import Email, ImapInbox, InboxError, MailpitInbox, extract_code, extract_link, recipient, wait_for_email


def mail(text, subject="Hello", to=("a@example.test",)):
    return Email(to=to, subject=subject, text=text, received=time.time())


def test_the_code_on_a_line_about_a_code_wins_over_other_numbers():
    assert extract_code(mail("Order 2026 shipped.\nYour verification code is 482913.")) == "482913"
    assert extract_code(mail("nothing", subject="Your code: 7731")) == "7731"
    with pytest.raises(InboxError, match="no code"):
        extract_code(mail("no digits here"))


def test_links_and_recipients():
    assert extract_link(mail("Click https://app.test/magic?t=abc123. Thanks")) == "https://app.test/magic?t=abc123"
    assert recipient({"name": "x", "login": "someone@example.test"}) == "someone@example.test"
    assert recipient({"email": "a@b.test", "other": "c@d.test"}) == "a@b.test"


def test_waiting_gives_up_with_a_clear_reason():
    class Empty:
        def messages(self, since):
            return []

    with pytest.raises(InboxError, match="no email to x@example.test arrived"):
        wait_for_email(Empty(), to="x@example.test", since=0, timeout=0.2, poll=0.05)


def test_the_mailpit_api_is_read(shop, base_url):
    httpx.post(f"{base_url}/api/login-code", json={"email": "shopper@kulhad.test"})
    found = wait_for_email(MailpitInbox(f"{base_url}/mail"), to="shopper@kulhad.test", since=time.time() - 30, timeout=5)
    assert found.subject == "Your Kulhad & Co. sign-in code"
    assert len(extract_code(found)) == 6


def test_an_imap_mailbox_is_read(monkeypatch):
    message = EmailMessage()
    message["To"] = "Tester <tester@example.test>"
    message["Subject"] = "Confirm your account"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message.set_content("Your one-time code is 554433.")

    class FakeImap:
        def __init__(self, host, port):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            assert (user, password) == ("tester", "secret")

        def select(self, folder, readonly=False):
            return "OK", [b"1"]

        def search(self, charset, *criteria):
            assert criteria[0] == "SINCE"
            return "OK", [b"1"]

        def fetch(self, number, parts):
            return "OK", [(b"1 (RFC822 {n})", message.as_bytes())]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    found = wait_for_email(ImapInbox("imap.example.test", "tester", "secret"), to="tester@example.test",
                           since=time.time() - 60, timeout=1)
    assert extract_code(found) == "554433"


def test_the_inbox_comes_from_the_spec_or_the_environment(monkeypatch):
    monkeypatch.delenv("INBOX_URL", raising=False)
    monkeypatch.delenv("INBOX_IMAP_HOST", raising=False)
    assert inbox_module.inbox_for("") is None
    assert isinstance(inbox_module.inbox_for("http://localhost:8025"), MailpitInbox)
    monkeypatch.setenv("INBOX_IMAP_HOST", "imap.example.test")
    assert isinstance(inbox_module.inbox_for(""), ImapInbox)


SIGN_IN_WITH_CODE = [
    ("click", "a:Log in"),
    ("click", "Sign in with an email code"),
    ("type", "Email", "{{email}}"),
    ("click", "Email me a code"),
    ("type", "Sign-in code", "{{email_code}}"),
    ("click", "button:Sign in"),
]


def test_signing_in_with_an_emailed_code_end_to_end(run):
    result = run("login-with-code", SIGN_IN_WITH_CODE, evidence=["Hi, Test Shopper"])
    assert result.verdict == "pass", result.reason
    typed = next(step for step in result.steps if "email_code" in step.description)
    assert typed.outcome == "changed"  # the code went in without the model ever seeing it


def test_a_code_that_is_never_accepted_is_caught(run):
    result = run("login-with-code", SIGN_IN_WITH_CODE, bugs={"otp-wrong-code"}, evidence=["Hi, Test Shopper"])
    assert result.verdict == "fail"
