"""Saying "I've read that guess" without opening the listing.

A draft card carries a "N to review" chip: the item specifics the AI inferred
rather than read off the item. They publish perfectly well — they are simply
the ones most likely to be wrong — and the only way to answer them was to open
the listing. A seller working a grid of twenty fresh drafts was making twenty
round trips into the editor to say twenty times that the guess was fine.

The route that ends those trips has one rule worth a test file: the card sends
the ASPECT NAMES it has read, never the specifics list it is holding. That
list came from the last `/api/listings` load, and the same reasoning that
produced `PATCH /api/listings/{id}` applies with more force here, because this
listing has a guaranteed concurrent writer — "Enrich all" fills specifics on
drafts in a worker thread, which is exactly the listing whose guesses the
seller is reading. A card that sent the whole array back would erase every row
that landed since it loaded, and it would do it at the moment the seller was
telling us the listing looked right.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

# Two AI guesses and a seller's own answer. "Features" holds two rows for one
# aspect — eBay's multi-selects are tick boxes, and the chip counts them once.
SPECIFICS = [
    {"name": "Brand", "value": "Levi's", "confidence": "medium"},
    {"name": "Colour", "value": "", "confidence": "medium"},
    {"name": "Colour", "value": "Navy", "confidence": "medium"},
    {"name": "Features", "value": "Distressed", "confidence": "medium"},
    {"name": "Features", "value": "Button Fly", "confidence": "medium"},
    {"name": "Department", "value": "Men", "confidence": ""},
]

STORED = {
    "id": "lst1", "user_id": "u1", "status": "draft",
    "listing": {"title": "Levi's 501 jeans", "price": 45.0,
                "item_specifics": [dict(s) for s in SPECIFICS]},
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    saved: dict = {}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        {**STORED, "listing": {
                            **STORED["listing"],
                            "item_specifics": [dict(s) for s in SPECIFICS]}}
                        if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)
    # No engine in a test, so the row-locked write reports "nothing written"
    # and the route falls back to saving the copy it read. Both paths end at
    # upsert_listing, which is what these tests read.
    monkeypatch.setattr(db, "mutate_listing_data", lambda *a, **k: None)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda lid, data, **k: saved.update(data) or True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    return TestClient(main.app), saved


def confirm(client, aspects):
    return client.post("/api/listings/lst1/specifics/confirm",
                       json={"aspects": aspects})


def rows(saved, name):
    return [s for s in saved["item_specifics"]
            if s["name"].strip().lower() == name.lower()]


def test_a_tick_clears_that_aspects_flag_and_nothing_else(api):
    client, saved = api
    assert confirm(client, [{"name": "Brand"}]).status_code == 200

    assert rows(saved, "Brand")[0]["confidence"] == ""
    assert rows(saved, "Brand")[0]["value"] == "Levi's", \
        "confirming a guess must not change the value it is confirming"
    assert [s["confidence"] for s in rows(saved, "Colour")] == ["medium", "medium"], \
        "a tick on Brand cleared an aspect the seller never looked at"


def test_one_tick_clears_every_row_of_a_multi_select(api):
    """eBay's tick-box specifics hold one row per ticked value, and the card
    shows ONE flag for the group — so one ✓ has to clear the group, or the
    count falls by one and the chip still says there is something to read."""
    client, saved = api
    assert confirm(client, [{"name": "Features"}]).status_code == 200

    assert [s["confidence"] for s in rows(saved, "Features")] == ["", ""]


def test_a_correction_lands_on_the_answer_row_not_the_empty_leftover(api):
    """Colour is stored as an empty leftover row followed by the real answer
    (a cleared field leaves its row behind). A writer that stopped at the
    first row would fill the blank and leave "Navy" sitting underneath it."""
    client, saved = api
    assert confirm(client, [{"name": "Colour", "value": "Black"}]).status_code == 200

    assert [s["value"] for s in rows(saved, "Colour")] == ["", "Black"]
    assert [s["confidence"] for s in rows(saved, "Colour")] == ["", ""]


def test_the_card_never_sends_the_specifics_list(api):
    """The finding this route exists for. The card holds whatever
    /api/listings last handed it; the server holds whatever has landed since.
    Naming the aspects means the ✓ is applied to the server's rows, so a
    background enrichment's work survives the seller pressing it."""
    client, saved = api
    body = confirm(client, [{"name": "Brand"}]).json()

    assert body["confirmed"] == 1
    assert [s["name"] for s in saved["item_specifics"]] == \
        [s["name"] for s in SPECIFICS], \
        "a confirmation dropped rows it was never told about"
    assert rows(saved, "Department")[0]["confidence"] == ""


