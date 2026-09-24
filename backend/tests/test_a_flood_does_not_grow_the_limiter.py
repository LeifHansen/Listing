"""A flood of refused attempts is not stored, and answers do not change.

ratelimit.check appended every attempt, refused or not, and rebuilt the key's
whole list under the one global lock on every call. One address hammering an
endpoint with no login in front of it (/api/client-errors, the studio) grew a
single list without bound for the fifteen-minute window, and each check cost
more than the last — quadratic in the flood, on the event loop for the async
callers. Only the newest limit+1 attempts can ever decide an answer, so that is
all a key keeps.
"""
from __future__ import annotations

import pytest

from backend import ratelimit


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_a_flooding_key_holds_no_more_than_it_can_use():
    t0 = 1_000_000.0
    for i in range(5000):
        ratelimit.check("studio:1.2.3.4", now=t0 + i * 0.01, max_attempts=120)
    assert len(ratelimit._hits["studio:1.2.3.4"]) == 121


def _answers(limit: int, times: list[float]) -> list[bool]:
    ratelimit.reset()
    return [ratelimit.check("k", now=t, max_attempts=limit) for t in times]


def _reference(limit: int, times: list[float]) -> list[bool]:
    """The rule as it always read, with nothing trimmed: allowed while the
    attempts inside the window, this one included, number at most `limit`."""
    seen: list[float] = []
    out = []
    for t in times:
        seen = [s for s in seen if t - s < ratelimit.WINDOW_SECONDS] + [t]
        out.append(len(seen) <= limit)
    return out


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_the_answers_are_the_ones_it_always_gave(limit):
    w = ratelimit.WINDOW_SECONDS
    # A burst, a lull shorter than the window, another burst, and a gap past
    # the window: every place a trimmed list could disagree with a full one.
    times = ([i * 1.0 for i in range(25)]
             + [w / 2 + i for i in range(25)]
             + [w + 30 + i for i in range(25)]
             + [3 * w + i for i in range(5)])
    assert _answers(limit, times) == _reference(limit, times)
