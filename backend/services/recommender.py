"""Seller action recommendations — the app's 'what should I do next' engine.

Rules over the signals we already have (listing status, age, price, photos,
promotion, missing details) turn a pile of listings into a short, ranked list
of concrete next actions: finish a draft, promote a live one, drop a stale
price, add photos, fill in missing details.

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


# "Fill in details" fills ONE thing: eBay's item specifics for the listing's
# category, read off its own photos. So the question that decides whether to
# offer it is "are those specifics still blank", and nothing else.
#
# It used to be decided by missing_info instead -- any note the AI or the app
# had left on the listing -- and that could not work, because a note is
# evidence of the opposite. Every draft runs the same fill at draft time and
# then drops the notes it answered, so a note still on a listing is one the
# fill has ALREADY failed to answer once. Pressing the button re-ran that
# same pass, was charged for it, added nothing, and left the note in place --
# so the suggestion never went away and the count never moved. Narrowing
# WHICH notes counted (2026-09-02) made the group smaller without breaking
# the loop; the count is what breaks it.
#
# Below this many filled specifics, the fill has real room to work. This is
# the CHEAP proxy for the exact truth -- how many of THIS category's aspects
# are unanswered -- which needs eBay's aspect list for the category and so
# cannot simply be asked for a whole store at a time. It is never wrong in the
# direction that matters: a listing with nothing filled is always one the fill
# can help.
#
# It is a proxy, though, and it is blind in one direction: a listing with
# Material, Type and Brand filled has three specifics and passes this, while
# Subject, Era, Occasion, Packaging and Character sit blank and eBay's own
# suggester offers all five to the seller on the next screen. So the caller
# now counts the real thing where it can afford to (main._blank_specifics_by_id
# spends a small budget of cached Taxonomy lookups per dashboard load) and
# passes it as `blank_specifics`; this stands wherever it could not.
MIN_SPECIFICS = 3

# ...and how many have to be BLANK, when the caller could afford to ask eBay
# which aspects this listing's category actually publishes. One empty box is
# not an errand; three is the difference between a listing buyers can filter
# to and one they cannot.
MIN_BLANK_SPECIFICS = 3


def filled_specifics(listing: dict) -> int:
    """How many of a listing's item specifics actually carry a value."""
    return sum(1 for s in (listing.get("item_specifics") or [])
               if isinstance(s, dict) and str(s.get("value") or "").strip())