def test_an_aspect_the_listing_does_not_have_is_added_only_with_a_value(api):
    """A ✓ on an aspect this listing no longer carries is nothing to record.
    A corrected VALUE for one is a seller answering it, and is kept."""
    client, saved = api
    assert confirm(client, [{"name": "Size", "value": "32"}]).status_code == 200
    assert rows(saved, "Size") == [{"name": "Size", "value": "32", "confidence": ""}]

    saved.clear()
    assert confirm(client, [{"name": "Material"}]).json()["confirmed"] == 0
    assert saved == {}, "a tick on an absent aspect wrote to the listing"


def test_the_change_is_queued_for_ebay(api):
    """A corrected specific has to reach a live listing on the next revise,
    and a revise only sends the fields the seller is known to have edited."""
    client, saved = api
    confirm(client, [{"name": "Colour", "value": "Black"}])

    assert "item_specifics" in saved["dirty_fields"]


def test_an_empty_confirmation_is_refused(api):
    client, saved = api
    assert client.post("/api/listings/lst1/specifics/confirm",
                       json={"aspects": []}).status_code == 400
    assert client.post("/api/listings/lst1/specifics/confirm",
                       json={"aspects": [{"name": "  "}]}).status_code == 400
    assert saved == {}


def test_a_listing_that_is_not_yours_is_not_found(api, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_uid", lambda _r: "someone-else")
    assert confirm(client := api[0], [{"name": "Brand"}]).status_code == 404
    assert client.post("/api/listings/nope/specifics/confirm",
                       json={"aspects": [{"name": "Brand"}]}).status_code == 404


def test_the_locked_write_is_the_one_a_database_gets(api, monkeypatch):
    """The fixture above stands in for "no row to lock", which is the fallback.
    With a database the write goes through db.mutate_listing_data, so the rows
    the ✓ is applied to are read back INSIDE the lock — the background
    enrichment cannot land between our read and our write."""
    from backend import db, main

    locked: dict = {}

    def fake_mutate(lid, mutate, **kwargs):
        # A row holding a specific that landed after the card loaded.
        stored = {**STORED["listing"],
                  "item_specifics": [dict(s) for s in SPECIFICS]
                  + [{"name": "Size", "value": "32", "confidence": "medium"}]}
        locked["out"] = mutate(stored)
        return locked["out"]

    monkeypatch.setattr(db, "mutate_listing_data", fake_mutate)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)

    body = confirm(api[0], [{"name": "Brand"}]).json()

    assert body["confirmed"] == 1
    names = [s["name"] for s in locked["out"]["item_specifics"]]
    assert "Size" in names, "the locked write dropped a row the card never saw"
    assert [s for s in locked["out"]["item_specifics"]
            if s["name"] == "Size"][0]["confidence"] == "medium", \
        "a tick on Brand also cleared a guess the seller has not seen"


def test_a_tick_on_a_flag_already_cleared_writes_nothing(api, monkeypatch):
    """The subtle half of the lock. mutate_listing_data answers None for "no
    database", "no such row", "the write failed" AND "nothing to change", and
    only the first two mean the locked row was never read.

    Treated as one, a ✓ on an aspect the row had already had cleared — by the
    enrichment thread, by another tab — fell through to the copy this request
    loaded, found the flag still set on it, and wrote that older copy back:
    the lost update the lock exists to stop, on the path least likely to be
    noticed."""
    from backend import db, main

    saved = api[1]
    read = {"n": 0}

    def fake_mutate(lid, mutate, **kwargs):
        read["n"] += 1
        # The row as it stands AFTER something else confirmed Brand.
        return mutate({**STORED["listing"], "item_specifics": [
            {"name": "Brand", "value": "Levi's", "confidence": ""}]})

    monkeypatch.setattr(db, "mutate_listing_data", fake_mutate)
    monkeypatch.setattr(main.storage, "save_listing",
                        lambda *a, **k: saved.update({"disk": True}))

    body = confirm(api[0], [{"name": "Brand"}]).json()

    assert read["n"] == 1
    assert body["confirmed"] == 0
    assert saved == {}, "a no-op confirmation wrote the request's older copy back"


def test_a_write_that_did_not_land_is_reported_as_one(api, monkeypatch):
    """Same three-way None, the other side of it: the row was read and there
    WAS something to change, so a None here is the write failing. Reporting
    that as a save is how a seller ticks a flag that comes back tomorrow."""
    from backend import db

    def fake_mutate(lid, mutate, **kwargs):
        mutate({**STORED["listing"],
                "item_specifics": [dict(s) for s in SPECIFICS]})
        return None   # the commit did not land

    monkeypatch.setattr(db, "mutate_listing_data", fake_mutate)

    assert confirm(api[0], [{"name": "Brand"}]).status_code == 503
