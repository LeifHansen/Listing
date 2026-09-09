"""A live listing that eBay has in a SALE must not read as a broken price.

The reported bug, from a seller editing a live Hawaiian shirt listing priced
at $39.99: the save came back

    The price is missing or invalid

with the price field ringed red — over a filled-in, perfectly valid price.
The listing was in an eBay sale (Markdown Manager / "Discounts" in Seller
Hub) with revisions blocked, and eBay's refusal is about the sale.

Two branches of the taxonomy claimed that sentence and both answered it
wrongly. It says "price", so the price branch called the price missing;
it says "cannot be revised", so the frozen-listing branch blamed a pending
Best Offer that did not exist. Neither named the sale, which is the only
fact the seller can act on — the price is fine, the listing is fine, and the
thing to change is the discount in Seller Hub.

The same shape as the pending-offer bug in
test_a_pending_offer_does_not_lock_the_editor.py: a temporary eBay
restriction reported as a missing field, sending the seller to fix something
that was never wrong.
"""
from __future__ import annotations

import json

import pytest

from backend import ebay_errors
from backend.services import ebay_trading

# Three wordings of the same refusal. eBay renamed the feature twice —
# Markdown Manager, then Promotional sale, then Discounts on Seller Hub in
# 2024 — and the sentence an account gets depends on which vintage it was set
# up under, so the app cannot key on any single one.
SALE_SAYS = [
    ("This item is currently in a Promotional Sale and its price cannot be "
     "revised. Remove the item from the sale to change the price."),
    ("Item price cannot be revised because the item is part of a Markdown "
     "Manager sale."),
    ("The price cannot be revised: this listing is in a discount and the sale "
     "is set to block price revisions."),
]


@pytest.mark.parametrize("said", SALE_SAYS)
def test_a_sale_lock_is_not_reported_as_a_missing_price(said):
    """The bug itself. Fails against the old taxonomy: target "price",
    title "The price is missing or invalid", and a red ring round a $39.99
    the seller had just typed."""
    issue = ebay_errors.explain({"errorId": "21916626", "message": said})

    assert issue["target"] != "price"
    assert "missing or invalid" not in issue["title"].lower()
    assert "sale" in issue["title"].lower()


@pytest.mark.parametrize("said", SALE_SAYS)
def test_the_sale_is_named_and_so_is_the_way_out(said):
    """The seller's next move is in Seller Hub, not in this form — so the
    answer has to say which sale, and how to lift it."""
    issue = ebay_errors.explain({"errorId": "21916626", "message": said})
    fix = issue["fix"].lower()

    assert "seller hub" in fix or "marketing" in fix
    assert "sale" in fix
    # And it must not tell them to go and re-type a price that is fine.
    assert "greater than $0" not in fix


@pytest.mark.parametrize("said", SALE_SAYS)
def test_a_sale_lock_is_not_blamed_on_a_best_offer(said):
    """"cannot be revised" is also the pending-Best-Offer sentence, and that
    branch sits above the price one. Fails against a fix that only tightens
    the price branch: the seller is then told an offer is waiting on a
    listing that has none, which is a different wrong answer, not a fix."""
    issue = ebay_errors.explain({"errorId": "21916626", "message": said})
    said_back = f"{issue['title']} {issue['fix']}".lower()

    assert "best offer" not in said_back
    assert "auction" not in said_back


def test_the_field_is_not_ringed_for_something_no_field_can_fix():
    """`generic` is how the editor is told there is nothing to open: see
    publishShared.fixTargetFor, which skips generic and account targets. A
    "Fix this" button pointing at the price is the same wrong claim in
    button form."""
    issue = ebay_errors.explain({"errorId": "21916626", "message": SALE_SAYS[0]})

    assert issue["target"] == "generic"


def test_ebays_own_sentence_survives_into_the_answer():
    """Whatever wording this account gets, the seller sees eBay's own words —
    the app's copy is a guess about which sale feature is switched on, and
    eBay's sentence is the evidence."""
    issue = ebay_errors.explain(
        {"errorId": "21916626", "message": "Price revision blocked.",
         "longMessage": SALE_SAYS[1]})

    assert "Markdown" in issue["fix"]


