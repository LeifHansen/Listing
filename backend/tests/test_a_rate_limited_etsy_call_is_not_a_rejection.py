"""A 429 from Etsy is neither a rejection nor an unknown outcome.

It used to be reported as "Etsy rejected the listing" — which sends the
seller looking for a field to fix when the only thing wrong is the clock.
A 429 is Etsy saying it did nothing; it carries its own wait, and a short
one is waited through once before anybody hears about it.
"""
from __future__ import annotations

import pytest

from backend.services import etsy


class _Resp:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return {"listing_id": 7}


@pytest.fixture
def clock(monkeypatch):
    slept = []
    monkeypatch.setattr(etsy.time, "sleep", lambda s: slept.append(s))
    return slept


def _serve(monkeypatch, answers):
    calls = []

    def _post(*a, **k):
        calls.append(1)
        return answers.pop(0)

    monkeypatch.setattr(etsy.httpx, "post", _post)
    return calls


def test_a_short_wait_is_waited_through_once(monkeypatch, clock):
    calls = _serve(monkeypatch, [_Resp(429, {"Retry-After": "2"}), _Resp(200)])
    assert etsy.create_draft_listing("tok", "1", {"title": "x"})["listing_id"] == "7"
    assert calls == [1, 1]
    assert clock == [2.0]


def test_a_second_429_is_reported_as_a_slow_down_not_a_rejection(monkeypatch, clock):
    _serve(monkeypatch, [_Resp(429, {"Retry-After": "2"}), _Resp(429, {"Retry-After": "30"})])
    with pytest.raises(etsy.RateLimited) as caught:
        etsy.create_draft_listing("tok", "1", {"title": "x"})
    err = caught.value
    assert err.retry_after == 30.0
    assert err.outcome_unknown is False
    assert "rejected" not in err.issues[0]["title"].lower()
    assert "slow down" in err.issues[0]["title"].lower()
    assert "nothing was changed" in err.issues[0]["fix"].lower()


def test_a_long_wait_is_not_slept_through(monkeypatch, clock):
    calls = _serve(monkeypatch, [_Resp(429, {"Retry-After": "120"})])
    with pytest.raises(etsy.RateLimited) as caught:
        etsy.create_draft_listing("tok", "1", {"title": "x"})
    assert calls == [1]
    assert clock == []
    assert caught.value.retry_after == 120.0


def test_a_429_without_a_retry_after_gets_the_default(monkeypatch, clock):
    _serve(monkeypatch, [_Resp(429), _Resp(429)])
    with pytest.raises(etsy.RateLimited) as caught:
        etsy.create_draft_listing("tok", "1", {"title": "x"})
    assert clock == [etsy.RETRY_AFTER_DEFAULT]
    assert caught.value.retry_after == etsy.RETRY_AFTER_DEFAULT
