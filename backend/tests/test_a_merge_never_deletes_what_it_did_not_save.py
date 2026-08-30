"""The merge consumes the sources. It has to have saved the target first.

`/api/listings/merge` copies every source listing's photos onto the master,
writes the merged record, and then DELETES the sources — database rows, local
photos and R2 objects. Its own docstring says so: "the sources are then
deleted (DB + disk + R2)".

`db.upsert_listing` swallows its failures by design, so that write could not
land and the deletes went ahead anyway. What the seller has afterwards is
nothing: the sources gone with their photos purged, the master still holding
its pre-merge record, and `{"ok": true}` on the screen. Unlike almost
everything else in this app that is not recoverable by trying again — there is
nothing left to try it on.

The same rule the strict save already uses (`PATCH /api/listings/{id}`, see
main._sticky_status): a write that did not land raises rather than reporting
success. Here it also has to happen BEFORE the destructive half, which is the
whole point — the order is the fix, not the check.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def merging(monkeypatch, tmp_path):
    from backend import config, main, storage

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(storage.config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *_a, **_k: None)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_purge_session_images", lambda sid: None)

    def _run(save_lands: bool):
        rows = {
            "target": {"id": "target", "user_id": "u1", "status": "draft",
                       "listing": {"title": "A lamp", "images": []}},
            "src-1": {"id": "src-1", "user_id": "u1", "status": "draft",
                      "listing": {"title": "A lamp", "images": []}},
        }
        deleted: list[str] = []

        # A database that EXISTS and whose write failed. Without a database
        # at all there is nothing to fail: the disk copy is the store, and
        # db.delete_listing is a no-op too.
        monkeypatch.setattr(main.db, "enabled", lambda: True)
        monkeypatch.setattr(main.db, "get_listing", lambda i: rows.get(i))
        monkeypatch.setattr(main.db, "upsert_listing",
                            lambda *_a, **_k: save_lands)
        monkeypatch.setattr(main.db, "delete_listing",
                            lambda sid, *_a, **_k: deleted.append(sid) or True)
        resp = TestClient(main.app).post(
            "/api/listings/merge",
            json={"target_id": "target", "source_ids": ["src-1"]})
        return resp, deleted
    return _run


def test_a_merge_that_could_not_save_deletes_nothing(merging):
    """The finding. Deleting here is unrecoverable — there is no copy left to
    try again from."""
    resp, deleted = merging(save_lands=False)

    assert deleted == [], "consumed the sources without saving the merge"
    assert resp.status_code == 503, resp.text
    assert "ok" not in resp.json(), resp.text


def test_a_merge_that_saved_still_consumes_the_sources(merging):
    resp, deleted = merging(save_lands=True)

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert deleted == ["src-1"]
    assert resp.json()["removed"] == ["src-1"]
