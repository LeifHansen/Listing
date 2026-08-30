""""eBay said no" and "we never heard back" are not the same answer.

`_call` collapsed three different endings into one bare TradingError:

  * eBay answered with Ack=Failure and reasons -- a definitive NO. Nothing was
    created, changed or ended.
  * the transport failed before a single byte reached eBay (DNS, connection
    refused, connect timeout) -- also a definitive no, for a different reason.
  * the request went out and the answer never came back (read timeout, reset
    connection, a 5xx from in front of eBay) -- and on a WRITE that is not a
    no. eBay may well have created the listing.

That third case is the one that costs a seller something. A publish whose
response is lost leaves a live eBay listing with nothing in the app pointing
at it: the record stays a draft (or, on a relist, goes back to Inactive), the
seller is told it failed, and the next store sync imports the very listing
this app made as a SECOND card -- the duplicate pair publish_guard exists to
prevent, reached from the other end.

It also can't be recovered by asking the seller to retry, because the app has
already told them the publish failed. Someone who believes that does not
retry.

So the transport says which of the three happened, and only a WRITE can be
unknown: a GetItem that times out changed nothing, and telling a seller to go
check eBay after a failed read is a false alarm. The two Verify calls are
reads too -- VerifyAddFixedPriceItem is eBay's dry run and creates nothing,
which is the whole reason it exists.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import ebay_trading


def _ok(call: str, extra: str = "") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{call}Response xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack>{extra}"
        f"</{call}Response>").encode()


def _rejected(call: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{call}Response xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Failure</Ack>"
        "<Errors><SeverityCode>Error</SeverityCode>"
        "<ErrorCode>37</ErrorCode>"
        "<LongMessage>Input data is invalid.</LongMessage></Errors>"
        f"</{call}Response>").encode()


class _Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}


@pytest.fixture()
def transport(monkeypatch):
    """Make the next Trading call end however the test says."""
    def _serve(outcome):
        def _post(*_a, **_k):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(ebay_trading.httpx, "post", _post)
    return _serve


def _end(transport, outcome):
    """Run a WRITE (EndItem) and hand back whatever it raised."""
    transport(outcome)
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.end_listing("tok", "110")
    return caught.value


# ---------------------------------------------------- the unknown endings

@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out waiting for the response"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.WriteTimeout("timed out sending the request"),
])
def test_a_lost_response_on_a_write_is_unknown_not_failed(transport, failure):
    """The request was on the wire. eBay may have acted on it."""
    assert isinstance(_end(transport, failure), ebay_trading.UnknownOutcome)


def test_a_server_error_on_a_write_is_unknown(transport):
    """A 500 comes from something that already had the request in hand."""
    assert isinstance(_end(transport, _Resp(status=500)),
                      ebay_trading.UnknownOutcome)


def test_an_unreadable_answer_to_a_write_is_unknown(transport):
    """eBay answered 200 and we could not parse it. That is our failure to
    read an outcome, not evidence there wasn't one."""
    assert isinstance(_end(transport, _Resp(content=b"<not xml")),
                      ebay_trading.UnknownOutcome)


def test_an_unrecognised_transport_failure_is_unknown(transport):
    """The default has to be unknown. A new httpx error class, or one nobody
    thought about, must not arrive claiming nothing was sent."""
    assert isinstance(_end(transport, RuntimeError("something new")),
                      ebay_trading.UnknownOutcome)


# ------------------------------------------------- the definitive endings

@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
    httpx.PoolTimeout("no connection available"),
])
def test_a_request_that_never_left_is_a_plain_failure(transport, failure):
    """No bytes reached eBay, so nothing there changed. Telling the seller to
    go and check would be a false alarm on the app's most-used path."""
    assert not isinstance(_end(transport, failure), ebay_trading.UnknownOutcome)


def test_ebays_own_rejection_stays_definitive(transport):
    """Ack=Failure with reasons IS eBay's answer. It must keep mapping to the
    fix-it issues rather than becoming 'we don't know'."""
    assert not isinstance(_end(transport, _Resp(content=_rejected("EndItem"))),
                          ebay_trading.UnknownOutcome)


