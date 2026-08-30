"""A number is not a price until it says which money it is.

`notifications.notify_sold` writes the alert a seller acts on — the one that
says an item sold and to get it shipped — and it formatted the amount as
`f" for ${price:,.2f}"`, a dollar sign hardcoded into the sentence. The app
supports other currencies: `Listing.currency` is a real field, the editor
sets it, and eBay reports the sale in it.

So a seller on eBay.co.uk who sold something for £45 was told it sold "for
$45.00" — a number attached to the wrong money, in the message they use to
decide whether the sale was worth shipping and at what cost.

`listing_merge` already had the right rule and kept it to itself: a dollar
sign for USD, otherwise the amount and the code, because "€45.00" invites
guessing at which euro-adjacent symbol was meant while "45.00 EUR" does not.
It is now shared rather than duplicated — the second copy is where these drift.
"""
from __future__ import annotations

import pytest

from backend.money import money


class _Recorded:
    """The last notification db.add_notification was asked to write."""

    def __init__(self):
        self.rows: list[dict] = []

    def __call__(self, user_id, **fields):
        self.rows.append({"user_id": user_id, **fields})


@pytest.fixture()
def sold(monkeypatch):
    from backend.services import notifications

    written = _Recorded()
    monkeypatch.setattr(notifications.db, "add_notification", written)

    def _run(listing: dict):
        notifications.notify_sold("u1", "rec-1", listing, sold_quantity=1)
        return written.rows[-1] if written.rows else None
    return _run


# --------------------------------------------------------------- the rule

def test_dollars_keep_the_symbol():
    assert money(45, "USD") == "$45.00"
    assert money(1234.5, "usd") == "$1,234.50"


def test_anything_else_names_the_code():
    # "£"/"€" would be a guess about which of several currencies share a
    # symbol; the ISO code cannot be misread.
    assert money(45, "GBP") == "45.00 GBP"
    assert money(45, "EUR") == "45.00 EUR"


def test_a_missing_currency_is_treated_as_dollars():
    """Listing.currency defaults to USD, so an absent one is not unknown —
    it is the default, and the rest of the app already reads it that way."""
    assert money(45, "") == "$45.00"
    assert money(45, None) == "$45.00"


def test_an_unusable_amount_has_no_price_to_show():
    assert money(None, "USD") is None
    assert money("not a number", "USD") is None


# --------------------------------------------------- and where it is used

def test_a_sale_in_pounds_is_not_reported_in_dollars(sold):
    """The finding."""
    row = sold({"title": "A lamp", "sold_price": 45.0, "currency": "GBP"})

    assert "$" not in row["body"]
    assert "45.00 GBP" in row["body"]


def test_a_sale_in_dollars_still_reads_as_dollars(sold):
    row = sold({"title": "A lamp", "sold_price": 45.0, "currency": "USD"})

    assert "$45.00" in row["body"]


def test_a_sale_with_no_price_says_nothing_about_money(sold):
    """It has always been allowed to omit the amount; it must not start
    inventing a zero."""
    row = sold({"title": "A lamp", "currency": "GBP"})

    assert "GBP" not in row["body"]
    assert "$" not in row["body"]
    assert "sold on eBay" in row["body"]


def test_the_sold_price_beats_the_asking_price(sold):
    """An accepted offer settles below the asking price, and the alert is
    about what actually came in."""
    row = sold({"title": "A lamp", "price": 60.0, "sold_price": 45.0,
                "currency": "USD"})

    assert "$45.00" in row["body"]
    assert "60" not in row["body"]


# ----------------------------------------- the floors the checklist quotes

def _listing(**over):
    from backend.models import Listing
    base = {"title": "A thing", "price": 0.10, "quantity": 1,
            "category_id": "1234", "condition": "USED_GOOD",
            "description": "words"}
    base.update(over)
    return Listing(**base)


def test_the_ebay_price_floor_is_quoted_in_the_listings_currency():
    """A checklist that says "eBay's minimum price is $0.99" to a seller
    listing in pounds is naming the wrong money on the one screen that exists
    to tell them what to fix."""
    from backend.services import preflight

    issues = preflight.validate(
        _listing(currency="GBP"), mode="live", has_fulfillment=True,
        has_payment=True, has_return=True, has_location=True, connected=True)
    price = [i for i in issues if i["target"] == "price"]
    assert price, "the floor check should still fire"
    said = price[0]["title"] + " " + price[0]["fix"]
    assert "$" not in said
    assert "GBP" in said


def test_the_ebay_price_floor_still_reads_as_dollars_on_a_usd_listing():
    from backend.services import preflight

    issues = preflight.validate(
        _listing(currency="USD"), mode="live", has_fulfillment=True,
        has_payment=True, has_return=True, has_location=True, connected=True)
    price = [i for i in issues if i["target"] == "price"]
    assert "$0.99" in price[0]["title"]


def test_the_etsy_price_floor_follows_the_same_rule():
    from backend.marketplaces import mapping_etsy

    issues = mapping_etsy.preflight(_listing(price=0.05, currency="GBP"), {})
    price = [i for i in issues if i["target"] == "price"]
    assert price, "the floor check should still fire"
    said = price[0]["title"] + " " + price[0]["fix"]
    assert "$" not in said
    assert "cents" not in said.lower(), "cents is a currency claim too"
