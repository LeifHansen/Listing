"""eBay as a MarketplaceProvider.

This is the existing eBay publish pipeline MOVED out of main.py, not
rewritten: the imported-listing revise/relist branch, the Trading-API path
for new live listings, preflight gating, ship-from self-heal, the Inventory
API fallback (drafts/dry-runs/revises) and Promoted Listings all behave
exactly as before. main.py keeps thin same-named wrappers so every other
/api/ebay/* route is untouched.

PublishOutcome.raw carries the exact legacy JSON body for each branch, so
the orchestrator can return it verbatim on single-eBay publishes.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import HTTPException

from .. import config, db, ebay_auth, ebay_errors, storage
from ..config import log
from ..models import Listing
from ..services import (ebay, ebay_trading, image_import, listing_sync,
                        preflight, promotions, taxonomy)
from ..services.background import run_in_background
from . import register
from .base import PublishContext, PublishOutcome
from .state import STICKY_STATUSES

# Access tokens live ~2 hours; refreshing one per request added a serial
# ~300-600ms eBay round-trip to every creds-needing call (a dashboard load
# fires several). Keyed by the refresh token itself, so a reconnect (new
# refresh token) naturally misses the cache.
_TOKEN_CACHE: dict[str, tuple[float, str]] = {}


def access_token_for(refresh_token: str) -> str:
    hit = _TOKEN_CACHE.get(refresh_token)
    if hit and time.time() < hit[0]:
        return hit[1]
    fresh = ebay_auth.refresh_access_token(refresh_token)
    # Drop the cached token 90s before eBay's expiry so an in-flight request
    # never carries one that dies mid-call.
    expires = float(fresh.get("expires_at") or (time.time() + 1800))
    if len(_TOKEN_CACHE) > 50:
        _TOKEN_CACHE.clear()
    _TOKEN_CACHE[refresh_token] = (max(time.time() + 60, expires - 90),
                                   fresh["access_token"])
    return fresh["access_token"]


def creds_for(uid: Optional[str]) -> Optional[dict]:
    """Build live eBay creds for this user, or None if not connected."""
    if not uid:
        return None
    acct = db.get_ebay_account(uid)
    if not acct or not acct.get("refresh_token"):
        return None
    try:
        access_token = access_token_for(acct["refresh_token"])
    except Exception as exc:  # noqa: BLE001 - fall back to dry-run, but log it
        # A token-refresh outage otherwise looks identical to "not connected"
        # and silently dry-runs a live publish; log so it's debuggable.
        log.warning(f"ebay: token refresh failed for user {uid}: {exc}")
        return None
    return {
        "access_token": access_token,
        "fulfillment_policy_id": acct.get("fulfillment_policy_id", ""),
        "payment_policy_id": acct.get("payment_policy_id", ""),
        "return_policy_id": acct.get("return_policy_id", ""),
        "merchant_location_key": acct.get("merchant_location_key", ""),
        "ship_from_postal": acct.get("ship_from_postal", ""),
        "_uid": uid,
    }


def auto_promote_enabled(uid: Optional[str]) -> bool:
    """Account default: promote every newly published listing at eBay's
    recommended ad rate. ON unless explicitly turned off in Settings — sellers
    reported publishes landing unpromoted and only discovering it later from
    the Dashboard nags. Anonymous/env-token publishes stay explicit-only."""
    if not uid:
        return False
    try:
        value = db.get_prefs(uid).get("auto_promote")
    except Exception:  # noqa: BLE001 - prefs are optional
        return True
    return True if value is None else bool(value)


def promote(record_id: str, listing: Listing, creds: Optional[dict],
            rate: Optional[float] = None,
            ebay_listing_id: Optional[str] = None) -> dict:
    """Turn Promoted Listings on for one listing and run the ad call.

    The single place that decides an ad rate: an explicit `rate` wins, else
    eBay's recommendation for `ebay_listing_id`, else the default. A 0% rate
    makes promote_listing silently no-op — which is exactly why listings with
    Promote toggled on were never actually promoted — so the rate is always
    filled in here. Mutates listing.promote/ad_rate_percent so the caller can
    persist what really ran, and never raises: a promotion problem must not
    fail the publish that preceded it."""
    try:
        listing.promote = True
        if not rate or rate <= 0:
            rate = None
            if ebay_listing_id:
                recommended = promotions.suggested_ad_rates(
                    creds, [str(ebay_listing_id)])
                rate = recommended.get(str(ebay_listing_id))
        listing.ad_rate_percent = round(rate or promotions.DEFAULT_AD_RATE, 1)
        return promotions.promote_listing(record_id, listing, creds)
    except Exception as exc:  # noqa: BLE001 - promotion must never break publish
        log.warning("promote failed (%s): %s", record_id, exc)
        return {"promoted": False, "message": f"Promotion failed: {exc}"}


def refresh_eps_urls(listing_id: str, token: str, item_id: str,
                     uid: Optional[str]) -> None:
    """After eBay ingests our /media photo URLs on a revise, fetch the fresh
    EPS URLs it minted and store them as the listing's sync references. The
    local working copies remain the editable truth either way — these URLs
    only matter for unchanged-photo publishes and the read-only fallback."""
    fresh = ebay_trading.get_listing(token, item_id)
    urls = fresh.get("image_urls") or []
    if not urls:
        return

    def _set_urls(data: dict) -> dict:
        data["image_urls"] = urls
        return data

    # Locked merge of just this field: this runs on a background thread while
    # the publish request is still folding the OTHER marketplaces' outcomes
    # into the same row. Rewriting the whole blob from a stale read here used
    # to drop whichever side wrote second.
    if db.mutate_listing_data(listing_id, _set_urls, user_id=uid) is None:
        return
    log.info("EPS refresh: %s now references %d eBay-hosted photos",
             listing_id, len(urls))


def preflight_issues(uid: Optional[str], listing: Listing, mode: str) -> list[dict]:
    """Run the full pre-publish checklist for this user's account state."""
    creds = creds_for(uid)
    connected = bool(creds) or config.ebay_ready()
    if creds:
        fulfillment = listing.fulfillment_policy_id or creds.get("fulfillment_policy_id") or ""
        has_payment = bool(creds.get("payment_policy_id"))
        has_return = bool(creds.get("return_policy_id"))
        # A saved ship-from ZIP is enough: the Trading publish path sends the
        # postal code directly, and the Inventory path re-creates the eBay
        # location from that ZIP just before publishing. Demanding a
        # merchantLocationKey here blocked publishes that would have worked.
        has_location = bool(creds.get("merchant_location_key")
                            or creds.get("ship_from_postal"))
    else:
        fulfillment = listing.fulfillment_policy_id or config.EBAY_FULFILLMENT_POLICY_ID or ""
        has_payment = bool(config.EBAY_PAYMENT_POLICY_ID)
        has_return = bool(config.EBAY_RETURN_POLICY_ID)
        has_location = bool(config.EBAY_MERCHANT_LOCATION_KEY)

    # What the chosen shipping policy actually ships with (per-service weight
    # caps are the classic silent publish killer, e.g. Standard Envelope's 3 oz).
    services = (ebay_auth.fulfillment_policy_services(creds["access_token"], fulfillment)
                if creds and fulfillment else [])

    required = None
    if config.taxonomy_ready() and (listing.category_id or "").strip().isdigit():
        try:
            asp = taxonomy.item_aspects(listing.category_id)
            required = [a["name"] for a in asp.get("aspects", []) if a.get("required")]
        except Exception:  # noqa: BLE001 - aspects are a best-effort check
            required = None

    return preflight.validate(
        listing, mode,
        has_fulfillment=bool(fulfillment), has_payment=has_payment,
        has_return=has_return, has_location=has_location, connected=connected,
        policy_services=services, required_aspects=required)


