"""Changing one field must not ship a stale copy of all the others.

`POST /api/save/{id}` is a full REPLACE, and it has to be — clearing a
subtitle is done by sending the listing without one. The problem is what got
built on top of it: the drafts strip changes a shipping policy or a category
by spreading the whole listing it happens to be holding and sending that.

The listing it is holding came from the last `/api/listings` load. So a title
fixed in the editor in another tab, a price corrected on a phone, or anything
a background store sync pulled in since is overwritten by the strip's older
copy the moment somebody picks a shipping policy from a card. The strip
refreshes after each of its own saves, which narrows the window to whatever
happened elsewhere — not to nothing.

The same reasoning already produced `PATCH /api/listings/{id}/images/order`:
"a reorder could overwrite a title edit made in another tab with a stale
copy". This is that endpoint for ordinary fields. A patch says what changed
and nothing else, so there is no stale copy to send.

The allowed set is deliberately small: the fields the card controls actually
offer. A patch route that accepts anything is a full replace with extra steps
— the caller can just send every field and reintroduce the bug.

`listing_format` joined that set for the selling-format picker on a draft
card, and it is the one member with a rule of its own: an unrecognised value
does not fail, it publishes. The Trading request branches on
`startswith("AUCTION")`, so "auctoin" goes out as a Buy It Now at whatever
`price` holds and nothing anywhere says so.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

STORED = {
    "id": "lst1", "user_id": "u1", "status": "draft",
    "listing": {"title": "The newer title someone just fixed",
                "price": 30.0, "quantity": 2,
                "category_id": "111", "fulfillment_policy_id": "FP-old"},
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    saved: dict = {}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        {**STORED, "listing": dict(STORED["listing"])}
                        if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda lid, data, **k: saved.update(data) or True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    return TestClient(main.app), saved


def test_a_patch_changes_only_what_it_names(api):
    """The finding: this used to arrive as the whole listing, title included,
    from whenever the strip last loaded."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"fulfillment_policy_id": "FP-new"})

    assert resp.status_code == 200, resp.text
    assert saved["fulfillment_policy_id"] == "FP-new"
    assert saved["title"] == "The newer title someone just fixed", \
        "a shipping-policy change overwrote the stored title"
    assert saved["price"] == 30.0


def test_the_patched_field_is_queued_for_ebay(api):
    """A live listing's shipping policy changed here has to actually reach
    eBay on the next revise, and the revise only sends dirty fields."""
    client, saved = api
    client.patch("/api/listings/lst1", json={"category_id": "222"})

    assert "category_id" in saved["dirty_fields"]


def test_the_merged_listing_comes_back(api):
    """So the caller can update its cache from the answer instead of from the
    copy it already had — which is the copy that was stale."""
    client, _ = api
    body = client.patch("/api/listings/lst1",
                        json={"category_id": "222"}).json()

    assert body["listing"]["category_id"] == "222"
    assert body["listing"]["title"] == "The newer title someone just fixed"


def test_a_field_outside_the_allowed_set_is_refused(api):
    """A patch route that accepts anything is a full replace with extra steps:
    the caller sends every field and the lost update is back."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"title": "from a stale tab"})

    assert resp.status_code == 400, resp.text
    assert saved == {}


def test_an_empty_patch_is_refused(api):
    client, saved = api
    assert client.patch("/api/listings/lst1", json={}).status_code == 400
    assert saved == {}


def test_a_stranger_cannot_patch_someone_elses_listing(api, monkeypatch):
    from backend import main

    client, saved = api
    monkeypatch.setattr(main, "_uid", lambda _r: "someone-else")

    resp = client.patch("/api/listings/lst1",
                        json={"category_id": "222"})
    assert resp.status_code == 404, resp.text
    assert saved == {}


def test_a_patch_that_did_not_commit_is_not_reported_as_saved(api, monkeypatch):
    from backend import db

    client, _ = api
    monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: False)

    resp = client.patch("/api/listings/lst1", json={"category_id": "222"})
    assert resp.status_code == 503, resp.text


def test_a_value_the_model_rejects_is_a_client_error(api):
    """Not a 500. The patch still goes through the Listing model, so a
    malformed value is caught before it reaches storage."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"category_id": {"not": "a category"}})

    assert 400 <= resp.status_code < 500, resp.text
    assert saved == {}


# --- the selling format ------------------------------------------------
#
# Buy It Now, auction, or both, chosen from the card rather than from inside
# the editor. The format and the starting bid travel together: an auction is
# priced by its opening bid, so a route that took one without the other would
# let a card switch a draft to a format it could not then finish.


def test_the_selling_format_can_be_changed_from_a_card(api):
    client, saved = api
    resp = client.patch("/api/listings/lst1", json={"listing_format": "AUCTION"})

    assert resp.status_code == 200, resp.text
    assert saved["listing_format"] == "AUCTION"
    assert saved["title"] == "The newer title someone just fixed"


def test_the_starting_bid_can_be_set_from_a_card(api):
    """Without this the picker sets a format the card cannot then price, and
    the seller has to open the editor anyway."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"listing_format": "AUCTION_BIN",
                              "auction_start_price": 0.99})

    assert resp.status_code == 200, resp.text
    assert saved["listing_format"] == "AUCTION_BIN"
    assert saved["auction_start_price"] == 0.99
    # The Buy It Now price is untouched by the switch: it is what the seller
    # wants for the item, and the opening bid is where they let it start.
    assert saved["price"] == 30.0


def test_a_format_we_cannot_publish_is_refused(api):
    """The failure this guards is silent, which is why it is guarded at all:
    an unrecognised format is not rejected by eBay, it is published as a Buy
    It Now."""
    client, saved = api
    resp = client.patch("/api/listings/lst1", json={"listing_format": "auctoin"})

    assert resp.status_code == 400, resp.text
    assert saved == {}


def test_a_format_is_stored_as_the_publisher_reads_it(api):
    """Case and stray whitespace are the caller's, not a different format."""
    client, saved = api
    resp = client.patch("/api/listings/lst1", json={"listing_format": " auction "})

    assert resp.status_code == 200, resp.text
    assert saved["listing_format"] == "AUCTION"


def test_the_format_is_queued_for_ebay(api):
    """Marked dirty like every other patched field. eBay will not revise a
    live listing's format -- services/ebay_trading.REVISABLE_FIELDS leaves it
    out -- and `unsendable_revise_fields` is what tells the seller so. It can
    only do that if the edit was recorded."""
    client, saved = api
    client.patch("/api/listings/lst1", json={"listing_format": "AUCTION"})

    assert "listing_format" in saved["dirty_fields"]


def test_the_auction_length_stays_a_full_editor_field(api):
    """Not every field the format uses belongs on a card. The length defaults
    to seven days and is chosen in the editor; the card would be a third
    control on an already-dense tile."""
    client, saved = api
    resp = client.patch("/api/listings/lst1", json={"auction_duration": "DAYS_10"})

    assert resp.status_code == 400, resp.text
    assert saved == {}
