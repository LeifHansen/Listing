"""Reconciling a listing that both the seller and eBay may have changed.

Not to be confused with services/listing_merge.py, which consolidates two
DRAFTS in this app. This is about one listing that exists in two places at
once, and about the question a mirror cannot answer.

The old sync refreshed price, quantity, counters and photos from eBay and
kept everything else local unless it was blank. So a title fixed in Seller
Hub never arrived — and worse, the revise then sent that stale local title
straight back over the newer one, reported as a successful update.

The reason it could not do better is that it had only two versions to compare
— what we hold and what eBay holds — and two versions cannot distinguish "the
seller edited this here" from "we are holding an old copy of it". Both look
like a difference.

The shadow is the third version: what eBay last told us, stored alongside the
record. With it, one unanswerable question becomes two answerable ones —

    did the local value change since the shadow?   (the seller edited it)
    did the remote value change since the shadow?  (eBay's copy moved on)

    seller only  -> keep local, and send it on the next revise
    eBay only    -> take remote
    both         -> a conflict: hold local, write nothing, ask the seller
    neither      -> nothing to do

Nothing here silently picks a side on a conflict. Picking local is the bug
this replaces; picking remote throws away what the seller just typed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..models import Listing
from .dirty_fields import TRACKED, _comparable

# Fields eBay owns outright: live counters and sale facts it reports and we
# never push back. They are taken from the remote copy without ceremony.
REMOTE_OWNED = ("watch_count", "sold_quantity", "view_url", "sold_price",
                "sold_at", "ebay_start_time")


@dataclass
class Merged:
    """The reconciled listing, plus anything that needs the seller."""

    listing: Listing
    #: field name -> {"local": ..., "remote": ...} for both-sided changes.
    conflicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: fields taken from eBay in this pass (for logging / the UI).
    took_remote: list[str] = field(default_factory=list)
    #: fields the seller changed that are still pending a push.
    kept_local: list[str] = field(default_factory=list)


def three_way(local: Listing, shadow: Optional[dict], remote: dict,
              dirty: Optional[set[str]] = None) -> Merged:
    """Reconcile `local` against `remote`, using `shadow` as the base.

    `dirty` names the fields the seller is known to have edited. It comes
    from services/dirty_fields and is an ADDITIONAL signal, not the only one:
    a local value that differs from the shadow is treated as edited even if
    the record predates dirty-tracking, which is what keeps existing drafts
    from being silently overwritten on the first sync after this ships.

    With no shadow, NOTHING is reconciled and the local copy stands. That is
    the conservative answer and it matters most on the day this ships: every
    record already in the database has no shadow, so a rule of "no base means
    take eBay's copy" would overwrite every seller's local work at once, on
    the first sync after deploy. Establishing the base is what that first
    sync is for; reconciliation starts on the second.

    (A genuinely new import is unaffected either way — its local copy IS the
    remote one, so there is nothing to choose between.)
    """
    dirty = set(dirty or local.dirty_fields or ())
    merged = local.model_copy(deep=True)
    out = Merged(listing=merged)

    # eBay's own facts, always.
    for name in REMOTE_OWNED:
        if name in remote:
            setattr(merged, name, remote[name])

    if not shadow:
        return out

    for name in TRACKED:
        if name not in remote:
            # eBay did not report this field, so it says nothing about it.
            continue
        base_v = _comparable(shadow.get(name))
        local_v = _comparable(getattr(local, name, None))
        remote_v = _comparable(remote.get(name))

        local_changed = (local_v != base_v) or (name in dirty)
        remote_changed = remote_v != base_v

        if remote_changed and not local_changed:
            setattr(merged, name, remote[name])
            out.took_remote.append(name)
        elif local_changed and remote_changed:
            if local_v == remote_v:
                # The same edit arrived from both directions. Nothing to ask.
                setattr(merged, name, remote[name])
                continue
            out.conflicts[name] = {"local": getattr(local, name, None),
                                   "remote": remote.get(name)}
        elif local_changed:
            out.kept_local.append(name)

    # A field under conflict must not also be queued for a push: writing it
    # would resolve the conflict in the local copy's favour, silently, which
    # is the behaviour being removed.
    merged.dirty_fields = sorted(dirty - set(out.conflicts))
    return out


def shadow_from(remote: dict) -> dict:
    """The snapshot to store as the new base after eBay confirms a state.

    Only the reconcilable fields: the shadow exists to answer "did this
    change since we last agreed", and carrying live counters in it would make
    every watch-count tick look like an edit.
    """
    return {name: remote[name] for name in TRACKED if name in remote}
