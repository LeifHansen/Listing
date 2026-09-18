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

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .. import config, db, etsy_auth
from ..config import log
from ..models import Listing
from ..services import etsy, image_import
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

    # Etsy's app-type wall. Until Etsy grants Commercial Access, only a
    # limited set of accounts may authorize this app — everyone else is
    # refused on Etsy's own consent page, with nothing redirected back for us
    # to catch. So we stop them here instead, with the truth, rather than
    # sending them out to a dead end. Retires itself the moment the tier
    # reads `commercial` (see config.etsy_access_pending).
    #
    # A property, not a constant, because the truth differs by tier and this
    # sentence is the only place a seller learns which wait they are in: an
    # unapproved app has not been cleared for anyone else at all, while an
    # approved personal app has seats — just not one for them yet. Telling a
    # seller we are "under review" after Etsy has approved us is a small lie
    # that outlives the wait it describes.
    @property
    def access_pending_note(self) -> str:
        if config.etsy_access_tier() == "personal":
            return ("Etsy has approved us for a limited number of shops so "
                    "far, and yours isn't one of them yet. Cross-posting to "
                    "Etsy opens to every seller once Etsy grants us "
                    "Commercial Access; nothing for you to set up.")
        return ("Etsy only lets our own shop connect until they approve our "
                "app for other sellers. Cross-posting to Etsy switches on "
                "here as soon as that lands; nothing for you to set up.")

    def access_pending(self, uid: Optional[str]) -> bool:
        # Short-circuit before the lookup: with the gate off the answer is no
        # for everyone, and this runs on every roster build.
        if not config.etsy_gate_active():
            return False
        user = db.get_user_by_id(uid) if uid else None
        return config.etsy_access_pending((user or {}).get("email", ""))

    # --- configuration / connection -------------------------------------
    def oauth_ready(self) -> bool:
        return config.etsy_oauth_ready()

    def oauth_missing(self) -> list[str]:
        return [name for name, val in (
            ("ETSY_CLIENT_ID", config.ETSY_CLIENT_ID),
            ("ETSY_SHARED_SECRET", config.ETSY_SHARED_SECRET),
            ("ETSY_REDIRECT_URI", config.ETSY_REDIRECT_URI),
        ) if not val]

    def authorize_url(self, state: str) -> tuple[str, dict]:
        verifier, challenge = etsy_auth.make_pkce_pair()
        # The PKCE verifier rides the flow cookie back to the callback.
        return etsy_auth.authorize_url(state, challenge), {"code_verifier": verifier}

    def exchange_code(self, code: str, flow: dict) -> dict:
        tokens = etsy_auth.exchange_code(code, str(flow.get("code_verifier") or ""))
        access = tokens["access_token"]
        shop_id, shop_name, currency = "", "", ""
        try:
            me = etsy_auth.fetch_me(access)
            shop_id = str(me.get("shop_id") or "")
            if shop_id:
                shop = etsy_auth.fetch_shop(access, shop_id)
                shop_name = str(shop.get("shop_name") or "")
                # The shop's own currency: an Etsy price is always in it, so
                # the preflight can say when a listing's price is in another.
                currency = str(shop.get("currency_code") or "").upper()
        except Exception as exc:  # noqa: BLE001 - identity is best-effort
            log.warning(f"etsy: shop lookup failed on connect: {exc}")
        settings = {}
        if shop_id:
            settings["shop_id"] = shop_id
        if currency:
            settings["currency_code"] = currency
        return {
            "refresh_token": tokens.get("refresh_token", ""),
            "external_username": shop_name,
            "external_id": shop_id,
            # save_marketplace_account merges settings, so a reconnect keeps
            # previously chosen shipping/return defaults.
            "settings": settings,
        }

    @staticmethod
    def _shop_id_of(acct: Optional[dict]) -> str:
        settings = (acct or {}).get("settings") or {}
        return str(settings.get("shop_id") or (acct or {}).get("external_id") or "")

    def account_status(self, uid: Optional[str]) -> dict:
        acct = db.get_marketplace_account(uid, "etsy") if uid else None
        connected = bool(acct and acct.get("refresh_token"))
        shop_id = self._shop_id_of(acct) if connected else ""
        return {
            "oauth_ready": self.oauth_ready(),
            "oauth_missing": self.oauth_missing(),
            "connected": connected,
            # A token with no shop behind it: the connect's shop lookup
            # failed and nothing has managed it since. creds_for tries again
            # on every use; until one lands, Settings says "reconnect" rather
            # than "connected" over a card that can't publish.
            "needs_reconnect": connected and not shop_id,
            "env": "production",   # Etsy has no sandbox
            "username": (acct.get("external_username") or "") if connected else "",
            "shop_id": shop_id,
        }

    def _heal_shop_id(self, uid: str, access: str) -> str:
        """Fetch and store the shop behind a connection that has none.

        exchange_code looks the shop up best-effort, and a lookup that failed
        there used to leave the account "connected" for good with nothing
        able to use it — Settings said connected, every publish said connect
        Etsy first. The same two calls, made again the next time the
        connection is used, are the way out; the answer is stored so it
        happens once."""
        try:
            me = etsy_auth.fetch_me(access)
            shop_id = str(me.get("shop_id") or "")
            if not shop_id:
                return ""
            shop = etsy_auth.fetch_shop(access, shop_id)
        except Exception as exc:  # noqa: BLE001 - reported on the status
            log.warning("etsy: shop lookup still failing for user %s: %s", uid, exc)
            return ""
        settings = {"shop_id": shop_id}
        currency = str(shop.get("currency_code") or "").upper()
        if currency:
            settings["currency_code"] = currency
        db.save_marketplace_account(
            uid, "etsy", external_id=shop_id,
            external_username=str(shop.get("shop_name") or ""), settings=settings)
        return shop_id

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
                    # connection dies when the old one expires. A write that
                    # fails is therefore a FAILED REFRESH, not a detail: Etsy
                    # has already invalidated the token still in the database,
                    # so carrying on would serve this one request and leave the
                    # connection permanently unrecoverable, with the next
                    # publish quietly falling through to a dry run. Better to
                    # fail now, loudly, while the seller is still here.
                    if fresh.get("refresh_token") and not db.save_marketplace_account(
                            uid, "etsy", refresh_token=fresh["refresh_token"]):
                        log.error(
                            "etsy: could not store the rotated refresh token for "
                            "user %s — the connection would break silently, so "
                            "treating this as a failed refresh.", uid)
                        return None
                    if len(_ACCESS_CACHE) > 50:
                        _ACCESS_CACHE.clear()
                    access = fresh["access_token"]
                    _ACCESS_CACHE[uid] = (
                        max(time.time() + 60, fresh["expires_at"] - 90), access)
        settings = dict(acct.get("settings") or {})
        shop_id = self._shop_id_of(acct)
        if not shop_id:
            shop_id = self._heal_shop_id(uid, access)
            if not shop_id:
                log.warning(f"etsy: user {uid} connected but no shop id on file")
                return None
            settings["shop_id"] = shop_id
        return {"access_token": access, "shop_id": shop_id,
                "settings": settings, "_uid": uid}

    def disconnect(self, uid: str) -> None:
        db.disconnect_marketplace_account(uid, "etsy")
        _ACCESS_CACHE.pop(uid, None)
        with _LOCKS_GUARD:
            _REFRESH_LOCKS.pop(uid, None)

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
        # Every mode, not just "live". createDraftListing itself refuses a
        # listing with no category, no attribution or no shipping profile, so
        # a draft save with an empty Etsy card used to go out and come back
        # as a raw "Etsy rejected the listing" instead of the targeted issues
        # this checklist already writes; and the editor asks with
        # mode="revise" before a live publish, which used to get an empty
        # list and a "Ready" it then had to take back. mapping_etsy.preflight
        # decides what a draft may still leave for later.
        return mapping_etsy.preflight(listing, self._settings_for(uid), mode)

    def _image_batches(self, ctx: PublishContext) -> list[tuple[str, bytes]]:
        """(filename, bytes) for up to MAX_PHOTOS photos — the seller's own
        photos first (the editable truth: the optimized file on the volume,
        or its copy in object storage once the reclaim pass has freed the
        local one); a listing imported from eBay may only have remote URLs,
        so those are fetched and re-uploaded.

        The remote fetch goes through image_import.fetch_ebay_image rather
        than httpx directly, and that is a security boundary, not a tidy-up.
        `image_urls` is not a server-owned field: it round-trips through the
        publish request body, so a bare `httpx.get(url)` here let a caller
        aim a request from inside the app at the metadata service, the
        database's private address or anything on localhost, and read the
        answer back off their own Etsy listing. fetch_ebay_image is HTTPS
        only, ebayimg.com only (re-checked on every redirect hop), with
        bounded redirects, a size cap and a content-type check — and nothing
        legitimate is outside it, because image_urls is only ever populated
        from eBay's own EPS URLs on import.
        """
        out: list[tuple[str, bytes]] = []
        for name in (ctx.listing.images or [])[:mapping_etsy.MAX_PHOTOS]:
            try:
                out.append((name, image_import.read_own_media(ctx.session_id, name)))
            except ValueError as exc:
                # One photo nobody can find any more is one photo short, not
                # a publish that stops; the count reaches the preflight.
                log.warning("etsy: photo %s/%s unavailable: %s",
                            ctx.session_id, name, exc)
        if not out:
            for i, url in enumerate((ctx.listing.image_urls or [])[:mapping_etsy.MAX_PHOTOS]):
                try:
                    out.append((f"photo-{i + 1}.jpg", image_import.fetch_ebay_image(url)))
                except Exception as exc:  # noqa: BLE001 - skip the bad one, keep going
                    log.warning("etsy: couldn't fetch %s: %s", url, exc)
        return out

    @staticmethod
    def photo_signature(batches: list[tuple[str, bytes]]) -> str:
        """One digest for an ordered photo set: the bytes are in hand at
        upload time anyway, and a name alone cannot tell a re-crop from the
        same file. Order matters — reordering photos is a change Etsy sees."""
        digest = hashlib.sha256()
        for _name, data in batches:
            digest.update(hashlib.sha256(data).digest())
        return digest.hexdigest() if batches else ""

    def _upload_photos(self, creds: dict, listing_id: str,
                       images: list[tuple[str, bytes]], first_rank: int = 1) -> None:
        """Photos upload CONCURRENTLY (bounded to stay well under Etsy's rate
        limits): rank is explicit per photo, so display order is preserved no
        matter which upload lands first, and 8 photos cost ~2 round trips of
        wall clock instead of 8. map() re-raises the first failure, aborting
        the publish the same way a serial loop would."""
        if not images:
            return

        def _push(job) -> None:
            rank, (name, data) = job
            etsy.upload_listing_image(
                creds["access_token"], creds["shop_id"], listing_id, data, name, rank)

        with ThreadPoolExecutor(max_workers=min(4, len(images))) as pool:
            list(pool.map(_push, enumerate(images, start=first_rank)))

    def _sync_photos(self, creds: dict, listing_id: str,
                     images: list[tuple[str, bytes]]) -> None:
        """Bring the Etsy listing's photos to the seller's current set.

        Etsy holds photos by id, not by content, so the reconciliation is
        the blunt one: everything Etsy has comes off and the current set
        goes on, in order. Which happens FIRST is the only subtlety. A live
        listing must never sit with no photo at all (Etsy will not keep a
        listing active without one), so when the two sets fit under the
        ceiling together the new photos go up before the old ones come
        down. Past the ceiling that is impossible, and the old ones come
        down first — the window is a few seconds, and logged.
        """
        current = etsy.list_listing_images(creds["access_token"], listing_id)
        stale = [img["listing_image_id"] for img in current]
        if len(stale) + len(images) <= mapping_etsy.MAX_PHOTOS:
            self._upload_photos(creds, listing_id, images)
            for image_id in stale:
                etsy.delete_listing_image(
                    creds["access_token"], creds["shop_id"], listing_id, image_id)
        else:
            log.info("etsy: %s photos + %s new exceed Etsy's %s; removing "
                     "first on listing %s", len(stale), len(images),
                     mapping_etsy.MAX_PHOTOS, listing_id)
            for image_id in stale:
                etsy.delete_listing_image(
                    creds["access_token"], creds["shop_id"], listing_id, image_id)
            self._upload_photos(creds, listing_id, images)

    def publish(self, ctx: PublishContext,
                creds: Optional[dict]) -> PublishOutcome:
        listing, mode = ctx.listing, ctx.mode
        settings = (creds or {}).get("settings") or self._settings_for(ctx.uid)
        payload = mapping_etsy.build_listing_payload(listing, settings)

        if not creds:
            if mode == "live":
                # P1-09's rule. "Published" is a claim about the listing being
                # live on Etsy, and nothing was created — so ok is False, the
                # way the eBay provider already answers this. eBay keys its
                # guard on EBAY_ENV because eBay HAS a sandbox and a dry run
                # is a real tool there; Etsy has none (see the comment below),
                # so there is no environment where a live dry run succeeded.
                # The payload still rides along in `raw` for whoever is
                # developing against it; what changes is the answer to "did
                # you list it".
                message = ("Connect your Etsy shop in Settings to publish. "
                           "Nothing was listed.")
                return PublishOutcome(
                    ok=False, message=message,
                    issues=[{"target": "account", "level": "error",
                             "title": "Etsy isn't connected",
                             "fix": "Connect Etsy in Settings, then publish "
                                    "again."}],
                    raw={"dry_run": False, "error": True, "mode": mode,
                         "message": message, "etsy_payload": payload})
            # Draft: nothing is claimed to be live, and Etsy has no sandbox —
            # so the exact payload IS the test.
            return PublishOutcome(
                ok=True, dry_run=True,
                message="Etsy dry run — connect Etsy in Settings to post for real.",
                raw={"dry_run": True, "mode": mode,
                     "message": "Etsy dry run — connect Etsy in Settings to post for real.",
                     "etsy_payload": payload})

        # Checked before anything is sent, in every mode: what Etsy would
        # refuse at create time is refused here first, with the field named.
        problems = [i for i in mapping_etsy.preflight(listing, settings, mode)
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
        # Bound before the try so the failure path below can always report it.
        listing_id = existing_id
        images = self._image_batches(ctx)
        photo_sig = self.photo_signature(images)

        try:
            if existing_id:
                # Revise in place.
                #
                # KNOWN GAP, recorded rather than half-fixed: this sends the
                # WHOLE payload, so it is P0-08's problem for Etsy — a seller
                # who fixed a title on etsy.com and then changed only the
                # price here has the title replaced by this app's copy, and is
                # told the update succeeded. eBay's answer to that is a
                # three-way merge against a remote shadow plus a revise
                # carrying only the dirty fields.
                #
                # Neither half is available here. There is no Etsy store sync,
                # so there is no shadow to reconcile against and no way to see
                # that etsy.com moved. And `dirty_fields.TRACKED` is
                # eBay-shaped: it does not cover `listing.etsy` (taxonomy,
                # who/when made, tags, materials), so filtering this patch by
                # it would silently stop sending everything on the Etsy card —
                # a new failure in place of the old one. Etsy has no sandbox
                # either, so neither change could be tested before shipping.
                # Price and stock first, and not through the PATCH at all:
                # updateListing has no such fields (Etsy ignored the ones
                # this used to send, and a price drop here left Etsy asking
                # the old price under a success message). They live on the
                # inventory record, read whole and written whole. First, so
                # that a refused inventory write leaves the listing exactly
                # as it was rather than live at the wrong price.
                inventory = etsy.get_listing_inventory(
                    creds["access_token"], existing_id)
                try:
                    body = mapping_etsy.build_inventory_body(
                        listing, inventory, settings)
                except ValueError as exc:
                    return PublishOutcome(
                        ok=False, listing_id=existing_id, message=str(exc),
                        issues=[{"target": "variations", "level": "error",
                                 "title": "This Etsy listing has variations",
                                 "fix": str(exc)}])
                etsy.update_listing_inventory(
                    creds["access_token"], existing_id, body)
                # Photos, when the set has moved since Etsy last received
                # it. The signature is the record's own memory of that; a
                # record from before it was kept re-sends once, which is the
                # honest answer to "did Etsy get these?" when nobody wrote
                # it down.
                if images and photo_sig != str(prev_state.get("photo_sig") or ""):
                    self._sync_photos(creds, existing_id, images)
                patch = {k: v for k, v in payload.items()
                         if k not in ("price", "quantity")}
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
                self._upload_photos(creds, listing_id, images)
                if mode == "live":
                    res = etsy.update_listing(
                        creds["access_token"], creds["shop_id"], listing_id,
                        {"state": "active"})
                    message = "Your listing is live on Etsy."
                else:
                    message = ("Draft created on your Etsy shop — publish it "
                               "live when you're ready.")
        except Exception as exc:  # noqa: BLE001 - see the listing_id note below
            issues = getattr(exc, "issues", None) or [
                {"target": "generic", "level": "error",
                 "title": "Etsy rejected the listing", "fix": str(exc)}]
            log.warning("etsy publish failed: session=%s: %s", ctx.session_id, exc)
            # listing_id, even on the failure. create_draft_listing may already
            # have MINTED a listing on the seller's shop, and only the photo
            # upload or the activate call after it failed. Dropping the id here
            # orphaned that listing: the retry saw no existing id, created a
            # SECOND one, and end-listing could never reach the first ("This
            # listing isn't on Etsy"). Every further retry added another.
            #
            # Catching Exception, not just ValueError: an httpx.ReadTimeout on
            # the photo upload is the same situation and used to escape to the
            # orchestrator's broad handler, which had no id to record either.
            # Refused by Etsy, or sent and never answered for? The client
            # raises its own UnknownOutcome for the second (see
            # services/etsy), and the flag is how the surfaces downstream can
            # tell the two apart without reading the sentence. See
            # PublishOutcome.
            return PublishOutcome(
                ok=False, listing_id=str(listing_id or ""),
                message=str(exc), issues=issues,
                outcome_unknown=bool(getattr(exc, "outcome_unknown", False)))

        url = res.get("url") or _view_url(listing_id)
        log.info("etsy publish ok: session=%s listing=%s mode=%s",
                 ctx.session_id, listing_id, mode)
        return PublishOutcome(
            ok=True, listing_id=listing_id, url=url,
            status="published" if mode == "live" else "draft",
            message=message,
            state={"photo_sig": photo_sig} if images else {},
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
