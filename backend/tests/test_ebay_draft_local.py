"""A draft is this app's, not eBay's.

Saving a draft on a CONNECTED account used to fall through to the Inventory
engine with `do_publish=False`, which created an inventory item and an
unpublished offer on the seller's eBay account.

Nothing good came of that. Inventory-based listings don't show in Seller Hub,
so the seller could neither find nor delete what was created; and the live
publish that follows goes out through the Trading API and mints an entirely
different item, so the offer is never claimed. Every draft save on a connected
account left one behind, and only a draft save could create them.

Of the tests here only `test_a_connected_seller_s_draft_never_reaches_ebay`
catches that defect; the rest are regression guards over an early return that
now recomputes what the old path returned.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import ebay_provider
from backend.marketplaces.base import PublishContext
from backend.models import Listing


def _draft_listing(**over) -> Listing:
    """A listing this app has never published: no source, no item id."""
    fields = {
        "title": "Vintage Pyrex mixing bowl",
        "condition": "USED_EXCELLENT",
        "category_id": "12345",
        "price": 24.99,
        "quantity": 1,
        "description": "A bowl.",
    }
    fields.update(over)
    return Listing(**fields)


@pytest.fixture
def no_ebay_calls(monkeypatch):
    """Every door out to eBay from the draft path, wired to fail loudly."""
    def _boom(*a, **k):
        raise AssertionError("a draft save reached eBay")
    monkeypatch.setattr(ebay_provider.ebay, "publish", _boom)
    monkeypatch.setattr(ebay_provider.listing_sync, "create_on_ebay", _boom)
    monkeypatch.setattr(ebay_provider.listing_sync, "push_edit", _boom)
    monkeypatch.setattr(ebay_provider.ebay_auth, "ensure_inventory_location", _boom)


@pytest.fixture
def recorded(monkeypatch):
    """Captures what the draft path writes to the record."""
    writes: list[dict] = []
    monkeypatch.setattr(
        ebay_provider.db, "upsert_listing",
        lambda sid, dump, status="", user_id=None, **k:
            writes.append({"status": status, "uid": user_id}) or True)
    return writes


def _save_draft(creds, prev_record=None):
    ctx = PublishContext(
        session_id="s1", listing=_draft_listing(), mode="draft",
        base_url="https://example.test", uid="u1",
        prev_record=prev_record or {})
    return ebay_provider.EbayProvider()._publish_locked(ctx, creds)


def test_a_connected_seller_s_draft_never_reaches_ebay(no_ebay_calls, recorded):
    """The defect. Fails against the old code, which called
    createOrReplaceInventoryItem and createOffer before returning."""
    out = _save_draft({"access_token": "tok", "ship_from_postal": "97201",
                       "merchant_location_key": "loc-1", "_uid": "u1"})
    assert out.ok and out.status == "draft"
    assert recorded == [{"status": "draft", "uid": "u1"}]


def test_a_disconnected_seller_s_draft_still_saves(no_ebay_calls, recorded):
    """Regression guard, not a caught defect: the unconnected case already
    stayed local. It fails against the old code only because the fixture
    blocks `ebay.publish`, which that path used to route through without
    reaching the network."""
    out = _save_draft(None)
    assert out.ok and out.status == "draft"
    assert "NOT on eBay" in out.message


def test_saving_a_draft_does_not_demote_a_live_listing(no_ebay_calls, recorded):
    """Regression guard: the old code computed this status correctly too
    (`"published" if was_live else mode`). Pinned because the new early
    return recomputes it, and getting it wrong would mark a live listing
    as a draft."""
    out = _save_draft({"access_token": "tok"}, prev_record={"status": "published"})
    assert out.status == "published"
    assert recorded == [{"status": "published", "uid": "u1"}]


def test_the_draft_is_not_reported_as_a_dry_run(no_ebay_calls, recorded):
    """Regression guard: `dry_run` drives the UI's "we generated a payload
    instead" panel, and the old draft return already left it false. Pinned
    because the new return builds `raw` from scratch."""
    out = _save_draft({"access_token": "tok"})
    assert out.dry_run is False
    assert out.raw["draft"] is True and out.raw["mode"] == "draft"
