"""The traffic report is held per SELLER, and a new listing costs one question.

eBay's Sell Analytics traffic report is a daily figure against an allowance
the whole app shares, and production's error feed has carried "eBay's daily
allowance is spent" every day since 2026-09-09. The hour-long cache that was
meant to spend it carefully was keyed by the access token's tail and by the
exact set of live listing ids, and neither is stable:

  * the access token is re-minted every two hours, so the same seller's report
    was read afresh on the first load after each refresh;
  * the id set changes with every listing that goes live, sells or ends — the
    grid reloads the numbers on each of those — so a bulk publish of twenty
    drafts re-read the WHOLE store's report twenty times, every page of it, to
    learn about one listing each time (which, published today, a report ending
    yesterday cannot even contain).

Now the report is held per eBay account: a listing it has already been asked
about this hour is answered from it, a listing it has not is asked about on its
own and joins it, and a listing that has left the live set simply leaves the
answer.
"""
from __future__ import annotations

import time

import httpx
import pytest

from backend.services import metrics

_HEADER = {"metrics": [{"key": "LISTING_IMPRESSION_TOTAL"},
                       {"key": "LISTING_VIEWS_TOTAL"}]}


@pytest.fixture(autouse=True)
def _reset():
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._traffic_quota_spent_until = 0.0
    yield
    metrics._CACHE.clear()
    metrics._TRAFFIC_CACHE.clear()
    metrics._traffic_quota_spent_until = 0.0


def _creds(token="tok-aaaaaaaaaaaaaaaa", uid="u1", ebay_user_id="e1"):
    return {"access_token": token, "_uid": uid, "ebay_user_id": ebay_user_id}


def _asked_ids(call: dict) -> list[str]:
    """The listing ids one traffic_report request named in its filter."""
    for part in call["params"]["filter"].split(","):
        if part.startswith("listing_ids:{"):
            return part[len("listing_ids:{"):-1].split("|")
    return []


def _serve(monkeypatch, views: dict[str, int], refuse_from: int = 0):
    """Answer every traffic_report call with the views in `views` for the
    ids it asked about — or, from call number `refuse_from` on (1-based; 0
    never), with eBay's 429 for a spent allowance. Returns the calls made."""
    calls: list[dict] = []

    def fake_get(url, **kw):
        call = {"url": url, "params": kw.get("params") or {}}
        calls.append(call)
        req = httpx.Request("GET", url)
        if refuse_from and len(calls) >= refuse_from:
            return httpx.Response(429, request=req, text=(
                '{"errors":[{"errorId":2001,"domain":"ACCESS",'
                '"category":"REQUEST","message":"Too many requests"}]}'))
        rows = [{"dimensionValues": [{"value": lid}],
                 "metricValues": [{"value": 0}, {"value": views[lid]}]}
                for lid in _asked_ids(call) if lid in views]
        return httpx.Response(200, request=req,
                              json={"header": _HEADER, "records": rows})

    monkeypatch.setattr(metrics.httpx, "get", fake_get)
    monkeypatch.setattr(metrics, "_active_counts", lambda *_a, **_k: {})
    monkeypatch.setattr(metrics, "_offers", lambda *_a, **_k: ({}, set()))
    return calls


def test_a_new_access_token_is_still_the_same_seller(monkeypatch):
    calls = _serve(monkeypatch, {"42": 3})
    first = metrics.listing_metrics(_creds(token="tok-aaaaaaaaaaaaaaaa"), ["42"])
    # Two hours on, eBay has minted a new access token for the same account.
    metrics._CACHE.clear()
    second = metrics.listing_metrics(_creds(token="tok-bbbbbbbbbbbbbbbb"), ["42"])
    assert first["42"]["views"] == second["42"]["views"] == 3
    assert len(calls) == 1, "a refreshed token re-read a report it already held"