def recommend_for(item: dict, metrics: Optional[dict] = None,
                  rate: Optional[float] = None, promoted: bool = False,
                  promotion_known: bool = True,
                  blank_specifics: Optional[int] = None) -> list[dict]:
    """Recommended actions for ONE listing record. Each rec:
    {listing_id, listing_title, type, label, reason, action, priority, rate}.
    `rate` is eBay's recommended Promoted Listings ad rate (%) for this listing,
    carried on promote recs so the UI can one-click promote at that rate.
    Higher priority = surface sooner.

    `promotion_known` is whether eBay actually answered when asked which
    listings already have ads. When it did not, no promote recommendation is
    made at all: promoting costs the seller a percentage of the sale, and
    `promoted=False` from an unanswered lookup is not evidence that a listing
    is unpromoted — it is the absence of evidence either way. Defaults True so
    a caller that does not pass it keeps its recommendations.

    `blank_specifics` is how many of eBay's item specifics for this listing's
    category it currently holds no value for, counted against eBay's own
    aspect list — see the "Fill in details" rule below. None means nobody
    counted (no category, the Taxonomy API down, or past the lookup budget one
    dashboard load may spend), and the rule falls back to `filled_specifics`.
    """
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
    # (No "Relist" nudge on an ended listing — removed on request: relisting is
    # done by hand, and the ended bucket picks up SOLD items too, because the
    # sync reconciles finished listings from eBay's unsold list and settles on
    # "ended" whenever a sale is missed. Offering to relist something already
    # sold is worse than offering nothing. An ended record now falls through to
    # the guard below and earns no recommendation at all.)
    if status not in ("published", "live"):
        return recs

    m = metrics or {}
    views = m.get("views")
    watchers = m.get("watchers")
    age = _age_days(item.get("created_at"))
    images = listing.get("images") or listing.get("image_urls") or []
    # Promoted if our own flag says so OR eBay reports a live ad (covers ads
    # created straight in Seller Hub) — so we never nag an already-promoted item.
    promoted = bool(listing.get("promote")) or promoted

    # Data-driven (real eBay traffic) beats the age heuristics below.
    # (No "Add a sale" nudge — removed on request: it read as noise.)
    if views is not None and views >= 30 and not watchers:
        add("lower_price", "Lower the price",
            f"{views} views but no watchers — buyers are looking; the price may be high.", 92)
    if (promotion_known and views is not None and views < 5
            and age and age >= 7):
        add("promote", "Promote",
            f"Only {views} views in {age} days — promote it to reach more buyers.", 90,
            rate_=rate)

    # Heuristics that need no eBay metrics.
    if promotion_known and not promoted:
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
    notes = [n for n in (listing.get("missing_info") or [])
             if str(n or "").strip()]
    # Two signals decide this, and they answer the same question at different
    # prices.
    #
    # `blank_specifics` is the TRUTH: how many of the aspects eBay publishes
    # for this listing's category it holds no value for, counted by the caller
    # (main._blank_specifics_by_id) against eBay's own aspect list. It is what
    # the group is actually about, and it is the only one of the two that can
    # see the case this app was shipping: a listing with Material, Type and
    # Brand filled and Subject, Era, Occasion, Packaging and Character blank
    # has plenty of specifics and is still missing the ones eBay's own
    # suggester offers the seller on the next screen.
    #
    # `filled_specifics` is the PROXY, and it is what stands when nobody
    # counted — no category on the listing, the Taxonomy API down, or the
    # store's categories past the lookup budget one dashboard load may spend
    # on a shared eBay allowance. It is never wrong in the direction that
    # matters: a listing with nothing filled is always one the fill can help.
    #
    # `enriched_at` is what ENDS it, and neither count can. Set whenever the
    # fill actually ran — including the run that added nothing, which is the
    # one that matters — it is the difference between "these specifics are
    # blank" and "these specifics are blank and the AI has already looked".
    # Without it a listing whose photos genuinely cannot answer its category
    # sits in the group forever, is charged for on every press, and moves the
    # count not at all: the loop a seller reads, correctly, as the button not
    # working. What is left for them then is to LOOK, which is the other rec.
    enriched = str(listing.get("enriched_at") or "").strip()
    have = filled_specifics(listing)
    if blank_specifics is None:
        worth_filling = have < MIN_SPECIFICS
        reason = ("None of eBay's item specifics are filled in — buyers filter "
                  "by these." if not have else
                  f"Only {have} of eBay's item specifics "
                  f"{'is' if have == 1 else 'are'} filled in — buyers filter "
                  "by these.")
    else:
        worth_filling = blank_specifics >= MIN_BLANK_SPECIFICS
        reason = (f"{blank_specifics} of eBay's item specifics are still blank "
                  "— buyers filter by these.")
    if not enriched and worth_filling:
        add("specifics", "Fill in details", reason, 45)
    elif notes:
        # Notes on a listing whose specifics are filled are what the fill
        # could NOT answer: a measurement, an authentication, a flaw only the
        # person holding it can see. They earn a nudge to LOOK, never a button
        # that would charge for the same empty pass again.
        n = len(notes)
        add("verify", "Check details",
            f"{n} thing{'' if n == 1 else 's'} the AI left for you to check.", 40)
    return recs


def recommendations(items: list[dict], metrics_by_id: Optional[dict] = None,
                    rates_by_id: Optional[dict] = None,
                    promoted_ids: Optional[set] = None,
                    promotion_known: bool = True, limit: int = 8,
                    blanks_by_id: Optional[dict] = None) -> list[dict]:
    """Ranked recommendations across many listing records (best first). Keeps
    the single strongest action per listing so the list spans the whole
    portfolio instead of piling onto one item."""
    metrics_by_id = metrics_by_id or {}
    rates_by_id = rates_by_id or {}
    promoted_ids = promoted_ids or set()
    blanks_by_id = blanks_by_id or {}
    # Keep the strongest action per listing as they are generated, rather than
    # collecting every rec across the whole store and sorting the lot to throw
    # most of it away. A mirrored store is thousands of listings and each one
    # yields several recs, so the discarded list was the large one.
    #
    # Ties keep the FIRST rec seen for a listing, which is what sorting the
    # flat list did too (sorted() is stable and recommend_for emits in its own
    # deliberate order — the metrics-driven advice before the age heuristics).
    best: dict[str, dict] = {}
    for it in items:
        for r in recommend_for(
                it, metrics=metrics_by_id.get(it.get("id")),
                rate=rates_by_id.get(it.get("id")),
                promoted=it.get("id") in promoted_ids,
                promotion_known=promotion_known,
                blank_specifics=blanks_by_id.get(it.get("id"))):
            held = best.get(r["listing_id"])
            if held is None or r["priority"] > held["priority"]:
                best[r["listing_id"]] = r
    return sorted(best.values(), key=lambda x: -x["priority"])[:limit]
