"""Encryption for the marketplace refresh tokens this app holds at rest.

A refresh token is a long-lived key to a seller's whole store: it mints access
tokens for as long as the connection lasts (eBay's for ~18 months), and those
tokens can list, revise, end, and read orders. Held in plaintext, one leaked
database dump hands over every connected account — and nothing about the leak
would look like an eBay event to the seller.

The scheme is deliberately dull:

  - Fernet (AES-128-CBC + HMAC-SHA256, authenticated), so a tampered value
    fails to decrypt rather than decrypting to something else.
  - Ciphertext is stored with an `enc:v1:` marker. Anything without it is a
    value written before this existed and is returned as-is, so the rows
    migrate as they are rewritten instead of needing a flag day.
  - The marker carries a version, so a future scheme can be told apart from
    this one without guessing.

## The key

`TOKEN_ENCRYPTION_KEY` (a Fernet key: `Fernet.generate_key()`) if set.
Otherwise one derived from `SECRET_KEY`, so this protects a self-hosted or
local deployment with no extra configuration — the same default-on posture the
rest of the app takes.

**Rotating the key makes existing tokens unreadable**, and that includes
rotating `SECRET_KEY` when no explicit key is set. Sellers are not harmed
beyond having to reconnect (a decrypt failure reads as "not connected", never
as an error page), but it is a real cost: set `TOKEN_ENCRYPTION_KEY`
explicitly if `SECRET_KEY` is ever likely to change.
"""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config
from .config import log

_PREFIX = "enc:v1:"
# Ties the derived key to this purpose: the same SECRET_KEY used for a
# different job later derives a different key, so one compromise is not both.
#
# DO NOT rename this to match the product. It is not a label -- it is an input
# to the key derivation, and every refresh token in the database was encrypted
# under the key it produces. A different string is a different key, `decrypt`
# returns "" rather than raising (see below), and the result is every
# connected seller silently signed out of every marketplace with nothing
# logged as an error. Moving it needs a migration that re-encrypts the stored
# rows first. Pinned by tests/test_the_rename_stops_at_stored_data.py.
_INFO = b"quickflip/marketplace-refresh-token/v1"

_fernet: Fernet | None = None


def _derived_key(secret: str) -> bytes:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=_INFO).derive(secret.encode())
    return base64.urlsafe_b64encode(raw)


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        explicit = (config.TOKEN_ENCRYPTION_KEY or "").strip()
        _fernet = Fernet(explicit.encode() if explicit
                         else _derived_key(config.SECRET_KEY))
    return _fernet


def is_encrypted(value: str) -> bool:
    return (value or "").startswith(_PREFIX)


def encrypt(value: str) -> str:
    """Ciphertext for `value`, or `value` unchanged when there is nothing to
    protect (an empty string is how a disconnect is recorded)."""
    if not value:
        return value
    if is_encrypted(value):
        return value  # already ours; never double-wrap
    return _PREFIX + _cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """The plaintext behind `value`.

    A value with no marker predates this module and is returned as-is. A value
    that will not decrypt — the key changed, or the row was tampered with —
    returns "" rather than raising: every caller reads a refresh token to
    decide whether the account is connected, so "" routes the seller to
    reconnect, while an exception here would take down whatever page asked.
    """
    if not is_encrypted(value):
        return value or ""
    try:
        return _cipher().decrypt(value[len(_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        log.warning("crypto: a stored token could not be decrypted (%s) — "
                    "treating the account as disconnected. If "
                    "TOKEN_ENCRYPTION_KEY or SECRET_KEY changed, affected "
                    "sellers must reconnect.", type(exc).__name__)
        return ""


def reset_cache() -> None:
    """Forget the derived cipher. For tests that change the key."""
    global _fernet
    _fernet = None
