"""One item, one unit of stock, two marketplaces.

A listing crossposted to Etsy is the same object in the same box. When it
sells on eBay the Etsy copy goes on taking orders, and the seller finds out
when a second buyer pays for something they no longer have. So a sale or an
ending on eBay deactivates the Etsy copy — deactivated, not deleted, so it
can be put back if the eBay sale falls through.

The three rules: a listing that is only on eBay costs the sale nothing, a
takedown that fails is reported rather than swallowed, and nothing about
this can fail a sale.
"""
from __future__ import annotations

import pytest

from backend.services import inventory_mirror

def _on_both() -> dict:
    """A fresh record each time: the takedown writes into the marketplaces
    map, and a shared one would let the first test decide the rest."""
    return {"title": "Vintage mug", "price": 20.0, "quantity": 1,
            "marketplaces": {"ebay": {"status": "published"},
                             "etsy": {"status": "published", "listing_id": "77"}}}


EBAY_ONLY = {"title": "Vintage mug", "price": 20.0, "quantity": 1,
             "marketplaces": {"ebay": {"status": "published"}}}


class _Etsy:
    key, label = "etsy", "Etsy"

    def __init__(self, fail=False):
        self.fail = fail
        self.ended = []

    def creds_for(self, _uid):
        return {"access_token": "t", "shop_id": "1"}

    def end(self, ctx, creds):
        if self.fail:
            raise ValueError("Etsy is down")
        self.ended.append(ctx.session_id)
        return {"ended": True}


@pytest.fixture
def mirror(monkeypatch):
    """The takedown runs here and now rather than on a thread, so a test can
    read what it did."""
    provider = _Etsy()
    writes, notices = [], []
    monkeypatch.setattr(inventory_mirror, "get_marketplace",
                        lambda key: provider if key == "etsy" else None)
    monkeypatch.setattr(inventory_mirror.background, "run_in_background",
                        lambda fn, *a, **k: fn(*a))
    monkeypatch.setattr(inventory_mirror.db, "mutate_listing_data",
                        lambda rid, fn, **kw: writes.append(fn(_on_both())) or {})
    monkeypatch.setattr(inventory_mirror.db, "add_notification",
                        lambda *a, **k: notices.append((a, k)))
    return provider, writes, notices


def test_a_sale_deactivates_the_etsy_copy_and_files_it_as_ended(mirror):
    provider, writes, notices = mirror
    assert inventory_mirror.on_ebay_finished("u1", "rec-1", _on_both()) == ["etsy"]
    assert provider.ended == ["rec-1"]
    assert writes[-1]["marketplaces"]["etsy"]["status"] == "ended"
    assert writes[-1]["marketplaces"]["etsy"]["error"] == ""
    assert notices == []


def test_a_listing_only_on_ebay_costs_the_sale_nothing(mirror, monkeypatch):
    provider, writes, _notices = mirror

    def _never(*a, **k):
        raise AssertionError("a listing on eBay alone looked up a provider")

    monkeypatch.setattr(inventory_mirror, "get_marketplace", _never)
    assert inventory_mirror.on_ebay_finished("u1", "rec-1", EBAY_ONLY) == []
    assert inventory_mirror.on_ebay_finished("u1", "rec-1", {}) == []
    assert inventory_mirror.on_ebay_finished(None, "rec-1", _on_both()) == []
    assert provider.ended == [] and writes == []


def test_an_etsy_entry_that_is_not_live_is_left_alone(mirror):
    provider, _writes, _notices = mirror
    ended = {**_on_both(), "marketplaces": {"etsy": {"status": "ended"}}}
    assert inventory_mirror.on_ebay_finished("u1", "rec-1", ended) == []
    assert provider.ended == []


def test_a_failed_takedown_is_reported_not_swallowed(monkeypatch):
    provider = _Etsy(fail=True)
    writes, notices = [], []
    monkeypatch.setattr(inventory_mirror, "get_marketplace", lambda key: provider)
    monkeypatch.setattr(inventory_mirror.background, "run_in_background",
                        lambda fn, *a, **k: fn(*a))
    monkeypatch.setattr(inventory_mirror.db, "mutate_listing_data",
                        lambda rid, fn, **kw: writes.append(fn(_on_both())) or {})
    monkeypatch.setattr(inventory_mirror.db, "add_notification",
                        lambda *a, **k: notices.append((a, k)))

    # It does not raise: a sale is never failed over this.
    inventory_mirror.on_ebay_finished("u1", "rec-1", _on_both())

    entry = writes[-1]["marketplaces"]["etsy"]
    assert entry["status"] == "published", "it must not claim an ending that failed"
    assert "may still be for sale" in entry["error"]
    assert notices, "the seller is not told the item is still for sale on Etsy"
    kind = notices[-1][0][1]
    assert kind == "etsy_still_live"
    assert notices[-1][1]["dedupe_key"] == "etsy-still-live:rec-1"


def test_a_disconnected_shop_is_reported_the_same_way(monkeypatch):
    provider = _Etsy()
    monkeypatch.setattr(provider, "creds_for", lambda _uid: None)
    notices = []
    monkeypatch.setattr(inventory_mirror, "get_marketplace", lambda key: provider)
    monkeypatch.setattr(inventory_mirror.background, "run_in_background",
                        lambda fn, *a, **k: fn(*a))
    monkeypatch.setattr(inventory_mirror.db, "mutate_listing_data",
                        lambda rid, fn, **kw: {})
    monkeypatch.setattr(inventory_mirror.db, "add_notification",
                        lambda *a, **k: notices.append(k))
    inventory_mirror.on_ebay_finished("u1", "rec-1", _on_both())
    assert provider.ended == []
    assert notices and notices[-1]["dedupe_key"] == "etsy-still-live:rec-1"


def test_a_withheld_marketplace_is_simply_absent(monkeypatch):
    monkeypatch.setattr(inventory_mirror, "get_marketplace", lambda key: None)
    monkeypatch.setattr(inventory_mirror.background, "run_in_background",
                        lambda fn, *a, **k: fn(*a))
    called = []
    monkeypatch.setattr(inventory_mirror.db, "mutate_listing_data",
                        lambda *a, **k: called.append(1))
    inventory_mirror.on_ebay_finished("u1", "rec-1", _on_both())
    assert called == []
