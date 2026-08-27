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

from .. import config, db, ebay_auth, storage
from ..config import log
from ..models import Listing
from ..services import (ebay, ebay_account, ebay_trading, image_import,
                        listing_sync, preflight, promotions, publish_guard,
                        taxonomy)
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


def _with_current_policies(uid: str, acct: dict, access_token: str) -> dict:
    """Keep the saved account-scoped ids honest, without anyone asking.

    Business policy ids and merchant location keys belong to ONE eBay seller
    account. eBay rejects another account's outright, and the rejection names
    no field — the seller sees a generic "this listing or seller may be in
    violation of eBay policy" on EVERY publish, with nothing on screen that
    looks wrong, because the app's own Settings happily reports the ids as
    "set". Set is not the same as ours.

    These were only ever checked while connecting an account, and that check
    keeps the saved value whenever eBay cannot say what exists — correct on its
    own terms (an outage must never silently re-pick a seller's shipping) but
    it means a connect during any eBay hiccup leaves the PREVIOUS account's ids
    in place permanently, with no later pass to correct them.

    So re-check here, on the path every publish already takes, at most once per
    VERIFY_TTL per user. Anything that no longer exists on the connected
    account is replaced with that account's own default and written back, so
    the repair happens once rather than on every publish. A failed lookup still
    changes nothing, for the same reason as before.
    """
    if not ebay_account.verify_due(uid):
        return acct
    try:
        discovered = ebay_auth.fetch_policies_and_location(access_token)
        # Sync from whatever the connected eBay account actually has -- an id
        # that belongs to someone else is replaced, and an EMPTY slot is filled
        # from this account's own default.
        #
        # Filling blanks here is a deliberate reversal of #188, made on the
        # seller's instruction ("sync from whatever is set in ebay, or if unset,
        # prompt user to set"). #188 kept blanks alone to protect the "- none -"
        # option the Settings screen offers; the seller would rather the app
        # track eBay by itself and be told when it cannot. So a slot that is
        # STILL empty after this now means one thing only -- eBay has none of
        # that kind -- which is exactly the case `absent` reports and the
        # publish checklist turns into an instruction the seller can act on.
        result = ebay_account.reconcile_account_settings(
            access_token, acct, discovered, fill_blanks=True)
        changes, conclusive = result.changes, result.conclusive
    except Exception as exc:  # noqa: BLE001 - never block a publish on this
        log.warning("ebay: policy verification failed for %s: %s", uid, exc)
        return acct
    # Only a pass eBay actually answered counts. Neither lookup raises when it
    # fails, so "no changes" alone cannot tell a healthy account apart from an
    # eBay outage -- and starting the clock on an outage would suppress the
    # next TTL of repairs for the seller who most needs them.
    if conclusive:
        ebay_account.note_verified(uid)
        # Only a conclusive pass may claim eBay has none of something.
        ebay_account.note_absent(uid, result.absent)
    if not changes:
        return acct
    log.warning("ebay: account-scoped settings did not belong to @%s — "
                "repaired %s", acct.get("ebay_username") or "?",
                ", ".join(sorted(changes)))
    # Replacing an id that HELD a value is the tell-tale of a different eBay
    # account (see ebay_account.settings_were_dropped) -- the same signal the
    # connect handler acts on, so it has to do the same two things here, or the
    # repair is half a repair.
    if ebay_account.settings_were_dropped(changes, acct):
        # 1. Label what is already here as the previous account's. Without it
        #    listing_sync.belongs_to treats every unstamped record as this
        #    account's, and syncs and revises keep operating on the PREVIOUS
        #    seller's item ids.
        marked = db.stamp_ebay_account(
            uid, acct.get("ebay_username") or listing_sync.UNKNOWN_ACCOUNT)
        # 2. Drop the old store's ZIP. This one is not housekeeping: on a live
        #    publish, ebay_provider re-ensures the ship-from location from
        #    ship_from_postal and writes the returned key back over
        #    merchant_location_key. Left set, that stale ZIP would force our
        #    location to the old store's address and undo the location half of
        #    this repair inside the same request. Blank makes listing_sync
        #    re-read it from the account that is actually connected.
        changes["ship_from_postal"] = ""
        log.info("ebay: account switch detected on publish for uid=%s; "
                 "labelled %d existing listing(s), cleared ship-from", uid,
                 marked)
    db.save_ebay_account(uid, **changes)
    return {**acct, **changes}


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
    acct = _with_current_policies(uid, acct, access_token)
    return {
        "access_token": access_token,
        # Which eBay account this token is for. Everything that reads or writes
        # a listing on eBay is scoped by it — see listing_sync.belongs_to.
        "ebay_username": acct.get("ebay_username", "") or "",
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


# Message the seller sees when eBay took the listing but our own record of it
# didn't move. Rare, but the failure it names is the confusing one: the item is
# live, and the app still shows it as a draft.
RECORD_WARNING = ("It's live on eBay, but we couldn't update your copy here. "
                  "Refresh in a moment — don't publish it again, that would "
                  "post a second listing.")


def _record_published(session_id: str, dump: dict, status: str,
                      uid: Optional[str]) -> bool:
    """Persist a publish outcome, retrying once, and say whether it landed.

    db.upsert_listing swallows its own failures by design (a database blip
    must never break a request). For a status write that is fine — except
    right here, where the write IS the outcome: it's what moves the listing
    out of Drafts, what tells the next publish "already listed, revise it",
    and what stops a second live listing being posted for the same item. So
    this one gets a retry and, failing that, a loud log and a warning the
    seller can actually act on.
    """
    if db.upsert_listing(session_id, dump, status=status, user_id=uid):
        return True
    log.warning("publish: status write failed (session=%s status=%s) — retrying",
                session_id, status)
    if db.upsert_listing(session_id, dump, status=status, user_id=uid):
        return True
    log.error("publish: COULD NOT record status=%s for session=%s — the "
              "listing is live on eBay but our record still says otherwise",
              status, session_id)
    return False


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


# The listing fields that say "this is already on eBay". The server owns them
# outright — the browser only ever echoes back what a previous publish told it,
# and an echo that lost them reads as "never listed". Believing that costs a
# duplicate live listing, so the stored record wins on every one of them.
_IDENTITY_FIELDS = ("ebay_listing_id", "source", "view_url")


def _with_stored_identity(ctx: PublishContext) -> PublishContext:
    """`ctx` with the eBay identity fields taken from the stored record, and
    prev_record re-read inside the publish lock.

    Called under publish_guard.session_lock, so the snapshot it takes is the
    one the create-vs-revise decision is made from: any earlier publish of this
    listing has already finished and committed its item id.
    """
    fresh = db.get_listing(ctx.session_id)
    if not fresh:
        # Nothing stored (a brand-new session, or no DB): the payload is all
        # there is. The idempotency key still guards the create itself.
        return ctx
    stored = fresh.get("listing") or {}
    for field_name in _IDENTITY_FIELDS:
        value = stored.get(field_name)
        if value and value != getattr(ctx.listing, field_name, None):
            log.info("publish: taking %s from the stored record for session=%s",
                     field_name, ctx.session_id)
            setattr(ctx.listing, field_name, value)
    return PublishContext(
        session_id=ctx.session_id, listing=ctx.listing, mode=ctx.mode,
        base_url=ctx.base_url, uid=ctx.uid, prev_record=fresh)


def has_stored_connection(uid: Optional[str]) -> bool:
    """True when this user has connected eBay, whatever `creds_for` just said.

    `creds_for` answers None for two very different situations: "this seller
    never connected eBay" and "this seller is connected but the token refresh
    just failed". The first is the dry-run case. The second must never fall
    through to the env-configured operator credentials — that publishes one
    seller's listing onto the account whose refresh token is in the
    deployment's environment.

    Deliberately a plain lookup with no refresh of its own. Asking `creds_for`
    again would re-attempt the token exchange, and an attempt that happened to
    succeed on the retry would answer "not broken" — reopening the fallback
    this exists to close. Paired with a known-None `creds`, the stored token
    IS the answer: it is there, and it did not work a moment ago.
    """
    if not uid:
        return False
    acct = db.get_ebay_account(uid)
    return bool(acct and acct.get("refresh_token"))


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
    # caps are the classic silent publish killer, e.g. Standard Envelope's 3 oz)
    # — and whether the account still has that policy at all.
    services, policy_exists = (
        ebay_auth.fulfillment_policy_lookup(creds["access_token"], fulfillment)
        if creds and fulfillment else ([], None))

    required = None
    if config.taxonomy_ready() and (listing.category_id or "").strip().isdigit():
        try:
            asp = taxonomy.item_aspects(listing.category_id)
            required = [a["name"] for a in asp.get("aspects", []) if a.get("required")]
        except Exception:  # noqa: BLE001 - aspects are a best-effort check
            required = None

    issues = preflight.validate(
        listing, mode,
        has_fulfillment=bool(fulfillment), has_payment=has_payment,
        has_return=has_return, has_location=has_location, connected=connected,
        policy_services=services, required_aspects=required)
    # A slot still empty after the sync above means eBay has none of that kind
    # -- there is nothing to sync from, and no dropdown in this app can fix it.
    # Saying "choose one in Settings" here sends the seller to an empty list;
    # the only thing that helps is telling them to create it on eBay.
    ABSENT_COPY = {
        "fulfillment_policy_id": ("shipping", "shipping"),
        "payment_policy_id": ("policies", "payment"),
        "return_policy_id": ("policies", "return"),
    }
    if mode == "live" and creds:
        for field in ebay_account.absent_for(uid or ""):
            target_kind = ABSENT_COPY.get(field)
            if not target_kind or (creds.get(field) or "").strip():
                continue
            target, kind = target_kind
            issues.append({
                "target": target, "level": "error",
                "title": f"Your eBay account has no {kind} policy yet",
                "fix": (f"eBay won't accept a listing without one, and there "
                        f"isn't a {kind} policy on the account you're connected "
                        f"as (@{creds.get('ebay_username') or 'your account'}) "
                        f"for us to use. Create one on eBay under Seller Hub → "
                        f"Account → Business policies, then publish again — "
                        f"we'll pick it up automatically."),
            })

    if mode == "live" and policy_exists is False:
        # eBay says this account has no such policy. Almost always a policy id
        # left over from a different eBay account — publishing with it fails
        # inside eBay, where nothing points at the shipping card.
        per_listing = bool((listing.fulfillment_policy_id or "").strip())
        issues.append({
            "target": "shipping", "level": "error",
            "title": "That shipping policy isn't on your eBay account",
            "fix": ("Pick a shipping policy on the Shipping card"
                    if per_listing else
                    "Choose a shipping policy in Settings → Listing defaults")
            + " — the saved one doesn't exist on the eBay account you're "
              "connected as, which is what happens to policies carried over "
              "from another account.",
        })
    return issues


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
        # Forget the "checked recently" mark too: the next connect may be a
        # different seller, and trusting the previous account's pass is exactly
        # how another account's policy ids survive a switch.
        ebay_account.forget_verified(uid)
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
        """Publish/revise one listing on eBay.

        Wraps the real work in the publish guard: publishes of the SAME listing
        run one at a time, and the record is re-read inside the lock so the
        decision to create a new eBay listing is made from what the server
        knows, never from a payload the browser may have assembled before the
        first publish. Two attempts that overlap (a reload mid-publish, a
        double tap, a retried request) otherwise both read "never listed" and
        each create a live listing — the duplicate pairs sellers were seeing.
        """
        with publish_guard.session_lock(ctx.session_id):
            ctx = _with_stored_identity(ctx)
            return self._publish_locked(ctx, creds)

    def _publish_locked(self, ctx: PublishContext,
                        creds: Optional[dict]) -> PublishOutcome:
        session_id, listing, mode = ctx.session_id, ctx.listing, ctx.mode
        prev_rec = ctx.prev_record
        already_live = prev_rec.get("status") in ("published", "live")
        # Which of the three publish routes this request takes is the single
        # most useful thing to know when a publish "does nothing" — create,
        # revise, or the Inventory fallback, and what decided it. One line,
        # no listing content.
        log.info("publish route: session=%s mode=%s connected=%s "
                 "prev_status=%s already_live=%s imported=%s has_item_id=%s",
                 session_id, mode, bool(creds), prev_rec.get("status") or "none",
                 already_live, listing_sync.is_imported(listing),
                 bool(listing.ebay_listing_id))

        # A listing IMPORTED from eBay (or published by us through Trading)
        # isn't Inventory-API managed, so edits go back through the Trading
        # API instead of the publish path below.
        if listing_sync.is_imported(listing):
            uid = ctx.uid
            # This listing lives on an eBay account that is no longer the
            # connected one. Reviving it here would revise (or relist) another
            # seller's item under this account's policies — refuse plainly and
            # say which account owns it.
            # Only a NAMED other account refuses the write: an owner we could
            # not name is not proof of a different store, and blocking on it
            # strands every imported listing the seller has (see
            # listing_sync.UNKNOWN_ACCOUNT).
            owner = listing_sync.named_account_of(listing)
            connected = ((creds or {}).get("ebay_username") or "").strip()
            if creds and owner and connected and owner != connected:
                message = (f"This listing belongs to your other eBay account "
                           f"(@{owner}); you're connected as @{connected}. "
                           "Reconnect that account to change it.")
                db.upsert_listing(session_id, listing.model_dump(),
                                  status=prev_rec.get("status") or "published",
                                  user_id=uid)
                return PublishOutcome(
                    ok=False, message=message,
                    issues=[{"target": "account", "level": "error",
                             "title": "That's on a different eBay account",
                             "fix": message}],
                    raw={"dry_run": False, "error": True, "mode": mode,
                         "message": message})
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
            # The checklist runs on this route too. It used to gate only the
            # Inventory publish below, but every listing this app creates is
            # stamped source="ebay" the moment it goes live, so from the second
            # publish onward a seller took this route and got no checklist at
            # all — the one path where an eBay rejection is guaranteed to be
            # the seller's first sign of a problem.
            #
            # A relist IS a new listing (same create_on_ebay call as a first
            # publish), so it answers to the full live contract. A revise only
            # has to satisfy what it actually sends — see preflight.validate.
            problems = preflight.errors_only(
                preflight_issues(uid, listing, "live" if relist else "revise"))
            if problems and not relist and not config.EBAY_PREFLIGHT_BLOCKS_REVISE:
                # Observing, not blocking yet — see EBAY_PREFLIGHT_BLOCKS_REVISE.
                log.info("revise would be blocked by preflight: session=%s "
                         "issues=%s", session_id,
                         [p.get("title") for p in problems])
                problems = []
            if problems:
                db.upsert_listing(session_id, listing.model_dump(),
                                  status=prev_status, user_id=uid)
                log.info("%s blocked by preflight: session=%s issues=%d",
                         "relist" if relist else "revise", session_id,
                         len(problems))
                return PublishOutcome(
                    ok=False, issues=problems,
                    message=f"Not quite ready — {len(problems)} thing"
                            f"{'s' if len(problems) != 1 else ''} to fix "
                            "before eBay will accept it:",
                    raw={"dry_run": False, "error": True, "mode": mode,
                         "message": f"Not quite ready — {len(problems)} thing"
                                    f"{'s' if len(problems) != 1 else ''} to "
                                    "fix before eBay will accept it:",
                         "issues": problems})
            # Captured before the create overwrites it: a relist mints a new
            # item id, and the OLD one is what makes this relist's idempotency
            # key distinct from the publish that first listed the item.
            ended_item_id = listing.ebay_listing_id or ""
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
                    # Keyed on the item being replaced: a retried relist reuses
                    # the key (so it can't double-list), while an intentional
                    # later relist of a different item gets its own.
                    res = listing_sync.create_on_ebay(
                        creds["access_token"], listing, urls, creds=creds,
                        idempotency_key=publish_guard.idempotency_key(
                            session_id, replacing_item_id=ended_item_id))
                else:
                    res = listing_sync.push_edit(creds["access_token"], listing,
                                                 image_urls=urls)
            except ValueError as exc:  # TradingError — eBay's own reason
                log.warning("%s (imported) failed: session=%s: %s",
                            "relist" if relist else "revise", session_id, exc)
                db.upsert_listing(session_id, listing.model_dump(),
                                  status=prev_status, user_id=uid)
                issues = ebay_account.publish_block_issues(exc, creds)
                return PublishOutcome(
                    ok=False, message=str(exc), issues=issues,
                    raw={"dry_run": False, "error": True, "mode": "live",
                         "message": str(exc), "issues": issues})
            # The record's owner. Publishing through this account's creds IS
            # the ownership fact, so write it down — without this the record's
            # ebay_account stayed empty forever and the account-switch
            # bookkeeping (count_foreign_listings, release) could never see
            # it. Fills a blank only: a stamped record's history is not this
            # call's to rewrite.
            if not listing.ebay_account:
                listing.ebay_account = ((creds or {}).get("ebay_username")
                                        or "").strip()
            recorded = _record_published(session_id, listing.model_dump(),
                                         "published", uid)
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
                     **({} if recorded else {"record_warning": RECORD_WARNING}),
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
                    creds["access_token"], listing, urls, creds=creds,
                    idempotency_key=publish_guard.idempotency_key(session_id))
            except ValueError as exc:  # TradingError — eBay's own reason
                log.warning("trading publish failed: session=%s: %s", session_id, exc)
                db.upsert_listing(session_id, listing.model_dump(),
                                  status="draft", user_id=ctx.uid)
                issues = ebay_account.publish_block_issues(exc, creds)
                return PublishOutcome(
                    ok=False, message=str(exc), issues=issues,
                    raw={"dry_run": False, "error": True, "mode": "live",
                         "message": str(exc), "issues": issues})
            # Record the item id FIRST. Everything below is optional extra work
            # (photo bookkeeping, promotion) and none of it is worth risking the
            # one write that stops the next publish creating a second listing:
            # an id that never lands is indistinguishable from "never listed".
            # The record's owner. Publishing through this account's creds IS
            # the ownership fact, so write it down — without this the record's
            # ebay_account stayed empty forever and the account-switch
            # bookkeeping (count_foreign_listings, release) could never see
            # it. Fills a blank only: a stamped record's history is not this
            # call's to rewrite.
            if not listing.ebay_account:
                listing.ebay_account = ((creds or {}).get("ebay_username")
                                        or "").strip()
            recorded = _record_published(session_id, listing.model_dump(),
                                         "published", ctx.uid)
            storage.save_listing(session_id, listing)
            result = {"published": True, "mode": "live",
                      "listing_id": res["listing_id"],
                      **({} if recorded else {"record_warning": RECORD_WARNING}),
                      "message": ("Your listing is live on eBay."
                                  if not res.get("already_listed") else
                                  "This listing was already live on eBay — "
                                  "reusing it instead of posting a duplicate.")}
            if listing.promote or auto_promote_enabled(ctx.uid):
                result["promote_status"] = promote(
                    session_id, listing, creds,
                    rate=listing.ad_rate_percent,
                    ebay_listing_id=res["listing_id"])
                # promote() records the rate it actually used on the listing.
                db.upsert_listing(session_id, listing.model_dump(),
                                  status="published", user_id=ctx.uid)
            log.info("trading publish ok: session=%s item=%s adopted=%s",
                     session_id, res["listing_id"],
                     bool(res.get("already_listed")))
            listing_id = str(res["listing_id"])
            return PublishOutcome(
                ok=True, listing_id=listing_id, status="published",
                url=_view_url(listing, listing_id),
                message=result["message"], raw=result)

        # Below here a live publish with no creds falls back to the env-config
        # single-tenant path, which carries the OPERATOR's refresh token and
        # policy ids. That is the right behaviour for a deployment with no
        # accounts at all, and completely wrong for a signed-in seller whose
        # token refresh happened to fail a moment ago — their listing would go
        # live on the operator's eBay account. `creds_for` returns None for
        # both, so ask which one this is before letting it through.
        if mode == "live" and not creds and has_stored_connection(ctx.uid):
            log.warning("publish refused: session=%s eBay token refresh failed "
                        "for uid=%s", session_id, ctx.uid)
            msg = ("We couldn't reach your eBay account just now, so nothing "
                   "was published. Try again in a minute — if it keeps "
                   "happening, reconnect eBay in Settings.")
            return PublishOutcome(
                ok=False, message=msg,
                raw={"dry_run": False, "error": True, "mode": mode,
                     "message": msg})

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
        recorded = (_record_published(session_id, dump, status, ctx.uid)
                    if result.get("published")
                    else db.upsert_listing(session_id, dump, status=status,
                                           user_id=ctx.uid))
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
        if result.get("published") and not recorded:
            result["record_warning"] = RECORD_WARNING
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
