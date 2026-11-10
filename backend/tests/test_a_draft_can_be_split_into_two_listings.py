"""Splitting a draft that was written for two items.

The uploader's bulk toggle was left off, every photo of two items went into
one upload, and the AI wrote one listing describing both. Merge has an
inverse now: the seller names the photos that belong to the other item, those
move to a brand-new draft the AI writes from them, and -- unless the seller
says otherwise -- the AI rewrites this one from the photos it keeps, because
its title and description were about both.

What that must not do: touch a listing that is on a marketplace, leave either
draft without a photo, charge for a chain after a photo has moved, or lose
what the seller told the AI about the pile.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main, objstore, ratelimit, storage  # noqa: E402


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "split@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("split@example.com")["id"]
    monkeypatch.setattr(objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(objstore, "restore", lambda key, path: False)
    monkeypatch.setattr(main.config, "vision_ready", lambda: True)
    return client, dbmod, uid


@pytest.fixture()
def chains(monkeypatch):
    """The identify chains started, as (session, uid, charge); the AI itself
    never runs here."""
    started = []

    def start(session_id, uid, spent):
        started.append((session_id, uid, spent))
        return f"job-{len(started)}"

    monkeypatch.setattr(main, "_start_identify_job", start)
    return started


BOTH = {
    "title": "Blue polo and a vintage champagne bottle", "price": 30.0,
    "description": "A polo. Also a bottle.", "condition": "USED_GOOD",
    "images": ["a.jpg", "b.jpg", "c.jpg"], "quantity": 1,
    "package_weight_lb": 1, "package_weight_oz": 4,
    "fulfillment_policy_id": "fp-1", "store_category_id": "sc-9",
    "item_specifics": [{"name": "Brand", "value": "Lacoste"}],
}


def _put(dbmod, uid, rid, listing=BOTH, status="draft",
         photos=("a.jpg", "b.jpg", "c.jpg"), notes="", item_notes=""):
    d = storage.optimized_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    for name in photos:
        (d / name).write_bytes(b"jpeg-" + name.encode())
    storage.save_notes(rid, notes)
    storage.save_item_notes(rid, item_notes)
    assert dbmod.upsert_listing(rid, listing, status=status, user_id=uid)
    return rid


def _split(client, rid, **body):
    return client.post(f"/api/listings/{rid}/split",
                       json={"photos": ["c.jpg"], **body})


def test_the_other_items_photos_move_to_a_new_draft_the_ai_writes(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "two-in-one", notes="estate sale pile",
               item_notes="a polo and a bottle")

    resp = _split(client, rid, notes="the bottle is a 1970s Perrier-Jouët",
                  keep_notes="the polo is a men's L")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_id = body["id"]
    assert new_id != rid and body["from"] == rid
    assert body["photos"] == 1 and body["remaining"] == ["a.jpg", "b.jpg"]
    # The photo is the new draft's now, and no longer this one's: in the
    # records and on the disk.
    row = dbmod.get_listing(new_id)
    assert row["status"] == "draft" and row["user_id"] == uid
    assert row["listing"]["images"] == ["c.jpg"]
    assert (storage.optimized_dir(new_id) / "c.jpg").read_bytes() == b"jpeg-c.jpg"
    original = dbmod.get_listing(rid)["listing"]
    assert original["images"] == ["a.jpg", "b.jpg"]
    assert not (storage.optimized_dir(rid) / "c.jpg").exists()
    assert (storage.optimized_dir(rid) / "a.jpg").is_file()
    # The new draft starts with the seller's own settings and none of the
    # text that was written for both items -- that is the chain's to write.
    copy = row["listing"]
    assert copy["fulfillment_policy_id"] == "fp-1"
    assert copy["store_category_id"] == "sc-9"
    assert copy["package_weight_lb"] == 1 and copy["package_weight_oz"] == 4
    assert copy["title"] == "" and copy["description"] == ""
    assert copy["item_specifics"] == [] and copy["price"] in (None, 0, 0.0)
    # Both chains run: the new draft from its photo, this one from the two it
    # keeps -- and the client is told which job is which.
    assert [(s, u) for s, u, _ in chains] == [(new_id, uid), (rid, uid)]
    assert body["jobs"] == {"new": "job-1", "original": "job-2"}
    # What the seller told the AI about the pile goes with the photos, and
    # what they said about each item is written where its chain reads it.
    assert storage.load_notes(new_id) == "estate sale pile"
    assert storage.load_item_notes(new_id) == "the bottle is a 1970s Perrier-Jouët"
    assert storage.load_item_notes(rid) == "the polo is a men's L"


def test_without_the_rewrite_this_listing_keeps_its_text(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "keep-text", item_notes="a polo and a bottle")

    resp = _split(client, rid, redraft=False, keep_notes="the polo")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    original = dbmod.get_listing(rid)["listing"]
    assert original["images"] == ["a.jpg", "b.jpg"]
    assert original["title"] == BOTH["title"]
    # One chain, for the new draft only; this one's note is left alone too,
    # since nothing is going to read it.
    assert [s for s, _, _ in chains] == [body["id"]]
    assert body["jobs"] == {"new": "job-1"}
    assert storage.load_item_notes(rid) == "a polo and a bottle"


def test_the_photos_are_named_in_the_listings_own_order(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "ordered", photos=("a.jpg", "b.jpg", "c.jpg", "d.jpg"),
               listing={**BOTH, "images": ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]})

    body = client.post(f"/api/listings/{rid}/split",
                       json={"photos": ["d.jpg", "b.jpg", "nope.jpg"]}).json()

    assert dbmod.get_listing(body["id"])["listing"]["images"] == ["b.jpg", "d.jpg"]
    assert body["remaining"] == ["a.jpg", "c.jpg"]


def test_both_drafts_keep_at_least_one_photo(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "all-or-nothing")

    none = client.post(f"/api/listings/{rid}/split", json={"photos": []})
    every = client.post(f"/api/listings/{rid}/split",
                        json={"photos": ["a.jpg", "b.jpg", "c.jpg"]})
    unknown = client.post(f"/api/listings/{rid}/split", json={"photos": ["x.jpg"]})

    assert none.status_code == 400 and every.status_code == 400
    assert unknown.status_code == 400
    assert dbmod.get_listing(rid)["listing"]["images"] == ["a.jpg", "b.jpg", "c.jpg"]
    assert chains == []


def test_a_listing_on_a_marketplace_is_not_split(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "live-1",
               listing={**BOTH, "ebay_listing_id": "111"}, status="published")

    resp = _split(client, rid)

    assert resp.status_code == 409
    assert "draft" in resp.json()["detail"]
    assert dbmod.get_listing(rid)["listing"]["images"] == ["a.jpg", "b.jpg", "c.jpg"]
    assert (storage.optimized_dir(rid) / "c.jpg").is_file()
    assert chains == []


def test_someone_elses_draft_is_a_404(seller, chains):
    client, dbmod, uid = seller
    rid = _put(dbmod, "someone-else", "theirs")

    assert _split(client, rid).status_code == 404
    assert chains == []


def test_a_broke_seller_is_refused_before_a_photo_moves(seller, chains,
                                                        monkeypatch):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "broke")
    asked = []

    def charge(request, feature, units=1):
        asked.append(feature)
        if len(asked) == 2:   # the rewrite's charge: out of tokens
            raise HTTPException(402, "Not enough tokens.")
        return {"ok": True, "entry_id": f"e{len(asked)}", "user_id": uid}

    given_back = []
    monkeypatch.setattr(main.deps, "charge_ai", charge)
    monkeypatch.setattr(main.tokens, "refund",
                        lambda r, units=None: given_back.append(r) or True)

    resp = _split(client, rid)

    assert resp.status_code == 402
    # The first charge went back, nothing moved, nothing was started.
    assert [r["entry_id"] for r in given_back] == ["e1"]
    assert dbmod.get_listing(rid)["listing"]["images"] == ["a.jpg", "b.jpg", "c.jpg"]
    assert (storage.optimized_dir(rid) / "c.jpg").is_file()
    assert chains == []
