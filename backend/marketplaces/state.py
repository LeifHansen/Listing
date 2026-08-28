"""Per-marketplace publish state, kept inside the listing JSON blob.

Pure functions over plain dicts (CI-testable: no httpx, no sqlalchemy).
The listing record's `data["marketplaces"]` maps marketplace key ->
{listing_id, url, status, published_at, error}; the top-level `status`
column keeps its existing dashboard semantics via derive_top_status.
"""
from __future__ import annotations

import datetime as _dt

from .base import PublishOutcome

# A background save/refine/autofill must never DEMOTE a listing's lifecycle
# status (see the note above main._STICKY_STATUSES, which aliases this).
STICKY_STATUSES = ("published", "live", "ended", "sold", "unlisted")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def merge_state(data: dict, key: str, outcome: PublishOutcome,
                now: str = "") -> dict:
    """Fold one marketplace's publish outcome into the listing data dict.

    Mutates and returns `data`. Rules:
    - dry-runs record nothing (no remote state was created);
    - a successful attempt updates status/listing_id/url and clears error;
    - a failed attempt records the error but never rewrites lifecycle state
      (a blocked revise doesn't un-publish a live listing);
    - eBay's id is mirrored to the legacy top-level `ebay_listing_id` in both
      directions, so old records and old clients keep working.
    """
    if outcome.dry_run:
        return data
    states = data.setdefault("marketplaces", {})
    entry = dict(states.get(key) or {})
    if outcome.ok:
        if outcome.status:
            if outcome.status == "published" and entry.get("status") != "published":
                entry["published_at"] = now or _now_iso()
            entry["status"] = outcome.status
        if outcome.listing_id:
            entry["listing_id"] = str(outcome.listing_id)
        if outcome.url:
            entry["url"] = outcome.url
        entry["error"] = ""
    else:
        entry["error"] = outcome.message or "Publish failed."
    if key == "ebay":
        if entry.get("listing_id"):
            data["ebay_listing_id"] = entry["listing_id"]
        elif data.get("ebay_listing_id"):
            entry["listing_id"] = str(data["ebay_listing_id"])
    states[key] = entry
    return data


def owned_state_from(stored: dict, incoming_ebay_id: str = "") -> tuple[dict, str]:
    """The server-owned publish state a client round-trip must not overwrite.

    Returns (marketplaces map, ebay_listing_id) to apply onto an incoming
    listing: the stored map always wins, and the legacy top-level eBay id is
    only filled in when the client didn't carry one.

    Why the client's copy can't be trusted: `marketplaces` is written solely
    by publish/end/sync, but every save round-trips the whole listing. A
    second browser tab — or the editor's image-edit auto-save, whose in-memory
    copy predates a publish — sends a map that is missing entries. Honoring it
    erases live listing ids, and the next publish then CREATES a duplicate
    live listing instead of revising the one that exists.
    """
    states = stored.get("marketplaces") or {}
    ebay_id = str(incoming_ebay_id or stored.get("ebay_listing_id") or "")
    return {key: dict(value or {}) for key, value in states.items()}, ebay_id


# Listing fields only the server may write. Publish, end and sync set them;
# every save round-trips the whole listing, so a client copy that differs is a
# stale copy, never an edit.
#
# `source` is the one that bites. A listing this app has published carries
# source="ebay", and that is what routes its next edit down the revise path.
# A save that blanks it makes a live record look brand new, and the next
# publish CREATES A SECOND LIVE LISTING instead of revising the one that
# exists — the same duplicate `marketplaces` is protected from, through a
# field nothing was protecting.
#
# `ebay_listing_id` is deliberately NOT here: owned_state_from fills it only
# when the client didn't carry one, and both callers keep that.
SERVER_OWNED_FIELDS = ("source", "view_url", "ebay_account")


def restore_server_fields(listing, stored: dict) -> list[str]:
    """Copy SERVER_OWNED_FIELDS from the stored record onto `listing`.

    A stored value wins over whatever the client sent; a blank stored value
    leaves the client's alone, so a first publish can still stamp these.
    Returns the names actually changed, for the caller to log.
    """
    changed = []
    for name in SERVER_OWNED_FIELDS:
        value = stored.get(name)
        if value and value != getattr(listing, name, None):
            setattr(listing, name, value)
            changed.append(name)
    return changed


def derive_top_status(prev_status: str, outcomes: dict[str, PublishOutcome],
                      mode: str) -> str:
    """The top-level status column after a multi-marketplace publish.

    Existing single-eBay semantics, generalized: any marketplace going live
    makes the record 'published'; sticky statuses are never demoted by a
    failed or partial attempt; an all-dry-run pass records 'dry_run'; and a
    draft save stays 'draft'.
    """
    if any(o.ok and o.status == "published" for o in outcomes.values()):
        return "published"
    if prev_status in STICKY_STATUSES:
        return prev_status
    if outcomes and all(o.dry_run for o in outcomes.values()):
        return "dry_run"
    return "draft"
