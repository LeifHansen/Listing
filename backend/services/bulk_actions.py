"""Applying one suggested action to many listings at once.

The dashboard's suggested actions are already grouped ("Lower prices · 12"),
and until now the only thing a seller could do with a group was open each
listing and repeat the same edit twelve times. These are the group-level verbs:
work out the change per listing, push it to the marketplace, and report what
happened item by item.

The rules that matter for a bulk edit, as opposed to a single one:

  * Nothing is implicit about scope. The caller names the listings, so a group
    of twelve can never turn into the seller's whole store.
  * A listing that can't take the change is SKIPPED with a reason, not failed.
    Bulk actions run over whatever the recommendation engine grouped, which
    includes listings that have since sold or ended.
  * One listing's failure never stops the run. The eBay call happens per
    listing, and a rejected revise on item three must not strand items four
    through twelve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import log
from ..money import charm_price

# Below this, eBay's own minimums and the seller's shipping costs make the
# listing pointless — and a rounding-to-zero price is a bug, not a discount.
MIN_PRICE = 0.99
# A bulk cut bigger than this is more likely a slipped decimal point than an
# intention. The editor is still there for a genuine 95%-off clearance.
MAX_PERCENT = 75.0


@dataclass
class BulkResult:
    """What a bulk run did, per listing and in total."""
    changed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "changed": len(self.changed), "skipped": len(self.skipped),
            "failed": len(self.failed),
            "total": len(self.changed) + len(self.skipped) + len(self.failed),
            "results": {"changed": self.changed, "skipped": self.skipped,
                        "failed": self.failed},
        }


def lower_price(current: float, percent: float) -> Optional[float]:
    """`current` reduced by `percent`, or None when the cut can't apply.

    Landed on a .99 the way every price this app chooses is (a 20% cut off
    $25.00 is $20.00, and a whole dollar is the one thing these must not be),
    floored at MIN_PRICE, and None when the listing has no usable price or the
    result wouldn't actually be lower — a "changed" count that includes no-ops
    is a lie about what the bulk action did.

    Except where the .99 would undo the cut. Landing on the nearest .99 moves
    the price by up to half a dollar in either direction, and on a cheap
    listing that is more than the whole cut: 10% off $4.99 is $4.49, whose
    nearest .99 is the $4.99 it started on. That used to come back as a no-op
    and a skip, so "Lower all" skipped every listing under about $5 at its
    default 10% — while the suggestion, which the cut never cleared, stayed on
    screen asking for it. The seller asked for a cut, so where the charm point
    would take it away they get the cut itself, to the cent and rounded down.
    """
    try:
        price = round(float(current or 0), 2)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    exact = price * (1 - percent / 100.0)
    new_price = charm_price(exact)
    if new_price is None or new_price >= price:
        # Rounded to six places first so float noise (448.99999…) cannot
        # floor a whole cent off the cut.
        new_price = math.floor(round(exact * 100, 6)) / 100
    if new_price < MIN_PRICE:
        new_price = MIN_PRICE
    return new_price if new_price < price else None


def validate_percent(percent) -> float:
    """The requested percentage, or ValueError with what a seller can act on."""
    try:
        value = float(percent)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a percentage to lower prices by.") from exc
    if value <= 0:
        raise ValueError("Enter a percentage above 0 to lower prices by.")
    if value > MAX_PERCENT:
        raise ValueError(
            f"That's a {value:g}% cut — bulk price drops are capped at "
            f"{MAX_PERCENT:g}%. Open a listing to discount it further.")
    return value


def run(records: list[dict], apply_one: Callable[[dict], dict],
        on_each: Optional[Callable[[int, str], None]] = None) -> BulkResult:
    """`apply_one` over each record, collecting outcomes.

    `apply_one` returns {"ok": True, ...} to count as changed, {"skip":
    "reason"} to skip, or raises to fail — and a raise is contained here so the
    rest of the run continues.

    A skip may add "needs_you": False to say that nothing about it is the
    seller's to do — a sold listing cannot be revised by anyone, and counting
    it under "still need you" asks them for work that does not exist. The
    default is True, so a reason that has not thought about it is still put in
    front of them; the lie worth avoiding is the other one.

    `on_each(index, title)` (optional) is told which listing is next, before
    it is started — for a run polled as a background job, which has to say
    where it is while it is there.
    """
    result = BulkResult()
    for i, rec in enumerate(records):
        rid = rec.get("id") or ""
        title = ((rec.get("listing") or {}).get("title")
                 or rec.get("title") or "this listing")
        if on_each:
            on_each(i, title)
        try:
            outcome = apply_one(rec) or {}
        except Exception as exc:  # noqa: BLE001 - one listing must not sink the run
            log.warning("bulk action failed for %s: %s", rid, exc)
            result.failed.append({"listing_id": rid, "title": title,
                                  "message": str(exc)[:200]})
            continue
        if outcome.get("skip"):
            result.skipped.append({"listing_id": rid, "title": title,
                                   "message": outcome["skip"],
                                   "needs_you": outcome.get("needs_you", True)})
        elif outcome.get("ok"):
            result.changed.append({"listing_id": rid, "title": title,
                                   **{k: v for k, v in outcome.items()
                                      if k != "ok"}})
        else:
            result.failed.append({
                "listing_id": rid, "title": title,
                "message": outcome.get("message") or "Couldn't apply the change."})
    return result
