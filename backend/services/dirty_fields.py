"""Which fields the seller actually edited, worked out by diff.

A revise must carry only what changed. Everything else this app holds is a
snapshot of the marketplace taken at the last sync, and re-sending a snapshot
is not a no-op — it overwrites whatever the marketplace has now, which may be
newer (Seller Hub, the eBay app, a category remap eBay applied itself).

The app has no edit-event stream to read that from, so it is recovered the
only way it can be: by comparing the listing arriving from the editor against
the one already stored. Fields that differ are the seller's edits; fields that
match are unproven and stay out of the request.

`quantity` is the field where this stops being tidiness and becomes
correctness — eBay reads a revise's Quantity as the new AVAILABLE stock, so
re-sending an import-time total restocks units that already sold. See
tests/test_ebay_quantity_contract.py.
"""
from __future__ import annotations

from typing import Any, Optional

from ..config import log
from ..models import Listing

# The fields a seller edits and a revise can carry. Deliberately not every
# field on the model: server-owned identity (ebay_listing_id, source,
# marketplaces, ...) is restored from storage on every save and is never the
# seller's edit, and live counters (watch_count, sold_quantity) are the
# marketplace's to report, not ours to push back.
TRACKED = (
    "title", "subtitle", "description", "brand", "condition",
    "condition_description", "category_id", "price", "quantity", "currency",
    "listing_format", "auction_start_price", "auction_duration",
    "package_weight_lb", "package_weight_oz", "package_length_in",
    "package_width_in", "package_height_in", "fulfillment_policy_id",
    "item_specifics", "images", "image_urls",
)


def _comparable(value: Any) -> Any:
    """A form two versions of a field can be compared in."""
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    if isinstance(value, dict):
        return {k: _comparable(v) for k, v in sorted(value.items())}
    if hasattr(value, "model_dump"):
        return _comparable(value.model_dump())
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        # 3 and 3.0 are the same price. JSON storage round-trips ints to
        # floats, and calling that an edit would mark price dirty on every
        # save of an untouched listing.
        return int(value)
    return value


def changed_fields(incoming: Listing, stored: Optional[dict]) -> list[str]:
    """The TRACKED fields where `incoming` differs from the stored record.

    Both sides are normalized through the Listing model before comparing.
    That is what makes the comparison about VALUES rather than shapes: the
    editor sends item specifics as models and storage holds them as dicts, a
    price saved as 25 reads back as 25.0, and a record written before a field
    existed simply gets that field's default. Comparing the raw forms instead
    reports an edit on every one of those, which puts the entire stale
    snapshot back into the revise payload — the exact overwrite this module
    exists to prevent.

    A listing with nothing stored yet (a brand-new draft) reports no changes:
    it has no marketplace copy to overwrite, and a create sends every field
    regardless.
    """
    if not stored:
        return []
    try:
        before = Listing(**stored)
    except Exception:  # noqa: BLE001 - unparseable stored record
        # Can't establish a baseline, so nothing can be PROVEN edited. Silence
        # is the safe answer: it sends less, where the alternative is sending
        # a payload built from a record we could not even read.
        log.warning("dirty: stored listing did not parse; no edits inferred")
        return []
    return [name for name in TRACKED
            if _comparable(getattr(incoming, name, None))
            != _comparable(getattr(before, name, None))]


def accumulate(incoming: Listing, stored: Optional[dict]) -> Listing:
    """Add this save's edits to whatever edits are already pending.

    Marks accumulate rather than replace because a seller edits across several
    saves before publishing: change the price, save; change the title, save;
    publish. Replacing on each save would leave only the last one, and the
    revise would drop the price change it was told about two saves ago.

    They are cleared when the marketplace accepts them, not when they are sent
    — see Listing.clear_dirty.

    What accumulates is the SERVER's record plus what this save actually
    changed, measured by diffing against the stored copy. `incoming`'s own
    list is deliberately ignored: it arrives from a client, every field a
    revise sends overwrites whatever eBay has now, and a client naming one it
    did not change would push a stale snapshot over newer Seller Hub work —
    the same harm the three-way merge exists to prevent, reached from the
    other end. Nothing is lost by ignoring it: the diff sees every real
    change, and server-side callers that mark a field explicitly write through
    upsert_listing rather than through here.
    """
    pending = set((stored or {}).get("dirty_fields") or [])
    incoming.dirty_fields = sorted(pending | set(changed_fields(incoming, stored)))
    return incoming
