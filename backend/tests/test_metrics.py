"""eBay traffic-report parsing.

The dashboard's views/impressions counters read every listing as 0 for as long
as the parser looked for the metric column names under `header.metricKeys` — a
field the live Analytics API never sends (it names them in `header.metrics`).
Nothing failed loudly: each record simply parsed into an empty dict. These
tests pin the response shapes eBay actually returns.
"""
from __future__ import annotations

import datetime as _dt

import httpx
import pytest

from backend.services import metrics


@pytest.fixture(autouse=True)
def _clear_cache():
    metrics._CACHE.clear()
    yield
    metrics._CACHE.clear()


def _record(listing_id: str, *values):
    return {"dimensionValues": [{"value": listing_id, "applicable": True}],
            "metricValues": [{"value": v, "applicable": True} for v in values]}


def _report(header: dict, records: list) -> dict:
    return {"header": header, "records": records,
            "startDate": "2026-07-19T07:00:00.000Z",
            "endDate": "2026-08-18T07:00:00.000Z"}


def _serve(monkeypatch, payload, status: int = 200, text: str = ""):
    """Answer the traffic_report call with `payload`, and record the request."""
    seen: list[dict] = []

    def fake_get(url, **kw):
        seen.append({"url": url, **kw})
        if status != 200:
            return httpx.Response(status, text=text, request=httpx.Request("GET", url))
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(metrics.httpx, "get", fake_get)
    return seen


# eBay's documented shape: header.metrics is a list of objects carrying the
# column's key, and metricValues line up with it positionally.
_LIVE_HEADER = {
    "dimensionKeys": [{"key": "LISTING"}],
    "metrics": [
        {"key": "LISTING_IMPRESSION_TOTAL", "dataType": "INTEGER",
         "localizedName": "Total impressions on eBay site"},
        {"key": "LISTING_VIEWS_TOTAL", "dataType": "INTEGER",
         "localizedName": "Total listing views"},
    ],
}


def test_parses_views_and_impressions(monkeypatch):
    _serve(monkeypatch, _report(_LIVE_HEADER, [_record("110040606450", 411, 37)]))
    assert metrics._traffic("tok", ["110040606450"]) == {
        "110040606450": {"impressions": 411, "views": 37}}


def test_columns_map_by_name_not_request_order(monkeypatch):
    """eBay may return the columns in its own order — follow the header."""
    header = {"metrics": [{"key": "LISTING_VIEWS_TOTAL"},
                          {"key": "LISTING_IMPRESSION_TOTAL"}]}
    _serve(monkeypatch, _report(header, [_record("42", 37, 411)]))
    assert metrics._traffic("tok", ["42"]) == {"42": {"views": 37, "impressions": 411}}


def test_accepts_metric_keys_as_plain_strings(monkeypatch):
    header = {"metricKeys": ["LISTING_IMPRESSION_TOTAL", "LISTING_VIEWS_TOTAL"]}
    _serve(monkeypatch, _report(header, [_record("42", 9, 3)]))
    assert metrics._traffic("tok", ["42"]) == {"42": {"impressions": 9, "views": 3}}


def test_unnamed_columns_fall_back_to_requested_order(monkeypatch):
    _serve(monkeypatch, _report({}, [_record("42", 9, 3)]))
    assert metrics._traffic("tok", ["42"]) == {"42": {"impressions": 9, "views": 3}}


def test_unnamed_columns_of_unexpected_width_are_not_guessed(monkeypatch):
    """Three unlabelled columns for two requested metrics: report nothing
    rather than pin the wrong number to a listing."""
    _serve(monkeypatch, _report({}, [_record("42", 9, 3, 1)]))
    assert metrics._traffic("tok", ["42"]) == {"42": {}}


def test_values_survive_floats_strings_and_junk(monkeypatch):
    _serve(monkeypatch, _report(_LIVE_HEADER, [
        _record("1", 411.0, "37"),
        _record("2", None, "n/a"),
    ]))
    assert metrics._traffic("tok", ["1", "2"]) == {
        "1": {"impressions": 411, "views": 37},
        "2": {"impressions": 0, "views": 0}}


def test_request_asks_for_the_metrics_it_parses(monkeypatch):
    seen = _serve(monkeypatch, _report(_LIVE_HEADER, []))
    metrics._traffic("tok", ["1", "2"])
    params = seen[0]["params"]
    assert params["dimension"] == "LISTING"
    assert set(params["metric"].split(",")) == set(metrics._FIELD_BY_METRIC)
    assert "listing_ids:{1|2}" in params["filter"]


def test_missing_scope_asks_for_a_reconnect(monkeypatch):
    _serve(monkeypatch, None, status=403,
           text='{"errors":[{"message":"Insufficient permissions to fulfill the request."}]}')
    with pytest.raises(metrics.TrafficUnavailable) as err:
        metrics._traffic("tok", ["42"])
    assert err.value.needs_reconnect is True


def test_server_blip_is_not_a_reconnect(monkeypatch):
    _serve(monkeypatch, None, status=500, text="internal error")
    with pytest.raises(metrics.TrafficUnavailable) as err:
        metrics._traffic("tok", ["42"])
    assert err.value.needs_reconnect is False


