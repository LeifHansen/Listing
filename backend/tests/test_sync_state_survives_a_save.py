"""A save must not be able to erase the sync's own bookkeeping.

`remote_shadow` is what eBay last told us a listing said. It is the BASE the
three-way merge reconciles against, and without it the merge deliberately does
nothing — the local copy stands. `conflicts` is the record of fields the
seller and eBay both changed, which is what keeps those fields out of a
revise.

Neither was in SERVER_OWNED_FIELDS, so `POST /api/save/{id}` took whatever the
client sent. Today's editor happens to echo them back (`fromListing` spreads
the whole stored listing), but that is precisely the assumption
_restore_server_state exists to refuse: "any client round-trip can be stale — a
second browser tab, or the editor's image-edit auto-save whose copy was loaded
before a publish".

A tab opened before the first sync holds a listing with no shadow. Saving from
it wipes the shadow, and the next sync then reconciles nothing — so a title
the seller fixed in Seller Hub is silently ignored again, which is the whole
bug the shadow was added to fix. The same save would clear a recorded conflict
and make the field sendable, resolving it in the local copy's favour without
asking.

These are server-maintained fields. Only the sync writes the shadow, and only
the sync and the resolve endpoint write conflicts — so a stored value always
wins over anything a client sends.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

SHADOW = {"title": "Blue lamp", "price": 25.0}
CONFLICT = {"title": {"local": "Mine", "remote": "Theirs"}}

STORED = {
    "id": "lst1", "user_id": "u1", "status": "published",
    "listing": {"title": "Blue lamp", "price": 25.0, "quantity": 1,
                "source": "ebay", "ebay_listing_id": "110001",
                "remote_shadow": SHADOW, "conflicts": CONFLICT},
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    saved: dict = {}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        {**STORED, "listing": dict(STORED["listing"])}
                        if lid == "lst1" else None)
    monkeypatch.setattr(db, "get_listing_strict", lambda lid: None)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda lid, data, **k: saved.update(data) or True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    return TestClient(main.app), saved


def _stale_save(client, **over):
    """What a tab loaded before the first sync would post: no shadow, no
    conflicts, because its copy predates both."""
    body = {"title": "Blue lamp", "price": 25.0, "quantity": 1}
    body.update(over)
    return client.post("/api/save/lst1", json=body)


def test_a_stale_save_cannot_erase_the_sync_base(api):
    """The finding. Without the shadow the merge reconciles nothing, so a
    title fixed in Seller Hub is silently ignored — the exact bug the shadow
    exists to fix."""
    client, saved = api
    assert _stale_save(client).status_code == 200

    assert saved["remote_shadow"] == SHADOW, \
        "a save wiped the base the three-way merge reconciles against"


def test_a_stale_save_cannot_erase_a_recorded_conflict(api):
    """Clearing it makes the field sendable again, which resolves the
    conflict in the local copy's favour without asking — the behaviour the
    merge was written to remove."""
    client, saved = api
    _stale_save(client)

    assert saved["conflicts"] == CONFLICT


def test_the_seller_s_actual_edits_still_save(api):
    """The guard is narrow: it protects two bookkeeping fields, not the
    listing."""
    client, saved = api
    _stale_save(client, title="A better title", price=30.0)

    assert saved["title"] == "A better title"
    assert saved["price"] == 30.0


def test_a_first_save_can_still_establish_them(api, monkeypatch):
    """A stored blank leaves the client's value alone, so nothing is frozen
    out on a record that has none yet — same rule as every other
    server-owned field."""
    from backend import db

    client, saved = api
    monkeypatch.setattr(db, "get_listing", lambda lid: {
        "id": "lst1", "user_id": "u1", "status": "draft",
        "listing": {"title": "Blue lamp"}})

    _stale_save(client, remote_shadow=SHADOW)
    assert saved["remote_shadow"] == SHADOW


def test_a_publish_does_not_clear_the_question_either(monkeypatch):
    """The publish path restores the same fields (_with_stored_identity), and
    it must: a revise deliberately does NOT send a conflicted field, so the
    conflict is still open afterwards and the seller still has to answer it.
    Clearing it on a successful publish would lose the question and make the
    field sendable on the next edit."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext
    from backend.models import Listing

    monkeypatch.setattr(ebay_provider.db, "get_listing",
                        lambda _sid: {**STORED, "listing": dict(STORED["listing"])})

    # What a client would post: its own copy, with neither field.
    ctx = PublishContext(
        session_id="lst1", listing=Listing(title="Blue lamp", price=25.0),
        mode="live", base_url="https://app.example", uid="u1", prev_record={})

    out = ebay_provider._with_stored_identity(ctx)

    assert out.listing.conflicts == CONFLICT
    assert out.listing.remote_shadow == SHADOW
