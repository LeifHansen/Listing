"""Password guessing against ONE account is throttled, whatever the address.

The auth limiter was keyed on the client address alone. An attacker with a
pool of addresses -- trivially, one IPv6 /64, which is 2^64 of them -- could
therefore guess at a single seller's password at any rate, bounded only by
our bcrypt CPU.

So login also counts FAILED attempts per email address. Three properties:

- it keys on the address TYPED, not on an account, and counts a failure the
  same way whether or not an account exists -- so being throttled says
  nothing about whether there is an account behind the address;
- a successful login does not count, so a seller who signs in often is never
  slowed by it;
- it is a window, not a lock: once the guessing stops, the account opens
  again on its own. Nothing here can lock a real seller out for good.

And the address half groups IPv6 by /64, the block one subscriber is handed,
so rotating through it is one client rather than 2^64 of them.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit

EMAIL = "target@example.com"
PASSWORD = "the-real-password"


@pytest.fixture()
def client(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    c = TestClient(main.app)
    assert c.post("/api/auth/signup",
                  json={"email": EMAIL, "password": PASSWORD}).status_code < 400
    c.cookies.clear()
    ratelimit.reset()
    yield c
    ratelimit.reset()


def _login(client, email, password, ip):
    return client.post("/api/auth/login",
                       json={"email": email, "password": password},
                       headers={"Fly-Client-IP": ip})


def test_guesses_from_many_addresses_still_run_out(client):
    for i in range(ratelimit.ACCOUNT_MAX_FAILURES):
        assert _login(client, EMAIL, "guess", f"10.0.{i}.1").status_code == 401

    # A fresh address, and even the right password: the account is resting.
    assert _login(client, EMAIL, PASSWORD, "10.9.9.9").status_code == 429
    # Another account is untouched by it.
    assert _login(client, "someone-else@example.com", "guess",
                  "10.9.9.10").status_code == 401


def test_an_unknown_address_is_throttled_exactly_like_a_real_one(client):
    """Otherwise the 429 itself would say which addresses have accounts."""
    for i in range(ratelimit.ACCOUNT_MAX_FAILURES):
        assert _login(client, "nobody@example.com", "guess",
                      f"10.1.{i}.1").status_code == 401
    assert _login(client, "nobody@example.com", "guess",
                  "10.9.9.9").status_code == 429


def test_signing_in_successfully_never_counts(client):
    for i in range(ratelimit.ACCOUNT_MAX_FAILURES + 5):
        assert _login(client, EMAIL, PASSWORD, f"10.2.{i}.1").status_code == 200


def test_the_account_opens_again_once_the_window_passes():
    ratelimit.reset()
    key = "login-account:target@example.com"
    for _ in range(ratelimit.ACCOUNT_MAX_FAILURES):
        ratelimit.check(key, now=1000.0,
                        max_attempts=ratelimit.ACCOUNT_MAX_FAILURES)
    assert not ratelimit.allowed(key, now=1000.0,
                                 max_attempts=ratelimit.ACCOUNT_MAX_FAILURES)
    assert ratelimit.allowed(key, now=1000.0 + ratelimit.WINDOW_SECONDS + 1,
                             max_attempts=ratelimit.ACCOUNT_MAX_FAILURES)


def test_one_ipv6_block_is_one_client(client):
    for i in range(ratelimit.MAX_ATTEMPTS):
        res = _login(client, f"spray{i}@example.com", "guess",
                     f"2001:db8:0:0::{i + 1:x}")
        assert res.status_code == 401
    # Same /64, new interface id: still the same client.
    assert _login(client, "spray-next@example.com", "guess",
                  "2001:db8::ffff:1").status_code == 429
    # The neighbouring /64 is somebody else.
    assert _login(client, "spray-next@example.com", "guess",
                  "2001:db8:0:1::1").status_code == 401


def test_the_account_delete_check_is_throttled_per_account_too(client):
    """The same guess, through the other door that takes a password: with a
    stolen session and a pool of addresses, the delete's re-authentication
    was a per-address limit only."""
    assert _login(client, EMAIL, PASSWORD, "10.3.0.1").status_code == 200
    for i in range(ratelimit.ACCOUNT_MAX_FAILURES):
        res = client.post("/api/account/delete", json={"password": "guess"},
                          headers={"Fly-Client-IP": f"10.4.{i}.1"})
        assert res.status_code == 401, res.text[:200]
    res = client.post("/api/account/delete", json={"password": PASSWORD},
                      headers={"Fly-Client-IP": "10.9.9.9"})
    assert res.status_code == 429
