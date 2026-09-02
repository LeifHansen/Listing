"""A publish that could not read the record must not decide it is new.

`_with_stored_identity` runs inside the publish lock and is the snapshot the
create-vs-revise decision is made from. The comment above it is unambiguous
about why: the browser only ever echoes back what a previous publish told it,
"an echo that lost them reads as 'never listed'", and "believing that costs a
duplicate live listing, so the stored record wins on every one of them".

The stored record cannot win if the read failed. `db.get_listing` collapses
"no such listing" and "the read could not be performed" into `None` — its own
comment says so, and says that is right for callers that just want the record
and wrong for a security check. This is neither: it is the read that decides
whether an item already exists on eBay, and

    fresh = db.get_listing(ctx.session_id)
    if not fresh:
        # Nothing stored (a brand-new session, or no DB)

reads one Postgres blip as a brand-new session.

The idempotency key narrows the window and does not close it. A create
repeated shortly after the first is refused by eBay's own UUID check and
recovered; one sent long enough afterwards that eBay no longer holds the UUID
is accepted, and the seller has the same item listed twice.

This is a READ, so refusing costs nothing — nothing has been sent. The route
turns `StorageUnavailable` into a 503, which is the honest answer: we could
not check whether this is already live, so we did not publish.

`db.get_listing_strict` already answers the question properly, with a
sentinel that separates the two cases. It has been there the whole time; the
ownership guard uses it and spells out the same reasoning.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend import errors
from backend.marketplaces import ebay_provider
from backend.marketplaces.base import PublishContext
from backend.models import Listing

LIVE_ITEM = "110011223344"


def _ctx(**over) -> PublishContext:
    """A publish of a listing whose browser copy lost the eBay item id.

    Exactly the shape the comment describes: a second tab, or an auto-save
    whose copy predates the publish.
    """
    return PublishContext(
        session_id="sess-1",
        listing=Listing(title="Vintage Levi's 501", description="Nice.",
                        price=45.0, quantity=1, **over),
        mode="live", base_url="https://app.test", uid="u1", prev_record={})


def test_an_unreadable_store_does_not_read_as_a_brand_new_listing(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_listing_strict",
                        lambda _id: ebay_provider.db.UNAVAILABLE)

    with pytest.raises(errors.StorageUnavailable):
        ebay_provider._with_stored_identity(_ctx())


def test_the_refusal_says_what_to_do_without_naming_the_database(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_listing_strict",
                        lambda _id: ebay_provider.db.UNAVAILABLE)

    with pytest.raises(errors.StorageUnavailable) as caught:
        ebay_provider._with_stored_identity(_ctx())
    message = str(caught.value).lower()
    assert "try again" in message
    assert "postgres" not in message and "sql" not in message


def test_a_listing_that_really_is_new_still_publishes(monkeypatch):
    """The case the old code was written for, and it still works: nothing
    stored is a real answer for a session that has never been saved."""
    monkeypatch.setattr(ebay_provider.db, "get_listing_strict", lambda _id: None)

    ctx = ebay_provider._with_stored_identity(_ctx())
    assert ctx.listing.ebay_listing_id in ("", None)
    assert ctx.prev_record == {}


def test_a_stored_item_id_still_wins_over_the_browsers_copy(monkeypatch):
    """The protection this function exists for, unchanged."""
    stored = {"id": "sess-1", "status": "published",
              "listing": {"ebay_listing_id": LIVE_ITEM, "source": "ebay"}}
    monkeypatch.setattr(ebay_provider.db, "get_listing_strict", lambda _id: stored)

    ctx = ebay_provider._with_stored_identity(_ctx())
    assert ctx.listing.ebay_listing_id == LIVE_ITEM
    assert ctx.prev_record is stored


def test_it_does_not_catch_the_refusal_on_its_way_past(monkeypatch):
    """The other way this comes back.

    `db.get_listing` raises now, so this function needs no special handling —
    which means the way to reintroduce the bug is to wrap the read in a
    `try/except` that falls back to "nothing stored". A publish is not a
    place to be forgiving about that: nothing has been sent, so refusing is
    free, and believing it costs a duplicate live listing.
    """
    import inspect
    src = inspect.getsource(ebay_provider._with_stored_identity)
    assert "except" not in src, (
        "the create-vs-revise snapshot swallows something; a read failure "
        "must reach the route as a refusal, not become 'never listed'")
