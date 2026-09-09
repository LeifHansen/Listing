"""A label you may already have bought is not a label you failed to buy.

`easypost.buy_label` spends the seller's money: EasyPost charges their wallet
and hands back the label. A read timeout, a reset connection or a 5xx from in
front of EasyPost must not come back as "Couldn't reach EasyPost", which reads
as "nothing happened" -- that is how a seller buys the label again and pays
for two.

Same rule and same asymmetry as the eBay clients: only a connection that was
never established proves nothing was sent, and everything else -- including an
exception type nobody anticipated -- is unknown. The reads and the quote are
exempt: asking again is free.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import easypost

ORDER = {"order_id": "12-345", "ship_to": {
    "name": "Jordan Buyer", "address1": "1 Main St", "city": "Columbus",
    "state": "OH", "postal_code": "43004", "country": "US"}}
SHIP_FROM = {"name": "Seller", "address1": "9 Elm St", "city": "Austin",
             "state": "TX", "postal_code": "78701", "country": "US"}


class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture()
def transport(monkeypatch):
    """Make the next EasyPost call end however the test says."""
    def _serve(outcome, method="post"):
        def _go(*_a, **_k):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(easypost.httpx, method, _go)
    return _serve


def _buy(transport, outcome):
    transport(outcome)
    with pytest.raises(easypost.EasyPostError) as caught:
        easypost.buy_label("EZTKkey", "shp_1", "rate_1")
    return caught.value


# ------------------------------------------------ the money-bearing call

@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out waiting for the response"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
    httpx.WriteTimeout("timed out sending the request"),
])
def test_a_lost_answer_to_a_purchase_is_unknown(transport, failure):
    assert isinstance(_buy(transport, failure), easypost.UnknownOutcome)


def test_a_server_error_on_a_purchase_is_unknown(transport):
    assert isinstance(_buy(transport, _Resp(status=502, text="bad gateway")),
                      easypost.UnknownOutcome)


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
    httpx.PoolTimeout("no connection available"),
])
def test_a_purchase_that_never_left_is_a_plain_failure(transport, failure):
    """No connection, no charge. Sending the seller to check their wallet
    every time their wifi drops before the request goes out would train them
    to ignore the warning that matters."""
    err = _buy(transport, failure)
    assert not isinstance(err, easypost.UnknownOutcome)
    assert "couldn't reach easypost" in str(err).lower()


def test_an_unrecognised_failure_on_a_purchase_is_unknown(transport):
    assert isinstance(_buy(transport, RuntimeError("something new")),
                      easypost.UnknownOutcome)


def test_easyposts_own_refusal_stays_definitive(transport):
    """A 4xx is EasyPost declining the request, not a lost answer -- and it
    carries EasyPost's reason, not a status code."""
    err = _buy(transport, _Resp(status=422, payload={"error": {
        "code": "SHIPMENT.POSTAGE.FAILURE",
        "message": "Unable to buy: insufficient funds",
        "errors": [{"field": "rate", "message": "no longer available"}]}}))
    assert not isinstance(err, easypost.UnknownOutcome)
    assert "insufficient funds" in str(err)
    assert "rate: no longer available" in str(err)


def test_a_bad_key_says_to_reconnect_and_never_echoes_it(transport):
    err = _buy(transport, _Resp(status=401, payload={"error": {
        "code": "UNAUTHORIZED", "message": "Invalid API key EZTKkey"}}))
    assert "reconnect it in Settings" in str(err)
    assert "EZTKkey" not in str(err)


def test_the_purchase_message_does_not_imply_nothing_happened(transport):
    said = str(_buy(transport, httpx.ReadTimeout("timed out"))).lower()
    assert "couldn't reach easypost" not in said
    # Name the thing to do: reopen the order, which is where the reconcile
    # lives. "Something went wrong" sends them nowhere.
    assert "reopen" in said and "before buying again" in said


def test_the_module_answers_the_question_like_the_other_clients():
    """So a caller can ask ANY EasyPost failure whether money may have
    moved, without importing this module's classes."""
    assert easypost.UnknownOutcome.outcome_unknown is True
    assert easypost.EasyPostError.outcome_unknown is False
    assert issubclass(easypost.UnknownOutcome, easypost.EasyPostError)


# --------------------------------------------------------- reads are exempt

def test_a_lost_quote_is_not_an_unknown_outcome(transport):
    """A shipment costs nothing and reserves nothing until it is bought."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(easypost.EasyPostError) as caught:
        easypost.create_shipment("EZTKkey", ORDER, {"weight_lb": 1}, SHIP_FROM)
    assert not isinstance(caught.value, easypost.UnknownOutcome)


def test_a_lost_refund_is_not_an_unknown_outcome(transport):
    """Money comes back, never away; EasyPost refuses a duplicate request."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(easypost.EasyPostError) as caught:
        easypost.refund_label("EZTKkey", "shp_1")
    assert not isinstance(caught.value, easypost.UnknownOutcome)


@pytest.mark.parametrize("call", [
    lambda: easypost.retrieve_shipment("EZTKkey", "shp_1"),
    lambda: easypost.verify_key("EZTKkey"),
])
def test_a_lost_read_is_not_an_unknown_outcome(transport, call):
    transport(httpx.ReadTimeout("timed out"), method="get")
    with pytest.raises(easypost.EasyPostError) as caught:
        call()
    assert not isinstance(caught.value, easypost.UnknownOutcome)


def test_a_purchase_reads_back_what_the_dialog_needs(transport):
    transport(_Resp(status=200, payload={
        "id": "shp_1", "tracking_code": "9400111",
        "postage_label": {"label_url": "https://files/label.pdf"},
        "selected_rate": {"carrier": "USPS", "service": "GroundAdvantage",
                          "rate": "6.07", "currency": "USD"},
        "tracker": {"public_url": "https://track/x"},
    }))
    got = easypost.buy_label("EZTKkey", "shp_1", "rate_1")
    assert got["purchased"] is True
    assert got["tracking_number"] == "9400111"
    assert got["label_url"] == "https://files/label.pdf"
    assert (got["carrier"], got["service"], got["cost"]) == ("USPS", "GroundAdvantage", "6.07")
    assert got["tracker_url"] == "https://track/x"
    assert got["ebay_carrier"] == "USPS"


def test_a_shipment_without_a_label_reads_as_not_purchased(transport):
    """The reconcile relies on this: a lost answer whose shipment has no
    postage_label was NOT bought."""
    transport(_Resp(status=200, payload={"id": "shp_1", "rates": []}), method="get")
    assert easypost.retrieve_shipment("EZTKkey", "shp_1")["purchased"] is False
