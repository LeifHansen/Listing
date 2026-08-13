"""Depop as a MarketplaceProvider (partner-gated Selling API).

Registered unconditionally but invisible until the operator supplies the
DEPOP_* partner credentials (oauth_ready() gates the UI and available()).
Depop has no draft state — creating a product puts it on sale — so draft
publishes are save-only, and photos ride as public URLs from the existing
/media / R2 hosting (the same URLs eBay ingests).
"""
from __future__ import annotations

import time
from typing import Optional

from .. import config, db, depop_auth
from ..config import log
from ..models import Listing
from ..services import depop, ebay
from . import register
from .base import PublishContext, PublishOutcome
from . import mapping_depop

_ACCESS_CACHE: dict[str, tuple[float, str]] = {}   # uid -> (expiry, token)


class DepopProvider:
    key = "depop"
    label = "Depop"

    # --- configuration / connection -------------------------------------
    def oauth_ready(self) -> bool:
        return config.depop_oauth_ready()

    def oauth_missing(self) -> list[str]:
        return [name for name, val in (
            ("DEPOP_CLIENT_ID", config.DEPOP_CLIENT_ID),
            ("DEPOP_CLIENT_SECRET", config.DEPOP_CLIENT_SECRET),
            ("DEPOP_AUTH_URL", config.DEPOP_AUTH_URL),
            ("DEPOP_TOKEN_URL", config.DEPOP_TOKEN_URL),
            ("DEPOP_REDIRECT_URI", config.DEPOP_REDIRECT_URI),
        ) if not val]

    def authorize_url(self, state: str) -> tuple[str, dict]:
        return depop_auth.authorize_url(state), {}

    def exchange_code(self, code: str, flow: dict) -> dict:
        tokens = depop_auth.exchange_code(code)
        return {
            "refresh_token": tokens.get("refresh_token", ""),
            "external_username": "",
            "external_id": "",
            "settings": {},
        }

    def account_status(self, uid: Optional[str]) -> dict:
        acct = db.get_marketplace_account(uid, "depop") if uid else None
        connected = bool(acct and acct.get("refresh_token"))
        return {
            "oauth_ready": self.oauth_ready(),
            "oauth_missing": self.oauth_missing(),
            "connected": connected,
            "env": "production",
            "username": (acct.get("external_username") or "") if connected else "",
        }

    def creds_for(self, uid: Optional[str]) -> Optional[dict]:
        if not uid:
            return None
        acct = db.get_marketplace_account(uid, "depop")
        if not acct or not acct.get("refresh_token"):
            return None
        hit = _ACCESS_CACHE.get(uid)
        if hit and time.time() < hit[0]:
            return {"access_token": hit[1], "_uid": uid}
        try:
            fresh = depop_auth.refresh_access_token(acct["refresh_token"])
        except Exception as exc:  # noqa: BLE001 - dry-run, but log it
            log.warning(f"depop: token refresh failed for user {uid}: {exc}")
            return None
        if fresh.get("refresh_token") and fresh["refresh_token"] != acct["refresh_token"]:
            db.save_marketplace_account(uid, "depop",
                                        refresh_token=fresh["refresh_token"])
        if len(_ACCESS_CACHE) > 50:
            _ACCESS_CACHE.clear()
        _ACCESS_CACHE[uid] = (max(time.time() + 60, fresh["expires_at"] - 90),
                              fresh["access_token"])
        return {"access_token": fresh["access_token"], "_uid": uid}

    def disconnect(self, uid: str) -> None:
        db.disconnect_marketplace_account(uid, "depop")
        _ACCESS_CACHE.pop(uid, None)

    # --- listing lifecycle -----------------------------------------------
    def supports(self) -> dict:
        return {"draft": False, "edit": True, "end": True, "auction": False,
                "max_photos": mapping_depop.MAX_PHOTOS}

    def preflight(self, uid: Optional[str], listing: Listing,
                  mode: str) -> list[dict]:
        if mode != "live":
            return []
        return mapping_depop.preflight(listing)

    def publish(self, ctx: PublishContext,
                creds: Optional[dict]) -> PublishOutcome:
        listing, mode = ctx.listing, ctx.mode
        payload = mapping_depop.build_product_payload(listing)
        # Photos as public URLs (the /media / R2 hosting eBay already
        # ingests from), capped at Depop's limit.
        urls = ebay.image_urls_for(ctx.session_id, listing, ctx.base_url)
        payload["photos"] = urls[:mapping_depop.MAX_PHOTOS]

        if mode == "draft":
            # No draft state on Depop: the local draft is already saved by
            # the orchestrator; nothing to send.
            return PublishOutcome(
                ok=True, status="",
                message="Depop has no drafts — this goes to Depop when you "
                        "publish live.",
                raw={"dry_run": False, "mode": "draft",
                     "message": "Depop has no drafts — this goes to Depop "
                                "when you publish live."})

        if not creds:
            return PublishOutcome(
                ok=True, dry_run=True,
                message="Depop dry run — connect Depop in Settings to post for real.",
                raw={"dry_run": True, "mode": mode,
                     "message": "Depop dry run — connect Depop in Settings to post for real.",
                     "depop_payload": payload})

        problems = [i for i in mapping_depop.preflight(listing)
                    if i.get("level", "error") == "error"]
        if problems:
            n = len(problems)
            return PublishOutcome(
                ok=False, issues=problems,
                message=f"Not quite ready for Depop — {n} thing"
                        f"{'s' if n != 1 else ''} to fix:")

        prev_listing = ctx.prev_record.get("listing") or {}
        prev_state = ((prev_listing.get("marketplaces") or {}).get("depop") or {})
        existing_id = str(prev_state.get("listing_id") or "")

        try:
            if existing_id:
                res = depop.update_product(creds["access_token"], existing_id,
                                           payload)
                message = "Your Depop listing has been updated."
            else:
                res = depop.create_product(creds["access_token"], payload)
                message = "Your listing is live on Depop."
        except ValueError as exc:   # DepopError — Depop's own reason
            issues = getattr(exc, "issues", None) or [
                {"target": "generic", "level": "error",
                 "title": "Depop rejected the listing", "fix": str(exc)}]
            log.warning("depop publish failed: session=%s: %s",
                        ctx.session_id, exc)
            return PublishOutcome(ok=False, message=str(exc), issues=issues)

        listing_id = res.get("listing_id") or existing_id
        log.info("depop publish ok: session=%s product=%s",
                 ctx.session_id, listing_id)
        return PublishOutcome(
            ok=True, listing_id=listing_id, url=res.get("url") or "",
            status="published", message=message,
            raw={"published": True, "mode": mode, "listing_id": listing_id,
                 "url": res.get("url") or "", "message": message})

    def end(self, ctx: PublishContext, creds: dict) -> dict:
        prev_listing = ctx.prev_record.get("listing") or {}
        state = ((prev_listing.get("marketplaces") or {}).get("depop") or {})
        product_id = str(state.get("listing_id") or "")
        if not product_id:
            raise ValueError("This listing isn't on Depop.")
        depop.delete_product(creds["access_token"], product_id)
        return {"ended": True,
                "message": "Removed from Depop — publish live again to relist it."}


register(DepopProvider())
