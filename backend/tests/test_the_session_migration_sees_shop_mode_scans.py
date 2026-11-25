"""The session-id migration reads EVERY row, Shop Mode scans included.

Its one safety property is that a legacy directory name which is itself a
live session id is never claimed (see test_session_id_migration). That only
holds if "live session ids" really is all of them. The script asked
`list_listings` for its ids, and `list_listings` leaves SCANNED rows out by
default -- its own docstring says a migration must not. So a scan's directory
was invisible to the guard: an attacker saves a draft named after a scan's id
plus "-", and the migration walks the scan's photos into the attacker's
session.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import migrate_session_ids as mig  # noqa: E402


def test_a_scans_directory_cannot_be_claimed_by_a_lookalike(
        dbmod, tmp_path, monkeypatch):
    from backend import config

    root = tmp_path / "sessions"
    photo = root / "3aaeb40637a1" / "optimized" / "img_000.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"the victim's scan")
    monkeypatch.setattr(config, "SESSIONS_DIR", root)

    dbmod.upsert_listing("3aaeb40637a1", {"title": "a scan"},
                         status=dbmod.SCANNED, user_id="victim")
    dbmod.upsert_listing("3aaeb40637a1-", {"title": "lookalike"},
                         user_id="attacker")

    ids = mig._session_ids()
    assert "3aaeb40637a1" in ids, "the scan's id must be seen"

    moved, _, collisions = mig.migrate_disk(ids, apply=True)
    assert (moved, collisions) == (0, 1)
    assert photo.read_bytes() == b"the victim's scan"
    assert not (root / "3aaeb40637a1-").exists()


def test_an_unreadable_database_still_aborts(dbmod, monkeypatch):
    from backend import errors

    monkeypatch.setattr(dbmod, "all_listing_ids", lambda: None)
    try:
        mig._session_ids()
    except errors.StorageUnavailable:
        return
    raise AssertionError("a failed read must not look like an empty store")
