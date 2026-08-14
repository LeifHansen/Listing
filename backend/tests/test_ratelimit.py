"""The auth rate limiter — the guard on the only endpoints an attacker can
hammer for free (login, signup, the account-delete password check).

Time is injected rather than slept, so the window behavior is tested exactly
without making the suite slow.
"""
from __future__ import annotations

import pytest

from backend import ratelimit


@pytest.fixture(autouse=True)
def clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_allows_up_to_the_limit_then_blocks():
    for i in range(ratelimit.MAX_ATTEMPTS):
        assert ratelimit.check("login:1.2.3.4", now=1000.0), f"attempt {i + 1} rejected"
    assert not ratelimit.check("login:1.2.3.4", now=1000.0)
    # Still blocked while the window holds.
    assert not ratelimit.check("login:1.2.3.4", now=1000.0 + ratelimit.WINDOW_SECONDS - 1)


def test_window_expiry_lets_a_client_back_in():
    for _ in range(ratelimit.MAX_ATTEMPTS + 5):
        ratelimit.check("login:1.2.3.4", now=1000.0)
    later = 1000.0 + ratelimit.WINDOW_SECONDS + 1
    assert ratelimit.check("login:1.2.3.4", now=later)


def test_buckets_and_clients_are_independent():
    """One attacker must not lock out everyone else — nor block their own
    signup attempts by exhausting login."""
    for _ in range(ratelimit.MAX_ATTEMPTS + 1):
        ratelimit.check("login:1.2.3.4", now=1000.0)
    assert not ratelimit.check("login:1.2.3.4", now=1000.0)
    assert ratelimit.check("login:5.6.7.8", now=1000.0)      # another client
    assert ratelimit.check("signup:1.2.3.4", now=1000.0)     # another endpoint


def test_key_table_does_not_grow_without_bound():
    """A spray across many IPs must not grow the dict forever; cold keys are
    swept once the cap is reached."""
    for i in range(ratelimit._MAX_KEYS + 500):
        ratelimit.check(f"login:10.0.{i // 256}.{i % 256}", now=1000.0)
    # Every key above is still inside its window, so the sweep can't drop them;
    # advancing past the window and touching one more must collect the rest.
    ratelimit.check("login:everyone-else-is-cold",
                    now=1000.0 + ratelimit.WINDOW_SECONDS + 1)
    assert len(ratelimit._hits) < ratelimit._MAX_KEYS