def test_it_reaches_the_seller_through_a_trading_error_too():
    """The live-edit path is Trading's ReviseFixedPriceItem, so the refusal
    arrives as a TradingError, not as a REST body."""
    exc = ebay_trading.TradingError(SALE_SAYS[0], code="21916626", detail="")
    issue = ebay_errors.from_trading_error(exc)[0]

    assert issue["target"] == "generic"
    assert "sale" in issue["title"].lower()


# --- the guardrails: what must still read as a price problem ---------------

def test_a_genuinely_missing_price_still_says_so():
    """The branch this narrows still has a job. Fails against a fix that
    simply deletes the price claim."""
    issue = ebay_errors.from_response(json.dumps({"errors": [{
        "errorId": 25002,
        "message": "The price is missing or is not a valid amount.",
    }]}))[0]

    assert issue["target"] == "price"
    assert issue["title"] == "The price is missing or invalid"
    assert "greater than $0" in issue["fix"]


def test_an_invalid_price_still_says_so():
    """10008 — eBay's plain "the price is invalid". Guards the wording the
    240 taxonomy test already relies on."""
    exc = ebay_trading.TradingError("The price is invalid.", code="10008")
    issue = ebay_errors.from_trading_error(exc)[0]

    assert issue["target"] == "price"
    assert issue["title"] == "The price is missing or invalid"


def test_a_price_eBay_declines_for_another_reason_quotes_eBay():
    """Not every price rejection is an empty field. A price eBay won't take
    for a category, or in a currency it won't accept, is a filled-in price —
    and "Set a price greater than $0" under a $39.99 is advice that cannot
    be followed. eBay's own sentence is the informative thing, and the toast
    renders the TITLE and nothing else."""
    issue = ebay_errors.explain({
        "errorId": "21916567",
        "message": "The price you entered is higher than eBay allows for this "
                   "item."})

    assert issue["target"] == "price"          # there IS a field to open
    assert "missing" not in issue["title"].lower()
    assert "higher than eBay allows" in issue["title"]


def test_sale_locked_does_not_fire_on_every_sentence_with_a_sale_in_it():
    """The detector reads two halves — a sale word AND a refusal — because
    "sale", "sales" and "for sale" turn up all over eBay's error text."""
    assert not ebay_errors.sale_locked(
        "Your sales are subject to eBay's fees.")
    assert not ebay_errors.sale_locked(
        "This item is in a promotional sale.")   # a statement, not a refusal
    assert not ebay_errors.sale_locked(
        "Item specifics cannot be changed if an auction-style listing has a "
        "bid or ends within 12 hours, or a fixed price listing has a pending "
        "Best Offer.")
    assert ebay_errors.sale_locked(SALE_SAYS[0])


def test_the_pending_offer_refusal_still_reads_as_a_pending_offer():
    """The branch above the new one must keep its own case. Fails against a
    sale detector loose enough to swallow "a fixed price listing has a
    pending Best Offer"."""
    issue = ebay_errors.explain({
        "errorId": "21916626",
        "message": ("Item specifics cannot be changed if an auction-style "
                    "listing has a bid or ends within 12 hours, or a fixed "
                    "price listing has a pending Best Offer.")})

    assert "sale" not in issue["title"].lower()
    assert "Best Offer" in issue["fix"] or "best offer" in issue["fix"].lower()


def test_a_shipping_discount_is_not_an_item_sale():
    """"Discount" is two features on eBay. A combined-postage rule the app
    can't apply must not come back as "this item is in a sale", which would
    send the seller to a promotion that doesn't exist."""
    assert not ebay_errors.sale_locked(
        "The combined shipping discount cannot be applied to this listing.")
    # ...but a markdown that also mentions shipping is still a markdown.
    assert ebay_errors.sale_locked(
        "This item is in a Markdown Manager sale; its price and shipping "
        "discount cannot be revised.")


def test_a_promotional_shipping_discount_is_not_an_item_sale_either():
    """The words overlap both ways: "promotional shipping" contains
    "promotion". Fails against a guard that only asks whether an item-sale
    word appears somewhere in the sentence — the shipping phrase supplies
    one itself."""
    assert not ebay_errors.sale_locked(
        "The promotional shipping discount cannot be applied to this item.")
    assert not ebay_errors.sale_locked(
        "A free shipping promotion cannot be combined with this service.")
