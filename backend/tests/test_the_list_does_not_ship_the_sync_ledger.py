"""GET /api/listings sent 18 MiB to a phone, half of it server bookkeeping.

The audit's P1-10 says the route "can return up to 3,000 full JSON records"
and asks for server-side projection. Measured on a realistic store -- a
listing with a description, twelve photos, eighteen item specifics and a
synced remote shadow -- one record serialises to about 6.2 KB, so a seller at
`LISTING_LIST_CAP` downloads roughly **17.8 MiB**, on the busiest route in the
app, on the mobile build this ships as.

Just under half of that is `remote_shadow`: a complete second copy of the
listing, recording what eBay last said it contained. It is the base the
three-way merge reconciles against and it is read only by the server. It has
never been rendered, and `dirty_fields` -- the sync's other ledger, the set of
fields edited since that shadow -- has not either.

So the list omits both. Not an allowlist: a projection that names what the
client MAY see fails by dropping a field the UI needs, and that failure is
silent and looks like missing data. A short, justified omission list fails the
other way -- a new field is merely bigger than it had to be -- and that is the
right direction to be wrong in.

`GET /api/listings/{id}` is untouched and still answers with everything. The
distinction the route now draws is between browsing a store and opening one
listing, which is also the distinction the payload should have drawn.

The last test here is the one that keeps this honest: it reads the frontend
source and fails if any omitted field is referenced there at all. That is what
makes the omission a fact about the app rather than an assumption about it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main
from backend import ratelimit

FRONTEND_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"

SHADOW = {"title": "What eBay last told us", "price": 39.0,
          "description": "<p>The seller-hub copy.</p>"}


@pytest.fixture()
def seller(dbmod):
    """One signed-in seller with one synced listing, through the real app.

    `dbmod` (conftest) binds the db module to a scratch SQLite file with the
    real schema, so this exercises the actual query and serialisation path
    rather than a double's idea of one.
    """
    db = dbmod
    # Signups are rate limited per client IP, and every test here signs one
    # up: run after enough of the suite in one process and the fixture 429s
    # instead of the assertions running. Clearing the window is the test
    # isolating itself, not the limit being weakened -- it stays in force for
    # the request under test.
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "shadow@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    uid = db.get_user_by_email("shadow@example.com")["id"]
    db.upsert_listing("sess-1", {
        "title": "Nike Windbreaker", "price": 48.0, "brand": "Nike",
        "description": "<p>Bold colourblock, full zip.</p>",
        "images": ["https://i.ebayimg.com/images/g/AAA/s-l1600.jpg"],
        "image_urls": ["https://i.ebayimg.com/images/g/AAA/s-l1600.jpg"],
        "item_specifics": [{"name": "Brand", "value": "Nike"},
                           {"name": "Size", "value": "L"}],
        "ebay_listing_id": "110011223344",
        "remote_shadow": dict(SHADOW),
        "dirty_fields": ["price"],
        "conflicts": {"title": {"local": "Nike Windbreaker",
                              "remote": "Nike Windbreaker Jacket"}},
    }, status="published", user_id=uid)
    yield client
    db.delete_listing("sess-1", user_id=uid)


def _the_listing(client) -> dict:
    r = client.get("/api/listings")
    assert r.status_code == 200, r.text
    items = r.json()["listings"]
    assert len(items) == 1
    return items[0]["listing"]


def test_the_list_does_not_carry_the_remote_shadow(seller):
    assert "remote_shadow" not in _the_listing(seller)


def test_the_list_does_not_carry_the_dirty_ledger(seller):
    assert "dirty_fields" not in _the_listing(seller)


def test_the_shadow_is_not_merely_blanked(seller):
    """A present-but-empty key would still cost bytes and would read, to a
    client, as "eBay has told us nothing" -- which is the state that makes the
    merge stand down. Absent means absent."""
    body = json.dumps(_the_listing(seller))
    assert "remote_shadow" not in body
    assert "The seller-hub copy" not in body


def test_everything_the_list_view_actually_renders_survives(seller):
    listing = _the_listing(seller)
    for field in ("title", "price", "brand", "description", "images",
                  "image_urls", "item_specifics", "ebay_listing_id"):
        assert field in listing, f"the list stopped sending {field}"


def test_a_conflict_still_reaches_the_store(seller):
    """`conflicts` is bookkeeping too, but the seller has to SEE it: an
    unanswered conflict is an edit that never reaches eBay. It stays."""
    assert _the_listing(seller)["conflicts"]


def test_opening_one_listing_still_answers_with_everything(seller):
    r = seller.get("/api/listings/sess-1")
    assert r.status_code == 200, r.text
    full = r.json()["listing"]
    assert full["remote_shadow"] == SHADOW
    assert full["dirty_fields"] == ["price"]


def test_the_omitted_fields_are_ones_the_ui_never_reads():
    """The claim this projection rests on, checked against the real source.

    If a view starts rendering one of these, dropping it from the list is no
    longer a size optimisation -- it is a feature that works when you open a
    listing and not when you browse. Reading the frontend is how that gets
    noticed here rather than in someone's store.
    """
    sources = [p for p in FRONTEND_SRC.rglob("*.js*") if ".test." not in p.name]
    assert sources, "no frontend sources found — this test cannot vouch for anything"
    blob = "\n".join(p.read_text(errors="replace") for p in sources)
    for field in main.LIST_OMITTED_LISTING_FIELDS:
        assert field not in blob, (
            f"the frontend now references {field!r}, which GET /api/listings "
            f"no longer sends — either stop omitting it or stop reading it")


def test_saving_a_record_that_came_from_the_list_keeps_the_shadow(seller):
    """The one real risk in projecting: a round trip that erases what it omitted.

    The seller opens the app (list), edits a price, saves. The browser sends
    back the listing it was given -- which no longer carries the shadow or the
    dirty ledger. If either were taken from the request, browsing the store
    and saving would quietly destroy the base the next sync merges against,
    and the seller's fix on etsy.com or in Seller Hub would be silently
    reverted. That is the exact bug SERVER_OWNED_FIELDS was added to prevent
    (a shadow-less tab erasing the base), reached from a new direction.

    It does not happen, and this is why: the shadow is server-owned, so the
    stored value wins over anything a client sends or omits, and the dirty
    ledger accumulates from the stored marks plus a fresh diff, ignoring the
    client's list entirely. Both were already true; projecting the list makes
    the app depend on them, so they are asserted here rather than assumed.
    """
    from_list = _the_listing(seller)
    assert "remote_shadow" not in from_list        # the premise
    from_list["price"] = 44.0                      # the edit

    r = seller.post("/api/save/sess-1", json=from_list)
    assert r.status_code == 200, r.text

    after = seller.get("/api/listings/sess-1").json()["listing"]
    assert after["remote_shadow"] == SHADOW
    assert after["price"] == 44.0
    assert "price" in (after.get("dirty_fields") or [])


def test_no_read_route_leaks_the_shadow_by_another_door(seller):
    """The projection is on one route; the rule is about all of them.

    Several routes read the same capped store list -- duplicates, insights,
    the awaiting-shipment list, the sold archive -- and each builds its own
    answer. Today they all project by hand, and none echoes a record back
    whole. That is easy to undo: one `return {"listings": items}` added to a
    panel and the 3 KB comes back on a different path, where nobody would
    think to look for it.

    So this sweeps every GET the API exposes and fails if the shadow appears
    in any of them. A route that legitimately needs it would be a route that
    hands the merge base to a client, which is not a thing this app does.
    """
    # The one route that is meant to: opening a single listing hands the
    # editor and the merge dialog everything, which is the distinction this
    # whole change draws. Named here so the exemption is visible rather than
    # implied by a sweep that quietly skips path parameters.
    BY_DESIGN = {"/api/listings/{listing_id}"}

    checked = 0
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or "GET" not in (
                getattr(route, "methods", None) or set()):
            continue
        if path in BY_DESIGN:
            continue
        url = path.replace("{listing_id}", "sess-1").replace(
            "{session_id}", "sess-1")
        if "{" in url:
            continue          # needs an id this test has no meaningful value for
        res = seller.get(url)
        checked += 1
        if res.status_code >= 400:
            continue          # unconfigured marketplace, missing creds: fine
        assert "remote_shadow" not in res.text, f"{url} answers with the shadow"
        assert "The seller-hub copy" not in res.text, f"{url} leaks the shadow's contents"
    assert checked >= 20, f"the sweep only reached {checked} routes"
