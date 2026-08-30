"""A session token has to be cancellable.

The session JWT lives 30 days and nothing could invalidate one. Logout deleted
the cookie and that was all: the token itself stayed valid for the rest of the
month, so anyone who had a copy — a shared or borrowed device, a browser
profile left signed in, a token pulled out of a backup or a log — kept full
access to the account, and the seller had no way to end it. Changing the
password would not have helped either; nothing was keyed to it.

"Log out everywhere" is the control that answers that, and it needs somewhere
to record the cancellation. That is `sessions_valid_from` on the user row: a
token issued before it is refused. The row is already read on every
authenticated request (current_user resolves the subject to a user dict), so
this costs no extra query — which matters, because a revocation check that
costs a round trip per request is one that gets skipped under load.

Deliberately not a token blocklist. A blocklist has to be consulted, kept, and
expired, and it fails open when the store is unreachable. A timestamp on a row
that is already being read fails the same way the rest of auth does: no user,
no session.
"""
from __future__ import annotations

import datetime as _dt

import pytest

jwt = pytest.importorskip("jwt")


@pytest.fixture()
def store(dbmod):
    return dbmod


@pytest.fixture()
def signed_in(store, monkeypatch):
    """A user with a valid session token."""
    from backend import auth

    monkeypatch.setattr(auth, "db", store)
    store.create_user("u1", "a@b.c", "hash")
    return auth, auth.make_token("u1")


def _request(token: str):
    """The smallest thing current_user needs: a bearer token."""
    class _Req:
        cookies: dict = {}
        headers = {"Authorization": f"Bearer {token}"}

        class state:  # noqa: N801 - stands in for Starlette's request.state
            pass
    return _Req()


# ------------------------------------------------------------- the control

def test_a_token_works_before_anything_is_revoked(signed_in):
    auth, token = signed_in
    assert auth.current_user(_request(token))["id"] == "u1"


def test_revoking_kills_a_token_that_was_already_issued(signed_in, store):
    """The finding. This token stayed good for the rest of its 30 days."""
    auth, token = signed_in

    store.revoke_sessions("u1")

    assert auth.current_user(_request(token)) is None, \
        "a revoked session token still authenticated"


def test_signing_back_in_after_a_revocation_works(signed_in, store):
    """Otherwise "log out everywhere" locks the account permanently.

    The revocation is dated a few seconds back rather than the new token
    being dated forward: PyJWT refuses any token whose `iat` is in the
    future, so a forward-dated one never reaches the revocation check at all
    — it is rejected as immature. Backdating tests the same boundary from the
    side a real sign-in approaches it from.
    """
    auth, _ = signed_in
    earlier = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=5)
    store.revoke_sessions("u1", at=earlier)

    assert auth.current_user(_request(auth.make_token("u1")))["id"] == "u1"


def test_the_refused_window_is_only_the_revocation_second(signed_in, store):
    """The boundary is inclusive, so a sign-in inside the same whole second
    as the revocation is also refused. Bounding it here stops that quietly
    becoming a minute if the stamp is ever rounded to something coarser: one
    second earlier must already be enough."""
    auth, _ = signed_in
    one_second_back = (_dt.datetime.now(_dt.timezone.utc)
                       .replace(microsecond=0) - _dt.timedelta(seconds=1))
    store.revoke_sessions("u1", at=one_second_back)

    assert auth.current_user(_request(auth.make_token("u1"))) is not None


def test_revoking_one_account_leaves_another_alone(signed_in, store, monkeypatch):
    from backend import auth as auth_mod

    store.create_user("u2", "c@d.e", "hash")
    theirs = auth_mod.make_token("u2")
    store.revoke_sessions("u1")

    assert auth_mod.current_user(_request(theirs))["id"] == "u2"


def test_a_revocation_that_did_not_commit_is_reported(store, monkeypatch):
    """Silently failing to revoke is the worst outcome: the seller is told
    their other sessions are gone and they are not."""
    from backend.errors import StorageUnavailable

    monkeypatch.setattr(store, "_get_engine", lambda: None)
    with pytest.raises(StorageUnavailable):
        store.revoke_sessions("u1")


def test_revoking_an_unknown_user_is_not_a_silent_success(store):
    from backend.errors import StorageUnavailable

    with pytest.raises(StorageUnavailable):
        store.revoke_sessions("nobody")


# ------------------------------------------------- the boundary is inclusive

def test_a_token_issued_in_the_same_second_is_refused(signed_in, store):
    """`iat` is whole seconds, so a token minted in the same second as the
    revocation is indistinguishable from one minted just before it. Refusing
    is the only safe reading: the alternative leaves a one-second hole in the
    control, on the exact request that follows the button."""
    auth, _ = signed_in
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)

    from backend import config

    token = jwt.encode({"sub": "u1", "iat": now,
                        "exp": now + _dt.timedelta(days=30)},
                       config.SECRET_KEY, algorithm="HS256")
    store.revoke_sessions("u1", at=now)

    assert auth.current_user(_request(token)) is None


def test_a_token_with_no_issued_at_is_refused(signed_in, store):
    """Nothing this app mints lacks `iat`. One that does cannot be placed
    relative to the revocation, and "cannot tell" is not "allow"."""
    from backend import config

    auth, _ = signed_in
    store.revoke_sessions("u1")
    token = jwt.encode({"sub": "u1"}, config.SECRET_KEY, algorithm="HS256")

    assert auth.current_user(_request(token)) is None


def test_no_revocation_means_no_extra_checking(signed_in, store):
    """An account that has never revoked must behave exactly as before —
    this is on every authenticated request."""
    auth, token = signed_in
    assert store.get_user_by_id("u1").get("sessions_valid_from") in (None, "")
    assert auth.current_user(_request(token))["id"] == "u1"
