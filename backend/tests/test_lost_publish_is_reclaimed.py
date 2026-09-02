"""A publish whose answer went missing must not come back as a second card.

The sequence, all of it reachable today:

  1. The seller publishes. The AddFixedPriceItem goes out carrying a
     deterministic SKU (publish_guard.idempotency_key), which is what makes a
     repeat safe.
  2. eBay creates the listing.
  3. The answer never arrives -- a read timeout, a reset connection, a 5xx
     from something in front of eBay. The app cannot tell this from a
     rejection, so it reports a failed publish and leaves the record a draft.
  4. Nobody retries, because they were told it failed.
  5. The next store sync pulls the seller's active listings and finds one it
     has no record of -- this app's own listing -- and imports it as
     `ebay-<item>`: a SECOND card for the same item.

Step 5 is the visible damage, and it is the exact duplicate pair
publish_guard was built to prevent, reached from the other end: not two
creates, but one create the app forgot it made.

The SKU is what closes it. Every fixed-price create this app sends stamps
`qf-<session id>` (`-r<item>` on a relist) and sets
InventoryTrackingMethod=SKU so eBay keeps it. The sync already reads SKU off
every item it fetches, and already prefers an app-created record over a
mirror when it can match one -- it just had no way to match a record that
never got an item id written to it. Now it does, and the seller's draft
becomes the live listing it always was: same photos, same AI-written copy,
same card.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def a_sync(monkeypatch):
    """One eBay listing, one local record, and the sync run over them."""
    from backend import db
    from backend.services import ebay_trading, listing_sync

    saved: dict[str, dict] = {}

    def _run(known: list[dict], sku: str, ended: str = "") -> dict:
        skus = {"556677": sku}
        monkeypatch.setattr(ebay_trading, "active_listing_ids",
                            lambda *a, **k: ["556677"])
        monkeypatch.setattr(ebay_trading, "unsold_listing_ids",
                            lambda *a, **k: [ended] if ended else [])
        monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})
        monkeypatch.setattr(db, "list_listings", lambda **_k: known)
        monkeypatch.setattr(
            db, "upsert_listing",
            lambda rid, data, **k: saved.__setitem__(
                rid, {"data": data, **k}))
        monkeypatch.setattr(
            ebay_trading, "get_listing",
            lambda _t, item_id: {"title": "A thing", "price": 10.0,
                                 "quantity": 1,
                                 "sku": skus.get(item_id, ""),
                                 "ebay_listing_id": item_id})
        listing_sync.import_active("tok", "u1")
        return saved
    return _run


def _draft(session_id: str, **listing) -> dict:
    """A local record for a listing the app tried to publish."""
    return {"id": session_id, "status": "draft",
            "listing": {"title": "A thing", "price": 10.0, **listing}}


# ------------------------------------------------------- the regression

def test_the_lost_listing_lands_on_the_sellers_own_draft(a_sync):
    saved = a_sync([_draft("sess-abc")], sku="qf-sess-abc")

    # The finding: this used to write `ebay-556677`, leaving the seller with
    # their draft AND an imported copy of the same item.
    assert "ebay-556677" not in saved, "imported this app's own listing again"
    assert "sess-abc" in saved
    assert saved["sess-abc"]["data"]["ebay_listing_id"] == "556677"
    assert saved["sess-abc"]["status"] == "published"


def test_a_lost_relist_lands_on_the_record_it_relisted(a_sync):
    """A relist mints a NEW item id, and its key carries the ended item it
    replaced -- so it is a different SKU from the publish that first listed
    that item, and has to resolve back to the same record all the same.

    The record still holds the OLD id here, which is the whole shape of a lost
    relist: create_on_ebay overwrites ebay_listing_id only on the way out, and
    this attempt never got that far.
    """
    saved = a_sync([_draft("sess-abc", ebay_listing_id="998877")],
                   sku="qf-sess-abc-r998877")

    assert "ebay-556677" not in saved
    assert saved["sess-abc"]["data"]["ebay_listing_id"] == "556677", \
        "the relisted record should now point at the new listing"


# ------------------------------------------ what it must NOT match on

def test_an_unrelated_sku_still_imports_normally(a_sync):
    """A seller's own SKU on a listing made outside this app is not a claim on
    anything here. It imports as the mirror it is."""
    saved = a_sync([_draft("sess-abc")], sku="MY-SHELF-42")

    assert "ebay-556677" in saved
    assert "sess-abc" not in saved


def test_a_record_that_already_names_another_item_is_not_stolen(a_sync):
    """A record holding a DIFFERENT live item id is matched by that id, not by
    a stale key. Adopting here would point one card at two eBay listings and
    silently abandon the one it was actually publishing."""
    saved = a_sync([_draft("sess-abc", ebay_listing_id="111222")],
                   sku="qf-sess-abc")

    assert "sess-abc" not in saved, "a record with its own item id was stolen"
    assert "ebay-556677" in saved, "the unrecognised item should still import"


def test_a_blank_sku_matches_nothing(a_sync):
    """Most eBay listings carry no SKU at all. An empty one must not collide
    with every record that has no item id."""
    saved = a_sync([_draft("sess-abc")], sku="")

    assert "ebay-556677" in saved
    assert "sess-abc" not in saved


# ------------------------------------- one record, two eBay listings
#
# Caught by re-reading the change rather than by the tests above, and it would
# have LOST a live listing -- worse than the duplicate this feature removes.

def test_a_reclaimed_relist_does_not_lose_the_new_listing(a_sync):
    """The predecessor is still on eBay's ended list, and it matches the same
    record by item id.

    A relist has two eBay listings pointing at one card: the new live one
    (matched here by publish key) and the ended one it replaced (matched by
    the item id the record still holds). The import walks active listings
    first and ended ones after, so without a guard the ended listing is
    written over the live one -- the card goes back to Inactive and the live
    listing has no record at all. Nothing on any screen would show it.

    The ended predecessor gets its own `ebay-<item>` mirror instead, which is
    exactly what a relist whose response DID arrive already produces.
    """
    saved = a_sync([_draft("sess-abc", ebay_listing_id="998877")],
                   sku="qf-sess-abc-r998877", ended="998877")

    assert saved["sess-abc"]["data"]["ebay_listing_id"] == "556677", \
        "the ended predecessor overwrote the live relisted listing"
    assert saved["sess-abc"]["status"] == "published"
    assert "ebay-998877" in saved, "the ended listing should keep its own row"
    assert saved["ebay-998877"]["status"] == "ended"


def test_a_listing_that_never_saved_does_not_lock_the_record(a_sync,
                                                             monkeypatch):
    """A claim is a write, not a match.

    An item that matches a record and then fails to validate writes nothing.
    Marking the record claimed at the point of matching would lock out the
    listing that follows -- which in the relist case is the one holding the
    record's own item id -- leaving the record on its stale state with no
    second chance in this run.
    """
    from backend.models import Listing

    real = Listing.__init__

    def _reject_the_relist(self, **kwargs):
        # Only the newly relisted item, and every time it is built -- the
        # validation this is aimed at is the last of several constructions,
        # and failing just the first one lands in a try/except elsewhere.
        if kwargs.get("ebay_listing_id") == "556677":
            raise ValueError("eBay sent something we can't parse")
        real(self, **kwargs)

    monkeypatch.setattr(Listing, "__init__", _reject_the_relist)
    saved = a_sync([_draft("sess-abc", ebay_listing_id="998877")],
                   sku="qf-sess-abc-r998877", ended="998877")

    # The live relist failed to validate and wrote nothing, so the ended
    # predecessor -- which matches the same record by the item id it still
    # holds -- must still be free to update it.
    assert "sess-abc" in saved, "the record was locked by a write that never happened"
    assert "ebay-998877" not in saved


# ------------------------------- and the duplicates already out there
#
# Everything above stops NEW duplicates. A store that has already synced once
# since the lost publish has the pair on disk: the seller's draft, and an
# `ebay-<item>` mirror of the listing it became. On the next sync the mirror
# matches the item by id and wins, so the draft is never reclaimed and the
# pair is permanent.

def test_a_mirror_already_made_from_the_lost_listing_gives_way(a_sync):
    """The app's own record beats a mirror of the same item — which is what
    `_index_by_item` already does when both carry the id, applied to the case
    where only one of them does.

    The mirror is not deleted here; `_drop_stale_mirrors` removes it on the
    next pass, once the draft's record actually names the item. The point is
    that the draft stops being a stranded second card.
    """
    saved = a_sync(
        [_draft("sess-abc"),
         {"id": "ebay-556677", "status": "published",
          "listing": {"ebay_listing_id": "556677", "source": "ebay"}}],
        sku="qf-sess-abc")

    assert saved["sess-abc"]["data"]["ebay_listing_id"] == "556677", \
        "the draft stayed stranded beside a mirror of its own listing"
    assert saved["sess-abc"]["status"] == "published"


def test_the_apps_own_record_still_wins_when_it_holds_the_id(a_sync):
    """A record that already NAMES the item is matched by that, and must not
    be displaced by a key pointing at some other record."""
    saved = a_sync(
        [{"id": "sess-mine", "status": "published",
          "listing": {"ebay_listing_id": "556677", "source": "ebay"}},
         _draft("sess-abc")],
        sku="qf-sess-abc")

    # Both could claim it: sess-mine by id, sess-abc by key. The id wins.
    assert "sess-mine" in saved
    assert saved["sess-mine"]["data"]["ebay_listing_id"] == "556677"
    assert "sess-abc" not in saved
