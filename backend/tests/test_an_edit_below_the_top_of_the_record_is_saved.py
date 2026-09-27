"""An edit made below the top level of a listing's record is written.

db.mutate_listing_data is the one locked read-modify-write every
multi-writer path goes through: folding a publish's per-marketplace outcomes
into the record, the inventory mirror marking an Etsy copy ended after an eBay
sale, a failed takedown saying so on the card. It handed `mutate` a SHALLOW
copy of the stored blob. SQLAlchemy decides whether a JSON column changed by
comparing the new value to the loaded one with ==, and every nested object was
shared between the two, so an edit made in place below the top level changed
both sides at once, compared equal, and was never written. The status column
moved; `marketplaces.etsy` — the Etsy listing id, its url, its error — stayed
exactly as it was.

The cost is concrete: a crosspost whose Etsy id was dropped offers the same
item for Etsy again and mints a second live listing, and an eBay sale never
takes the Etsy copy down because the record never learned it was up. Only a
fold that ALSO changed a top-level key (eBay's mirrored ebay_listing_id)
carried its nested state along, which is why eBay publishing never showed it.

Every test here runs against the real module and a real SQLite file — the
suites that drive these paths through the app stand in for
mutate_listing_data, which is how this survived them.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import state as marketplace_state
from backend.marketplaces.base import PublishOutcome


@pytest.fixture
def db(dbmod):
    return dbmod


def _stored(db, listing_id: str) -> dict:
    rec = db.get_listing(listing_id)
    assert rec is not None
    return rec["listing"]


def test_an_etsy_publish_outcome_reaches_the_record(db):
    assert db.upsert_listing("s1", {"title": "Denim jacket", "marketplaces": {}},
                             status="draft", user_id="u1")
    outcome = PublishOutcome(ok=True, listing_id="E999", status="published",
                             url="https://www.etsy.com/listing/E999")

    def _fold(data: dict) -> dict:
        return marketplace_state.merge_state(data, "etsy", outcome)

    assert db.mutate_listing_data("s1", _fold, status="published", user_id="u1")
    etsy = _stored(db, "s1")["marketplaces"].get("etsy") or {}
    assert etsy.get("listing_id") == "E999", "the Etsy listing id was never saved"
    assert etsy.get("status") == "published"
    assert etsy.get("url") == "https://www.etsy.com/listing/E999"


def test_a_failed_attempts_error_reaches_the_record(db):
    db.upsert_listing("s1", {"title": "Denim jacket", "marketplaces": {}},
                      status="draft", user_id="u1")
    outcome = PublishOutcome(ok=False, message="Etsy needs a shipping profile.")
    db.mutate_listing_data(
        "s1", lambda d: marketplace_state.merge_state(d, "etsy", outcome))
    etsy = _stored(db, "s1")["marketplaces"].get("etsy") or {}
    assert etsy.get("error") == "Etsy needs a shipping profile."


def test_an_entry_edited_in_place_is_written(db):
    """The inventory mirror's shape: take the entry that is there and change
    one field of it, the way _mark does after an eBay sale."""
    db.upsert_listing("s1", {"title": "Denim jacket", "marketplaces": {
        "etsy": {"status": "published", "listing_id": "E1", "error": ""}}},
        status="published", user_id="u1")

    def _mark(stored: dict) -> dict:
        stored["marketplaces"]["etsy"]["status"] = "ended"
        return stored

    db.mutate_listing_data("s1", _mark, user_id="u1")
    assert _stored(db, "s1")["marketplaces"]["etsy"]["status"] == "ended", (
        "an ended Etsy copy still reads as live")


def test_the_mutate_cannot_reach_the_loaded_value(db):
    """What `mutate` receives is its own. Whatever it does to the dict it is
    handed, the comparison that decides the write is against what was read."""
    db.upsert_listing("s1", {"title": "Denim jacket",
                             "item_specifics": [{"name": "Size", "value": "M"}]},
                      status="draft", user_id="u1")

    def _edit(data: dict) -> dict:
        data["item_specifics"][0]["value"] = "L"
        return data

    db.mutate_listing_data("s1", _edit)
    assert _stored(db, "s1")["item_specifics"][0]["value"] == "L"


def test_a_top_level_edit_is_still_written(db):
    db.upsert_listing("s1", {"title": "Denim jacket"}, status="draft",
                      user_id="u1")
    db.mutate_listing_data("s1", lambda d: {**d, "offer_sent_at": "2026-09-24"})
    assert _stored(db, "s1")["offer_sent_at"] == "2026-09-24"
