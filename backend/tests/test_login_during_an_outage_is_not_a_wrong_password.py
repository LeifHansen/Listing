"""A database that cannot be reached is not a wrong password.

db.get_user_by_email answered a read failure with None, the same answer as
"no account with that address", and the login route said "Invalid email or
password" to both -- unless the status CACHE said the database was down. The
cache is refreshed every ten seconds, so for the gap after an outage began a
seller typing the right password was told it was wrong, and for the gap
after it ended a wrong password was "the database is down".

The lookup now raises StorageUnavailable, like get_user_by_id already did
for the same reason, and the route no longer consults the cache at all: the
error names the outage the moment it happens, and None means refused.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402

EMAIL = "seller@example.com"
PASSWORD = "password123"


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": EMAIL, "password": PASSWORD}).status_code < 400
    client.post("/api/auth/logout")
    return client, dbmod


def _outage(db, monkeypatch):
    def gone():
        raise RuntimeError("connection to server failed: Connection refused")
    monkeypatch.setattr(db, "_get_engine", gone)


def test_the_right_password_during_an_outage_is_a_503_not_a_401(seller, monkeypatch):
    client, db = seller
    # The cache still says "connected": its last probe ran before the outage.
    monkeypatch.setattr(db, "db_status",
                        lambda refresh=False: {"configured": True, "connected": True})
    _outage(db, monkeypatch)

    res = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert res.status_code == 503, res.text
    assert "temporarily unavailable" in res.json()["detail"]
    assert "Invalid" not in res.json()["detail"]


def test_a_wrong_password_after_an_outage_is_still_a_401(seller, monkeypatch):
    """The mirror image: the database is back, the cache has not noticed."""
    client, db = seller
    monkeypatch.setattr(db, "db_status",
                        lambda refresh=False: {"configured": True, "connected": False,
                                               "error": "stale"})

    res = client.post("/api/auth/login", json={"email": EMAIL, "password": "not-it"})

    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password"


def test_the_lookup_itself_refuses_rather_than_answering_no_such_account(dbmod, monkeypatch):
    _outage(dbmod, monkeypatch)
    with pytest.raises(dbmod.StorageUnavailable):
        dbmod.get_user_by_email(EMAIL)
