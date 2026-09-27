"""When eBay fails to create the promotion campaign, the seller reads a
sentence, and the next publishes do not ask again straight away.

Production's feed carried "promote failed ... campaign create failed (500):
{"errors":[{"errorId":35001,"domain":"API_MARKETING",...". 35001 is eBay's
generic internal error: nothing about the listing or the account can fix it.
Every promoted publish after one asked again — a GET and a POST, a warning row
each (x13), the same failure once per listing in a bulk run — and the seller
was shown "Couldn't start the promotion: campaign create failed (500):
{"errors":[...]}", eBay's JSON envelope verbatim.
"""
from __future__ import annotations

import httpx
import pytest

from backend.models import Listing
from backend.services import promotions

BODY_35001 = ('{"errors":[{"errorId":35001,"domain":"API_MARKETING",'
              '"category":"APPLICATION","message":"There was a problem with an '
              'eBay internal system or process."}]}')


@pytest.fixture(autouse=True)
def _clean():
    promotions._CAMPAIGN_CACHE.clear()
    promotions._CREATE_FAILED.clear()
    yield
    promotions._CAMPAIGN_CACHE.clear()
    promotions._CREATE_FAILED.clear()


class _Client:
    """httpx.Client stand-in: no campaign exists, and creating one 500s."""
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        _Client.calls.append(("GET", url))
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"campaigns": []})

    def post(self, url, **kw):
        _Client.calls.append(("POST", url))
        return httpx.Response(500, request=httpx.Request("POST", url),
                              text=BODY_35001)


def _promote(creds=None):
    listing = Listing(title="Denim jacket", promote=True, ad_rate_percent=5.0,
                      ebay_listing_id="110001", source="ebay")
    return promotions.promote_listing(
        "s1", listing, creds or {"access_token": "tok-aaaaaaaaaaaa", "_uid": "u1"})


def test_the_seller_reads_a_sentence_not_ebays_json(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    out = _promote()
    assert out["promoted"] is False
    assert "{" not in out["message"] and "35001" not in out["message"]
    assert "eBay's side" in out["message"]


def test_the_next_publishes_do_not_ask_again_straight_away(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _promote()
    asked = len(_Client.calls)
    for _ in range(5):
        out = _promote()
        assert out["promoted"] is False
    assert len(_Client.calls) == asked, "a failing create was asked again"


def test_the_pause_ends(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _promote()
    asked = len(_Client.calls)
    promotions._CREATE_FAILED["EBAY_US:u1"] -= promotions._CREATE_BACKOFF + 1
    _promote()
    assert len(_Client.calls) > asked


def test_another_sellers_promotions_are_not_paused(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _promote({"access_token": "tok-aaaaaaaaaaaa", "_uid": "u1"})
    asked = len(_Client.calls)
    _promote({"access_token": "tok-bbbbbbbbbbbb", "_uid": "u2"})
    assert len(_Client.calls) > asked