def test_a_listing_going_live_is_asked_about_on_its_own(monkeypatch):
    calls = _serve(monkeypatch, {"a": 5, "b": 7, "c": 1})
    metrics.listing_metrics(_creds(), ["a", "b"])
    metrics._CACHE.clear()
    status: dict = {}
    out = metrics.listing_metrics(_creds(), ["a", "b", "c"], status)
    assert len(calls) == 2
    assert _asked_ids(calls[1]) == ["c"], (
        "one new listing re-read the whole store's report")
    assert (out["a"]["views"], out["b"]["views"], out["c"]["views"]) == (5, 7, 1)
    assert status["traffic_ok"] is True


def test_a_listing_that_has_ended_leaves_the_answer(monkeypatch):
    calls = _serve(monkeypatch, {"a": 5, "b": 7})
    metrics.listing_metrics(_creds(), ["a", "b"])
    metrics._CACHE.clear()
    out = metrics.listing_metrics(_creds(), ["a"])
    assert len(calls) == 1, "a smaller store is already answered by the held report"
    assert out["a"]["views"] == 5
    assert "b" not in out, "an ended listing's numbers were reported as live"


def test_a_listing_nobody_viewed_is_still_nought_when_it_joins(monkeypatch):
    """eBay's report lists what happened, not what didn't: a listing it was
    asked about and did not mention has no views — and that stays true for a
    listing read as a newcomer."""
    _serve(monkeypatch, {"a": 5})
    metrics.listing_metrics(_creds(), ["a"])
    metrics._CACHE.clear()
    out = metrics.listing_metrics(_creds(), ["a", "quiet"])
    assert out["quiet"]["views"] == 0


def test_a_newcomer_does_not_push_the_hour_back(monkeypatch):
    """Joining the held report must not restart its clock: otherwise a store
    that gains a listing every fifty minutes would never have its older
    figures read again."""
    calls = _serve(monkeypatch, {"a": 5, "b": 7})
    start = time.time()
    monkeypatch.setattr(metrics.time, "time", lambda: start)
    metrics.listing_metrics(_creds(), ["a"])
    monkeypatch.setattr(metrics.time, "time", lambda: start + 50 * 60)
    metrics._CACHE.clear()
    metrics.listing_metrics(_creds(), ["a", "b"])
    assert _asked_ids(calls[-1]) == ["b"]
    monkeypatch.setattr(metrics.time, "time",
                        lambda: start + metrics._TRAFFIC_TTL + 1)
    metrics._CACHE.clear()
    metrics.listing_metrics(_creds(), ["a", "b"])
    assert len(calls) == 3
    assert sorted(_asked_ids(calls[-1])) == ["a", "b"], (
        "an hour after the report was read, all of it is read again")


def test_a_newcomer_while_the_allowance_is_spent_is_unknown_not_unviewed(
        monkeypatch):
    # The allowance runs out on the question about the newcomer.
    calls = _serve(monkeypatch, {"a": 5, "c": 1}, refuse_from=2)
    metrics.listing_metrics(_creds(), ["a"])
    metrics._CACHE.clear()
    out = metrics.listing_metrics(_creds(), ["a", "c"])
    assert len(calls) == 2
    assert out["a"]["views"] == 5, "what the held report knew still stands"
    assert "views" not in out.get("c", {}), (
        "a listing eBay was never asked about was reported as unviewed")
    # And while it stays spent, the newcomer is not asked about again.
    metrics._CACHE.clear()
    metrics.listing_metrics(_creds(), ["a", "c"])
    assert len(calls) == 2


def test_two_sellers_never_share_a_report(monkeypatch):
    calls = _serve(monkeypatch, {"42": 3})
    metrics.listing_metrics(_creds(uid="u1", ebay_user_id="e1"), ["42"])
    metrics._CACHE.clear()
    metrics.listing_metrics(_creds(uid="u2", ebay_user_id="e2"), ["42"])
    assert len(calls) == 2