def test_a_rejected_authorization_stays_definitive(transport):
    """A 4xx is refused at the gate, before eBay processes the request."""
    assert not isinstance(_end(transport, _Resp(status=401)),
                          ebay_trading.UnknownOutcome)


# ------------------------------------------------ only writes are unknown

def test_a_lost_read_is_never_an_unknown_outcome(transport):
    """A GetItem that times out changed nothing on eBay. Reporting it as an
    unknown outcome would send sellers to check a listing nobody touched --
    and the sync runs thousands of these."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.get_listing("tok", "110")
    assert not isinstance(caught.value, ebay_trading.UnknownOutcome)


def test_a_lost_verify_is_never_an_unknown_outcome(transport, monkeypatch):
    """VerifyAddFixedPriceItem is eBay's dry run -- it creates nothing, which
    is the entire reason the app calls it. A lost one has no outcome to be
    unknown about."""
    monkeypatch.setattr(ebay_trading, "build_add_item",
                        lambda *a, **k: ("AddFixedPriceItem", "<Item/>"))
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.verify_listing("tok", object(), [])
    assert not isinstance(caught.value, ebay_trading.UnknownOutcome)


# ------------------------------------------- what the seller is told

def test_the_message_says_the_outcome_is_unknown(transport):
    said = str(_end(transport, httpx.ReadTimeout("timed out"))).lower()
    # No "nothing was changed" here -- that is the one thing we cannot say.
    assert "nothing was changed" not in said
    assert "check" in said, "the seller has to be told to look before retrying"


def test_a_rate_limit_is_still_a_rate_limit(transport):
    """RateLimited is a definite refusal with a retry-after, and it is
    recognised BEFORE the unknown-outcome rule -- a 429 means eBay declined to
    process the request, not that it might have."""
    transport(_Resp(status=429, headers={"Retry-After": "5"}))
    with pytest.raises(ebay_trading.RateLimited) as caught:
        ebay_trading.end_listing("tok", "110")
    assert not isinstance(caught.value, ebay_trading.UnknownOutcome)
    assert caught.value.retry_after == 5


# ------------------------------------------- the publish that may have landed
#
# item_id_for_sku's own docstring says what it is for: "After a publish whose
# response never arrived, this answers 'did that listing actually go up, and
# what is it?'". It was only ever called from the arm that handles eBay's
# explicit "you already sent this" rejection -- the one case where eBay ANSWERS
# -- and never from the case it was written for. So the lookup that could
# settle a lost publish existed, worked, and was not reached.

def _draft():
    from backend.models import Listing
    return Listing(title="A thing", price=10.0, quantity=1,
                   category_id="1234", condition="USED_GOOD")


@pytest.fixture()
def creating(monkeypatch):
    """listing_sync.create_on_ebay with everything but the create stubbed."""
    from backend.services import listing_sync, taxonomy

    monkeypatch.setattr(taxonomy, "sanitize_specifics", lambda *_a, **_k: None)
    monkeypatch.setattr(listing_sync, "publish_policies", lambda *_a, **_k: {})

    def _run(create, lookup):
        monkeypatch.setattr(listing_sync.ebay_trading, "create_listing", create)
        monkeypatch.setattr(listing_sync.ebay_trading, "item_id_for_sku", lookup)
        return listing_sync.create_on_ebay(
            "tok", _draft(), ["https://example.test/a.jpg"],
            creds={"ship_from_postal": "94103", "ebay_username": "seller"},
            idempotency_key="qf-abc")
    return _run


def _lost(*_a, **_k):
    raise ebay_trading.UnknownOutcome(
        "lost contact", call="AddFixedPriceItem")


def test_a_lost_publish_adopts_the_listing_it_actually_made(creating):
    """eBay took it; the answer went missing. The item is found by the key the
    publish travelled under and adopted, so the seller gets the listing they
    asked for instead of a draft beside a live listing nobody here knows
    about."""
    res = creating(_lost, lambda _t, sku: "556677" if sku == "qf-abc" else "")

    assert res["listing_id"] == "556677"
    assert res.get("already_listed") is True


def test_a_lookup_that_finds_nothing_stays_unknown(creating):
    """item_id_for_sku answers "" both when eBay has no such listing AND when
    the lookup itself failed -- and during the outage that lost the publish,
    the second is likely. Reading "" as "eBay never made it" would turn the
    one case that must not be retried blindly into a plain failure."""
    with pytest.raises(ebay_trading.UnknownOutcome):
        creating(_lost, lambda *_a, **_k: "")


def test_a_definite_rejection_is_not_sent_looking_for_a_listing(creating):
    """eBay said no. There is nothing to adopt, and a GetItem per rejected
    publish is a call spent to confirm what eBay already stated."""
    looked = []

    def _rejected(*_a, **_k):
        raise ebay_trading.TradingError("The price is invalid.")

    with pytest.raises(ebay_trading.TradingError) as caught:
        creating(_rejected, lambda t, sku: looked.append(sku) or "")
    assert not isinstance(caught.value, ebay_trading.UnknownOutcome)
    assert looked == []


# --------------------------------------------- and the headline says so

def test_the_issue_headline_does_not_claim_a_rejection():
    """The fix panel and the bulk cards render an issue's TITLE, and the short
    surfaces render only the title. `from_trading_error` sent every uncoded
    failure to the generic branch -- "eBay rejected the listing" -- so the one
    error that must not say that said it in the largest text on the screen,
    directly above a body saying the opposite.

    A seller reading "eBay rejected the listing" fixes something and publishes
    again. That is how the duplicate happens.
    """
    from backend import ebay_errors

    issue = ebay_errors.from_trading_error(
        ebay_trading.UnknownOutcome("lost contact",
                                    call="AddFixedPriceItem"))[0]

    assert "rejected" not in issue["title"].lower()
    assert issue["target"] == "generic", "there is no field to fix"
    said = (issue["title"] + " " + issue["fix"]).lower()
    assert "check" in said


def test_a_coded_rejection_still_reads_as_a_rejection():
    """The generic branch is right for everything else, and must stay."""
    from backend import ebay_errors

    issue = ebay_errors.from_trading_error(
        ebay_trading.TradingError("Input data is invalid.", code="37"))[0]
    assert issue["title"] == "eBay rejected the listing"


# ------------------------------------- and a request that never left, either
#
# The other half of the same rule. "eBay rejected the listing" is a claim
# about something eBay did, and on a connection that was never made it did
# nothing. That title is what the short surfaces render, so it sends the
# seller hunting through fields when the problem is the network.

def test_an_unreachable_failure_is_not_titled_a_rejection(transport):
    from backend import ebay_errors

    err = _end(transport, httpx.ConnectError("connection refused"))
    assert not isinstance(err, ebay_trading.UnknownOutcome)

    issue = ebay_errors.from_trading_error(err)[0]
    assert "rejected" not in issue["title"].lower()
    assert "reach" in issue["title"].lower()
    # It may say the one thing the unknown case may not.
    assert "nothing was sent" in issue["fix"].lower()


def test_a_read_that_could_not_reach_ebay_says_the_same(transport):
    """A read has no outcome to be unknown about, but "rejected" is just as
    wrong for it."""
    from backend import ebay_errors

    transport(httpx.ConnectError("connection refused"))
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.get_listing("tok", "110")
    issue = ebay_errors.from_trading_error(caught.value)[0]
    assert "rejected" not in issue["title"].lower()


def test_ebays_coded_rejection_is_untouched_by_that(transport):
    from backend import ebay_errors

    issue = ebay_errors.from_trading_error(
        ebay_trading.TradingError("Input data is invalid.", code="37"))[0]
    assert issue["title"] == "eBay rejected the listing"
