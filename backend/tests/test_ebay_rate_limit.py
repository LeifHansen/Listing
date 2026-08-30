"""Hitting eBay's call limit is not "this listing failed".

The Trading client turned every non-200 into `eBay returned <code> for
<call>` and every rejection into a per-listing failure. eBay's rate limit
arrives as both — HTTP 429 on the transport, or ErrorCode 21919144 in the XML
— so when a sync ran into it, three things went wrong at once:

  - each remaining listing was counted as FAILED, which reads as "eBay
    rejected these listings" when eBay never looked at them;
  - the pass kept going, firing hundreds more calls into a wall. The limits
    are per seller and windowed, so the extra calls do not merely fail: they
    hold the window open and make the wait longer;
  - the seller was shown a raw HTTP status code, which tells them nothing they
    can act on.

eBay's own message names the wait ("You have exceeded your maximum call limit
of 3000 for 5 seconds. Try again after 5 seconds."), and a 429 carries
Retry-After. Both are the answer to "when can we try again?", and both were
being thrown away.

Contract, verified against eBay's published documentation rather than assumed:

  - **21919144** is the seller-level call-limit error, returned in the
    response body with Ack=Failure, not as an HTTP status
    (developer.ebay.com KB 2137). The limits are per seller and per window —
    5000 Add-listing calls / 30s, 1200 Revise / 30s — so they are reachable
    by one busy seller, not only by a busy application.
  - The application-level daily quota is a different, separately worded
    refusal ("Your application has exceeded usage limit on this call, please
    make call to Developer Analytics API..."). Its numeric code is not
    something this repository can cite with confidence, so it is matched on
    eBay's wording as a documented fallback and labelled as one — not
    invented as a code.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading


def _xml(error_code: str, message: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Failure</Ack>"
        "<Errors><SeverityCode>Error</SeverityCode>"
        f"<ErrorCode>{error_code}</ErrorCode>"
        f"<LongMessage>{message}</LongMessage></Errors>"
        "</GetItemResponse>").encode()


class _Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}


@pytest.fixture()
def ebay(monkeypatch):
    """Answer the next Trading call with whatever the test supplies."""
    def _serve(resp):
        monkeypatch.setattr(ebay_trading.httpx, "post",
                            lambda *a, **k: resp)
    return _serve


# --------------------------------------------------------- it is named

def test_a_seller_call_limit_is_its_own_condition(ebay):
    """21919144, eBay's documented seller-level limit."""
    ebay(_Resp(content=_xml(
        "21919144",
        "You have exceeded your maximum call limit of 3000 for 5 seconds. "
        "Try again after 5 seconds.")))

    with pytest.raises(ebay_trading.RateLimited):
        ebay_trading.get_listing("tok", "110")


def test_an_http_429_is_the_same_condition(ebay):
    """The transport can refuse before eBay's XML is ever produced."""
    ebay(_Resp(status=429, headers={"Retry-After": "30"}))

    with pytest.raises(ebay_trading.RateLimited):
        ebay_trading.get_listing("tok", "110")


def test_the_application_quota_wording_is_recognised(ebay):
    """eBay's daily application quota. Matched on its published wording,
    because this repository cannot cite its numeric code with confidence —
    and guessing a code would be worse than saying which is which."""
    ebay(_Resp(content=_xml(
        "518",
        "Your application has exceeded usage limit on this call, please make "
        "call to Developer Analytics API to check your call usage.")))

    with pytest.raises(ebay_trading.RateLimited):
        ebay_trading.get_listing("tok", "110")


def test_a_monthly_selling_allowance_is_not_a_call_limit(ebay):
    """The likeliest misfire, and a harmful one. eBay caps how many items a
    seller may LIST in a month; that refusal also says "exceeded" and "limit"
    and it is not a rate limit. Reporting it as one tells the seller to wait a
    few seconds for something that will not change until eBay raises their
    allowance or the month turns — advice that wastes their time and hides the
    real answer, which is to request a higher limit."""
    ebay(_Resp(content=_xml(
        "21919188",
        "You have exceeded your monthly listing limit of 10 items. Request a "
        "higher selling limit from eBay.")))

    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.get_listing("tok", "110")
    assert not isinstance(caught.value, ebay_trading.RateLimited), \
        "a monthly SELLING allowance was reported as a call rate limit"


