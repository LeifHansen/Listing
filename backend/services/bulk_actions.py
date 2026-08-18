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

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import log

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

    Rounded to cents the way a seller would write it, floored at MIN_PRICE, and
    None when the listing has no usable price or the result wouldn't actually
    be lower — a "changed" count that includes no-ops is a lie about what the
    bulk action did.
    """
    try:
        price = round(float(current or 0), 2)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    new_price = round(price * (1 - percent / 100.0), 2)
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


def run(records: list[dict], apply_one: Callable[[dict], dict]) -> BulkResult:
    """`apply_one` over each record, collecting outcomes.

    `apply_one` returns {"ok": True, ...} to count as changed, {"skip":
    "reason"} to skip, or raises to fail — and a raise is contained here so the
    rest of the run continues.
    """
    result = BulkResult()
    for rec in records:
        rid = rec.get("id") or ""
        title = ((rec.get("listing") or {}).get("title")
                 or rec.get("title") or "this listing")
        try:
            outcome = apply_one(rec) or {}
        except Exception as exc:  # noqa: BLE001 - one listing must not sink the run
            log.warning("bulk action failed for %s: %s", rid, exc)
            result.failed.append({"listing_id": rid, "title": title,
                                  "message": str(exc)[:200]})
            continue
        if outcome.get("skip"):
            result.skipped.append({"listing_id": rid, "title": title,
                                   "message": outcome["skip"]})
        elif outcome.get("ok"):
            result.changed.append({"listing_id": rid, "title": title,
                                   **{k: v for k, v in outcome.items()
                                      if k != "ok"}})
        else:
            result.failed.append({
                "listing_id": rid, "title": title,
                "message": outcome.get("message") or "Couldn't apply the change."})
    return result
