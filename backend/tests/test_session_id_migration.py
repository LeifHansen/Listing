"""The one-shot move from stripped storage names to canonical ones.

The naming rule became injective, which closes an authorization bypass but
also renames the directory every imported listing already uses ("ebay123" ->
"ebay-123"). This migration performs that move.

Its safety rests on one design choice worth pinning: it is driven by the
session ids that exist as DATABASE ROWS, never by directory names. A request
can ask for any id it likes, but only a real row moves anything — so the
migration itself cannot be steered into walking one seller's files into
another seller's name, which is the very bug being fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import migrate_session_ids as mig  # noqa: E402


@pytest.fixture()
def sessions(tmp_path, monkeypatch):
    from backend import config

    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", root)
    monkeypatch.setattr(mig.config, "SESSIONS_DIR", root)
    return root


def _make(root: Path, name: str, content: bytes = b"photo") -> Path:
    d = root / name / "optimized"
    d.mkdir(parents=True)
    (d / "img_000.jpg").write_bytes(content)
    return root / name


def test_a_dry_run_moves_nothing(sessions):
    _make(sessions, "ebay123")
    moved, _, _ = mig.migrate_disk(["ebay-123"], apply=False)
    assert moved == 1
    assert (sessions / "ebay123").exists()
    assert not (sessions / "ebay-123").exists()


def test_apply_moves_the_legacy_directory_to_its_canonical_name(sessions):
    _make(sessions, "ebay123", b"the photo")
    moved, _, collisions = mig.migrate_disk(["ebay-123"], apply=True)

    assert (moved, collisions) == (1, 0)
    assert not (sessions / "ebay123").exists()
    assert (sessions / "ebay-123" / "optimized" / "img_000.jpg").read_bytes() \
        == b"the photo"


def test_a_collision_is_reported_and_neither_side_is_touched(sessions):
    """Both names populated means two real sessions' files are in play. That
    is a case for a human, not one to guess about — overwriting either would
    destroy a seller's photos."""
    _make(sessions, "ebay123", b"legacy photo")
    _make(sessions, "ebay-123", b"canonical photo")

    moved, _, collisions = mig.migrate_disk(["ebay-123"], apply=True)

    assert (moved, collisions) == (0, 1)
    assert (sessions / "ebay123" / "optimized" / "img_000.jpg").read_bytes() \
        == b"legacy photo"
    assert (sessions / "ebay-123" / "optimized" / "img_000.jpg").read_bytes() \
        == b"canonical photo"


def test_running_twice_is_a_no_op(sessions):
    """An interrupted run must be safe to simply re-run."""
    _make(sessions, "ebay123", b"the photo")
    mig.migrate_disk(["ebay-123"], apply=True)
    moved, skipped, collisions = mig.migrate_disk(["ebay-123"], apply=True)

    assert (moved, collisions) == (0, 0)
    assert skipped == 1
    assert (sessions / "ebay-123" / "optimized" / "img_000.jpg").exists()


def test_an_already_canonical_session_is_left_alone(sessions):
    """Ordinary uploaded sessions are pure hex — their name never changes."""
    _make(sessions, "3aaeb40637a1")
    moved, skipped, _ = mig.migrate_disk(["3aaeb40637a1"], apply=True)
    assert (moved, skipped) == (0, 1)
    assert (sessions / "3aaeb40637a1" / "optimized" / "img_000.jpg").exists()


def test_a_lookalike_id_cannot_steal_a_real_sessions_directory(sessions):
    """The property that makes this migration safe, and the one it got wrong
    on the first attempt.

    "3aaeb40637a1-" strips to "3aaeb40637a1", which is a real session. Being
    driven by database rows is NOT sufficient protection, because saving a
    draft needs no account: an attacker can create the row "3aaeb40637a1-"
    and then the migration itself walks the victim's photos into the
    attacker's name — reintroducing, through the repair, the exact bypass
    being repaired.

    So a legacy name that is ITSELF a live session id is never claimed.
    """
    _make(sessions, "3aaeb40637a1", b"the victim's photo")

    moved, _, collisions = mig.migrate_disk(
        ["3aaeb40637a1", "3aaeb40637a1-"], apply=True)

    assert (moved, collisions) == (0, 1)
    assert (sessions / "3aaeb40637a1" / "optimized" / "img_000.jpg").read_bytes() \
        == b"the victim's photo"
    assert not (sessions / "3aaeb40637a1-").exists()


def test_two_ids_stripping_to_the_same_name_are_both_refused(sessions):
    """"ebay-123" and "e-bay123" both strip to "ebay123". Nothing can tell
    whose photos are in that directory, so neither may claim it."""
    _make(sessions, "ebay123", b"ambiguous photo")

    moved, _, collisions = mig.migrate_disk(["ebay-123", "e-bay123"],
                                            apply=True)

    assert moved == 0 and collisions == 2
    assert (sessions / "ebay123" / "optimized" / "img_000.jpg").read_bytes() \
        == b"ambiguous photo"


def test_an_unambiguous_legacy_name_still_migrates(sessions):
    """The guard must not block the ordinary case it exists to allow."""
    _make(sessions, "ok123")
    moved, _, collisions = mig.migrate_disk(["ok-123"], apply=True)
    assert (moved, collisions) == (1, 0)
    assert (sessions / "ok-123" / "optimized" / "img_000.jpg").exists()
