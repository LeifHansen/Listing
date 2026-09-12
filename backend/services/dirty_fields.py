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
    "condition_description", "condition_descriptors", "category_id",
    "store_category_id",
    "price", "quantity", "currency",
    "listing_format", "auction_start_price", "auction_duration",
    "package_weight_lb", "package_weight_oz", "package_length_in",
    "package_width_in", "package_height_in", "fulfillment_policy_id",
    "item_specifics", "images", "image_urls",
    # The listing's video. Tracked because a revise CAN carry it -- eBay takes
    # <VideoDetails> on ReviseItem -- and because without it the one edit that
    # adds a video to a live listing would be the one edit that never reached
    # eBay. Its entries carry eBay's moderation status, which changes under
    # us; `comparable_field` below drops that so a video moving from
    # PROCESSING to LIVE is not read as the seller having edited anything.
    "videos",
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


def comparable_field(name: str, value: Any) -> Any:
    """`_comparable`, with the one field whose stored form carries more than
    its meaning. A condition descriptor is eBay's ids (which descriptor,
    which value, what free text); the labels beside them are eBay's wording
    at the time, kept for display. An import has no labels and the editor
    fills them in, and that must not read as the seller re-grading the card
    -- it would put the condition into every revise and into every sync
    conflict. So descriptors compare on ids and text alone."""
    if name == "videos":
        # A video is the seller's edit only in WHICH video it is, so it
        # compares on identity alone -- eBay's id when there is one, the
        # local filename until there is.
        #
        # Everything else in the entry moves without the seller touching it.
        # The moderation status and eBay's message arrive minutes to days
        # after the save, and comparing those would mark the field edited on
        # every status poll: <VideoDetails> into every unrelated revise, and
        # a listing showing unsaved changes nobody made.
        #
        # The id is preferred over the file because the two sides of a sync
        # describe the same video differently -- this app holds a file, a
        # size and a status; eBay reports an id -- and the id is the only
        # thing both can say. Comparing the whole entry made every record
        # with a video permanently "locally edited" against its own shadow.
        out = []
        for entry in (value or []):
            d = entry.model_dump() if hasattr(entry, "model_dump") else entry
            if not isinstance(d, dict):
                continue
            out.append(str(d.get("ebay_video_id") or "").strip()
                       or str(d.get("file") or "").strip())
        return [v for v in out if v]
    if name == "condition_descriptors":
        out = []
        for entry in (value or []):
            d = entry.model_dump() if hasattr(entry, "model_dump") else entry
            if not isinstance(d, dict):
                continue
            values = d.get("values") or []
            if isinstance(values, str):
                values = [values]
            out.append({"id": str(d.get("id") or "").strip(),
                        "values": [str(v).strip() for v in values if str(v).strip()],
                        "text": str(d.get("text") or "").strip()})
        return [d for d in out if d["id"] and (d["values"] or d["text"])]
    return _comparable(value)


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
            if comparable_field(name, getattr(incoming, name, None))
            != comparable_field(name, getattr(before, name, None))]


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
