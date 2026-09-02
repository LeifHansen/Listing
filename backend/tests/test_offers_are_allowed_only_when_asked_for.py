"""The "Allow offers" switch, and the two ways a switch like this goes wrong.

Turning it on has to actually reach eBay — a settings screen whose toggle
changes nothing is worse than no toggle, because the seller believes offers
are on and prices as if buyers can negotiate. So the first half of this file
follows the flag from the preference row into the XML the publish sends.

The second half is the part that matters more. "Allow offers" changes what
the listing IS: the price stops being the price, and the seller signs up to
answer offers on it. That is a decision, so:

  * an ABSENT preference is not a yes. A seller who has never opened
    Settings has not asked for offers.
  * an UNREADABLE preference is not a yes either. db.get_prefs RAISES on a
    read failure, and a database blip must not put every listing published
    during it up for negotiation.
  * "no minimum" is the setting, so nothing may quietly acquire one.
    MinimumBestOfferPrice and BestOfferAutoAcceptPrice are eBay's
    auto-decline and auto-accept thresholds; picking either on the seller's
    behalf would bin a buyer, or sell the item, at a number they never named.
  * a REVISE is not a new listing. The switch says "new listings", and
    flipping it must not walk back through a live store opening hundreds of
    existing listings to offers.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading, listing_sync

POLICIES = {"fulfillment_policy_id": "f1", "payment_policy_id": "p1",
            "return_policy_id": "r1"}


@pytest.fixture
def listing():
    return Listing(title="A brass desk lamp", price=48.0, category_id="20697",
                   description="Works.", quantity=1)


def _xml(listing, **kw):
    _call, body = ebay_trading.build_add_item(listing, ["https://x/1.jpg"],
                                              POLICIES, "97201", **kw)
    return body


# ------------------------------------------------- the flag reaches the wire

def test_the_switch_puts_best_offer_in_the_publish(listing):
    """What the seller is promised: offers on, on a new listing."""
    assert ("<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled>"
            "</BestOfferDetails>") in _xml(listing, best_offer=True)


def test_offers_stay_off_when_the_switch_is_off(listing):
    assert "BestOffer" not in _xml(listing, best_offer=False)


def test_a_listing_carries_no_minimum_offer(listing):
    """"No minimum" is the whole setting. eBay auto-declines below
    MinimumBestOfferPrice and auto-accepts at BestOfferAutoAcceptPrice, and
    this app names neither — every offer reaches the seller to answer."""
    body = _xml(listing, best_offer=True)
    assert "MinimumBestOfferPrice" not in body
    assert "BestOfferAutoAcceptPrice" not in body


def test_an_auction_publishes_without_offers_rather_than_failing(listing):
    """eBay has no Best Offer on auction-format listings. Sending it anyway
    rejects the whole publish, so the seller would lose the listing rather
    than lose the offers — and AUCTION_BIN is an auction too."""
    for fmt in ("AUCTION", "AUCTION_BIN"):
        listing.listing_format = fmt
        listing.auction_start_price = 9.99
        body = _xml(listing, best_offer=True)
        assert "BestOffer" not in body, fmt
        assert "<ListingType>Chinese</ListingType>" in body, fmt


def test_the_dry_run_preview_shows_the_offers_the_publish_would_carry(
        monkeypatch, listing, tmp_path):
    """The preview exists so a seller can read the request a publish would
    make. One that omits Best Offer describes a different request than the
    one their switch is about to send."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext

    monkeypatch.setattr(ebay_provider.ebay, "image_urls_for", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda _uid: None)
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}.json")
    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "sandbox")
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1})

    out = ebay_provider.EbayProvider()._dry_run(PublishContext(
        session_id="s1", listing=listing, mode="live",
        base_url="https://example.test", uid="u1", prev_record={}))
    assert "<BestOfferEnabled>true</BestOfferEnabled>" in out.raw["payload"]["xml"]


def test_the_publish_reads_the_sellers_own_switch(monkeypatch, listing):
    """End to end: the saved preference, not a parameter someone remembered
    to pass at one of the two call sites that create listings."""
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
                        lambda _uid: {"allow_offers": 1})
    listing_sync.create_on_ebay(
        "tok", listing, ["https://x/1.jpg"],
        creds={"access_token": "tok", "ship_from_postal": "97201",
               "_uid": "u1"})
    assert sent["best_offer"] is True


# ------------------------------------------------- and only when asked for

def test_a_seller_who_never_chose_gets_no_offers(monkeypatch):
    """Silence is not a request to negotiate."""
    monkeypatch.setattr(listing_sync.db, "get_prefs", lambda _uid: {})
    assert listing_sync.offers_enabled("u1") is False


def test_an_unreadable_preference_never_turns_offers_on(monkeypatch):
    """The sharp one. get_prefs RAISES on a read failure, and a blip must not
    list the seller's items open to negotiation."""
    def _boom(_uid):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(listing_sync.db, "get_prefs", _boom)
    assert listing_sync.offers_enabled("u1") is False


def test_an_explicit_yes_is_honoured(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1})
    assert listing_sync.offers_enabled("u1") is True


def test_an_explicit_no_is_honoured(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 0})
    assert listing_sync.offers_enabled("u1") is False


def test_an_anonymous_publish_never_allows_offers(monkeypatch):
    """There is nobody whose switch it would be."""
    assert listing_sync.offers_enabled(None) is False
    assert listing_sync.offers_enabled("") is False
    assert listing_sync.publish_best_offer(None) is False
    assert listing_sync.publish_best_offer({}) is False


def test_a_revise_never_opens_a_live_listing_to_offers(monkeypatch, listing):
    """The switch says NEW listings. Turning it on must not send BestOffer
    through ReviseItem for every listing already on the seller's store."""
    listing.source = "ebay"
    listing.ebay_listing_id = "110040602158"
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1})
    sent = {}

    class _Trading:
        def revise_listing(self, _token, _item_id, _listing, **kw):
            sent.update(kw)
            return {"ok": True}

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.taxonomy, "sanitize_specifics",
                        lambda _l: None)
    listing_sync.push_edit("tok", listing)
    assert "best_offer" not in sent


def test_the_probe_asks_about_the_publish_it_is_diagnosing(monkeypatch,
                                                           listing):
    """publish_block_issues re-puts the listing to eBay's validator to find
    out WHY a publish was refused. A probe that drops Best Offer is a probe
    answering a different question — and would clear a listing eBay refused
    precisely because of it."""
    seen = []

    class _Trading:
        def verify_listing(self, _token, _candidate, _urls, **kw):
            seen.append(kw)

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1})
    verify = listing_sync.verifier(
        "tok", ["https://x/1.jpg"],
        creds={"ship_from_postal": "97201", "_uid": "u1"})
    verify(listing)
    assert seen[0]["best_offer"] is True
