"""One item, two marketplaces, one unit of stock.

A listing crossposted from eBay to Etsy is the same object in the same box:
when it sells on eBay, the Etsy copy is still taking orders. Nobody is
watching for that — the seller finds out when a second buyer pays for
something they no longer have, which on Etsy costs them a cancellation and
their star rating.

So the moment this app files an eBay listing as sold or ended, it takes the
Etsy copy down. Deactivated, never deleted: the listing stays in the
seller's Etsy shop as a draft they can reactivate if the eBay sale falls
through, which is also what `EtsyProvider.end` already does for the End
button.

Best-effort and in the background, because the alternative is worse in both
directions: a sale is not failed over an Etsy call, and a takedown that
cannot be made is not left silent either — it is written onto the listing's
Etsy state and raised as a notification, so the one listing still live
somewhere is the one the seller is told about.

The other direction does not exist yet: nothing reads the seller's Etsy
shop, so a sale ON Etsy cannot reach eBay. The chip on the card and the
crosspost wizard both say so rather than implying a mirror that is only
half built.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..config import log
from ..marketplaces import get as get_marketplace
from ..marketplaces.base import PublishContext
from ..models import Listing
from . import background

# The marketplaces an eBay sale can take down. Etsy today; the list is what
# makes adding the next one a line rather than a second copy of this module.
MIRRORED = ("etsy",)


def _live_elsewhere(data: dict) -> list[str]:
    states = (data or {}).get("marketplaces") or {}
    return [key for key in MIRRORED
            if ((states.get(key) or {}).get("status") == "published")]


def on_ebay_finished(user_id: Optional[str], record_id: str, data: dict,
                     why: str = "sold on eBay") -> list[str]:
    """An eBay listing has ended or sold: take its copies elsewhere down.

    Returns the marketplaces it went after, so a caller can log it; the work
    itself happens on a background thread. Returns early — before any
    provider lookup or database read — for the overwhelming majority of
    listings, which are on eBay alone.
    """
    keys = _live_elsewhere(data)
    if not keys or not user_id or not record_id:
        return []
    for key in keys:
        background.run_in_background(
            _deactivate, key, user_id, record_id, data, why,
            what=f"{key} takedown after an eBay ending")
    return keys


def _deactivate(key: str, user_id: str, record_id: str, data: dict,
                why: str) -> None:
    provider = get_marketplace(key)
    if provider is None:      # withheld by config, or gone
        return
    creds = provider.creds_for(user_id)
    listing = Listing(**{k: v for k, v in (data or {}).items()
                         if k in Listing.model_fields})
    ctx = PublishContext(session_id=record_id, listing=listing, mode="live",
                         base_url="", uid=user_id,
                         prev_record={"listing": data or {}})
    label = getattr(provider, "label", key)
    try:
        if not creds:
            raise ValueError(f"{label} isn't connected any more.")
        provider.end(ctx, creds)
    except Exception as exc:  # noqa: BLE001 - reported, never raised at a sale
        log.warning("%s takedown failed for %s: %s", key, record_id, exc)
        _report_still_live(key, label, user_id, record_id, data, exc)
        return
    log.info("%s: %s deactivated after the eBay listing %s", key, record_id, why)

    def _mark(stored: dict) -> dict:
        states = stored.setdefault("marketplaces", {})
        entry = dict(states.get(key) or {})
        entry.update(status="ended", error="")
        states[key] = entry
        return stored

    db.mutate_listing_data(record_id, _mark, user_id=user_id)


def _report_still_live(key: str, label: str, user_id: str, record_id: str,
                       data: dict, exc: Exception) -> None:
    """Say so on the listing AND in the bell.

    A takedown that failed is the one case where the seller has to act: the
    item is sold and still for sale somewhere. Written to the marketplace
    entry (so the card's chip turns amber with the reason) and raised once
    per listing as a notification, because nobody reads a card they are not
    already looking at.
    """
    message = (f"Sold on eBay, but we couldn't take it off {label} — "
               f"it may still be for sale there. ({exc})")

    def _mark(stored: dict) -> dict:
        states = stored.setdefault("marketplaces", {})
        entry = dict(states.get(key) or {})
        entry["error"] = message
        states[key] = entry
        return stored

    try:
        db.mutate_listing_data(record_id, _mark, user_id=user_id)
        title = (data or {}).get("title") or "An item"
        db.add_notification(
            user_id, f"{key}_still_live",
            f"“{title}” may still be for sale on {label}",
            body=message, listing_id=record_id,
            dedupe_key=f"{key}-still-live:{record_id}")
    except Exception as write_exc:  # noqa: BLE001 - never fail a sale over this
        log.warning("could not record the %s takedown failure for %s: %s",
                    key, record_id, write_exc)
