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


def test_the_cap_holds_DURING_a_spray_not_only_after_it():
    """The case above lets time pass, which is what makes the cheap sweep
    work. A real spray does not: thousands of distinct keys arrive inside one
    window, nothing is expired, and the sweep finds no candidates at all.

    That was the hole. The eviction pass collected only keys older than the
    window, so under exactly the traffic _MAX_KEYS exists to survive it
    collected nothing and the dict grew for as long as the flood lasted — the
    cap was a comment rather than a bound.
    """
    for i in range(ratelimit._MAX_KEYS * 2):
        # One fixed instant: no key is ever old enough to expire.
        ratelimit.check(f"login:10.1.{i // 256}.{i % 256}", now=1000.0)
    assert len(ratelimit._hits) <= ratelimit._MAX_KEYS


def test_eviction_never_drops_the_caller_being_counted():
    """Evicting a key forgives its attempts. Dropping the key we are in the
    middle of counting would hand an attacker a fresh allowance on the very
    request that triggered the eviction."""
    attacker = "login:9.9.9.9"
    for _ in range(ratelimit.MAX_ATTEMPTS + 1):
        ratelimit.check(attacker, now=1000.0)
    assert not ratelimit.check(attacker, now=1000.0)
    # Now flood past the cap from everywhere else, then come back.
    for i in range(ratelimit._MAX_KEYS * 2):
        ratelimit.check(f"login:10.2.{i // 256}.{i % 256}", now=1000.0)
    assert not ratelimit.check(attacker, now=1000.0), \
        "the flood bought the attacker a clean slate"
