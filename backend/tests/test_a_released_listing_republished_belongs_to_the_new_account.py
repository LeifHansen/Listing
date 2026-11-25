"""A listing released from an old eBay account, then republished, is the new
account's.

"Release" turns the previous account's listings into ordinary drafts. It
cleared the username, the item id and the source -- but not the immutable
account id, which is what ownership is actually decided on. Republished on
the new account, the record then carried the new username beside the OLD id:
`owns` and `matchable` both said no for the account that had just listed it,
so the status sweep never archived it when it sold, and every store import
brought the same item in again as a second card.

Two halves: release clears the id with the rest, and a create stamps both
halves of the owner from the account that actually minted the item.
"""
from __future__ import annotations

from backend.models import Listing
from backend.services import ebay_account, ebay_trading, listing_sync

OLD = {"ebay_username": "alice", "ebay_user_id": "ID_A"}
NEW = {"access_token": "tok", "ship_from_postal": "97201", "_uid": "u1",
       "ebay_username": "bob", "ebay_user_id": "ID_B"}


def _old_record():
    return {"title": "Brass lamp", "price": 30.0, "source": "ebay",
            "ebay_listing_id": "110000000001", "ebay_account": "alice",
            "ebay_account_id": "ID_A",
            "view_url": "https://www.ebay.com/itm/110000000001"}


def test_release_clears_the_immutable_account_id_too():
    data = ebay_account.unlink_ebay(_old_record())
    assert data["ebay_account_id"] == ""
    assert data["ebay_account"] == ""


def _create(monkeypatch, listing, creds):
    class _Trading:
        AlreadyListedError = ebay_trading.AlreadyListedError
        TradingError = ebay_trading.TradingError
        UnknownOutcome = ebay_trading.UnknownOutcome

        def create_listing(self, *_a, **_kw):
            return {"published": True, "listing_id": "110000000002",
                    "view_url": "https://www.ebay.com/itm/110000000002"}

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs", lambda _uid: {})
    listing_sync.create_on_ebay("tok", listing, ["https://x/1.jpg"],
                                creds=creds)


def test_a_create_stamps_the_account_that_minted_the_item(monkeypatch):
    """Even over a stale id left by an older release: a create mints a new
    item on THIS account, so whatever the record said before is history."""
    stale = {k: v for k, v in _old_record().items()
             if k not in ("source", "ebay_listing_id", "view_url")}
    stale["ebay_account"] = ""
    listing = Listing(**stale)
    _create(monkeypatch, listing, NEW)

    assert listing.ebay_account == "bob"
    assert listing.ebay_account_id == "ID_B"
    record = listing.model_dump()
    assert listing_sync.owns(record, NEW)
    assert listing_sync.matchable(record, NEW)
    assert not listing_sync.owns(record, OLD)


def test_a_create_without_a_readable_identity_claims_no_old_id(monkeypatch):
    """No id on the connection means "unknown", which `owns` reads by name --
    never the previous account's id carried forward."""
    listing = Listing(title="Brass lamp", price=30.0, ebay_account_id="ID_A")
    _create(monkeypatch, listing, {**NEW, "ebay_user_id": ""})
    assert listing.ebay_account_id == ""
    assert listing_sync.owns(listing.model_dump(), {**NEW, "ebay_user_id": ""})
