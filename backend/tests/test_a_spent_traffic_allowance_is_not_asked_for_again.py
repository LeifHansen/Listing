"""eBay's daily allowance for the traffic report, once spent, stays spent.

Production's error feed on 2026-09-09 carried `traffic_report 429` twenty-eight
times in thirty-six hours: one store, two pages of listings, asked afresh on
every dashboard load once a two-minute cache had expired, against a report
that only ever changes once a day. After the allowance ran out every load was
another refused call and another WARNING row saying the same thing.

So the report has an hour-long cache of its own, a 429 is remembered until
midnight Pacific (when eBay resets application limits), a stale report stands
in meanwhile, and the skip is logged as information rather than as a failure.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.services import metrics


@pytest.fixture(autouse=True)
def _reset():
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._traffic_quota_spent_until = 0.0
    yield
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._traffic_quota_spent_until = 0.0


_HEADER = {"metrics": [{"key": "LISTING_IMPRESSION_TOTAL"},
                       {"key": "LISTING_VIEWS_TOTAL"}]}


def _report(*rows):
    return {"header": _HEADER,
            "records": [{"dimensionValues": [{"value": lid}],
                         "metricValues": [{"value": i}, {"value": v}]}
                        for lid, i, v in rows]}


def _serve(monkeypatch, answers):
    """Answer successive traffic_report calls from `answers`: a dict is a
    200 body, an int is that status with an eBay-shaped error body. Returns
    the list of calls made."""
    calls: list[str] = []
    queue = list(answers)

    def fake_get(url, **kw):
        calls.append(url)
        nxt = queue.pop(0) if queue else answers[-1]
        req = httpx.Request("GET", url)
        if isinstance(nxt, int):
            return httpx.Response(nxt, request=req, text=(
                '{"errors":[{"errorId":2001,"domain":"ACCESS",'
                '"category":"REQUEST","message":"Too many requests"}]}'))
        return httpx.Response(200, request=req, json=nxt)

    monkeypatch.setattr(metrics.httpx, "get", fake_get)
    monkeypatch.setattr(metrics, "_active_counts", lambda *_a, **_k: {})
    monkeypatch.setattr(metrics, "_offers", lambda *_a, **_k: ({}, set()))
    return calls


def test_the_report_is_read_once_an_hour_not_once_a_load(monkeypatch):
    calls = _serve(monkeypatch, [_report(("42", 10, 3))])
    creds = {"access_token": "tok"}
    first = metrics.listing_metrics(creds, ["42"])
    # The short cache is what the grid and the insights share; clearing it is
    # what a new request two minutes later looks like. `fresh` is the seller
    # pressing "Sync with eBay", which must re-ask about offers, not traffic.
    metrics._CACHE.clear()
    second = metrics.listing_metrics(creds, ["42"], fresh=True)
    assert first["42"]["views"] == second["42"]["views"] == 3
    assert len(calls) == 1, "a daily figure was fetched twice in one hour"


def test_a_429_is_remembered_until_the_reset(monkeypatch, caplog):
    calls = _serve(monkeypatch, [429])
    creds = {"access_token": "tok"}
    status: dict = {}
    with caplog.at_level(logging.INFO, logger="thryft.metrics"):
        first = metrics.listing_metrics(creds, ["42"], status)
        assert "views" not in first.get("42", {}), "a refused report filled in a nought"
        assert status["traffic_ok"] is False
        assert status["needs_reconnect"] is False, "a spent quota is not a scope problem"
        assert metrics._traffic_quota_spent_until > time.time()
        metrics._CACHE.clear()
        again = metrics.listing_metrics(creds, ["42"])
        assert "views" not in again.get("42", {})
    assert len(calls) == 1, "eBay was asked again while the allowance was spent"
    # One WARNING, when the latch was set -- one row a day in the error feed
    # for the operator -- and nothing for the loads skipped after it.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "allowance" in warnings[0].getMessage()


def test_a_refusal_that_is_not_a_quota_still_warns(monkeypatch, caplog):
    _serve(monkeypatch, [500])
    with caplog.at_level(logging.INFO, logger="thryft.metrics"):
        metrics.listing_metrics({"access_token": "tok"}, ["42"])
    assert metrics._traffic_quota_spent_until == 0.0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_held_report_stands_in_while_the_allowance_is_spent(monkeypatch):
    calls = _serve(monkeypatch, [_report(("42", 10, 3)), 429])
    creds = {"access_token": "tok"}
    assert metrics.listing_metrics(creds, ["42"])["42"]["views"] == 3
    # An hour on, the held report is stale and eBay is asked — and refuses.
    later = time.time() + metrics._TRAFFIC_TTL + 1
    monkeypatch.setattr(metrics.time, "time", lambda: later)
    metrics._CACHE.clear()
    status: dict = {}
    again = metrics.listing_metrics(creds, ["42"], status)
    assert again["42"]["views"] == 3, "yesterday's figures are still yesterday's figures"
    assert status["traffic_ok"] is True
    assert len(calls) == 2
    # And from here until the reset, not even asked.
    metrics._CACHE.clear()
    metrics.listing_metrics(creds, ["42"])
    assert len(calls) == 2


def test_the_reset_is_the_next_midnight_pacific():
    now = datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc)  # 15:30 PDT
    reset = datetime.fromtimestamp(metrics._quota_reset_time(now),
                                   ZoneInfo("America/Los_Angeles"))
    assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
    assert reset.date() == (now.astimezone(reset.tzinfo) + timedelta(days=1)).date()
    assert reset.timestamp() > now.timestamp()


def test_a_second_page_is_not_asked_for_after_the_first_is_refused(monkeypatch):
    """A store bigger than one request: once the allowance is gone every
    later page is a refusal too, so the sweep stops rather than collecting
    one 429 per page."""
    calls = _serve(monkeypatch, [429])
    ids = [str(i) for i in range(metrics._ID_CHUNK * 3)]
    metrics.listing_metrics({"access_token": "tok"}, ids)
    assert len(calls) == 1
