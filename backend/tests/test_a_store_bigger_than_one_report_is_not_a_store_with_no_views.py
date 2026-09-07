"""Reported as: "make sure these metrics are accurate on each card. They look
low" — a grid of live listings, nearly every one of them reading 0 views and
0 watchers.

Two counters, two ways of turning "we did not ask" into a number.

**Views.** eBay's `getTrafficReport` takes at most 200 ids in its
`listing_ids` filter. The request sent `listing_ids[:200]` and dropped the
rest on the floor — no error, no log line, just a shorter question. Then the
nought-filling at the bottom of `listing_metrics`, which exists so that a
listing eBay's report omits reads as "0 views" rather than as a blank card,
filled in every id it had been *handed*, not every id eBay had been *asked*
about. So the dropped listings came back as a measured zero.

The ids are sorted and eBay's ascend with age, so the split was not random:
the oldest 200 listings carried real traffic and everything newer — every
listing the seller had just put up and was actually watching — read as
traffic nobody had.

**Watchers.** The same shape one layer down. The sweep over the account's
active list is bounded at 25 pages, and stopping at the cap was
indistinguishable from reaching the end, so the listings past it were filled
in as nought watchers too.

Both are now filled in only where the answer was actually obtained. And the
report asks for eBay's full 90 days rather than 30, which is the window a
seller is comparing against in Seller Hub.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import httpx
import pytest

from backend.services import ebay_trading, metrics


@pytest.fixture(autouse=True)
def _clear_cache():
    metrics._CACHE.clear()
    yield
    metrics._CACHE.clear()


_HEADER = {"metrics": [{"key": "LISTING_IMPRESSION_TOTAL"},
                       {"key": "LISTING_VIEWS_TOTAL"}]}


def _ids_asked(params: dict) -> list[str]:
    """The ids one traffic_report request actually carried."""
    part = next(f for f in params["filter"].split(",")
                if f.startswith("listing_ids:"))
    return part[len("listing_ids:{"):-1].split("|")


def _echo_traffic(monkeypatch, *, fails: set = frozenset(), status: int = 500,
                  text: str = "eBay is having a moment"):
    """Answer every pass with one record per id asked about — id 7 gets 7
    views — so a listing missing from the result is one that was never asked
    about. Passes carrying any id in `fails` are refused instead."""
    seen: list[list[str]] = []

    def fake_get(url, **kw):
        ids = _ids_asked(kw["params"])
        seen.append(ids)
        req = httpx.Request("GET", url)
        if set(ids) & set(fails):
            return httpx.Response(status, text=text, request=req)
        return httpx.Response(200, request=req, json={
            "header": _HEADER,
            "records": [{"dimensionValues": [{"value": i}],
                         "metricValues": [{"value": 0}, {"value": int(i)}]}
                        for i in ids],
        })

    monkeypatch.setattr(metrics.httpx, "get", fake_get)
    return seen


# ------------------------------------------------------- views, every listing

def test_every_listing_is_asked_about_not_just_the_first_two_hundred(monkeypatch):
    ids = [str(i) for i in range(1000, 1500)]  # 500 live listings
    seen = _echo_traffic(monkeypatch)

    out = metrics._traffic("tok", ids)

    assert [len(p) for p in seen] == [200, 200, 100], "asked in eBay-sized passes"
    assert sorted(i for p in seen for i in p) == sorted(ids), "and asked about all of them"
    assert len(out) == 500
    assert out["1499"] == {"impressions": 0, "views": 1499}, \
        "the 500th listing carries its own number, not the 200th's leftovers"


def test_no_pass_exceeds_the_limit_ebay_will_answer(monkeypatch):
    """200 is eBay's documented ceiling for the listing_ids filter — one over
    and the whole report is refused, which is how this gets found the hard way."""
    seen = _echo_traffic(monkeypatch)
    metrics._traffic("tok", [str(i) for i in range(401)])
    assert max(len(p) for p in seen) <= metrics._ID_CHUNK == 200


def test_the_report_asks_for_the_full_window_ebay_offers(monkeypatch):
    """30 days under a label the seller reads as "views" is an undercount
    against Seller Hub, which counts a listing's whole life. 90 is eBay's max."""
    import datetime as dt

    seen: list[dict] = []
    monkeypatch.setattr(metrics.httpx, "get", lambda url, **kw: (
        seen.append(kw["params"]),
        httpx.Response(200, request=httpx.Request("GET", url),
                       json={"header": _HEADER, "records": []}))[1])

    metrics._traffic("tok", ["42"])

    span = next(f for f in seen[0]["filter"].split(",") if f.startswith("date_range:"))
    start, end = span[len("date_range:["):-1].split("..")
    days = (dt.datetime.strptime(end, "%Y%m%d") - dt.datetime.strptime(start, "%Y%m%d")).days
    assert days == 90


# ------------------------------------- a pass that failed is not a quiet nought

def test_a_listing_in_a_pass_that_failed_reports_nothing_not_nought(monkeypatch):
    """The listings eBay answered for keep their numbers; the ones in the
    refused pass carry no views at all. A blank is readable as "we don't
    know" — a 0 is not."""
    ids = [str(i) for i in range(1000, 1300)]  # two passes: 200 + 100
    _echo_traffic(monkeypatch, fails={"1250"})  # kills the second pass only
    monkeypatch.setattr(metrics, "_watchers", lambda *_a, **_k: {})
    status: dict = {}

    out = metrics.listing_metrics({"access_token": "tok"}, ids, status)

    covered, missing = sorted(ids)[:200], sorted(ids)[200:]
    assert all(out[i]["views"] == int(i) for i in covered)
    assert all("views" not in out.get(i, {}) for i in missing), \
        "never asked about is never nought"
    assert status["traffic_ok"] is True, "some of the report was read"


