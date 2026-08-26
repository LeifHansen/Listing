"""Finding listings that look like the same item listed twice.

Before the publish guard landed, a publish that raced or got retried could put
two live listings on the seller's account for one item. The app can stop making
new ones, but it can't undo the ones already there: those are two real eBay
listings, and only the seller can decide which to end. This finds the likely
pairs and shows the evidence, so that decision takes a glance instead of a
scroll through hundreds of listings.

The honest name is "possible". A reseller can legitimately have two live
listings with the same title — two copies of the same DVD, the same shirt in
two sizes where the size lives in a specific rather than the title. So nothing
here decides anything: it groups candidates, says WHY each one is suspicious,
and ranks them so the obvious ones (listed a minute apart, same price) come
first and the thin ones (a shared title and nothing else) come last.

The strongest tell is time. A seller listing two copies of something does it
deliberately, minutes or days apart; a double-publish mints both within
seconds, because it was one click that went out twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

LIVE_STATUSES = ("published", "live")

# Two listings created inside this window were almost certainly one publish
# that went out twice. Wide enough to survive eBay's own timestamp rounding and
# a slow second request, far short of "the seller sat down and listed both".
TOGETHER_SECONDS = 15 * 60

# Below this many characters a title is too generic to group on ("vase", "lot")
# — matching on it would pair unrelated inventory.
_MIN_TITLE = 8


def normalize_title(title: str) -> str:
    """A title reduced to what two listings of one item would share.

    Case, punctuation and spacing all drift between a listing the app wrote and
    the same listing read back from eBay, and none of that drift means the
    items differ.
    """
    lowered = str(title or "").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", lowered).split())


def _item_id(rec: dict) -> str:
    return str((rec.get("listing") or {}).get("ebay_listing_id") or "").strip()


def _is_mirror(rec: dict) -> bool:
    """True for a row the store sync created (id "ebay-<item>") rather than a
    listing this app published under its own session id."""
    return str(rec.get("id") or "").startswith("ebay-")


def _listed_at(rec: dict) -> Optional[datetime]:
    """When the listing went live on eBay, as an aware datetime.

    eBay's own start time is the truth and both sides of a duplicate pair carry
    it once synced; the row's created_at is the fallback for a listing eBay
    hasn't been asked about yet.
    """
    listing = rec.get("listing") or {}
    for raw in (listing.get("ebay_start_time"), rec.get("created_at")):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _price(rec: dict):
    try:
        value = float((rec.get("listing") or {}).get("price") or 0)
    except (TypeError, ValueError):
        return None
    return round(value, 2) if value > 0 else None


def _format(rec: dict) -> str:
    return str((rec.get("listing") or {}).get("listing_format")
               or "FIXED_PRICE").upper()


def _gap_seconds(records: list[dict]) -> Optional[float]:
    """Seconds between the earliest and latest listing in the group."""
    times = [t for t in (_listed_at(r) for r in records) if t]
    if len(times) < 2:
        return None
    return (max(times) - min(times)).total_seconds()


def _describe_gap(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds apart"
    if seconds < 90 * 60:
        return f"{int(round(seconds / 60))} minutes apart"
    if seconds < 48 * 3600:
        return f"{int(round(seconds / 3600))} hours apart"
    return f"{int(round(seconds / 86400))} days apart"


def _evidence(group: list[dict]) -> tuple[list[str], list[str]]:
    """(reasons this looks like a duplicate, reasons to doubt it)."""
    reasons: list[str] = []
    caveats: list[str] = []

    gap = _gap_seconds(group)
    if gap is not None and gap <= TOGETHER_SECONDS:
        reasons.append(f"Listed {_describe_gap(gap)} — a sign of one publish "
                       "that went out twice.")
    elif gap is not None:
        caveats.append(f"Listed {_describe_gap(gap)}, so this may well be two "
                       "items you listed separately.")

    prices = {p for p in (_price(r) for r in group) if p is not None}
    if len(prices) == 1 and len(group) > 1:
        reasons.append("Same price.")
    elif len(prices) > 1:
        caveats.append("Different prices — you may have priced two real items "
                       "differently.")

    # One row the app published, one the sync pulled back: the exact shape the
    # double-publish left behind.
    if any(_is_mirror(r) for r in group) and any(not _is_mirror(r) for r in group):
        reasons.append("One was created here and one came back from your eBay "
                       "store — the pattern the old publish bug left behind.")

    if len({_format(r) for r in group}) > 1:
        caveats.append("One is an auction and one is Buy It Now, which is "
                       "often deliberate.")
    return reasons, caveats


def _confidence(reasons: list[str], caveats: list[str]) -> str:
    """How much the evidence supports "this is a duplicate"."""
    if any("auction" in c for c in caveats):
        # Running the same item as both an auction and a fixed price is a real
        # selling strategy; never rank that as a likely mistake.
        return "low"
    if len(reasons) >= 2:
        return "high"
    return "medium" if reasons else "low"


_RANK = {"high": 0, "medium": 1, "low": 2}

# Sorts a listing with no usable timestamp to the end rather than the start,
# so a missing date never pretends to be the original.
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def find(records: list[dict]) -> list[dict]:
    """Groups of live listings that may be the same item listed more than once.

    Each group: {title, confidence, reasons, caveats, listings:[...]}, strongest
    evidence first. Only LIVE listings count — an ended or sold twin is already
    resolved — and a group needs at least two DISTINCT eBay item ids, so the
    app's own record and the sync's mirror of that SAME item never look like a
    duplicate of each other.
    """
    by_title: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("status") not in LIVE_STATUSES:
            continue
        if not _item_id(rec):
            continue  # nothing live on eBay to be a duplicate of
        key = normalize_title((rec.get("listing") or {}).get("title")
                              or rec.get("title") or "")
        if len(key) < _MIN_TITLE:
            continue
        by_title.setdefault(key, []).append(rec)

    groups: list[dict] = []
    for members in by_title.values():
        # Distinct eBay items only: two rows for ONE item is a sync artifact the
        # store sync already cleans up, not a duplicate listing.
        seen: dict[str, dict] = {}
        for rec in members:
            item = _item_id(rec)
            # Prefer the app's own record — it owns the photos and the AI's
            # words, so it's the one a seller would want to keep.
            if item not in seen or (_is_mirror(seen[item]) and not _is_mirror(rec)):
                seen[item] = rec
        if len(seen) < 2:
            continue
        # Oldest first: the original is what the seller usually keeps, and it
        # reads as "this one, then the accidental second one".
        group = sorted(seen.values(), key=lambda r: _listed_at(r) or _FAR_FUTURE)
        listed_at = [_listed_at(r) for r in group]
        reasons, caveats = _evidence(group)
        groups.append({
            "title": (group[0].get("listing") or {}).get("title")
                     or group[0].get("title") or "Untitled",
            "confidence": _confidence(reasons, caveats),
            "reasons": reasons,
            "caveats": caveats,
            "listings": [{
                "listing_id": r.get("id"),
                "ebay_listing_id": _item_id(r),
                "price": _price(r),
                "currency": (r.get("listing") or {}).get("currency") or "USD",
                "view_url": (r.get("listing") or {}).get("view_url") or "",
                # Parsed once above, not twice here — the ternary called
                # _listed_at again just to ask whether the first call
                # returned anything.
                "listed_at": when.isoformat() if when else None,
                "created_here": not _is_mirror(r),
                "watch_count": (r.get("listing") or {}).get("watch_count") or 0,
            } for r, when in zip(group, listed_at)],
        })

    groups.sort(key=lambda g: (_RANK[g["confidence"]], -len(g["listings"]),
                               g["title"].lower()))
    return groups
