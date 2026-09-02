"""A full volume is a condition, not a crash.

Five upload paths already recognise ENOSPC and answer 507 with a sentence a
seller can act on — "The server is out of storage space — try again shortly."
Every OTHER write does not. `POST /api/save/{id}`, both PATCH routes, and
anything else that reaches `storage.save_listing` let the OSError out, and
FastAPI turns it into "Internal Server Error".

That is wrong in the two ways the invalid-session-id handler above it already
documents: the seller is shown a fault with no next step, and a real 500 —
the kind worth paging about — is buried under a condition that is merely
operational. It is also the failure mode this app is MOST likely to hit,
because it runs on one small Fly volume that holds every seller's photos.

Handled centrally for the same reason StorageUnavailable is: a route added
later cannot forget to. Scoped to the three errnos that mean "the write could
not be stored" — ENOSPC, EDQUOT, EROFS — so an ordinary I/O bug still surfaces
as the 500 it is.
"""
from __future__ import annotations

import errno

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402


class FakeDb:
    UNAVAILABLE = object()

    def enabled(self):
        return False

    def get_listing(self, rid):
        return {"id": rid, "listing": {}, "status": "draft"}

    def get_listing_strict(self, rid):
        return None

    def upsert_listing(self, *a, **k):
        return True

    def __getattr__(self, name):
        raise AttributeError(name)


@pytest.fixture
def client(monkeypatch):
    from backend import main
    monkeypatch.setattr(main, "db", FakeDb())
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    return main, TestClient(main.app, raise_server_exceptions=False)


LISTING = {"title": "Vintage Levi's 501", "description": "Nice.",
           "price": 45.0, "quantity": 1}


def _full_disk(main, monkeypatch, err=errno.ENOSPC):
    def _boom(*a, **k):
        raise OSError(err, "No space left on device")
    monkeypatch.setattr(main.storage, "save_listing", _boom)


def test_the_editor_save_says_the_server_is_out_of_space(client, monkeypatch):
    main, api = client
    _full_disk(main, monkeypatch)
    res = api.post("/api/save/s1", json=LISTING)
    assert res.status_code == 507, f"answered {res.status_code}: {res.text[:200]}"
    assert "storage" in res.text.lower() or "space" in res.text.lower()
    assert "internal server error" not in res.text.lower()


@pytest.mark.parametrize("err", [errno.ENOSPC, errno.EDQUOT, errno.EROFS])
def test_every_way_a_volume_refuses_a_write_reads_the_same(client, monkeypatch, err):
    """Over quota and mounted read-only are the same news to a seller."""
    main, api = client
    _full_disk(main, monkeypatch, err)
    assert api.post("/api/save/s1", json=LISTING).status_code == 507


def test_an_ordinary_io_error_is_still_a_server_fault(client, monkeypatch):
    """The handler must not swallow bugs. A broken pipe or a missing
    directory is not an operational condition a seller can wait out."""
    main, api = client
    _full_disk(main, monkeypatch, errno.EPIPE)
    assert api.post("/api/save/s1", json=LISTING).status_code == 500


def test_the_message_never_names_the_server_s_filesystem(client, monkeypatch):
    main, api = client

    def _boom(*a, **k):
        raise OSError(errno.ENOSPC,
                      "No space left on device: '/data/sessions/s1/listing.json'")
    monkeypatch.setattr(main.storage, "save_listing", _boom)

    res = api.post("/api/save/s1", json=LISTING)
    assert res.status_code == 507
    assert "/data/" not in res.text and "listing.json" not in res.text