def test_a_refusal_the_seller_must_fix_stops_after_the_first_pass(monkeypatch):
    """A token without the analytics scope refuses every pass identically.
    Learning that once is enough — a big store would otherwise spend a dozen
    round trips on it, every time the grid loads."""
    seen = _echo_traffic(monkeypatch, fails={"1000"}, status=403,
                         text='{"errors":[{"message":"Insufficient permissions"}]}')

    with pytest.raises(metrics.TrafficUnavailable) as err:
        metrics._traffic("tok", [str(i) for i in range(1000, 2000)])

    assert err.value.needs_reconnect is True
    assert len(seen) == 1, "stopped rather than asked five times over"


def test_when_every_pass_fails_the_report_is_unreadable_not_empty(monkeypatch):
    """Nothing came back at all — that has to raise, or the caller fills the
    whole store in as nought."""
    ids = [str(i) for i in range(1000, 1300)]
    _echo_traffic(monkeypatch, fails=set(ids))
    monkeypatch.setattr(metrics, "_watchers", lambda *_a, **_k: {})
    status: dict = {}

    out = metrics.listing_metrics({"access_token": "tok"}, ids, status)

    assert status == {"traffic_ok": False, "needs_reconnect": False}
    assert all("views" not in m for m in out.values())


# ---------------------------------------------- watchers, past the page cap

def _active_page(ids: list[str], total_pages: int) -> ET.Element:
    root = ET.Element("GetMyeBaySellingResponse")
    active = ET.SubElement(root, "ActiveList")
    arr = ET.SubElement(active, "ItemArray")
    for i in ids:
        item = ET.SubElement(arr, "Item")
        ET.SubElement(item, "ItemID").text = i
        ET.SubElement(item, "WatchCount").text = str(int(i) % 10)
    pag = ET.SubElement(active, "PaginationResult")
    ET.SubElement(pag, "TotalNumberOfPages").text = str(total_pages)
    return root


def test_a_sweep_that_ran_out_of_pages_says_so(monkeypatch):
    pages = []

    def fake_call(_name, _token, body):
        pages.append(body)
        return _active_page([str(9000 + len(pages))], total_pages=99)

    monkeypatch.setattr(ebay_trading, "_call", fake_call)
    status: dict = {}
    out = ebay_trading.watch_counts("tok", max_pages=3, status=status)

    assert len(pages) == 3 and len(out) == 3
    assert status["complete"] is False


def test_a_sweep_that_reached_the_end_says_that_too(monkeypatch):
    monkeypatch.setattr(ebay_trading, "_call",
                        lambda *_a, **_k: _active_page(["9001", "9002"], total_pages=1))
    status: dict = {}
    assert ebay_trading.watch_counts("tok", status=status) == {"9001": 1, "9002": 2}
    assert status["complete"] is True


def test_listings_past_the_watch_sweep_are_unknown_not_unwatched(monkeypatch):
    """The half the sweep reached keeps its counts; the half it never got to
    shows no watcher figure rather than a zero it never measured."""
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: {})

    def short_sweep(_token, status=None):
        if status is not None:
            status["complete"] = False
        return {"42": 6}

    monkeypatch.setattr(metrics, "_watchers", short_sweep)

    out = metrics.listing_metrics({"access_token": "tok"}, ["42", "43"], {})

    assert out == {"42": {"watchers": 6}}
    assert "43" not in out, "not reached is not nought"
