"""A test inbox, for flows that email the user: sign-in codes, magic links, confirmations.

In a spec the agent types {{email_code}}, or does goto {{email_link}}. Right before
acting, Nightshift fetches the newest email sent to the test address since the test
started, and fills in the first code or link in it. The model never reads the inbox;
it only says where the code goes, which keeps it simple for small models.

Text messages work the same way: {{sms_code}} is the code in the newest SMS to the test
phone number (data called `phone` or `mobile`), read from an SMS inbox that answers the same
Mailpit-shaped API: `sms_inbox:` in the spec, or SMS_INBOX_URL. SMS gateways each keep their
own log, so for a real one that is a small adapter; staging setups that use a fixed test OTP
need none of this and put the code in the spec's data.

Two ways to reach the mail:
  - a Mailpit-style HTTP API: `inbox:` in the spec, or INBOX_URL (e.g. http://localhost:8025).
    Mailpit is the usual mail catcher in development and CI.
  - an IMAP mailbox: INBOX_IMAP_HOST, INBOX_IMAP_USER, INBOX_IMAP_PASSWORD (port 993, TLS),
    for a real test mailbox.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from typing import Protocol

import httpx

CODE_RE = re.compile(r"(?<![\w-])(\d{4,8})(?![\w-])")
LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+")
DYNAMIC = ("email_code", "email_link", "totp_code", "sms_code")  # filled at typing time (inbox, authenticator), not from spec data


class InboxError(RuntimeError):
    """No email arrived, or the inbox couldn't be read. The message says which."""


@dataclass(frozen=True)
class Email:
    to: tuple[str, ...]
    subject: str
    text: str
    received: float  # unix time


class Inbox(Protocol):
    def messages(self, since: float) -> list[Email]:
        """Emails received at or after `since`, newest first."""
        ...


class MailpitInbox:
    """Mailpit's HTTP API: GET /api/v1/messages, then GET /api/v1/message/{ID}."""

    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=10)

    def messages(self, since: float) -> list[Email]:
        listing = self.client.get(f"{self.url}/api/v1/messages", params={"limit": 50})
        listing.raise_for_status()
        found = []
        for item in listing.json().get("messages") or []:
            received = _iso_time(item.get("Created", ""))
            if received < since:
                continue
            full = self.client.get(f"{self.url}/api/v1/message/{item['ID']}")
            full.raise_for_status()
            body = full.json()
            text = body.get("Text") or _strip_html(body.get("HTML") or "")
            to = tuple(str(a.get("Address", "")).lower() for a in item.get("To") or [])
            found.append(Email(to, str(item.get("Subject", "")), text, received))
        return sorted(found, key=lambda m: m.received, reverse=True)


class ImapInbox:
    """Any IMAP mailbox over TLS. Searches by day (IMAP's SINCE), then filters to the second."""

    def __init__(self, host: str, user: str, password: str, port: int = 993, folder: str = "INBOX") -> None:
        self.host, self.user, self.password, self.port, self.folder = host, user, password, port, folder

    def messages(self, since: float) -> list[Email]:
        day = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%d-%b-%Y")
        found = []
        with imaplib.IMAP4_SSL(self.host, self.port) as imap:
            imap.login(self.user, self.password)
            imap.select(self.folder, readonly=True)
            status, data = imap.search(None, "SINCE", day)
            if status != "OK":
                raise InboxError(f"IMAP search failed: {status}")
            for number in reversed((data[0] or b"").split()[-30:]):
                status, parts = imap.fetch(number, "(RFC822)")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                message = email.message_from_bytes(parts[0][1], policy=email.policy.default)
                try:
                    received = parsedate_to_datetime(message["Date"]).timestamp()
                except (TypeError, ValueError):
                    continue
                if received < since:
                    continue
                part = message.get_body(preferencelist=("plain", "html"))
                text = part.get_content() if part else ""
                if part and part.get_content_type() == "text/html":
                    text = _strip_html(text)
                to = tuple(addr.lower() for _, addr in getaddresses(message.get_all("To", [])))
                found.append(Email(to, str(message["Subject"] or ""), text, received))
        return sorted(found, key=lambda m: m.received, reverse=True)


def inbox_for(spec_inbox: str = "") -> Inbox | None:
    """The spec's inbox, else one from the environment, else None."""
    url = spec_inbox or os.getenv("INBOX_URL", "")
    if url:
        return MailpitInbox(url)
    if os.getenv("INBOX_IMAP_HOST"):
        return ImapInbox(os.environ["INBOX_IMAP_HOST"], os.getenv("INBOX_IMAP_USER", ""),
                         os.getenv("INBOX_IMAP_PASSWORD", ""), int(os.getenv("INBOX_IMAP_PORT") or 993))
    return None


def sms_inbox_for(spec_sms_inbox: str = "") -> Inbox | None:
    """The spec's SMS inbox, else SMS_INBOX_URL, else None."""
    url = spec_sms_inbox or os.getenv("SMS_INBOX_URL", "")
    return MailpitInbox(url) if url else None


def wait_for_email(inbox: Inbox, *, to: str, since: float, timeout: float = 30, poll: float = 1.5,
                   what: str = "email") -> Email:
    """The newest message to `to` (an address, or a phone number) received since `since`, waiting for it."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            mails = inbox.messages(since)
        except (httpx.HTTPError, OSError, imaplib.IMAP4.error) as exc:
            raise InboxError(f"could not read the inbox ({type(exc).__name__})") from exc
        for mail in mails:
            if not to or _same_recipient(to, mail.to):
                return mail
        if time.monotonic() >= deadline:
            raise InboxError(f"no {what} to {to or 'the test recipient'} arrived within {timeout:.0f} s")
        time.sleep(poll)


def _same_recipient(wanted: str, addresses: tuple[str, ...]) -> bool:
    if "@" in wanted:
        return wanted.lower() in addresses
    # A phone number: +91 98765 43210, 919876543210 and 9876543210 are the same phone.
    digits = re.sub(r"\D", "", wanted)[-10:]
    return bool(digits) and any(re.sub(r"\D", "", a)[-10:] == digits for a in addresses)


def extract_code(mail: Email) -> str:
    """The one-time code: the first 4 to 8 digit number, preferring one on a line that mentions a code."""
    lines = [mail.subject, *mail.text.splitlines()]
    for line in lines:
        if re.search(r"code|otp|pin|verify|verification", line, re.IGNORECASE) and (match := CODE_RE.search(line)):
            return match.group(1)
    for line in lines:
        if match := CODE_RE.search(line):
            return match.group(1)
    raise InboxError(f"the email {mail.subject!r} has no code in it")


def extract_link(mail: Email) -> str:
    if match := LINK_RE.search(mail.text):
        return match.group(0).rstrip(".,")
    raise InboxError(f"the email {mail.subject!r} has no link in it")


def recipient(data: dict[str, str]) -> str:
    """The test address: data called `email`, else the first value that looks like one."""
    if "@" in data.get("email", ""):
        return data["email"]
    return next((v for v in data.values() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v)), "")


def phone_number(data: dict[str, str]) -> str:
    """The test phone number: data called phone or mobile (or ..._number)."""
    for key in ("phone", "mobile", "phone_number", "mobile_number"):
        if re.sub(r"\D", "", data.get(key, "")):
            return data[key]
    return ""


def _iso_time(text: str) -> float:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r'(?i)<a\s[^>]*href="([^"]+)"[^>]*>', r" \1 ", html)  # keep link targets: magic links live there
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
