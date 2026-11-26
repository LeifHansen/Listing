"""bcrypt reads 72 bytes of a password, and bcrypt 5 raises past that.

bcrypt 4 dropped the rest silently; the bump to 5 (2026-09-09) turned it into
a ValueError. Signup with a long passphrase became a 500, and every account
created before the bump with one was locked out for good: checkpw raised,
verify_password answered "wrong password", and there is no reset flow.
auth cuts the input to 72 bytes, which is exactly what bcrypt 4 did -- so a
hash made under 4 still verifies.
"""
from __future__ import annotations

import bcrypt

from backend import auth

LONG = "correct horse battery staple " * 4          # 116 ASCII bytes
EMOJI = "\N{GRINNING FACE}" * 19                       # 76 bytes of UTF-8


def test_a_long_password_hashes_and_verifies():
    for pw in (LONG, EMOJI):
        h = auth.hash_password(pw)
        assert auth.verify_password(pw, h)
        assert not auth.verify_password("nope", h)


def test_a_hash_made_by_bcrypt_4_still_verifies():
    """bcrypt 4 hashed only the first 72 bytes. Reproduce that hash directly
    and check today's verify accepts the full password the seller types."""
    legacy = bcrypt.hashpw(LONG.encode()[:72], bcrypt.gensalt(4)).decode()
    assert auth.verify_password(LONG, legacy)


def test_a_short_password_is_unchanged():
    h = auth.hash_password("hunter22")
    assert auth.verify_password("hunter22", h)
    assert not auth.verify_password("hunter23", h)
