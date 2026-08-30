"""Re-file session storage under the canonical, lossless session id.

Session ids used to be canonicalized for storage by DELETING every
non-alphanumeric character, so an imported listing with id "ebay-123" kept
its photos in `sessions/ebay123/`. That mapping is lossy, which is how two
distinct ids came to share one directory and how the ownership guard could be
walked around by appending a character to a known id (see
backend/tests/test_session_id_aliasing.py).

The rule is now injective: an id is accepted unchanged or refused. That closes
the bypass, but it also renames the directory and R2 prefix every imported
listing already uses. This script performs that one-shot move.

    # see what would move, touching nothing
    python3 scripts/migrate_session_ids.py

    # actually move
    python3 scripts/migrate_session_ids.py --apply

Safety, in order of how much it matters:

  - Dry run unless --apply is passed.
  - Driven by the session ids that actually exist as DATABASE ROWS, never by
    directory names. This is the property that makes it safe: a caller can
    ask for "abc123-" but only a real row can move anything, so the migration
    cannot be used to walk one session's files into another's name.
  - Refuses to overwrite. If the canonical destination already exists with
    contents, the pair is REPORTED as a collision and both sides are left
    alone — that is a case a human has to look at, not one to guess about.
  - Idempotent. A session already at its canonical name is skipped, so an
    interrupted run can simply be run again.
  - Local disk and R2 are migrated independently, so a failure in one does
    not strand the other half-done.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, db, errors, objstore, storage  # noqa: E402


def _legacy_dir(session_id: str) -> Path:
    return config.SESSIONS_DIR / storage.legacy_session_name(session_id)


def _canonical_dir(session_id: str) -> Path:
    return config.SESSIONS_DIR / storage.safe_session_name(session_id)


def _has_contents(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _session_ids() -> list[str]:
    """Every session id the database knows about.

    Listing ids ARE session ids in this app — the record id is the session
    the photos live under. Reading them from the database rather than from
    the filesystem is what keeps the migration from being steerable.
    """
    ids = []
    for rec in db.list_listings(limit=1_000_000):
        sid = (rec or {}).get("id") or ""
        try:
            storage.safe_session_name(sid)
        except ValueError:
            print(f"  ! skipping unmigratable id {sid!r} (not a valid session id)")
            continue
        ids.append(sid)
    return ids


def _unclaimable(session_ids: list[str]) -> dict[str, str]:
    """Legacy names no id may claim, mapped to why.

    Two ways a legacy name stops being safe to move:

    1. It is ITSELF a live session id. "3aaeb40637a1-" strips to
       "3aaeb40637a1", which is somebody's real session — moving it would
       walk the victim's photos into the lookalike's name and hand them over,
       reintroducing through the repair the exact bypass being repaired. An
       attacker can create such a row (saving a draft needs no account), so
       this is not hypothetical.
    2. Two different ids strip to it ("ebay-123" and "e-bay123" both give
       "ebay123"). Nothing can tell whose photos those are.
    """
    canonical = set(session_ids)
    claims: dict[str, list[str]] = {}
    for sid in session_ids:
        claims.setdefault(storage.legacy_session_name(sid), []).append(sid)

    blocked = {}
    for legacy_name, claimants in claims.items():
        real = [c for c in claimants if c != legacy_name]
        if not real:
            continue
        if legacy_name in canonical:
            blocked[legacy_name] = (
                f"{legacy_name!r} is itself a live session id, claimed by "
                f"{', '.join(repr(c) for c in real)}")
        elif len(real) > 1:
            blocked[legacy_name] = (
                f"claimed by more than one id: "
                f"{', '.join(repr(c) for c in real)}")
    return blocked


def migrate_disk(session_ids: list[str], apply: bool) -> tuple[int, int, int]:
    """Rename each legacy directory to its canonical name."""
    blocked = _unclaimable(session_ids)
    moved = skipped = collisions = 0
    for sid in session_ids:
        legacy, canonical = _legacy_dir(sid), _canonical_dir(sid)
        if legacy == canonical:
            skipped += 1
            continue
        if legacy.name in blocked:
            print(f"  UNSAFE {sid}: will not move {legacy.name}/ — "
                  f"{blocked[legacy.name]}")
            collisions += 1
            continue
        if not _has_contents(legacy):
            skipped += 1
            continue
        if _has_contents(canonical):
            print(f"  COLLISION {sid}: both {legacy.name}/ and "
                  f"{canonical.name}/ have contents — left untouched")
            collisions += 1
            continue
        print(f"  {'move' if apply else 'would move'} "
              f"{legacy.name}/ -> {canonical.name}/")
        if apply:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(canonical))
        moved += 1
    return moved, skipped, collisions


def migrate_r2(session_ids: list[str], apply: bool) -> tuple[int, int, int]:
    """Re-key each legacy object prefix under the canonical prefix.

    R2 has no rename, so every object is copied to its new key and the old
    one deleted only after the copy succeeds. An interrupted run leaves
    duplicates, never a hole.
    """
    if not objstore.enabled():
        print("  R2 is not configured — skipping the bucket half")
        return 0, 0, 0
    client = objstore._get_client()
    if client is None:
        print("  R2 client unavailable — skipping the bucket half")
        return 0, 0, 0

    blocked = _unclaimable(session_ids)
    copied = skipped = failed = 0
    paginator = client.get_paginator("list_objects_v2")
    for sid in session_ids:
        legacy_name = storage.legacy_session_name(sid)
        canonical_name = storage.safe_session_name(sid)
        if legacy_name == canonical_name:
            skipped += 1
            continue
        if legacy_name in blocked:
            # Same rule as the disk half, and for the same reason: copying
            # these objects would hand one seller's photos to another.
            print(f"  UNSAFE {sid}: will not re-key sessions/{legacy_name}/ — "
                  f"{blocked[legacy_name]}")
            failed += 1
            continue
        legacy_prefix = f"sessions/{legacy_name}/"
        canonical_prefix = f"sessions/{canonical_name}/"
        for page in paginator.paginate(Bucket=config.R2_BUCKET,
                                       Prefix=legacy_prefix):
            for obj in page.get("Contents", []):
                old_key = obj["Key"]
                new_key = canonical_prefix + old_key[len(legacy_prefix):]
                print(f"  {'copy' if apply else 'would copy'} "
                      f"{old_key} -> {new_key}")
                if not apply:
                    copied += 1
                    continue
                try:
                    client.copy_object(
                        Bucket=config.R2_BUCKET,
                        CopySource={"Bucket": config.R2_BUCKET, "Key": old_key},
                        Key=new_key)
                    client.delete_object(Bucket=config.R2_BUCKET, Key=old_key)
                    copied += 1
                except Exception as exc:  # noqa: BLE001 - report, keep going
                    print(f"  ! FAILED {old_key}: {exc}")
                    failed += 1
    return copied, skipped, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually move files (default: dry run)")
    args = ap.parse_args()

    if not args.apply:
        print("DRY RUN — nothing will be moved. Re-run with --apply.\n")

    try:
        ids = _session_ids()
    except errors.StorageUnavailable as exc:
        # An unreadable database used to answer []. This script would then
        # print "0 session id(s)", find nothing to move, and exit 0 -- a
        # migration reporting success having done nothing, which is worse
        # than not running it, because the next person believes it ran.
        print(f"ABORTED: couldn't read the listings from the database ({exc}).")
        print("Nothing was moved. Fix the connection and run this again.")
        return 2
    print(f"{len(ids)} session id(s) from the database\n")

    # The verbs follow --apply. The summary lines are what somebody pastes
    # into a ticket, and "12 moved, 0 collision(s)" from a DRY run reads as a
    # migration that has already happened -- under a header they may well have
    # scrolled past. A run that reports work it did not do is the same failure
    # this branch is about, aimed at the operator instead of the seller.
    did, will = ("moved", "re-keyed") if args.apply else ("to move", "to re-key")

    print("Local disk:")
    moved, skipped, collisions = migrate_disk(ids, args.apply)
    print(f"  {moved} {did}, {skipped} already canonical or empty, "
          f"{collisions} collision(s)\n")

    print("R2:")
    copied, r2_skipped, failed = migrate_r2(ids, args.apply)
    print(f"  {copied} object(s) {will}, {r2_skipped} already canonical, "
          f"{failed} failure(s)\n")

    if collisions or failed:
        print("FINISHED WITH ISSUES — see the COLLISION/FAILED lines above.")
        return 1
    print("Done." if args.apply else "Dry run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