def test_traffic_failure_is_reported_and_watchers_survive(monkeypatch):
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: (_ for _ in ()).throw(
        metrics.TrafficUnavailable("nope", needs_reconnect=True)))
    monkeypatch.setattr(metrics, "_watchers", lambda _t: {"42": 5})
    status: dict = {}
    out = metrics.listing_metrics({"access_token": "tok"}, ["42"], status)
    assert out == {"42": {"watchers": 5}}, "no views where none could be read"
    assert status == {"traffic_ok": False, "needs_reconnect": True}


def test_status_reports_success(monkeypatch):
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: {"42": {"views": 7}})
    monkeypatch.setattr(metrics, "_watchers", lambda _t: {})
    status: dict = {}
    # Both calls answered, so both numbers are known — the watch list simply
    # had nothing to say about this listing, which is nought watchers.
    assert metrics.listing_metrics({"access_token": "tok"}, ["42"], status) == {
        "42": {"views": 7, "watchers": 0}}
    assert status == {"traffic_ok": True, "needs_reconnect": False}


# ------------------------------ a measured nought is a nought, on every card

def test_a_listing_nobody_viewed_reports_nought_rather_than_nothing(monkeypatch):
    """Reported as: "why do some listings say 0 views, and some don't show
    views at all."

    eBay's traffic report lists what happened, not what didn't, so a listing
    nobody has looked at is simply absent from it. Absent reached the card as
    "no numbers for this one" and it drew no views row, while the listing
    beside it — mentioned by the report, with a zero in it — drew "0 views".
    Same fact about two listings, two different cards, and a seller with no
    way to tell which meaning either one carried.

    The report answered for every id it was asked about. Its answer for these
    is nought.
    """
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: {"42": {"views": 7}})
    monkeypatch.setattr(metrics, "_watchers", lambda _t: {"42": 3})

    out = metrics.listing_metrics({"access_token": "tok"}, ["42", "43"], {})

    assert out == {"42": {"views": 7, "watchers": 3},
                   "43": {"views": 0, "watchers": 0}}


def test_a_report_that_could_not_be_read_fills_nothing(monkeypatch):
    """The other half, and the reason this is filled at the source rather than
    on the card: an outage must not read as a store nobody visited."""
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: (_ for _ in ()).throw(
        metrics.TrafficUnavailable("nope", needs_reconnect=False)))
    monkeypatch.setattr(metrics, "_watchers", lambda _t: {"42": 3})
    status: dict = {}

    out = metrics.listing_metrics({"access_token": "tok"}, ["42", "43"], status)

    assert out == {"42": {"watchers": 3}, "43": {"watchers": 0}}
    assert "views" not in out["42"] and "views" not in out["43"]
    assert status["traffic_ok"] is False


def test_watch_counts_that_could_not_be_read_fill_nothing_either(monkeypatch):
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: {"42": {"views": 7}})
    monkeypatch.setattr(metrics, "_watchers", lambda _t: (_ for _ in ()).throw(
        RuntimeError("trading api down")))

    out = metrics.listing_metrics({"access_token": "tok"}, ["42", "43"], {})

    assert out == {"42": {"views": 7}, "43": {"views": 0}}


def test_cache_serves_the_status_too(monkeypatch):
    """A second call inside the TTL still has to answer "was traffic readable?"
    — the cached entry carries the status, not just the numbers."""
    calls = []

    def one_shot(_token, ids):
        calls.append(ids)
        return {"42": {"views": 7}}

    monkeypatch.setattr(metrics, "_traffic", one_shot)
    monkeypatch.setattr(metrics, "_watchers", lambda _t: {})
    first: dict = {}
    second: dict = {}
    assert metrics.listing_metrics({"access_token": "tok"}, ["42"], first) \
        == metrics.listing_metrics({"access_token": "tok"}, ["42"], second)
    assert first == second == {"traffic_ok": True, "needs_reconnect": False}
    assert len(calls) == 1


def test_no_creds_is_quiet():
    status: dict = {}
    assert metrics.listing_metrics(None, ["42"], status) == {}
    assert status == {"traffic_ok": False, "needs_reconnect": False}
    assert metrics.listing_metrics({"access_token": "tok"}, []) == {}


def test_the_report_never_asks_for_a_date_ebay_calls_the_future(monkeypatch):
    """eBay rejected the whole report — "Neither the start date nor the end
    date can be in the future" — because the end date was UTC *today* while
    eBay judges "future" in Pacific time. For the seven hours after UTC
    midnight the app was asking for tomorrow, so the dashboard lost views and
    impressions every night. The report only holds data through yesterday
    anyway, so ending there is both correct and safe in every timezone."""
    seen = _serve(monkeypatch, _report(_LIVE_HEADER, []))
    metrics._traffic("tok", ["42"])

    date_filter = next(f for f in seen[0]["params"]["filter"].split(",")
                       if f.startswith("date_range:"))
    start, end = date_filter[len("date_range:["):-1].split("..")
    today = _dt.datetime.now(_dt.timezone.utc).date()
    assert end == f"{today - _dt.timedelta(days=1):%Y%m%d}"
    assert start < end
