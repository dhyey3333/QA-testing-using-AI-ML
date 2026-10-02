"""Authenticator-app codes (TOTP, RFC 6238), so a test can get past two-factor sign-in.

    data:
      totp_secret: ${SHOP_TOTP_SECRET}    # the base32 secret behind the test account's QR code

The agent types {{totp_code}}; the runner computes the current code right before typing.
Like email codes, the model never sees the secret or the code. Standard library only:
it is a few lines of HMAC, not worth a dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

SECRET_KEY = "totp_secret"  # the data key that holds the secret; never typed, never shown to the model


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    cleaned = secret.replace(" ", "").upper()
    key = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{code:0{digits}d}"
