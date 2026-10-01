"""Best Offer limits reach eBay as the seller's own numbers, and only then.

"Allow offers" used to be the whole feature: every offer reached the seller,
because the two prices eBay auto-accepts at and auto-declines below were
never sent. Settings now lets the seller set each as a percentage of the
asking price, and this file follows those percentages from the preference
row to the XML -- and pins the four ways they must NOT appear:

  * never on an auction (eBay has no Best Offer there at all);
  * never when the limit is unset, 0 or out of range -- "no minimum" stays
    the default, exactly as before the limits existed;
  * never as a floor at or above the accept price, which eBay refuses, and
    which would otherwise fail the whole publish on a pair the prefs route
    should already have turned away;
  * never from an unreadable preference -- a database blip is not a choice.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading, listing_sync

POLICIES = {"fulfillment_policy_id": "f1", "payment_policy_id": "p1",
            "return_policy_id": "r1"}


@pytest.fixture
def listing():
    return Listing(title="A brass desk lamp", price=50.0, category_id="20697",
                   description="Works.", quantity=1)


def _xml(listing, **kw):
    _call, body = ebay_trading.build_add_item(listing, ["https://x/1.jpg"],
                                              POLICIES, "97201", **kw)
    return body


def test_the_limits_become_amounts_off_this_listings_price(listing):
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 90,
                                  "auto_decline_pct": 60})
    assert ('<ListingDetails><BestOfferAutoAcceptPrice currencyID="USD">45.00'
            "</BestOfferAutoAcceptPrice>"
            '<MinimumBestOfferPrice currencyID="USD">30.00'
            "</MinimumBestOfferPrice></ListingDetails>") in body
    # And the switch itself still rides along -- the limits qualify it.
    assert "<BestOfferEnabled>true</BestOfferEnabled>" in body


def test_one_limit_alone_is_sent_alone(listing):
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 0,
                                  "auto_decline_pct": 50})
    assert "BestOfferAutoAcceptPrice" not in body
    assert '<MinimumBestOfferPrice currencyID="USD">25.00' in body


def test_no_limits_means_no_minimum_exactly_as_before(listing):
    for terms in (None, {}, {"enabled": True, "auto_accept_pct": 0,
                             "auto_decline_pct": 0}):
        body = _xml(listing, best_offer=True, best_offer_terms=terms)
        assert "<BestOfferEnabled>true</BestOfferEnabled>" in body
        assert "ListingDetails" not in body, terms
        assert "MinimumBestOfferPrice" not in body, terms


def test_an_auction_never_carries_limits(listing):
    listing.listing_format = "AUCTION"
    listing.auction_start_price = 9.99
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 90,
                                  "auto_decline_pct": 60})
    assert "BestOffer" not in body
    assert "ListingDetails" not in body


def test_a_floor_at_or_above_the_accept_price_is_dropped_not_sent(listing):
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 60,
                                  "auto_decline_pct": 90})
    assert '<BestOfferAutoAcceptPrice currencyID="USD">30.00' in body
    assert "MinimumBestOfferPrice" not in body


def test_a_limit_at_or_above_the_full_price_is_not_sent(listing):
    """100% of the price is the price; eBay refuses a limit there, and a
    seller who typed it meant 'take the asking price', which the listing
    already does."""
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 100,
                                  "auto_decline_pct": 0})
    assert "ListingDetails" not in body


def test_a_listing_with_no_price_gets_no_limits(listing):
    listing.price = 0
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 90,
                                  "auto_decline_pct": 60})
    assert "ListingDetails" not in body


def test_the_limits_use_the_listings_own_currency(listing):
    listing.currency = "CAD"
    body = _xml(listing, best_offer=True,
                best_offer_terms={"enabled": True, "auto_accept_pct": 90,
                                  "auto_decline_pct": 0})
    assert 'currencyID="CAD">45.00' in body


# ------------------------------------------------- the preference, read once

def test_the_terms_are_read_off_the_sellers_preferences(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1,
                                      "best_offer_auto_accept_pct": 90,
                                      "best_offer_auto_decline_pct": 60})
    assert listing_sync.offers_terms("u1") == {
        "enabled": True, "auto_accept_pct": 90.0, "auto_decline_pct": 60.0}


def test_an_out_of_range_or_garbage_limit_reads_as_unset(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1,
                                      "best_offer_auto_accept_pct": 140,
                                      "best_offer_auto_decline_pct": "lots"})
    terms = listing_sync.offers_terms("u1")
    assert terms["enabled"] is True
    assert terms["auto_accept_pct"] == 0.0
    assert terms["auto_decline_pct"] == 0.0


def test_an_unreadable_preference_sets_no_limits_and_no_offers(monkeypatch):
    def _boom(_uid):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(listing_sync.db, "get_prefs", _boom)
    assert listing_sync.offers_terms("u1") == {
        "enabled": False, "auto_accept_pct": 0.0, "auto_decline_pct": 0.0}
    assert listing_sync.offers_terms(None)["enabled"] is False
    assert listing_sync.publish_best_offer_terms({})["enabled"] is False


def test_the_publish_forwards_the_same_terms_it_switched_on(monkeypatch,
                                                            listing):
    """create_on_ebay reads the switch and the limits in one go and hands
    both to the Trading call -- a publish must not carry Best Offer from one
    read and the limits from another."""
    sent = {}

    class _Trading:
        AlreadyListedError = ebay_trading.AlreadyListedError
        TradingError = ebay_trading.TradingError
        UnknownOutcome = ebay_trading.UnknownOutcome

        def create_listing(self, *_a, **kw):
            sent.update(kw)
            return {"published": True, "listing_id": "110040602158",
                    "view_url": "https://www.ebay.com/itm/110040602158"}

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1,
                                      "best_offer_auto_accept_pct": 90,
                                      "best_offer_auto_decline_pct": 60})
    listing_sync.create_on_ebay(
        "tok", listing, ["https://x/1.jpg"],
        creds={"access_token": "tok", "ship_from_postal": "97201",
               "_uid": "u1"})
    assert sent["best_offer"] is True
    assert sent["best_offer_terms"]["auto_accept_pct"] == 90.0
    assert sent["best_offer_terms"]["auto_decline_pct"] == 60.0


def test_the_probe_carries_the_limits_the_publish_would(monkeypatch, listing):
    seen = []

    class _Trading:
        def verify_listing(self, _token, _candidate, _urls, **kw):
            seen.append(kw)

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1,
                                      "best_offer_auto_accept_pct": 80})
    verify = listing_sync.verifier(
        "tok", ["https://x/1.jpg"],
        creds={"ship_from_postal": "97201", "_uid": "u1"})
    verify(listing)
    assert seen[0]["best_offer"] is True
    assert seen[0]["best_offer_terms"]["auto_accept_pct"] == 80.0


def test_the_dry_run_preview_shows_the_limits_too(monkeypatch, listing,
                                                  tmp_path):
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext

    monkeypatch.setattr(ebay_provider.ebay, "image_urls_for", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda _uid: None)
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}.json")
    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "sandbox")
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1,
                                      "best_offer_auto_accept_pct": 90})

    out = ebay_provider.EbayProvider()._dry_run(PublishContext(
        session_id="s1", listing=listing, mode="live",
        base_url="https://example.test", uid="u1", prev_record={}))
    assert "<BestOfferAutoAcceptPrice" in out.raw["payload"]["xml"]
