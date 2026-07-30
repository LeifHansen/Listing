"""Seller action recommendations — the app's 'what should I do next' engine.

Rules over the signals we already have (listing status, age, price, photos,
promotion, missing details) turn a pile of listings into a short, ranked list
of concrete next actions: finish a draft, relist an ended item, promote a live
one, drop a stale price, add a sale, add photos.

eBay traffic (views/watchers), when available, sharpens these: a listing with
lots of views but no watchers is priced too high; one with watchers but no sale
wants a nudge (offer/sale); one with almost no views wants promotion. Pass a
per-listing metrics dict ({views, watchers}) to fold those in — without it, the
age heuristics still produce useful advice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STALE_DAYS = 21   # a live listing this old with no sale → nudge price/sale
FEW_PHOTOS = 3    # fewer than this → suggest adding photos


def _age_days(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def recommend_for(item: dict, metrics: Optional[dict] = None,
                  rate: Optional[float] = None, promoted: bool = False) -> list[dict]:
    """Recommended actions for ONE listing record. Each rec:
    {listing_id, listing_title, type, label, reason, action, priority, rate}.
    `rate` is eBay's recommended Promoted Listings ad rate (%) for this listing,
    carried on promote recs so the UI can one-click promote at that rate.
    Higher priority = surface sooner."""
    listing = item.get("listing") or {}
    status = item.get("status")
    lid = item.get("id")
    title = listing.get("title") or item.get("title") or "this listing"
    recs: list[dict] = []

    def add(type_: str, label: str, reason: str, priority: int,
            action: str = "open", rate_: Optional[float] = None):
        recs.append({"listing_id": lid, "listing_title": title, "type": type_,
                     "label": label, "reason": reason, "action": action,
                     "priority": priority, "rate": rate_})

    if status == "unlisted":
        add("finish", "Finish & list",
            "Ready to sell — just a few fields from going live.", 60)
        return recs
    if status == "ended":
        add("relist", "Relist",
            "Ended without selling — relist it to give it another run.", 55)
        return recs
    if status not in ("published", "live"):
        return recs

    m = metrics or {}
    views = m.get("views")
    watchers = m.get("watchers")
    age = _age_days(item.get("created_at"))
    images = listing.get("images") or []
    # Promoted if our own flag says so OR eBay reports a live ad (covers ads
    # created straight in Seller Hub) — so we never nag an already-promoted item.
    promoted = bool(listing.get("promote")) or promoted

    # Data-driven (real eBay traffic) beats the age heuristics below.
    # (No "Add a sale" nudge — removed on request: it read as noise.)
    if views is not None and views >= 30 and not watchers:
        add("lower_price", "Lower the price",
            f"{views} views but no watchers — buyers are looking; the price may be high.", 92)
    if views is not None and views < 5 and age and age >= 7:
        add("promote", "Promote",
            f"Only {views} views in {age} days — promote it to reach more buyers.", 90,
            rate_=rate)

    # Heuristics that need no eBay metrics.
    if not promoted:
        add("promote", "Promote",
            "Not promoted yet — promoted listings show up far more often.", 70,
            rate_=rate)
    if age is not None and age >= STALE_DAYS:
        add("lower_price", "Lower the price",
            f"Live {age} days — a price drop can restart interest.", 68)
    if len(images) < FEW_PHOTOS:
        n = len(images)
        add("photos", "Add more photos",
            f"Only {n} photo{'' if n == 1 else 's'} — more angles mean more sales.", 50)
    if listing.get("missing_info"):
        add("specifics", "Fill in details",
            "Some fields buyers filter by are still blank.", 45)
    return recs


def recommendations(items: list[dict], metrics_by_id: Optional[dict] = None,
                    rates_by_id: Optional[dict] = None,
                    promoted_ids: Optional[set] = None, limit: int = 8) -> list[dict]:
    """Ranked recommendations across many listing records (best first). Keeps
    the single strongest action per listing so the list spans the whole
    portfolio instead of piling onto one item."""
    metrics_by_id = metrics_by_id or {}
    rates_by_id = rates_by_id or {}
    promoted_ids = promoted_ids or set()
    best: dict[str, dict] = {}
    ranked_all: list[dict] = []
    for it in items:
        ranked_all.extend(recommend_for(
            it, metrics=metrics_by_id.get(it.get("id")),
            rate=rates_by_id.get(it.get("id")),
            promoted=it.get("id") in promoted_ids))
    for r in sorted(ranked_all, key=lambda x: -x["priority"]):
        if r["listing_id"] not in best:
            best[r["listing_id"]] = r
    return sorted(best.values(), key=lambda x: -x["priority"])[:limit]
