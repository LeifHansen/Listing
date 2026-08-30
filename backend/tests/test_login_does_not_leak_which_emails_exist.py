"""A failed login must not say which half was wrong — including by timing.

`auth.login` short-circuits: `if not rec` returns before bcrypt runs. The
message is correctly the same either way ("Invalid email or password"), but
the CLOCK was not. bcrypt is deliberately slow, and measured on this machine a
verify costs ~270ms against ~0ms for an address with no account. That gap is
the entire answer to "does this person have an account here?", legible over
any network, on an endpoint that takes an email and a guess.

It matters more than the usual because of what the account IS: a seller's
connected eBay identity, their photos, and their listings. Confirming an
address has an account here is the first step of a credential-stuffing run
against exactly the people worth targeting, and it lets an attacker spend
their rate-limit budget only on addresses they have confirmed.

So the work happens either way. A record that isn't there is checked against a
fixed hash of a value nobody can supply, at the same cost factor a real one
uses -- the constant-time comparison the password itself already gets, applied
to the question of whether the account exists at all.

Asserted on the WORK rather than on a stopwatch: a timing assertion in CI is a
flake, and "bcrypt ran" is what the timing is a proxy for.
"""
from __future__ import annotations

import pytest

bcrypt = pytest.importorskip("bcrypt")


@pytest.fixture()
def attempts(monkeypatch):
    """Run a login and report how many bcrypt verifies it cost."""
    from backend import auth

    # Captured ONCE, outside _run. Taking it inside meant the second call
    # wrapped the first call's wrapper, so both runs appended to the first
    # run's list and the counts matched no matter what the code did — a test
    # that passed against the very short-circuit it exists to catch.
    real = auth.bcrypt.checkpw
    seen: list[list[str]] = []

    def _counted(password, hashed):
        if seen:                       # verifies outside a _run aren't counted
            seen[-1].append(hashed.decode()[:7])
        return real(password, hashed)

    monkeypatch.setattr(auth.bcrypt, "checkpw", _counted)

    def _run(stored):
        seen.append([])
        monkeypatch.setattr(auth.db, "get_user_by_email", lambda _e: stored)
        return auth.login("someone@example.test", "a guess"), seen[-1]
    return _run


def _account(password: str = "the real one") -> dict:
    from backend import auth
    return {"id": "u1", "email": "someone@example.test",
            "password_hash": auth.hash_password(password)}


def test_an_unknown_email_costs_the_same_work_as_a_known_one(attempts):
    """The finding. One of these used to skip bcrypt entirely."""
    missing, missing_calls = attempts(None)
    known, known_calls = attempts(_account())

    assert missing is None and known is None, "both attempts must fail"
    assert len(missing_calls) == len(known_calls) == 1


def test_the_right_password_still_works(attempts):
    """The dummy comparison must not become a way to fail a real login."""
    from backend import auth

    monkeypatch_free = _account("hunter2hunter2")
    from unittest.mock import patch
    with patch.object(auth.db, "get_user_by_email",
                      lambda _e: monkeypatch_free):
        user = auth.login("someone@example.test", "hunter2hunter2")
    assert user is not None
    assert user["id"] == "u1"
    assert "password_hash" not in user, "the hash must never ride along"


def test_a_record_with_no_hash_still_does_the_work(attempts):
    """A row whose hash is blank -- a half-written signup, an import -- must
    not become the fast path either."""
    _, calls = attempts({"id": "u1", "email": "someone@example.test",
                         "password_hash": ""})
    assert len(calls) == 1


def test_the_dummy_hash_cannot_be_matched(attempts):
    """It has to be a hash of something no request can carry. If any password
    verified against it, an unknown email would LOG IN."""
    from backend import auth

    assert auth.login("nobody@example.test", "") is None
    # And directly: nothing supplied by a caller may match it.
    for guess in ("", " ", "password", auth._ABSENT_PASSWORD_HASH):
        assert not auth.verify_password(guess, auth._ABSENT_PASSWORD_HASH)


def test_the_dummy_hash_costs_what_a_real_one_costs(attempts):
    """Same cost factor, or the timing gap simply moves rather than closing.
    bcrypt encodes the work factor in the hash itself, so this is readable
    without running it."""
    from backend import auth

    real = auth.hash_password("anything")
    assert auth._ABSENT_PASSWORD_HASH.split("$")[2] == real.split("$")[2]
