"""A fulfillment you may already have filed is not a fulfillment that failed.

`mark_shipped` is irreversible: eBay records the fulfillment and emails the
buyer the tracking. It used to collapse every ending into one message -- a
read timeout, a reset connection or a 5xx from in front of eBay came back as
"Couldn't reach eBay", which reads as "nothing happened" -- so the seller
filed a second fulfillment against one order.

Same rule and same asymmetry as the Trading client: only a connection that was
never established proves nothing was sent, and everything else -- including an
exception type nobody anticipated -- is unknown. Reads are exempt: a GetOrder
that times out changed nothing.

(The label purchase that used to share this file moved to the seller's own
EasyPost account; its outcome suite is test_a_label_you_may_have_bought_on_
easypost_is_not_a_label_you_failed_to_buy.py.)
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


def _mark(transport, outcome):
    transport(outcome)
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.mark_shipped("tok", "order-1", "1Z999", "USPS",
                                 line_items=[{"lineItemId": "1", "quantity": 1}])
    return caught.value


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out waiting for the response"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.WriteTimeout("timed out sending the request"),
])
def test_a_lost_answer_to_mark_shipped_is_unknown(transport, failure):
    """eBay may have recorded the fulfillment and emailed the buyer. Filing a
    second one against the same order is its own mess."""
    assert isinstance(_mark(transport, failure), ebay_orders.UnknownOutcome)


def test_a_server_error_on_mark_shipped_is_unknown(transport):
    assert isinstance(_mark(transport, _Resp(status=502, text="bad gateway")),
                      ebay_orders.UnknownOutcome)


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
    httpx.PoolTimeout("no connection available"),
])
def test_a_fulfillment_that_never_left_is_a_plain_failure(transport, failure):
    assert not isinstance(_mark(transport, failure), ebay_orders.UnknownOutcome)


def test_ebays_own_refusal_stays_definitive(transport):
    """A 400 is eBay declining the request, not a lost answer."""
    assert not isinstance(_mark(transport, _Resp(status=400, text="bad tracking")),
                          ebay_orders.UnknownOutcome)


def test_the_fulfillment_message_does_not_imply_nothing_happened(transport):
    said = str(_mark(transport, httpx.ReadTimeout("timed out"))).lower()
    assert "couldn't reach ebay" not in said
    assert "check" in said


def test_the_carrier_is_sent_as_ebay_spells_it(monkeypatch):
    """eBay links the tracking number to the carrier's site by the exact
    string. FedEx upper-cased to FEDEX is a carrier eBay does not know."""
    sent = {}

    def _post(url, headers=None, json=None, timeout=None):
        sent.update(json or {})
        return _Resp(status=200)
    monkeypatch.setattr(ebay_orders.httpx, "post", _post)
    ebay_orders.mark_shipped("tok", "order-1", "7489", "FedEx",
                             line_items=[{"lineItemId": "1", "quantity": 1}])
    assert sent["shippingCarrierCode"] == "FedEx"


# --------------------------------------------------------- reads are exempt

def test_a_lost_read_is_not_an_unknown_outcome(transport):
    """Reading an order changes nothing on eBay."""
    transport(httpx.ReadTimeout("timed out"), method="get")
    with pytest.raises(ebay_orders.OrdersError) as caught:
        ebay_orders.get_order("tok", "order-1")
    assert not isinstance(caught.value, ebay_orders.UnknownOutcome)
