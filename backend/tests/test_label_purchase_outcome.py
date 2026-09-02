"""A label you may already have bought is not a label you failed to buy.

`purchase_label` spends the seller's money: eBay generates the label, charges
for the postage, and uploads the tracking to the order. `mark_shipped` is
irreversible in a different way -- eBay records the fulfillment and emails the
buyer the tracking.

Both collapsed every ending into one message. A read timeout, a reset
connection or a 5xx from in front of eBay came back as "Couldn't reach eBay",
which reads as "nothing happened" -- so the seller buys the label again and
pays for two, or files a second fulfillment against one order.

Same rule and same asymmetry as the Trading client: only a connection that was
never established proves nothing was sent, and everything else -- including an
exception type nobody anticipated -- is unknown. Reads are exempt: a GetOrder
that times out changed nothing.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import ebay_orders


class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture()
def transport(monkeypatch):
    """Make the next eBay call end however the test says."""
    def _serve(outcome, method="post"):
        def _go(*_a, **_k):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(ebay_orders.httpx, method, _go)
    return _serve


def _buy(transport, outcome):
    transport(outcome)
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.purchase_label("tok", "quote-1", "rate-1")
    return caught.value


# ------------------------------------------------ the money-bearing call

@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out waiting for the response"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.WriteTimeout("timed out sending the request"),
])
def test_a_lost_answer_to_a_purchase_is_unknown(transport, failure):
    assert isinstance(_buy(transport, failure), ebay_orders.UnknownOutcome)


def test_a_server_error_on_a_purchase_is_unknown(transport):
    assert isinstance(_buy(transport, _Resp(status=502, text="bad gateway")),
                      ebay_orders.UnknownOutcome)


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
    httpx.PoolTimeout("no connection available"),
])
def test_a_purchase_that_never_left_is_a_plain_failure(transport, failure):
    """No connection, no charge. Sending the seller to check their eBay
    postage every time their wifi drops before the request goes out would
    train them to ignore the warning that matters."""
    assert not isinstance(_buy(transport, failure), ebay_orders.UnknownOutcome)


def test_an_unrecognised_failure_on_a_purchase_is_unknown(transport):
    assert isinstance(_buy(transport, RuntimeError("something new")),
                      ebay_orders.UnknownOutcome)


def test_ebays_own_refusal_stays_definitive(transport):
    """A 400 is eBay declining the request, not a lost answer."""
    assert not isinstance(_buy(transport, _Resp(status=400, text="bad rate")),
                          ebay_orders.UnknownOutcome)


def test_the_purchase_message_does_not_imply_nothing_happened(transport):
    said = str(_buy(transport, httpx.ReadTimeout("timed out"))).lower()
    assert "couldn't reach ebay" not in said
    assert "check" in said
    # Name the thing to check. "Something went wrong" sends them nowhere.
    assert "label" in said or "postage" in said


# ------------------------------------------------- and the fulfillment

def test_a_lost_answer_to_mark_shipped_is_unknown(transport):
    """eBay may have recorded the fulfillment and emailed the buyer. Filing a
    second one against the same order is its own mess."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.mark_shipped("tok", "order-1", "1Z999", "USPS",
                                 line_items=[{"lineItemId": "1", "quantity": 1}])
    assert isinstance(caught.value, ebay_orders.UnknownOutcome)


# --------------------------------------------------------- reads are exempt

def test_a_lost_read_is_not_an_unknown_outcome(transport):
    """Downloading a label changes nothing on eBay."""
    transport(httpx.ReadTimeout("timed out"), method="get")
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.download_label("tok", "ship-1")
    assert not isinstance(caught.value, ebay_orders.UnknownOutcome)


def test_a_lost_quote_is_not_an_unknown_outcome(transport):
    """A shipping quote costs nothing and reserves nothing. Asking again is
    free, so there is no outcome to be in doubt about."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.create_shipping_quote(
            "tok", {"order_id": "order-1",
                    "ship_to": {"address1": "1 Main St", "postal_code": "94103"}},
            {"weight_lb": 1}, {"postal_code": "94103"})
    assert not isinstance(caught.value, ebay_orders.UnknownOutcome)
