"""A draft Etsy may already hold is not a draft Etsy refused.

The Etsy provider is careful about this once a listing exists: its failure
path deliberately keeps `listing_id` so a photo upload or an activate call
that fails cannot orphan the listing and have the retry mint a second one. The
comment there says exactly that, and names httpx.ReadTimeout.

The one gap it cannot cover is the CREATE itself. If that request's answer
goes missing there is no id to keep, the retry creates a second draft on the
seller's shop, and — worse — the seller is told "Etsy rejected the listing",
which is the headline the fix panel and the bulk cards render. Someone who
reads "rejected" edits a field and publishes again.

Same rule as the Trading and orders clients: only a connection that was never
established proves nothing was sent; everything else is unknown. Reads are
exempt.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import etsy


class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture()
def transport(monkeypatch):
    def _serve(outcome, method="post"):
        def _go(*_a, **_k):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(etsy.httpx, method, _go)
    return _serve


def _create(transport, outcome):
    transport(outcome)
    with pytest.raises(etsy.EtsyError) as caught:
        etsy.create_draft_listing("tok", "shop-1", {"title": "A thing"})
    return caught.value


# ------------------------------------------------------ unknown endings

@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out waiting for the response"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
])
def test_a_lost_answer_to_a_create_is_unknown(transport, failure):
    assert isinstance(_create(transport, failure), etsy.UnknownOutcome)


def test_a_server_error_on_a_create_is_unknown(transport):
    assert isinstance(_create(transport, _Resp(status=503, text="busy")),
                      etsy.UnknownOutcome)


def test_an_unrecognised_failure_on_a_create_is_unknown(transport):
    assert isinstance(_create(transport, RuntimeError("something new")),
                      etsy.UnknownOutcome)


# --------------------------------------------------- definitive endings

@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
    httpx.PoolTimeout("no connection available"),
])
def test_a_create_that_never_left_is_a_plain_failure(transport, failure):
    assert not isinstance(_create(transport, failure), etsy.UnknownOutcome)


def test_etsys_own_rejection_stays_definitive(transport):
    """Etsy answered. Its words are what the seller needs, and the title has
    to keep saying so."""
    err = _create(transport, _Resp(status=400,
                                   payload={"error": "Title is too long."}))
    assert not isinstance(err, etsy.UnknownOutcome)
    assert err.issues[0]["title"] == "Etsy rejected the listing"
    assert "Title is too long." in err.issues[0]["fix"]


# ------------------------------------------------------------ the words

def test_the_headline_does_not_claim_a_rejection(transport):
    err = _create(transport, httpx.ReadTimeout("timed out"))
    issue = err.issues[0]
    assert "rejected" not in issue["title"].lower()
    said = (issue["title"] + " " + issue["fix"]).lower()
    assert "check" in said
    assert "draft" in said, "say where to look"


# ------------------------------------------------------- reads are exempt

def test_a_lost_read_is_not_an_unknown_outcome(transport):
    transport(httpx.ReadTimeout("timed out"), method="get")
    with pytest.raises(etsy.EtsyError) as caught:
        etsy.get_listing("tok", "123")
    assert not isinstance(caught.value, etsy.UnknownOutcome)
