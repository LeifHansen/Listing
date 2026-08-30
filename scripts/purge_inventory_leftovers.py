"""Delete the inventory items and unpublished offers old drafts left on eBay.

Saving a draft on a connected account used to create an inventory item and an
UNPUBLISHED offer on the seller's real eBay account (services/ebay.py's
`_push_live` with do_publish=False). That stopped -- drafts never touch eBay
now, and the Inventory publish engine is deleted -- but whatever earlier
drafts created is still sitting on the account.

It is invisible litter: inventory-based records don't appear in Seller Hub, so
a seller can neither see nor remove them, and nothing in the app can either
now that the Inventory client is gone. This script is the way to clear them.

    # see what would go, touching nothing
    python3 scripts/purge_inventory_leftovers.py --user <user-id>

    # actually delete
    python3 scripts/purge_inventory_leftovers.py --user <user-id> --apply

Safety, in order of how much it matters:

  - Dry run unless --apply is passed.
  - Only SKUs this app minted (the THRYFT- prefix) are ever considered, so
    inventory the seller created with another tool is never touched.
  - An item whose offer is PUBLISHED is a LIVE LISTING. It is skipped and
    reported, never deleted -- deleting one would end a real listing.

Run it from the repo root with the app's environment (DATABASE_URL, the eBay
client credentials), the same way the app runs.
"""
from __future__ import annotations

import argparse
import sys

import httpx

sys.path.insert(0, ".")

from backend import config, db, ebay_auth  # noqa: E402
from backend.services.ebay import rest_headers  # noqa: E402

# What sku_for() has always produced. Nothing else may be deleted.
SKU_PREFIX = "THRYFT-"
PAGE = 100


def _paged_skus(client: httpx.Client, token: str) -> list[str]:
    """Every inventory SKU on the account, oldest page first."""
    skus, offset = [], 0
    while True:
        r = client.get(f"{config.EBAY_API_BASE}/sell/inventory/v1/inventory_item",
                       headers=rest_headers(token),
                       params={"limit": PAGE, "offset": offset})
        r.raise_for_status()
        body = r.json()
        page = [i.get("sku", "") for i in (body.get("inventoryItems") or [])]
        skus.extend(s for s in page if s)
        if len(page) < PAGE:
            return skus
        offset += PAGE


def is_app_sku(sku: str) -> bool:
    """True only for a SKU this app minted.

    The one thing standing between this script and inventory the seller
    created with some other tool. sku_for() has always produced
    f"THRYFT-{session_id}".upper(), so the prefix is the whole test.
    """
    return (sku or "").upper().startswith(SKU_PREFIX)


def live_listing_ids(offers: list[dict]) -> list[str]:
    """Listing ids of any PUBLISHED offer among `offers`.

    A published offer IS a live listing. Deleting its inventory item ends
    that listing, so a non-empty answer here means hands off. An offer whose
    status is missing or unreadable is deliberately NOT treated as safe --
    only an explicitly non-PUBLISHED status clears an item for deletion.
    """
    return [str((o.get("listing") or {}).get("listingId") or o.get("offerId") or "?")
            for o in offers
            if str(o.get("status", "")).upper() != "UNPUBLISHED"]


def _offers(client: httpx.Client, token: str, sku: str) -> list[dict]:
    r = client.get(f"{config.EBAY_API_BASE}/sell/inventory/v1/offer",
                   headers=rest_headers(token), params={"sku": sku})
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json().get("offers") or []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="app user id whose eBay account to clean")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()

    # "No connected eBay account" must mean exactly that. A database that is
    # unreachable, unconfigured or refusing the connection reported the same
    # sentence, which sends the operator looking in entirely the wrong place —
    # and this is a script whose next step deletes things on a live account.
    #
    # db.get_ebay_account raises StorageUnavailable on a failed read now
    # (it used to swallow it and answer None), so the distinction is made for
    # us; the checks below still come first because they name the cause
    # precisely, and the except covers a connection that dies between them.
    if not config.DATABASE_URL:
        print("DATABASE_URL is not set, so there are no stored accounts to "
              "read. Run this with the app's environment.")
        return 1
    try:
        db._get_engine()
    except Exception as exc:  # noqa: BLE001 - report it, don't traceback
        print(f"Could not reach the database: {exc}")
        return 1

    try:
        account = db.get_ebay_account(args.user)
    except db.StorageUnavailable as exc:
        print(f"Could not read the stored eBay account: {exc}")
        print("Nothing was deleted. Fix the connection and run this again.")
        return 2
    if not account or not account.get("refresh_token"):
        print(f"No connected eBay account for user {args.user}. "
              "(Pass the app user id, not the eBay username.)")
        return 1
    token = ebay_auth.refresh_access_token(account["refresh_token"])["access_token"]
    who = account.get("ebay_username") or "(username not recorded)"
    print(f"Account: {who}   mode: {'APPLY' if args.apply else 'dry run'}\n")

    removed = live = failed = 0
    with httpx.Client(timeout=30) as client:
        ours = [s for s in _paged_skus(client, token) if is_app_sku(s)]
        print(f"{len(ours)} inventory item(s) minted by this app.\n")
        for sku in ours:
            offers = _offers(client, token, sku)
            unsafe = live_listing_ids(offers)
            if unsafe:
                print(f"  SKIP  {sku}  (live listing: {', '.join(unsafe)})")
                live += 1
                continue
            what = f"{len(offers)} offer(s) + item" if offers else "item only"
            if not args.apply:
                print(f"  would delete  {sku}  ({what})")
                removed += 1
                continue
            try:
                for offer in offers:
                    dr = client.delete(
                        f"{config.EBAY_API_BASE}/sell/inventory/v1/offer/{offer['offerId']}",
                        headers=rest_headers(token))
                    dr.raise_for_status()
                di = client.delete(
                    f"{config.EBAY_API_BASE}/sell/inventory/v1/inventory_item/{sku}",
                    headers=rest_headers(token))
                di.raise_for_status()
                print(f"  deleted  {sku}  ({what})")
                removed += 1
            except httpx.HTTPError as exc:
                print(f"  FAILED   {sku}: {exc}")
                failed += 1

    verb = "deleted" if args.apply else "would delete"
    print(f"\n{verb} {removed}; skipped {live} live; {failed} failed.")
    if not args.apply and removed:
        print("Re-run with --apply to delete them.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
