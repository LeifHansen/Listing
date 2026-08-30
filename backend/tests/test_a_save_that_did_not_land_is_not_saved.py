"""Three more places P0-06's rule had not reached.

`db.upsert_listing` swallows its failures and returns False; P0-06 made the
paths that DESTROY something check it. An inventory of every call site turned
up 26 that still drop the answer, most of them harmless (background mirrors
and job workers claim nothing). These three make a claim:

  * `POST /api/save/{id}` — the editor's save, the route every listing edit
    goes through — answered `{"saved": true}` regardless. The PATCH route
    directly beneath it in the same file already checks. A seller told their
    work is saved closes the tab.
  * `POST /api/{marketplace}/end-listing` — the Etsy/Depop twin of the eBay
    end route that was already fixed. The marketplace really did end the
    listing, so `ok` is not the lie; the lost write is, because the record
    still says `published` and the app goes on offering to revise something
    that is gone.
  * `POST /api/listings/merge` — the merged record's write is checked (a
    failure there must not delete the sources), but the source DELETES that
    follow are not, while the response reports `removed: source_ids` for all
    of them. A source that survived is a duplicate the seller was told had
    been consumed.

"Called, then enforced only when there is a database" is the shape used
throughout: `db.enabled() and not db.upsert_listing(...)` short-circuits, so
without a database the write would never run at all.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402


class FakeDb:
    """The parts of backend.db these routes touch. `landed` decides whether
    upsert_listing reports the write; `deleted` does the same for deletes."""

    def __init__(self, rows=None, landed=True, deleted=True):
        self.rows = rows or {}
        self.landed = landed
        self.deleted = deleted
        self.writes: list[str] = []
        self.deletes: list[str] = []

    def enabled(self):
        return True

    def get_listing(self, rid):
        return self.rows.get(rid)

    def get_listing_strict(self, rid):
        return self.rows.get(rid)

    def upsert_listing(self, rid, data, status="", user_id=None, when=None):
        self.writes.append(rid)
        if self.landed:
            self.rows.setdefault(rid, {"id": rid})["listing"] = data
        return self.landed

    def delete_listing(self, rid, user_id=None):
        self.deletes.append(rid)
        if self.deleted:
            self.rows.pop(rid, None)
        return self.deleted

    def list_listings(self, **kw):
        return list(self.rows.values())

    # `_assert_session_owner` compares the strict read against db.UNAVAILABLE.
    UNAVAILABLE = object()

    def __getattr__(self, name):  # anything else this route doesn't need
        raise AttributeError(name)


@pytest.fixture
def api(monkeypatch):
    from backend import main
    return main, TestClient(main.app)


def _listing():
    return {"title": "Vintage Levi's 501", "description": "Nice.",
            "price": 45.0, "quantity": 1}


def test_a_save_that_did_not_land_is_not_reported_as_saved(api, monkeypatch):
    main, client = api
    fake = FakeDb(landed=False)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)

    res = client.post("/api/save/s1", json=_listing())

    assert fake.writes == ["s1"], "the write must still be attempted"
    assert res.status_code == 503, f"answered {res.status_code}: {res.text[:200]}"
    assert "saved" not in res.text.lower() or "couldn" in res.text.lower()


def test_a_save_that_landed_still_says_so(api, monkeypatch):
    main, client = api
    fake = FakeDb(landed=True)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)

    res = client.post("/api/save/s1", json=_listing())
    assert res.status_code == 200 and res.json() == {"saved": True}


def test_a_merge_reports_only_the_sources_it_actually_removed(api, monkeypatch):
    """`removed` is a list the client acts on — it drops those cards."""
    main, client = api
    fake = FakeDb(rows={
        "t1": {"id": "t1", "listing": _listing(), "status": "draft"},
        "s1": {"id": "s1", "listing": _listing(), "status": "draft"},
    }, landed=True, deleted=False)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_purge_session_images", lambda *a, **k: None)
    monkeypatch.setattr(main, "_in_background", lambda fn, *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)

    res = client.post("/api/listings/merge",
                      json={"target_id": "t1", "source_ids": ["s1"]})
    assert res.status_code == 200, res.text
    assert res.json()["removed"] == [], (
        "the merge reported removing a source whose delete failed")
