"""Fill in a listing's blanks while it is still a draft — the last step before
it is published.

This is the same enrichment "Enrich all" runs from the dashboard
(`POST /api/listings/enrich`), moved to where it can actually land. There it
has to reach listings that are ALREADY live on eBay, and that path has four
separate ways to come back with every blank still blank: no category eBay
agrees with, photos that live on eBay rather than on this server, no connected
account, and a ReviseItem eBay declines. A draft has none of them — nothing is
live, the photos are on disk, and the answer is saved locally.

What has to hold for a one-tap fill that spends AI credits:

  * it enriches the listing in the REQUEST, not an older saved copy. The
    editor is open when this is pressed, so a fill that read from disk would
    hand back the listing as it was before the seller's last few edits and
    the form would adopt it — losing them;

  * what it filled is recorded as CHANGED. A listing already live on eBay is
    revised with only its dirty fields, so specifics filled here and left
    unmarked would be saved locally, reported as filled, and never reach the
    live listing — the exact silence `_enrich_one` has to mark_dirty around;

  * a pass that never ran is not billed and does not claim to have filled
    anything. `_enrich_listing` swallows its own failures and returns None
    for "didn't run", which is a different answer from "ran and found
    nothing"; and

  * another seller's session id is not a key to their listing;

  * and it runs as a JOB. One vision call over every photo plus a maker check
    routinely outlives the 90 seconds the client waits on a request, so the
    fill kept finishing and saving on the server after the editor had
    reported "Couldn't fill in the details" -- a working feature, reported
    broken. Everything that can refuse still refuses in the request, before
    the charge; the answer is the job's result.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.models import ItemSpecific


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    # The fill itself is under test elsewhere (main._enrich_listing); what
    # this file is about is which listing it is handed, what is persisted, and
    # what is reported back.
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "finish@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("finish@example.com")["id"]
    return client, dbmod, uid


def _with_photo(rid: str) -> None:
    """A real file where the enrichment looks for one — the route refuses a
    listing whose photos are no longer on the server."""
    from PIL import Image
    path = main.storage.optimized_dir(rid) / "img_00.jpg"
    Image.new("RGB", (8, 8), "white").save(path, "JPEG")


def _fills_size(seen: list):
    """A stand-in for the AI pass: records the listing it was handed and adds
    one specific, exactly as the real one does when it finds something."""
    def _fill(listing, paths):
        seen.append(listing.title)
        listing.item_specifics.append(
            ItemSpecific(name="Size", value="M", confidence="high"))
        return 1
    return _fill


def _body(rid: str, **over) -> dict:
    listing = {"title": "Nike hoodie", "category_id": "11450",
               "images": ["img_00.jpg"], "missing_info": ["size"], **over}
    return {"session_id": rid, "listing": listing, "mode": "draft"}


def _finish(client, res, timeout: float = 10.0) -> dict:
    """The job the route started, once its worker is done: the status body
    (result or error), so a test can assert on either."""
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"enrich job {job_id} never finished")


def _result(client, res) -> dict:
    body = _finish(client, res)
    assert not body.get("error"), body["error"]
    return body["result"]


def test_it_fills_the_blanks_and_hands_the_listing_back(seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("draft-1", _body("draft-1")["listing"],
                                status="draft", user_id=uid)
    _with_photo("draft-1")
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))

    res = client.post("/api/enrich/draft-1", json=_body("draft-1"))

    body = _result(client, res)
    assert body["added"] == 1
    names = [s["name"] for s in body["listing"]["item_specifics"]]
    assert "Size" in names
    # And WHAT it filled, by name and value: the evidence a seller who could
    # not tell whether the button did anything was asking for.
    assert body["filled"] == [{"name": "Size", "value": "M"}]
    # The note the fill answered stops being asked, so the dashboard's own
    # "Fill in details" suggestion doesn't sit there reading like a no-op.
    assert body["settled"] == 1
    assert body["listing"]["missing_info"] == []


def test_it_enriches_the_listing_in_front_of_the_seller(seller, monkeypatch):
    """The editor is open. The copy on disk is older than the copy on screen,
    and the answer is adopted straight into the form — so reading from disk
    would hand back a listing missing the seller's last few edits."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("draft-2", _body("draft-2")["listing"],
                                status="draft", user_id=uid)
    _with_photo("draft-2")
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_size(seen))

    res = client.post("/api/enrich/draft-2",
                      json=_body("draft-2", title="Nike hoodie, navy, large"))

    body = _result(client, res)
    assert seen == ["Nike hoodie, navy, large"]
    assert body["listing"]["title"] == "Nike hoodie, navy, large"
    stored = dbmod.get_listing("draft-2")["listing"]
    assert stored["title"] == "Nike hoodie, navy, large"


def test_what_it_filled_is_marked_as_changed(seller, monkeypatch):
    """A listing eBay is already showing is revised with only its dirty
    fields. Unmarked, the fill would be saved here, reported as done, and
    never reach the live listing."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("live-1", _body("live-1")["listing"],
                                status="published", user_id=uid)
    _with_photo("live-1")
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))

    res = client.post("/api/enrich/live-1", json=_body("live-1"))

    _result(client, res)
    assert "item_specifics" in dbmod.get_listing("live-1")["listing"]["dirty_fields"]


def test_filling_a_live_listing_does_not_demote_it(seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("live-2", _body("live-2")["listing"],
                                status="published", user_id=uid)
    _with_photo("live-2")
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))

    _result(client, client.post("/api/enrich/live-2", json=_body("live-2")))
    assert dbmod.get_listing("live-2")["status"] == "published"


def test_a_pass_that_never_ran_is_not_reported_as_a_fill(seller, monkeypatch):
    """None means the enrichment didn't run at all — no taxonomy, no model, no
    aspects for this category. Nothing was earned, so nothing is claimed."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("draft-3", _body("draft-3")["listing"],
                                status="draft", user_id=uid)
    _with_photo("draft-3")
    monkeypatch.setattr(main, "_enrich_listing", lambda listing, paths: None)

    res = client.post("/api/enrich/draft-3", json=_body("draft-3"))

    body = _finish(client, res)
    assert "category" in (body.get("error") or "").lower()
    assert not body.get("result")


def test_a_listing_with_no_photos_left_says_so(seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("draft-4", _body("draft-4")["listing"],
                                status="draft", user_id=uid)
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))

    res = client.post("/api/enrich/draft-4", json=_body("draft-4"))

    assert res.status_code == 400
    assert "photos" in res.json()["detail"].lower()


def test_a_listing_with_no_category_is_not_billed_for_the_discovery(seller,
                                                                    monkeypatch):
    """Item specifics are per category, so without one there is nothing to
    fill. Refused before the charge, not after."""
    client, dbmod, uid = seller
    body = _body("draft-5", category_id="")
    assert dbmod.upsert_listing("draft-5", body["listing"],
                                status="draft", user_id=uid)
    _with_photo("draft-5")
    charged: list = []
    monkeypatch.setattr(main, "_charge_ai",
                        lambda request, feature, units=1: charged.append(feature))
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))

    res = client.post("/api/enrich/draft-5", json=body)

    assert res.status_code == 400
    assert charged == []


def test_another_sellers_session_is_not_theirs_to_fill(seller, monkeypatch):
    """Session ids appear in media URLs and can leak; possession of one must
    not grant write access to someone else's listing."""
    client, dbmod, uid = seller
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", _body("theirs")["listing"],
                                status="draft", user_id=other["id"])
    _with_photo("theirs")
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_size(seen))

    res = client.post("/api/enrich/theirs", json=_body("theirs"))

    assert res.status_code == 404
    assert seen == []
