"""A 10% ad rate the seller was never quoted is not "eBay's recommended rate".

Auto-promote is the setting that says: promote each listing as it publishes,
"at eBay's recommended ad rate". The seller turns it on having read that
sentence and the fee disclosure beside it.

When the recommendation lookup came back with nothing — eBay had no suggestion
for that listing, or the call failed — the code fell through to
`DEFAULT_AD_RATE = 10.0` and promoted anyway. eBay's own recommendations are
usually low single digits, so the seller could be paying several times what
they agreed to, on a rate no screen ever showed them, chosen because a lookup
returned empty.

The manual path is untouched and always was fine: the editor's slider quotes
the rate and previews the fee in pounds before anything happens. This is only
about the path where nobody was asked.

Not promoting is the right failure direction. The listing is live and can be
promoted at any time, by hand, at a rate the seller picks — whereas an ad
charged at a rate they never saw cannot be taken back.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import ebay_provider
from backend.models import Listing


@pytest.fixture()
def promoted(monkeypatch):
    """Run promote() with eBay's recommendation controlled by the test."""
    calls: list = []
    monkeypatch.setattr(ebay_provider.promotions, "promote_listing",
                        lambda rid, listing, creds: calls.append(
                            listing.ad_rate_percent) or {"promoted": True})

    def _run(recommendation, **kw):
        monkeypatch.setattr(
            ebay_provider.promotions, "suggested_ad_rates",
            lambda creds, ids: ({"110001": recommendation}
                                if recommendation is not None else {}))
        listing = Listing(title="Blue lamp", price=25.0)
        out = ebay_provider.promote("s1", listing, {"access_token": "t"},
                                    ebay_listing_id="110001", **kw)
        return out, listing, calls
    return _run


# ------------------------------------------------- the automatic path

def test_ebays_recommendation_is_used_when_there_is_one(promoted):
    out, listing, calls = promoted(3.5, chosen_by_seller=False)

    assert out["promoted"] is True
    assert listing.ad_rate_percent == 3.5
    assert calls == [3.5]


def test_no_recommendation_means_no_promotion(promoted):
    """The finding: this promoted at 10% — a rate no screen had shown."""
    out, listing, calls = promoted(None, chosen_by_seller=False)

    assert out["promoted"] is False
    assert calls == [], "promoted at a rate the seller was never quoted"


def test_the_reason_is_legible(promoted):
    out, _l, _c = promoted(None, chosen_by_seller=False)

    text = (out.get("message") or "").lower()
    assert "rate" in text
    assert "promote" in text


def test_the_listing_is_not_left_marked_as_promoted(promoted):
    """It is not promoted, so the record must not say it is — the editor and
    the insights panel both read that flag."""
    _out, listing, _c = promoted(None, chosen_by_seller=False)

    assert listing.promote is False


# ------------------------------------------------------ the manual path

def test_a_rate_the_seller_picked_is_used_as_given(promoted):
    out, listing, calls = promoted(3.5, rate=7.5, chosen_by_seller=True)

    assert listing.ad_rate_percent == 7.5
    assert calls == [7.5]


def test_a_seller_who_asked_for_this_listing_still_gets_the_default(promoted):
    """Turning Promote on for ONE listing in the editor is an explicit choice
    made next to a slider and a fee preview. If eBay has no suggestion, the
    default is what that slider already showed them — this path is not the
    one where nobody was asked."""
    out, listing, calls = promoted(None, chosen_by_seller=True)

    assert out["promoted"] is True
    assert listing.ad_rate_percent == ebay_provider.promotions.DEFAULT_AD_RATE


def test_the_default_is_still_what_the_editor_offers():
    """If these drift apart, the seller is quoted one rate and charged
    another. The editor's slider defaults to 10 (PROMO_SUGGESTED)."""
    assert ebay_provider.promotions.DEFAULT_AD_RATE == 10.0
