"""Auto-promote bills at the rate the seller typed, or at eBay's suggestion.

Settings used to offer auto-promote as a bare on/off, and the rate it ran at
was whatever eBay suggested for each listing -- a number no screen showed.
It now takes an optional ad rate beside the switch. The contract:

  * a typed rate is used as given, for every auto-promoted listing, and eBay
    is never asked to suggest one;
  * a blank rate keeps the old behaviour: eBay's suggestion, and no
    promotion where eBay has none (test_ad_rate_is_never_invented covers the
    "none" half);
  * the per-listing Promote toggle keeps its own slider rate -- the account
    rate is for listings the seller never opened;
  * the switch and the rate are read together, and an outage reads as off.
"""
from __future__ import annotations

from backend.marketplaces import ebay_provider
from backend.models import Listing


def test_a_typed_rate_is_used_as_given(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": 1, "auto_promote_rate": 4.5})
    asked = []
    monkeypatch.setattr(ebay_provider.promotions, "suggested_ad_rates",
                        lambda *_a, **_k: asked.append(1) or {})
    monkeypatch.setattr(ebay_provider.promotions, "promote_listing",
                        lambda _rid, listing, _creds: {"promoted": True,
                                                       "ad_rate": listing.ad_rate_percent})
    on, rate = ebay_provider.auto_promote_settings("u1")
    assert (on, rate) == (True, 4.5)

    listing = Listing(title="A lamp", price=40.0)
    out = ebay_provider.promote("rec", listing, {"access_token": "t"},
                                rate=rate, ebay_listing_id="110",
                                chosen_by_seller=False)
    assert out["promoted"] is True and out["ad_rate"] == 4.5
    assert listing.ad_rate_percent == 4.5
    assert asked == [], "eBay was asked for a suggestion the seller had overridden"


def test_a_blank_rate_still_asks_ebay(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": 1})
    assert ebay_provider.auto_promote_settings("u1") == (True, None)
    monkeypatch.setattr(ebay_provider.promotions, "suggested_ad_rates",
                        lambda _creds, ids: {ids[0]: 3.1})
    monkeypatch.setattr(ebay_provider.promotions, "promote_listing",
                        lambda _rid, listing, _creds: {"promoted": True,
                                                       "ad_rate": listing.ad_rate_percent})
    listing = Listing(title="A lamp", price=40.0)
    out = ebay_provider.promote("rec", listing, {"access_token": "t"},
                                rate=None, ebay_listing_id="110",
                                chosen_by_seller=False)
    assert out["ad_rate"] == 3.1


def test_a_zero_or_garbage_rate_reads_as_blank(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": 1, "auto_promote_rate": 0})
    assert ebay_provider.auto_promote_settings("u1") == (True, None)
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": 1, "auto_promote_rate": "x"})
    assert ebay_provider.auto_promote_settings("u1") == (True, None)


def test_an_outage_is_off_with_no_rate(monkeypatch):
    def _boom(_uid):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(ebay_provider.db, "get_prefs", _boom)
    assert ebay_provider.auto_promote_settings("u1") == (False, None)
    assert ebay_provider.auto_promote_settings(None) == (False, None)


def test_the_switch_alone_still_answers_the_old_question(monkeypatch):
    """auto_promote_enabled is what the rest of the app reads; it must keep
    answering the switch and nothing but the switch."""
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": 0, "auto_promote_rate": 9})
    assert ebay_provider.auto_promote_enabled("u1") is False
