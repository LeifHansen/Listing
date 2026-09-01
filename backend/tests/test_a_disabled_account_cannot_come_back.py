"""A disabled account is out — through every door, immediately.

Two doors and a window. Login must refuse — with the SAME generic message
and the same bcrypt cost as a wrong password, because "this account is
disabled" confirms the account exists, which is the one thing the login
error is careful never to say (see auth._ABSENT_PASSWORD_HASH). Sessions
already minted must die at current_user — the token is self-contained and
good for 30 days, so a lockout that only reaches the next login isn't one.
And re-enabling must restore login, or "disable" is really "delete".
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit

EMAIL = "locked@example.com"
PASSWORD = "password123"


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": EMAIL,
                             "password": PASSWORD}).status_code < 400
    return client, dbmod, dbmod.get_user_by_email(EMAIL)["id"]


def test_a_live_session_dies_the_moment_the_account_is_disabled(seller):
    client, db, uid = seller
    assert client.get("/api/auth/me").json()["user"]["id"] == uid

    db.set_user_disabled(uid, True)
    assert client.get("/api/auth/me").json()["user"] is None


def test_login_refuses_with_the_generic_message(seller):
    client, db, uid = seller
    db.set_user_disabled(uid, True)

    res = client.post("/api/auth/login",
                      json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 401
    wrong = client.post("/api/auth/login",
                        json={"email": EMAIL, "password": "not-the-password"})
    assert res.json()["detail"] == wrong.json()["detail"], \
        "the refusal must not say WHY — that confirms the account exists"


def test_reenabling_restores_login(seller):
    client, db, uid = seller
    db.set_user_disabled(uid, True)
    db.set_user_disabled(uid, False)

    res = client.post("/api/auth/login",
                      json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["user"]["id"] == uid
