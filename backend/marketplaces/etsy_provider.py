"""Etsy as a MarketplaceProvider (Etsy Open API v3).

Publish flow: createDraftListing -> upload photos -> PATCH state=active for
live publishes (drafts stay Etsy drafts, real parity with the eBay staged
draft). No creds -> a dry-run outcome carrying the exact payload — Etsy has
no sandbox, so dry-run IS the offline test story.

Etsy rotates refresh tokens on every refresh; creds_for persists the
rotation immediately, under a per-user lock so two concurrent requests can't
refresh the same token twice and strand the connection.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .. import config, db, etsy_auth, storage
from ..config import log
from ..models import Listing
from ..services import etsy
from . import register
from .base import PublishContext, PublishOutcome
from . import mapping_etsy

_ACCESS_CACHE: dict[str, tuple[float, str]] = {}   # uid -> (expiry, token)
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(uid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _REFRESH_LOCKS.setdefault(uid, threading.Lock())


def _view_url(listing_id: str) -> str:
    return f"https://www.etsy.com/listing/{listing_id}" if listing_id else ""


class EtsyProvider:
    key = "etsy"
    label = "Etsy"

    # --- configuration / connection -------------------------------------
    def oauth_ready(self) -> bool:
        return config.etsy_oauth_ready()

    def oauth_missing(self) -> list[str]:
        return [name for name, val in (
            ("ETSY_CLIENT_ID", config.ETSY_CLIENT_ID),
            ("ETSY_REDIRECT_URI", config.ETSY_REDIRECT_URI),
        ) if not val]

    def authorize_url(self, state: str) -> tuple[str, dict]:
        verifier, challenge = etsy_auth.make_pkce_pair()
        # The PKCE verifier rides the flow cookie back to the callback.
        return etsy_auth.authorize_url(state, challenge), {"code_verifier": verifier}

    def exchange_code(self, code: str, flow: dict) -> dict:
        tokens = etsy_auth.exchange_code(code, str(flow.get("code_verifier") or ""))
        access = tokens["access_token"]
        shop_id, shop_name = "", ""
        try:
            me = etsy_auth.fetch_me(access)
            shop_id = str(me.get("shop_id") or "")
            if shop_id:
                shop = etsy_auth.fetch_shop(access, shop_id)
                shop_name = str(shop.get("shop_name") or "")
        except Exception as exc:  # noqa: BLE001 - identity is best-effort
            log.warning(f"etsy: shop lookup failed on connect: {exc}")
        return {
            "refresh_token": tokens.get("refresh_token", ""),
            "external_username": shop_name,
            "external_id": shop_id,
            # save_marketplace_account merges settings, so a reconnect keeps
            # previously chosen shipping/return defaults.
            "settings": {"shop_id": shop_id} if shop_id else {},
        }

    def account_status(self, uid: Optional[str]) -> dict:
        acct = db.get_marketplace_account(uid, "etsy") if uid else None
        connected = bool(acct and acct.get("refresh_token"))
        return {
            "oauth_ready": self.oauth_ready(),
            "oauth_missing": self.oauth_missing(),
            "connected": connected,
            "env": "production",   # Etsy has no sandbox
            "username": (acct.get("external_username") or "") if connected else "",
            "shop_id": (acct.get("external_id") or "") if connected else "",
        }

    def creds_for(self, uid: Optional[str]) -> Optional[dict]:
        if not uid:
            return None
        acct = db.get_marketplace_account(uid, "etsy")
        if not acct or not acct.get("refresh_token"):
            return None
        hit = _ACCESS_CACHE.get(uid)
        if hit and time.time() < hit[0]:
            access = hit[1]
        else:
            with _lock_for(uid):
                hit = _ACCESS_CACHE.get(uid)   # a racing request refreshed first?
                if hit and time.time() < hit[0]:
                    access = hit[1]
                else:
                    # Re-read inside the lock: the stored token may have been
                    # rotated since the check above, and refreshing a stale
                    # one kills the connection.
                    acct = db.get_marketplace_account(uid, "etsy") or acct
                    try:
                        fresh = etsy_auth.refresh_access_token(acct["refresh_token"])
                    except Exception as exc:  # noqa: BLE001 - dry-run, but log it
                        log.warning(f"etsy: token refresh failed for user {uid}: {exc}")
                        return None
                    # Etsy ROTATES the refresh token — persist it or the
                    # connection dies when the old one expires.
                    if fresh.get("refresh_token"):
                        db.save_marketplace_account(
                            uid, "etsy", refresh_token=fresh["refresh_token"])
                    if len(_ACCESS_CACHE) > 50:
                        _ACCESS_CACHE.clear()
                    access = fresh["access_token"]
                    _ACCESS_CACHE[uid] = (
                        max(time.time() + 60, fresh["expires_at"] - 90), access)
        settings = acct.get("settings") or {}
        shop_id = str(settings.get("shop_id") or acct.get("external_id") or "")
        if not shop_id:
            log.warning(f"etsy: user {uid} connected but no shop id on file")
            return None
        return {"access_token": access, "shop_id": shop_id,
                "settings": settings, "_uid": uid}

    def disconnect(self, uid: str) -> None:
        db.disconnect_marketplace_account(uid, "etsy")
        _ACCESS_CACHE.pop(uid, None)

    def forget_cached_creds(self, uid: str) -> None:
        """Reconnect invalidates the cache: the entry is keyed by user id, so
        connecting a DIFFERENT Etsy shop without disconnecting first would
        otherwise pair the new shop_id with the previous shop's cached token
        (403s, or worse, writes aimed at the wrong shop) until it expired."""
        _ACCESS_CACHE.pop(uid, None)

    # --- listing lifecycle -----------------------------------------------
    def supports(self) -> dict:
        return {"draft": True, "edit": True, "end": True, "auction": False,
                "max_photos": mapping_etsy.MAX_PHOTOS}

    def _settings_for(self, uid: Optional[str]) -> dict:
        acct = db.get_marketplace_account(uid, "etsy") if uid else None
        return (acct or {}).get("settings") or {}

    def preflight(self, uid: Optional[str], listing: Listing,
                  mode: str) -> list[dict]:
        if mode != "live":
            return []
        return mapping_etsy.preflight(listing, self._settings_for(uid))

    def _image_batches(self, ctx: PublishContext) -> list[tuple[str, bytes]]:
        """(filename, bytes) for up to MAX_PHOTOS photos — local optimized
        files first (the editable truth); a listing imported from eBay may
        only have remote URLs, so those are fetched and re-uploaded."""
        out: list[tuple[str, bytes]] = []
        opt = storage.optimized_dir(ctx.session_id)
        for name in (ctx.listing.images or [])[:mapping_etsy.MAX_PHOTOS]:
            path: Path = opt / name
            if path.is_file():
                out.append((name, path.read_bytes()))
        if not out:
            for i, url in enumerate((ctx.listing.image_urls or [])[:mapping_etsy.MAX_PHOTOS]):
                try:
                    resp = httpx.get(url, timeout=60)
                    resp.raise_for_status()
                    out.append((f"photo-{i + 1}.jpg", resp.content))
                except Exception as exc:  # noqa: BLE001 - skip the bad one, keep going
                    log.warning("etsy: couldn't fetch %s: %s", url, exc)
        return out

    def publish(self, ctx: PublishContext,
                creds: Optional[dict]) -> PublishOutcome:
        listing, mode = ctx.listing, ctx.mode
        settings = (creds or {}).get("settings") or self._settings_for(ctx.uid)
        payload = mapping_etsy.build_listing_payload(listing, settings)

        if not creds:
            # Dry-run: Etsy has no sandbox, so the exact payload IS the test.
            return PublishOutcome(
                ok=True, dry_run=True,
                message="Etsy dry run — connect Etsy in Settings to post for real.",
                raw={"dry_run": True, "mode": mode,
                     "message": "Etsy dry run — connect Etsy in Settings to post for real.",
                     "etsy_payload": payload})

        if mode == "live":
            problems = [i for i in mapping_etsy.preflight(listing, settings)
                        if i.get("level", "error") == "error"]
            if problems:
                n = len(problems)
                return PublishOutcome(
                    ok=False, issues=problems,
                    message=f"Not quite ready for Etsy — {n} thing"
                            f"{'s' if n != 1 else ''} to fix:")

        prev_listing = ctx.prev_record.get("listing") or {}
        prev_state = ((prev_listing.get("marketplaces") or {}).get("etsy") or {})
        existing_id = str(prev_state.get("listing_id") or "")

        try:
            if existing_id:
                # Revise in place. Photos aren't re-synced on revises (Etsy
                # keeps its copies); new photos ship with new listings.
                patch = dict(payload)
                if mode == "live" and prev_state.get("status") != "published":
                    patch["state"] = "active"
                res = etsy.update_listing(
                    creds["access_token"], creds["shop_id"], existing_id, patch)
                listing_id = res.get("listing_id") or existing_id
                message = ("Your Etsy listing has been updated."
                           if mode == "live" else "Etsy draft updated.")
            else:
                res = etsy.create_draft_listing(
                    creds["access_token"], creds["shop_id"], payload)
                listing_id = res["listing_id"]
                images = self._image_batches(ctx)
                for rank, (name, data) in enumerate(images, start=1):
                    etsy.upload_listing_image(
                        creds["access_token"], creds["shop_id"], listing_id,
                        data, name, rank)
                if mode == "live":
                    res = etsy.update_listing(
                        creds["access_token"], creds["shop_id"], listing_id,
                        {"state": "active"})
                    message = "Your listing is live on Etsy."
                else:
                    message = ("Draft created on your Etsy shop — publish it "
                               "live when you're ready.")
        except ValueError as exc:   # EtsyError — Etsy's own reason
            issues = getattr(exc, "issues", None) or [
                {"target": "generic", "level": "error",
                 "title": "Etsy rejected the listing", "fix": str(exc)}]
            log.warning("etsy publish failed: session=%s: %s", ctx.session_id, exc)
            return PublishOutcome(ok=False, message=str(exc), issues=issues)

        url = res.get("url") or _view_url(listing_id)
        log.info("etsy publish ok: session=%s listing=%s mode=%s",
                 ctx.session_id, listing_id, mode)
        return PublishOutcome(
            ok=True, listing_id=listing_id, url=url,
            status="published" if mode == "live" else "draft",
            message=message,
            raw={"published": mode == "live", "mode": mode,
                 "listing_id": listing_id, "url": url, "message": message})

    def end(self, ctx: PublishContext, creds: dict) -> dict:
        prev_listing = ctx.prev_record.get("listing") or {}
        state = ((prev_listing.get("marketplaces") or {}).get("etsy") or {})
        listing_id = str(state.get("listing_id") or "")
        if not listing_id:
            raise ValueError("This listing isn't on Etsy.")
        etsy.update_listing(creds["access_token"], creds["shop_id"],
                            listing_id, {"state": "inactive"})
        return {"ended": True,
                "message": "Etsy listing deactivated — it stays in your shop "
                           "as a draft you can reactivate."}


register(EtsyProvider())