def _view_url(listing: Listing, listing_id: str) -> str:
    """Public 'View on eBay' URL: the imported listing's own URL when eBay
    told us one, else the standard /itm/ form for the current environment."""
    if listing.view_url:
        return listing.view_url
    if not listing_id:
        return ""
    host = ("www.sandbox.ebay.com" if config.EBAY_ENV != "production"
            else "www.ebay.com")
    return f"https://{host}/itm/{listing_id}"


class EbayProvider:
    key = "ebay"
    label = "eBay"

    # --- configuration / connection -------------------------------------
    def oauth_ready(self) -> bool:
        return config.ebay_oauth_ready()

    def oauth_missing(self) -> list[str]:
        # Which server-side OAuth vars are absent (names only, never values) —
        # so "the button does nothing" is diagnosable from the UI.
        return [name for name, val in (
            ("EBAY_CLIENT_ID", config.EBAY_CLIENT_ID),
            ("EBAY_CLIENT_SECRET", config.EBAY_CLIENT_SECRET),
            ("EBAY_RUNAME", config.EBAY_RUNAME),
        ) if not val]

    def authorize_url(self, state: str) -> tuple[str, dict]:
        return ebay_auth.authorize_url(state=state), {}

    def exchange_code(self, code: str, flow: dict) -> dict:
        # eBay connects through its original /api/ebay/connect|callback routes
        # (they carry account-preserving policy logic); the generic
        # /api/{marketplace}/* routes never reach this provider because the
        # literal eBay routes are registered first.
        raise NotImplementedError("eBay uses /api/ebay/callback")

    def account_status(self, uid: Optional[str]) -> dict:
        acct = db.get_ebay_account(uid) if uid else None
        connected = bool(acct and acct.get("refresh_token"))
        return {
            "oauth_ready": self.oauth_ready(),
            "oauth_missing": self.oauth_missing(),
            "connected": connected,
            "env": config.EBAY_ENV,
            # Which eBay account is linked (empty for connections made before
            # the identity scope was added — reconnecting fills it in).
            "username": (acct.get("ebay_username") or "") if connected else "",
            "email": (acct.get("ebay_email") or "") if connected else "",
            "policies": {
                "fulfillment": bool(acct and acct.get("fulfillment_policy_id")),
                "payment": bool(acct and acct.get("payment_policy_id")),
                "return": bool(acct and acct.get("return_policy_id")),
                "location": bool(acct and acct.get("merchant_location_key")),
            } if connected else {},
        }

    def creds_for(self, uid: Optional[str]) -> Optional[dict]:
        return creds_for(uid)

    def disconnect(self, uid: str) -> None:
        db.disconnect_ebay_account(uid)

    # --- listing lifecycle -----------------------------------------------
    def supports(self) -> dict:
        return {"draft": True, "edit": True, "end": True, "auction": True,
                "max_photos": 24}

    def preflight(self, uid: Optional[str], listing: Listing,
                  mode: str) -> list[dict]:
        return preflight_issues(uid, listing, mode)

    def end(self, ctx: PublishContext, creds: dict) -> dict:
        # Ending an eBay listing stays on its original /api/ebay/end-listing
        # route (imported-vs-app routing + sticky-status writes live there).
        raise NotImplementedError("eBay uses /api/ebay/end-listing")

    def publish(self, ctx: PublishContext,
                creds: Optional[dict]) -> PublishOutcome:
        session_id, listing, mode = ctx.session_id, ctx.listing, ctx.mode
        prev_rec = ctx.prev_record
        already_live = prev_rec.get("status") in ("published", "live")

        # A listing IMPORTED from eBay (or published by us through Trading)
        # isn't Inventory-API managed, so edits go back through the Trading
        # API instead of the publish path below.
        if listing_sync.is_imported(listing):
            uid = ctx.uid
            # The record's REAL lifecycle stage decides what "publish" means
            # here: live → revise in place; ended/sold → relist as a NEW
            # listing (eBay refuses to revise an ended item). Status writes
            # preserve the truth — a failed attempt must not move an Inactive
            # record back to Active.
            prev_status = (prev_rec.get("status")
                           if prev_rec.get("status") in STICKY_STATUSES else "published")
            if mode == "draft":
                db.upsert_listing(session_id, listing.model_dump(),
                                  status=prev_status, user_id=uid)
                return PublishOutcome(
                    ok=True, message="Saved.",
                    raw={"dry_run": False, "mode": "draft",
                         "message": "Saved. Choose Update on eBay to push "
                                    "these changes to your live listing."})
            if not creds:
                raise HTTPException(400, "Connect eBay first.")
            relist = prev_status in ("ended", "sold")
            # Photo sync: local working copies are the truth. Edited since the
            # last sync (or photos added/removed) → send OUR /media URLs so
            # eBay ingests fresh copies; untouched → reuse the live EPS URLs
            # and skip the re-upload churn entirely. A relist always needs
            # URLs.
            urls, pushed_local = listing.image_urls or None, False
            local_names = [n for n in (listing.images or [])
                           if (storage.optimized_dir(session_id) / n).is_file()]
            if local_names and (not urls or relist
                                or image_import.images_changed(session_id, local_names)):
                urls = ebay.image_urls_for(session_id, listing, ctx.base_url)
                pushed_local = True
            try:
                if relist:
                    # "Open one to relist it fresh" — the Inactive tab's
                    # promise. A fresh listing also mints a new item id
                    # (search boost).
                    if not urls:
                        raise ValueError("This listing has no photos left to relist with.")
                    res = listing_sync.create_on_ebay(
                        creds["access_token"], listing, urls, creds=creds)
                else:
                    res = listing_sync.push_edit(creds["access_token"], listing,
                                                 image_urls=urls)
            except ValueError as exc:  # TradingError — eBay's own reason
                log.warning("%s (imported) failed: session=%s: %s",
                            "relist" if relist else "revise", session_id, exc)
                db.upsert_listing(session_id, listing.model_dump(),
                                  status=prev_status, user_id=uid)
                return PublishOutcome(
                    ok=False, message=str(exc),
                    issues=ebay_errors.from_response(str(exc)),
                    raw={"dry_run": False, "error": True, "mode": "live",
                         "message": str(exc),
                         "issues": ebay_errors.from_response(str(exc))})
            db.upsert_listing(session_id, listing.model_dump(),
                              status="published", user_id=uid)
            if pushed_local:
                # eBay accepted our copies: re-baseline the checksums and, in
                # the background (after the upsert above, so it can't be
                # overwritten), pull the new EPS URLs eBay minted so the sync
                # references stay current.
                image_import.mark_synced(session_id, local_names)
                run_in_background(refresh_eps_urls, session_id,
                                  creds["access_token"], listing.ebay_listing_id,
                                  uid, what="EPS URL refresh")
            log.info("%s (imported) ok: session=%s item=%s photos=%s",
                     "relist" if relist else "revise",
                     session_id, res.get("listing_id"),
                     "local-updated" if pushed_local else "unchanged")
            listing_id = str(res.get("listing_id") or "")
            return PublishOutcome(
                ok=True, listing_id=listing_id, status="published",
                url=_view_url(listing, listing_id),
                message=("Relisted! It's live on eBay as a fresh listing "
                         "with a new item number."
                         if relist else "Your eBay listing has been updated."),
                raw={"published": True, "revised": not relist,
                     "relisted": relist, "mode": "live",
                     "listing_id": res.get("listing_id"),
                     "message": ("Relisted! It's live on eBay as a fresh "
                                 "listing with a new item number."
                                 if relist else
                                 "Your eBay listing has been updated.")})

        # A listing that's already live must NEVER lose its 'published' status
        # in our records just because a revise attempt was blocked or errored —
        # the listing is still live on eBay either way.
        was_live = already_live
        # Pre-publish checklist: catch everything eBay would reject BEFORE the
        # round-trip, with field-targeted fixes. Only gates a real (connected)
        # live publish — dry-runs and drafts stay permissive.
        if mode == "live" and (creds or config.ebay_ready()):
            problems = preflight.errors_only(
                preflight_issues(ctx.uid, listing, "live"))
            if problems:
                db.upsert_listing(session_id, listing.model_dump(),
                                  status="published" if was_live else "draft",
                                  user_id=ctx.uid)
                log.info("publish blocked by preflight: session=%s issues=%d",
                         session_id, len(problems))
                return PublishOutcome(
                    ok=False, issues=problems,
                    message=f"Not quite ready — {len(problems)} thing"
                            f"{'s' if len(problems) != 1 else ''} to fix "
                            "before eBay will accept it:",
                    raw={"dry_run": False, "error": True, "mode": mode,
                         "message": f"Not quite ready — {len(problems)} thing"
                                    f"{'s' if len(problems) != 1 else ''} to fix before eBay will accept it:",
                         "issues": problems})
        # Self-heal the ship-from location on a live publish: re-ensure it
        # from the saved ZIP so a location missing its country (eBay
        # 'Item.Country empty') gets repaired without the user re-saving
        # settings.
        if mode == "live" and creds and creds.get("ship_from_postal"):
            try:
                key = ebay_auth.ensure_inventory_location(
                    creds["access_token"], creds["ship_from_postal"])
                if key:
                    creds["merchant_location_key"] = key
                    db.save_ebay_account(creds["_uid"], merchant_location_key=key)
            except Exception as exc:  # noqa: BLE001 - don't block publish on this
                log.warning(f"ebay: location re-ensure failed: {exc}")
        # NEW live listings go out through the Trading API, not the Inventory
        # API. An Inventory-API listing is "inventory-based" and eBay refuses
        # to let the seller edit it anywhere but the tool that made it —
        # Seller Hub answers "Inventory-based listing management is not
        # currently supported by this tool." Publishing through Trading
        # produces an ordinary listing they can edit in Seller Hub, the eBay
        # app, or here; source="ebay" then routes later edits from this app
        # down the same revise path imported listings use.
        if (mode == "live" and creds and not already_live
                and not listing_sync.is_imported(listing)
                and not listing.ebay_listing_id):
            urls = ebay.image_urls_for(session_id, listing, ctx.base_url)
            try:
                res = listing_sync.create_on_ebay(
                    creds["access_token"], listing, urls, creds=creds)
            except ValueError as exc:  # TradingError — eBay's own reason
                log.warning("trading publish failed: session=%s: %s", session_id, exc)
                db.upsert_listing(session_id, listing.model_dump(),
                                  status="draft", user_id=ctx.uid)
                return PublishOutcome(
                    ok=False, message=str(exc),
                    issues=ebay_errors.from_response(str(exc)),
                    raw={"dry_run": False, "error": True, "mode": "live",
                         "message": str(exc),
                         "issues": ebay_errors.from_response(str(exc))})
            storage.save_listing(session_id, listing)
            result = {"published": True, "mode": "live",
                      "listing_id": res["listing_id"],
                      "message": "Your listing is live on eBay."}
            if listing.promote or auto_promote_enabled(ctx.uid):
                result["promote_status"] = promote(
                    session_id, listing, creds,
                    rate=listing.ad_rate_percent,
                    ebay_listing_id=res["listing_id"])
            db.upsert_listing(session_id, listing.model_dump(),
                              status="published", user_id=ctx.uid)
            log.info("trading publish ok: session=%s item=%s",
                     session_id, res["listing_id"])
            listing_id = str(res["listing_id"])
            return PublishOutcome(
                ok=True, listing_id=listing_id, status="published",
                url=_view_url(listing, listing_id),
                message=result["message"], raw=result)

        log.info("publish request: session=%s mode=%s connected=%s", session_id,
                 mode, bool(creds))
        result = ebay.publish(session_id, listing, mode, ctx.base_url,
                              creds=creds, is_revise=was_live)
        # Record the outcome: published (live), draft, or dry-run. An errored
        # attempt never demotes a live listing, and never records "live" for a
        # listing that isn't (the old status=req.mode did exactly that).
        if result.get("published"):
            status = "published"
            log.info("publish OK: session=%s listing_id=%s revised=%s",
                     session_id, result.get("listing_id"), result.get("revised"))
        elif result.get("error"):
            status = "published" if was_live else "draft"
            log.warning("publish error: session=%s step=%s", session_id,
                        result.get("step"))
        elif result.get("dry_run"):
            status = "dry_run"
        else:
            status = "published" if was_live else mode
        dump = listing.model_dump()
        # Persist the eBay item id so the app can link to (and keep tracking)
        # the live listing across sessions.
        if result.get("listing_id"):
            dump["ebay_listing_id"] = str(result["listing_id"])
        db.upsert_listing(session_id, dump, status=status, user_id=ctx.uid)
        # Promoted Listings: once the item is live, best-effort create/refresh
        # its ad. Runs when the listing's Promote toggle is on OR the
        # account's auto-promote default (Settings) is — at the chosen rate,
        # else eBay's recommended rate. Never blocks or fails the publish; the
        # status is attached for the UI to show (incl. 'reconnect to grant ad
        # permissions').
        if result.get("published") and (listing.promote
                                        or auto_promote_enabled(ctx.uid)):
            result["promote_status"] = promote(
                session_id, listing, creds,
                rate=listing.ad_rate_percent,
                ebay_listing_id=result.get("listing_id"))
            if result["promote_status"].get("promoted"):
                # Re-record with the promote flag + actual rate so the
                # Dashboard and recommender see it as promoted.
                dump = listing.model_dump()
                if result.get("listing_id"):
                    dump["ebay_listing_id"] = str(result["listing_id"])
                db.upsert_listing(session_id, dump, status=status,
                                  user_id=ctx.uid)
        listing_id = str(result.get("listing_id") or "")
        return PublishOutcome(
            ok=not result.get("error"),
            listing_id=listing_id,
            url=_view_url(listing, listing_id) if result.get("published") else "",
            message=str(result.get("message") or ""),
            dry_run=bool(result.get("dry_run")),
            status=("published" if result.get("published")
                    else "draft" if status == "draft" and not result.get("error")
                    else ""),
            issues=list(result.get("issues") or []),
            raw=result)


register(EbayProvider())
