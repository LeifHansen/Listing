"""A database blip must not silently turn a signed-in seller into a stranger.

Every "who is asking" check ends at `db.get_user_by_id`, and that returned
None on any exception. `auth.current_user` reads None as "no valid session",
so one unreachable Postgres logged the whole app out server-side — not with an
error, but by answering every authenticated request as if nobody were there:

  * `/api/listings` answered `authed: false` with an empty list, so the store
    a seller has four hundred listings in rendered as the logged-out pitch;
  * `/api/notifications` answered an empty list with `checked: true` — the
    honest flag added for exactly this failure, defeated one layer upstream,
    because the logged-out branch has nothing to check;
  * `/api/ebay/status` answered `connected: false` without ever reading the
    account, because there was no uid to read it for;
  * writes then failed as 404s ("Listing not found"), since a record owned by
    a user does not belong to the anonymous caller the request had become.

None of those is a lie the app chose; they are all correct answers to the
wrong question. The fix is one layer down: a session lookup that could not
run is not a session that does not exist.

A caller with NO token is unaffected — nothing reads storage for them, and
the app's logged-out flows keep working. Only a request carrying a valid
session JWT whose user row cannot be read gets the 503, which is exactly the
set of requests that were being silently downgraded.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("jwt")
pytest.importorskip("fastapi")

from backend.errors import StorageUnavailable  # noqa: E402


@pytest.fixture
def signed_in(dbmod):
    from backend import auth
    rec = dbmod.create_user("u-sess", "sess@example.com",
                            auth.hash_password("hunter2hunter2"))
    assert rec not in (None, dbmod.EMAIL_TAKEN)
    return rec["id"]


def _break(dbmod, monkeypatch):
    def _boom():
        raise RuntimeError("connection to Neon reset by peer")
    monkeypatch.setattr(dbmod, "_get_engine", _boom)


def _request(token: str = ""):
    """The smallest thing auth.current_user reads: cookies, headers, state."""
    class _State:
        pass

    class _Request:
        def __init__(self):
            self.cookies = {"thryft_session": token} if token else {}
            self.headers = {}
            self.state = _State()

    return _Request()


def test_an_unreadable_user_row_is_not_a_missing_one(dbmod, signed_in, monkeypatch):
    assert dbmod.get_user_by_id(signed_in)["email"] == "sess@example.com"
    _break(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        dbmod.get_user_by_id(signed_in)


def test_a_deleted_account_is_still_just_gone(dbmod, signed_in):
    """"No such user" is a real answer and must stay cheap and quiet."""
    assert dbmod.get_user_by_id("nobody-at-all") is None


def test_a_valid_session_does_not_become_anonymous(dbmod, signed_in, monkeypatch):
    from backend import auth
    monkeypatch.setattr(auth, "db", dbmod)
    token = auth.make_token(signed_in)
    assert auth.current_user(_request(token))["id"] == signed_in

    _break(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        auth.current_user(_request(token))


def test_a_second_look_within_the_request_still_refuses(dbmod, signed_in,
                                                        monkeypatch):
    """current_user memoizes per request. The memo must not record the
    failure as "anonymous" — handlers call it several times, and the later
    calls would then quietly get the answer the first one refused to give."""
    from backend import auth
    monkeypatch.setattr(auth, "db", dbmod)
    token = auth.make_token(signed_in)
    req = _request(token)
    _break(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        auth.current_user(req)
    with pytest.raises(StorageUnavailable):
        auth.current_user(req)


def test_no_token_is_still_anonymous_during_an_outage(dbmod, monkeypatch):
    """The logged-out flows must keep working: nothing reads storage for a
    caller who never presented a session."""
    from backend import auth
    monkeypatch.setattr(auth, "db", dbmod)
    _break(dbmod, monkeypatch)
    assert auth.current_user(_request()) is None


def test_a_garbled_token_is_still_anonymous_during_an_outage(dbmod, monkeypatch):
    """An unreadable JWT never reaches storage, so it cannot be an outage."""
    from backend import auth
    monkeypatch.setattr(auth, "db", dbmod)
    _break(dbmod, monkeypatch)
    assert auth.current_user(_request("not-a-jwt")) is None
