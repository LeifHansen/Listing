"""Two requests for the same metrics at once cost one set of eBay calls.

The short metrics cache exists, in its own words, so "the dashboard and the
grid asking at once cost one set of eBay calls". They ask in the same render —
/api/insights and /api/ebay/listing-metrics — and both MISSED the cache
together, so each walked the account's active listings, asked about pending
offers and read the traffic report. The second now waits for the first and is
answered from what it cached.
"""
from __future__ import annotations

import threading
import time

import pytest

from backend.services import metrics


@pytest.fixture(autouse=True)
def _clean():
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._INFLIGHT.clear()
    yield
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._INFLIGHT.clear()


def _slow_walk(monkeypatch, calls):
    def walk(token, status=None):
        calls.append(1)
        time.sleep(0.2)          # an eBay walk takes a while
        return {"42": {"watchers": 3, "offers_received": 0}}
    monkeypatch.setattr(metrics, "_active_counts", walk)
    monkeypatch.setattr(metrics, "_offers", lambda *a, **k: ({}, set()))
    monkeypatch.setattr(metrics, "_traffic_report",
                        lambda token, ids, covered, account="": {})


def _together(n, fn):
    out = [None] * n
    threads = [threading.Thread(target=lambda i=i: out.__setitem__(i, fn()))
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    return out


def test_two_callers_at_once_share_one_walk(monkeypatch):
    calls = []
    _slow_walk(monkeypatch, calls)
    creds = {"access_token": "tok-aaaaaaaaaaaa"}
    answers = _together(3, lambda: metrics.listing_metrics(creds, ["42"]))
    assert len(calls) == 1, "each caller walked the account on its own"
    assert all(a["42"]["watchers"] == 3 for a in answers)
    assert metrics._INFLIGHT == {}, "the per-key lock outlived the work"


def test_a_sync_the_seller_pressed_still_asks_ebay(monkeypatch):
    """`fresh` is the seller asking for the truth on purpose; it must not be
    answered from the copy a concurrent ordinary load just cached."""
    calls = []
    _slow_walk(monkeypatch, calls)
    creds = {"access_token": "tok-aaaaaaaaaaaa"}
    metrics.listing_metrics(creds, ["42"])
    metrics.listing_metrics(creds, ["42"], fresh=True)
    assert len(calls) == 2
