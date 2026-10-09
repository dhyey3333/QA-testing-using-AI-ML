"""Client secrets (test passwords, API keys) and two-factor secrets, encrypted at rest.

Values are encrypted with Fernet (AES-128 in CBC mode with an HMAC-SHA256 check, from the
`cryptography` library) under one key per installation. The key is kept OUTSIDE the data
folder, so a copied data folder or a backup holds only ciphertext:

    NIGHTSHIFT_SECRET_KEY   the key itself (for a server that injects secrets as environment variables)
    NIGHTSHIFT_KEY_FILE     a file holding it (default: ~/.nightshift/secret.key, made on first use)

Lose the key and the secrets are gone: back it up separately from the data (deploy README).
A stored value without the "enc:v1:" prefix is from before encryption; it is read as it is
and encrypted the next time it is written (ProjectFiles.seal_secrets does all of them at start).
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


class VaultError(ValueError):
    """A stored secret that can't be decrypted: the key is not the one it was encrypted with."""


def key_file() -> Path:
    return Path(os.getenv("NIGHTSHIFT_KEY_FILE") or Path.home() / ".nightshift" / "secret.key")


def _load_key() -> bytes:
    if from_env := os.getenv("NIGHTSHIFT_SECRET_KEY", "").strip():
        return from_env.encode("ascii")
    path = key_file()
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Readable by its owner only, from the moment it exists.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key + b"\n")
    return key


class Vault:
    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(key or _load_key())

    def seal(self, value: str) -> str:
        return PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def open(self, stored: str) -> str:
        if not stored.startswith(PREFIX):
            return stored  # written before encryption existed
        try:
            return self._fernet.decrypt(stored[len(PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            raise VaultError(f"a secret can't be decrypted: the key in {key_file()} is not the one it "
                             "was saved with") from None


_default: Vault | None = None


def vault() -> Vault:
    """The installation's vault, loaded once."""
    global _default
    if _default is None:
        _default = Vault()
    return _default


def is_sealed(stored: str) -> bool:
    return stored.startswith(PREFIX)
