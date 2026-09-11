"""Seller action recommendations — the app's 'what should I do next' engine.

Rules over the signals we already have (listing status, age, price, photos,
missing details) turn a pile of listings into a short, ranked list of concrete
next actions: finish a draft, drop a stale price, add photos, fill in missing
details.

eBay traffic (views/watchers), when available, sharpens these: a listing with
lots of views but no watchers is priced too high. Pass a per-listing metrics
dict ({views, watchers}) to fold those in — without it, the age heuristics
still produce useful advice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STALE_DAYS = 21   # a live listing this old with no sale → nudge price/sale
FEW_PHOTOS = 3    # fewer than this → suggest adding photos

# What a price drop BUYS: neither price nudge below comes back until the new
# price has had this long to be seen.
#
# Both of them are computed from signals a drop does not move. The age
# heuristic counts from `created_at`, which never changes. The traffic one
# reads eBay's view count, which is cumulative for the life of the listing —
# the thirty views that earned "buyers are looking; the price may be high" are
# still thirty views the second after the price comes down, and stay so
# forever. So the group a seller had just cleared came straight back, same
# listings, same count, saying the same thing. Reported as the button not
# working, and from the outside it is not distinguishable from that.
#
# The same three weeks a listing gets before it is called stale in the first
# place. A new price deserves at least the run the old one got, and nothing is
# lost by waiting: if the cut does not work, the nudge is right again — and
# comes back on its own, worded from the drop rather than from the listing's
# birthday.
PRICE_QUIET_DAYS = STALE_DAYS

# How long a listing gets left alone after the AI fill has run on it, before
# "Check details" is allowed to nudge about the notes the fill could not
# answer.
#
# This exists because of what the seller actually sees. They open Home, find
# "Fill in details · 12" with a button on it, press the button, and wait
# several minutes while the AI reads twelve listings' photos and pushes the
# new specifics to eBay. It works. And the group they just cleared is
# replaced, in the same slot, by "Check details · 12" — the same twelve
# listings, still flagged, and this time with NO button on the group at all:
# just a list to open one at a time.
#
# Read from the outside that is indistinguishable from the button having done
# nothing, which is exactly how it was reported ("it should fill in all
# possible missing fields... I don't know why you keep showing me a list
# view... they are not updating as far as I can tell"). The notes behind it
# are real — "exact measurements", "confirm the signature" — but they are, by
# construction, the things the fill just declined to invent, and turning them
# into a fresh chore in the same minute asks the seller to finish work they
# have this second asked the app to finish for them.
#
# So the nudge waits a day. Nothing is lost: these notes have been on the
# listing since it was drafted and are not urgent, and after the quiet period
# they come back exactly as before.
VERIFY_QUIET_DAYS = 1


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


def _price(value) -> Optional[float]:
    """`value` as a comparable price, or None when it isn't one."""
    try:
        price = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def price_drop_stamp(stored: dict, new_price) -> str:
    """The `price_lowered_at` to persist for a listing being saved at
    `new_price`, given the record already stored for it.

    Now when this write actually LOWERS the asking price; otherwise whatever
    the stored record already held. This is the writer for the field the two
    price rules above read, and it lives beside them so the two cannot drift.

    Every write path derives it this way — from the stored price and nothing
    else — rather than honouring what arrived in the payload. That is what
    makes the field server-owned in the way that matters here: a second tab
    saving a copy loaded this morning cannot blank the stamp, and a payload
    cannot mint one to silence advice the listing has earned. It is also why
    the field is absent from state.SERVER_OWNED_FIELDS, whose rule is that the
    STORED value wins — under that rule the stamp could never move forward on
    the one write that is entitled to move it.
    """
    was, now_ = _price((stored or {}).get("price")), _price(new_price)
    if was is not None and now_ is not None and now_ < was:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return str((stored or {}).get("price_lowered_at") or "").strip()


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
                  blank_specifics: Optional[int] = None) -> list[dict]:
    """Recommended actions for ONE listing record. Each rec:
    {listing_id, listing_title, type, label, reason, action, priority}.
    Higher priority = surface sooner.

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
            action: str = "open"):
        recs.append({"listing_id": lid, "listing_title": title, "type": type_,
                     "label": label, "reason": reason, "action": action,
                     "priority": priority})

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
    # How long ago the asking price was last CUT, in days — None when it never
    # has been. Both price rules below are gated on it, because neither of the
    # signals they read moves when a seller takes the advice: see
    # PRICE_QUIET_DAYS above, and `price_lowered_at` on the model.
    since_cut = _age_days(str(listing.get("price_lowered_at") or "").strip() or None)
    quiet = since_cut is not None and since_cut < PRICE_QUIET_DAYS

    # Data-driven (real eBay traffic) beats the age heuristics below.
    # (No "Add a sale" nudge — removed on request: it read as noise.)
    if views is not None and views >= 30 and not watchers and not quiet:
        add("lower_price", "Lower the price",
            f"{views} views but no watchers — buyers are looking; the price may be high.", 92)

    # Heuristics that need no eBay metrics. The stale clock runs from the last
    # price cut where there has been one: what this rule is actually about is
    # how long the CURRENT price has been sitting there, and on a listing that
    # has been marked down twice the date it went up is no longer that.
    if since_cut is not None:
        if since_cut >= STALE_DAYS:
            add("lower_price", "Lower the price",
                f"Still here {since_cut} days after the last price drop — "
                "another cut can restart interest.", 68)
    elif age is not None and age >= STALE_DAYS:
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
    # How long ago the fill last ran on this listing, in days — None when it
    # never has. The quiet period below is the only thing that reads it.
    since_filled = _age_days(enriched) if enriched else None
    if not enriched and worth_filling:
        add("specifics", "Fill in details", reason, 45)
    elif notes and (since_filled is None or since_filled >= VERIFY_QUIET_DAYS):
        # Notes on a listing whose specifics are filled are what the fill
        # could NOT answer: a measurement, an authentication, a flaw only the
        # person holding it can see. They earn a nudge to LOOK, never a button
        # that would charge for the same empty pass again — and never in the
        # minutes right after the fill ran, which is the whole point of
        # VERIFY_QUIET_DAYS above.
        n = len(notes)
        add("verify", "Check details",
            f"{n} thing{'' if n == 1 else 's'} the AI left for you to check.", 40)
    return recs


def ranked(items: list[dict], metrics_by_id: Optional[dict] = None,
           blanks_by_id: Optional[dict] = None) -> list[dict]:
    """Every listing's strongest recommendation, best first. UNCAPPED.

    The cap belongs to whoever is rendering these, not to the ranking — and
    it has to be applied somewhere that can still say how many were left out.
    A count taken after a truncation is not a count of anything (see
    totals_by_type).
    """
    metrics_by_id = metrics_by_id or {}
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
                blank_specifics=blanks_by_id.get(it.get("id"))):
            held = best.get(r["listing_id"])
            if held is None or r["priority"] > held["priority"]:
                best[r["listing_id"]] = r
    return sorted(best.values(), key=lambda x: -x["priority"])


def totals_by_type(recs: list[dict]) -> dict[str, int]:
    """How many listings each recommendation type covers — counted on the
    WHOLE ranking, before any cap.

    This is the number the dashboard's group badge shows, and it has to be
    counted here because nothing downstream can. The badge used to be the
    length of the list that arrived, which on a store with more suggestions
    than the payload cap is the cap, not the count: a seller with 80 listings
    needing "Fill in details" read 50, pressed "Enrich all", watched 25 of
    them get filled and pushed to eBay — and came back to a badge still
    reading 50, because 25 that had been below the line took their place. The
    work happened; the number could not show it. That reads exactly like a
    button that does nothing, and it is the second time this group has been
    reported for it.
    """
    totals: dict[str, int] = {}
    for r in recs:
        totals[r["type"]] = totals.get(r["type"], 0) + 1
    return totals


def capped_by_type(recs: list[dict], per_type: int) -> list[dict]:
    """`recs` trimmed to at most `per_type` of each type, order preserved.

    The dashboard groups these by type, so a flat cap is the wrong shape for
    it twice over. It makes a group's membership depend on how busy the OTHER
    groups are — and past 50 stale prices it drops a whole group off the
    screen, taking the only button that clears it with it. Per type, every
    group that exists is reachable, and the payload stays bounded by the
    number of types.
    """
    kept: dict[str, int] = {}
    out = []
    for r in recs:
        seen = kept.get(r["type"], 0)
        if per_type and seen >= per_type:
            continue
        kept[r["type"]] = seen + 1
        out.append(r)
    return out


def recommendations(items: list[dict], metrics_by_id: Optional[dict] = None,
                    limit: int = 8,
                    blanks_by_id: Optional[dict] = None) -> list[dict]:
    """Ranked recommendations across many listing records (best first). Keeps
    the single strongest action per listing so the list spans the whole
    portfolio instead of piling onto one item.

    `limit` is a flat cap across every type. Callers that render these in
    GROUPS want `ranked` + `capped_by_type` + `totals_by_type` instead: a
    group cannot say how big it is from a list that was cut to fit.
    """
    return ranked(items, metrics_by_id=metrics_by_id,
                  blanks_by_id=blanks_by_id)[:limit]
