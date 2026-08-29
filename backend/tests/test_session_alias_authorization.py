"""Possession of a session id must not reach another user's photos.

The session-scoped write endpoints guard with _assert_session_owner, which
asks the DATABASE whose listing this is. The file operation that follows asks
STORAGE, under a different naming rule. Where the two disagreed, the guard
answered about one session and the write landed on another.

An anonymous caller who knew a victim's session id — it is in every /media
URL — could append one non-alphanumeric character and:

  - delete the victim's photos (and be told which ones remain),
  - overwrite the victim's saved listing,
  - upload files into the victim's directory,
  - run the studio and the AI over the victim's photos.

These are endpoint-level tests because that is where the two namespaces meet;
a unit test of the naming rule alone would not have caught it.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

VICTIM = "3aaeb40637a1"
# The alias: same storage directory under the old stripping rule, no matching
# database row, therefore no owner for the guard to object to.
ALIAS = VICTIM + "-"


@pytest.fixture()
def app_client(monkeypatch, tmp_path):
    from backend import config, db, main, storage

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    # The victim's listing exists and belongs to someone else.
    monkeypatch.setattr(
        db, "get_listing_strict",
        lambda sid: ({"id": VICTIM, "user_id": "victim-user",
                      "listing": {"title": "Victim's lamp"}}
                     if sid == VICTIM else None))
    monkeypatch.setattr(db, "get_listing", lambda sid: None)
    # The caller is anonymous — no account at all.
    monkeypatch.setattr(main, "_uid", lambda _r: "")

    victim_dir = storage.ensure_session(VICTIM)
    photo = victim_dir / "optimized" / "img_000.jpg"
    photo.write_bytes(b"the victim's photo")
    return TestClient(main.app, raise_server_exceptions=False), photo


def test_an_alias_cannot_delete_the_victims_photo(app_client):
    """The confirmed exploit: HTTP 200, the file gone from disk and R2, and
    the response body enumerating what the victim had left.

    The lookalike id is now a session of the caller's own — empty — so the
    call may still succeed. What must be true is that it touched nothing of
    the victim's and disclosed nothing about them.
    """
    client, photo = app_client
    resp = client.post("/api/delete-image",
                       json={"session_id": ALIAS, "name": "img_000.jpg"})
    assert photo.exists(), "the victim's photo was deleted through an alias"
    assert "img_000.jpg" not in resp.text, \
        "the response disclosed the victim's filenames"


def test_an_alias_cannot_overwrite_the_victims_listing(app_client):
    """Saving through the lookalike id writes the caller's own session, and
    must leave the victim's stored listing exactly as it was."""
    from backend import storage

    client, _ = app_client
    client.post(f"/api/save/{ALIAS}", json={"title": "Attacker's title"})

    victim_listing = storage.session_dir(VICTIM) / "listing.json"
    assert not victim_listing.exists() or \
        "Attacker's title" not in victim_listing.read_text(), \
        "the victim's saved listing was overwritten through an alias"
    assert storage.session_dir(ALIAS) != storage.session_dir(VICTIM)


def test_an_alias_cannot_read_the_victims_photos_through_media(app_client):
    client, _ = app_client
    resp = client.get(f"/media/{ALIAS}/optimized/img_000.jpg")
    assert resp.status_code in (400, 404), resp.text


def test_the_victims_own_id_still_404s_for_a_stranger(app_client):
    """The guard's original job has to keep working: the real id belongs to
    someone else, so a stranger is refused on that too."""
    client, photo = app_client
    resp = client.post("/api/delete-image",
                       json={"session_id": VICTIM, "name": "img_000.jpg"})
    assert resp.status_code == 404, resp.text
    assert photo.exists()