def test_an_ordinary_rejection_is_still_an_ordinary_rejection(ebay):
    """The guard must be narrow. A listing eBay refuses on its merits is a
    seller-fixable problem, and calling it a rate limit would tell them to
    wait for something that will never change on its own."""
    ebay(_Resp(content=_xml("21916884", "Dropped condition from Item specifics.")))

    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.get_listing("tok", "110")
    assert not isinstance(caught.value, ebay_trading.RateLimited)


# ------------------------------------------------- it carries the wait

def test_ebays_own_retry_after_is_kept(ebay):
    ebay(_Resp(status=429, headers={"Retry-After": "30"}))

    with pytest.raises(ebay_trading.RateLimited) as caught:
        ebay_trading.get_listing("tok", "110")
    assert caught.value.retry_after == 30


def test_the_wait_in_ebays_message_is_read_when_there_is_no_header(ebay):
    """"Try again after 5 seconds" is eBay answering the only question that
    matters here, in the body rather than a header."""
    ebay(_Resp(content=_xml(
        "21919144",
        "You have exceeded your maximum call limit of 3000 for 5 seconds. "
        "Try again after 5 seconds.")))

    with pytest.raises(ebay_trading.RateLimited) as caught:
        ebay_trading.get_listing("tok", "110")
    assert caught.value.retry_after == 5


def test_an_unparseable_wait_is_absent_not_invented(ebay):
    """`None`, not a made-up default. A number here is a promise about when
    eBay will answer, and this app is not in a position to make one."""
    ebay(_Resp(status=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))

    with pytest.raises(ebay_trading.RateLimited) as caught:
        ebay_trading.get_listing("tok", "110")
    assert caught.value.retry_after is None


def test_the_message_says_something_a_seller_can_act_on(ebay):
    """It used to be "eBay returned 429 for GetItem"."""
    ebay(_Resp(status=429, headers={"Retry-After": "30"}))

    with pytest.raises(ebay_trading.RateLimited) as caught:
        ebay_trading.get_listing("tok", "110")
    text = str(caught.value).lower()
    assert "429" not in text
    assert "getitem" not in text
    assert "limit" in text or "too many" in text


# ------------------------------------------------- and the sync stops

@pytest.fixture()
def a_store(monkeypatch):
    """A sync of N listings where eBay starts refusing after the first few."""
    from backend import db
    from backend.services import listing_sync

    def _run(total: int, ok_before_limit: int) -> tuple[dict, list[str]]:
        ids = [str(100 + i) for i in range(total)]
        monkeypatch.setattr(ebay_trading, "active_listing_ids",
                            lambda *a, **k: ids)
        monkeypatch.setattr(ebay_trading, "unsold_listing_ids", lambda *a, **k: [])
        monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})
        monkeypatch.setattr(db, "list_listings", lambda **_k: [])
        monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: None)

        asked: list[str] = []

        def _get(_token, item_id):
            asked.append(item_id)
            if len(asked) > ok_before_limit:
                raise ebay_trading.RateLimited("slow down", retry_after=30)
            return {"title": f"Item {item_id}", "price": 10.0, "quantity": 1}

        monkeypatch.setattr(ebay_trading, "get_listing", _get)
        # One worker, so "stopped after N" is a statement about the code and
        # not about how the pool happened to schedule.
        monkeypatch.setattr(listing_sync, "_FETCH_WORKERS", 1)
        return listing_sync.import_active("tok", "u1"), asked

    return _run


def test_the_sync_stops_asking_once_ebay_says_stop(a_store):
    """The finding. It used to keep going: 200 listings meant ~195 more calls
    into a windowed limit, which does not merely fail — it holds the window
    open and makes the wait longer."""
    result, asked = a_store(total=200, ok_before_limit=5)

    assert len(asked) < 200, \
        f"kept calling eBay after it refused ({len(asked)} calls)"
    assert result["rate_limited"] is True


def test_listings_ebay_never_looked_at_are_not_reported_as_failures(a_store):
    """"5 failed" reads as "eBay rejected these listings". It never saw them."""
    result, _ = a_store(total=200, ok_before_limit=5)

    assert result["failed"] == 0, \
        "listings skipped for a rate limit were counted as eBay rejections"
    assert result["imported"] == 5


def test_the_wait_is_passed_on_so_something_can_use_it(a_store):
    result, _ = a_store(total=50, ok_before_limit=2)
    assert result["retry_after"] == 30


def test_a_sync_that_never_hits_the_limit_says_so(a_store):
    result, asked = a_store(total=8, ok_before_limit=99)

    assert result["rate_limited"] is False
    assert result["retry_after"] is None
    assert len(asked) == 8
    assert result["imported"] == 8
