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

import threading
import time
from typing import Optional

from fastapi import HTTPException

from .. import config, db, ebay_auth, storage
from ..config import log
from ..models import Listing
from ..services import (ebay, ebay_account, ebay_trading, image_import,
                        listing_sync, preflight, promotions, publish_guard,
                        sync_merge, taxonomy)
from ..services.background import run_in_background
from . import register
from .base import PublishContext, PublishOutcome
from . import state as marketplace_state
from .state import STICKY_STATUSES

# Access tokens live ~2 hours; refreshing one per request added a serial
# ~300-600ms eBay round-trip to every creds-needing call (a dashboard load
# fires several). Keyed by the refresh token itself, so a reconnect (new
# refresh token) naturally misses the cache.
_TOKEN_CACHE: dict[str, tuple[float, str]] = {}
_TOKEN_CACHE_MAX = 50
_TOKEN_CACHE_LOCK = threading.Lock()


def _make_room() -> None:
    """Keep _TOKEN_CACHE bounded without flushing it.

    The cap used to be enforced with `.clear()`, which on a busy instance
    threw away every connected seller's token the moment the 51st arrived —
    so all of them paid a fresh ~300-600ms eBay round-trip on their next
    request, together, and the instance that was busy enough to fill the
    cache is exactly the one that could least afford it.

    Expired entries go first (they were dead anyway, and that alone almost
    always makes room). Only if the cache is still full does anything live
    get dropped, and then it is whatever expires soonest — the seller who was
    closest to paying for a refresh regardless.
    """
    now = time.time()
    for key in [k for k, (expires, _) in _TOKEN_CACHE.items() if expires <= now]:
        _TOKEN_CACHE.pop(key, None)
    while len(_TOKEN_CACHE) >= _TOKEN_CACHE_MAX:
        soonest = min(_TOKEN_CACHE, key=lambda k: _TOKEN_CACHE[k][0])
        _TOKEN_CACHE.pop(soonest, None)


def access_token_for(refresh_token: str) -> str:
    hit = _TOKEN_CACHE.get(refresh_token)
    if hit and time.time() < hit[0]:
        return hit[1]
    fresh = ebay_auth.refresh_access_token(refresh_token)
    # Drop the cached token 90s before eBay's expiry so an in-flight request
    # never carries one that dies mid-call.
    expires = float(fresh.get("expires_at") or (time.time() + 1800))
    with _TOKEN_CACHE_LOCK:
        if len(_TOKEN_CACHE) >= _TOKEN_CACHE_MAX:
            _make_room()
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
        # This pass already asked eBay which policies the account has. Keep
        # the answer: it is the only thing that can vet the shipping policy id
        # stored on a DRAFT, which is account-scoped like the others but lives
        # outside `acct` and so was never reconciled with them.
        ebay_account.remember_valid_policies(uid, result.valid)
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
        # Which eBay account this token is for. Everything that reads or
        # writes a listing on eBay is scoped by it — see listing_sync.owns.
        # The immutable id is what actually decides; the username is kept for
        # display and for records too old to carry an id.
        "ebay_user_id": acct.get("ebay_user_id", "") or "",
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
    recommended ad rate. OFF unless the seller explicitly turned it on.

    Promoted Listings Standard is COST_PER_SALE — eBay takes a percentage of
    the sale price (10% by default here) when an item sells through the
    promotion. So this is a spending decision, and two things that used to
    return True are not decisions at all:

      - an ABSENT preference, i.e. every seller who has never opened Settings
        and saved that field. Silence is not agreement to a fee.
      - an UNREADABLE preference. db.get_prefs answers {} on a database
        failure, so an outage enrolled sellers as surely as a choice did, and
        nothing recorded that it had.

    The second is indefensible whatever the right default is: "we could not
    find out" is never a yes. The first reverses a deliberate product choice
    — the previous default was ON because sellers reported publishes landing
    unpromoted — and it is reversed on purpose: an unpromoted listing is a
    missed opportunity the seller can fix, while an unasked-for ad fee is
    money taken from someone who never agreed.

    Per-listing Promote is untouched: ticking it IS explicit consent for that
    listing. Anonymous/env-token publishes stay explicit-only.
    """
    if not uid:
        return False
    try:
        value = db.get_prefs(uid).get("auto_promote")
    except Exception as exc:  # noqa: BLE001 - an outage is not consent
        log.warning("promote: couldn't read the auto-promote preference for "
                    "%s, treating as off: %s", uid, exc)
        return False
    return bool(value)


def promote(record_id: str, listing: Listing, creds: Optional[dict],
            rate: Optional[float] = None,
            ebay_listing_id: Optional[str] = None,
            chosen_by_seller: bool = True) -> dict:
    """Turn Promoted Listings on for one listing and run the ad call.

    The single place that decides an ad rate: an explicit `rate` wins, else
    eBay's recommendation for `ebay_listing_id`, else the default. A 0% rate
    makes promote_listing silently no-op — which is exactly why listings with
    Promote toggled on were never actually promoted — so the rate is always
    filled in here. Mutates listing.promote/ad_rate_percent so the caller can
    persist what really ran, and never raises: a promotion problem must not
    fail the publish that preceded it.

    `chosen_by_seller` says whether promoting THIS listing was an explicit
    choice — the editor's Promote toggle, which sits next to a rate slider and
    a live fee preview — or came from the account-wide auto-promote setting,
    whose whole promise is "at eBay's recommended ad rate".

    That distinction decides what happens when eBay has no recommendation.
    Falling through to DEFAULT_AD_RATE is right for the first: it is the rate
    the slider already showed them. For the second it is not — eBay's
    recommendations are usually low single digits, so a silent 10% can be
    several times what the seller agreed to, on a number no screen ever
    displayed. There, no rate means no promotion. The listing is live and can
    be promoted by hand at any time; an ad charged at a rate nobody was quoted
    cannot be taken back.
    """
    try:
        if not rate or rate <= 0:
            rate = None
            if ebay_listing_id:
                recommended = promotions.suggested_ad_rates(
                    creds, [str(ebay_listing_id)])
                rate = recommended.get(str(ebay_listing_id))
        if not rate and not chosen_by_seller:
            log.info("promote skipped (%s): no rate from eBay and the seller "
                     "did not pick one", record_id)
            return {"promoted": False,
                    "message": ("We didn't promote this one: eBay had no "
                                "suggested ad rate for it, and auto-promote "
                                "runs at eBay's rate. Turn on Promote in the "
                                "listing to pick your own.")}
        # Only now: the flag is what the editor and the insights panel read as
        # "this is promoted", so setting it before the decision would leave a
        # listing marked promoted that never was.
        listing.promote = True
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
def _with_stored_identity(ctx: PublishContext) -> PublishContext:
    """`ctx` with the eBay identity fields taken from the stored record, and
    prev_record re-read inside the publish lock.

    Called under publish_guard.session_lock, so the snapshot it takes is the
    one the create-vs-revise decision is made from: any earlier publish of this
    listing has already finished and committed its item id.
    """
    # get_listing_STRICT, not get_listing. The plain one collapses "no such
    # listing" and "the read could not be performed" into None -- its own
    # comment says that is right for callers who just want the record and
    # wrong for a security check, and this is neither. It is the read the
    # create-vs-revise decision is made from, and the note above is explicit
    # about what believing a lost identity costs: a duplicate live listing.
    # One Postgres blip used to read as a brand-new session.
    #
    # Refusing is free here because nothing has been sent yet: the route
    # turns this into a 503, which says we could not check whether the
    # listing is already live, so we did not publish. The idempotency key
    # narrows that window rather than closing it -- it refuses a create
    # repeated soon after the first, not one sent long enough afterwards that
    # eBay no longer holds the UUID.
    # db.get_listing RAISES on a read failure rather than answering None, and
    # this deliberately does not catch it. Nothing has been sent yet, so
    # refusing costs nothing and the route turns it into a 503 -- whereas
    # believing "nothing stored" would mean creating a second live listing
    # for an item that is already up, which is what the note above is about.
    fresh = db.get_listing(ctx.session_id)
    if not fresh:
        # Nothing stored: a brand-new session that has never been saved. The
        # payload is all there is, and the idempotency key guards the create.
        return ctx
    stored = fresh.get("listing") or {}
    # The item id is this function's own: it is what the create-vs-revise
    # decision reads, and a stored one always wins over the payload's.
    # publish_guard owns that read (and normalises it to a stripped string, so
    # an id stored as a number can't read as "different from the payload" and
    # trigger a pointless overwrite); this used to inline a rawer copy of it.
    stored_id = publish_guard.stored_item_id(fresh)
    if stored_id and stored_id != ctx.listing.ebay_listing_id:
        log.info("publish: taking ebay_listing_id from the stored record "
                 "for session=%s", ctx.session_id)
        ctx.listing.ebay_listing_id = stored_id
    changed = marketplace_state.restore_server_fields(ctx.listing, stored)
    if changed:
        log.info("publish: taking %s from the stored record for session=%s",
                 ", ".join(changed), ctx.session_id)
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
    # A connected SELLER, not a configured server. The env credentials are the
    # operator's; since the Inventory engine went they cannot publish anything,
    # so counting them as "connected" made the checklist judge an account that
    # no publish would ever use.
    connected = bool(creds)
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


def _label_list(names) -> str:
    """Field names as the words a seller uses, deduped, in a sentence.

    The label map folds several fields onto one word — both halves of a
    package weight are "package weight" — so "the package weight and the
    package weight" has to be impossible.
    """
    seen, out = set(), []
    for name in names:
        label = sync_merge.FIELD_LABELS.get(name, name.replace("_", " "))
        if label not in seen:
            seen.add(label)
            out.append(label)
    if not out:
        return ""
    return out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1]


def revise_message(conflicts: Optional[dict], relist: bool,
                   remapped: str = "", unsent: Optional[list] = None) -> str:
    """What to tell the seller after eBay accepted the change.

    A revise deliberately omits every field the seller and eBay have BOTH
    changed since the last agreed state — sending either value would silently
    overwrite somebody's work, which is what the three-way merge exists to
    stop. But this said "Your eBay listing has been updated" regardless, so a
    seller who had edited the title got a success message, an unchanged
    listing on eBay, and no reason. An error would have been kinder; at least
    an error prompts.

    `unsent` is the other half of the same honesty, for a different reason:
    edits this app CANNOT put in a revise at all (see
    ebay_trading.REVISABLE_FIELDS). In practice that is the package — a
    corrected weight is the commonest edit after a listing goes live, and
    eBay's calculated postage is charged off it — so "updated" on its own was
    a claim about the listing that was not true of the part the seller had
    just fixed.
    """
    if relist:
        return ("Relisted! It's live on eBay as a fresh listing with a new "
                "item number.")
    # eBay retires categories and moves the listing itself. Saying so matters
    # because the seller's next surprise is otherwise a set of required item
    # specifics they have never seen, for a category they did not choose.
    moved = (" eBay also moved it to a different category, because the one it "
             "was in has been retired." if remapped else "")
    stayed = ""
    if unsent:
        stayed = (f" The {_label_list(unsent)} stayed here — eBay doesn't let "
                  "this app change that on a live listing. Update it in Seller "
                  "Hub, or end the listing and relist it.")
    held = [d["label"] for d in sync_merge.describe_conflicts(conflicts)]
    if not held:
        return "Your eBay listing has been updated." + moved + stayed
    seen, names = set(), []
    for label in held:  # the label map folds several fields onto one word
        if label not in seen:
            seen.add(label)
            names.append(label)
    listed = names[0] if len(names) == 1 else (
        ", ".join(names[:-1]) + " and " + names[-1])
    return (f"Your eBay listing has been updated — except the {listed}, which "
            f"you and eBay both changed. Choose which version to keep."
            + moved + stayed)


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
            if problems and not config.EBAY_PREFLIGHT_BLOCKS_REVISE:
                # Observing, not blocking yet — see EBAY_PREFLIGHT_BLOCKS_REVISE.
                #
                # A RELIST is covered by the same flag, which it was not at
                # first. The flag exists because these listings are live (or
                # were), some were made outside this app, and a checklist that
                # has never run against them will find things eBay accepted
                # years ago. A relist is the SAME population — an imported
                # listing that ended — so it has the same problem, and blocking
                # it strands the seller on the Inactive tab's own promise that
                # they can relist any time.
                #
                # The concrete misfire is the package weight. Imported records
                # take it from GetItem's ShippingPackageDetails, which eBay
                # omits for flat-rate listings, so it lands at 0 — and the app's
                # own Trading builder proves it is optional, emitting no
                # <ShippingPackageDetails> at all when there is none. The
                # checklist would block "eBay needs a package weight" on a
                # listing demonstrably live on eBay without one.
                log.info("%s would be blocked by preflight: session=%s "
                         "issues=%s", "relist" if relist else "revise",
                         session_id, [p.get("title") for p in problems])
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
                issues = ebay_account.publish_block_issues(
                    exc, creds, listing=listing,
                    verify=listing_sync.verifier(creds["access_token"], urls,
                                                 creds))
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
            if not listing.ebay_account_id:
                listing.ebay_account_id = ((creds or {}).get("ebay_user_id")
                                           or "").strip()
            # eBay moved the listing to a live category, exactly as it can on
            # a create: store what it actually FILED. This has to happen
            # before the record is written below — set afterwards it would
            # live only in memory, and the next load would be back to the
            # retired id that every aspect lookup and revise is built from.
            remapped = str(res.get("category_id") or "")
            if remapped:
                listing.category_id = remapped
            # eBay took the edit, so the record and the listing agree again
            # and there is nothing left pending. Cleared here — on acceptance
            # — and not when the request was built: a revise that failed
            # leaves its edits marked, so the retry still carries them.
            # Everything eBay took is settled. What it could not take is
            # NOT: the record still holds a value the live listing does not,
            # so those marks stay — the same rule as a failed revise, where
            # the edits stay marked so the retry still carries them. Clearing
            # them would file the seller's correction as delivered.
            unsent = list(res.get("unsent") or ())
            listing.clear_dirty()
            if unsent:
                listing.mark_dirty(*unsent)
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
            message = revise_message(listing.conflicts, relist, remapped,
                                     unsent=unsent)
            return PublishOutcome(
                ok=True, listing_id=listing_id, status="published",
                url=_view_url(listing, listing_id),
                message=message,
                raw={"published": True, "revised": not relist,
                     "relisted": relist, "mode": "live",
                     "listing_id": res.get("listing_id"),
                     # What eBay was NOT told, and why. Without this the
                     # editor has no way to render the question, and the
                     # seller's held-back edit stays invisible.
                     "held_back": sync_merge.describe_conflicts(
                         listing.conflicts),
                     # Edits eBay was never offered, as opposed to the ones
                     # above that it was and we withheld. Different question,
                     # different answer: nobody chooses between two versions
                     # here — the app simply cannot send this one.
                     "unsent": unsent,
                     **({} if recorded else {"record_warning": RECORD_WARNING}),
                     "message": message})

        # A listing that's already live must NEVER lose its 'published' status
        # in our records just because a revise attempt was blocked or errored —
        # the listing is still live on eBay either way.
        was_live = already_live
        # A draft belongs to this app, not to eBay. Saving one used to fall
        # through to the Inventory engine with do_publish=False, which created
        # an inventory item and an UNPUBLISHED offer on the seller's account.
        # Nothing good came of that: inventory-based listings don't appear in
        # Seller Hub, so the seller could neither find nor delete them, and the
        # live publish that follows goes out through Trading and mints an
        # entirely different item — the offer is never claimed. Every draft
        # save on a connected account left one behind.
        if mode == "draft":
            db.upsert_listing(session_id, listing.model_dump(),
                              status="published" if was_live else "draft",
                              user_id=ctx.uid)
            message = ("Saved to your drafts. It is NOT on eBay — press "
                       "Publish Live when you're ready to list it."
                       if creds else
                       "Saved to your drafts — find it under Drafts. It is "
                       "NOT on eBay: connect your eBay account and press "
                       "Publish Live when you're ready to list it.")
            log.info("draft saved locally: session=%s connected=%s",
                     session_id, bool(creds))
            return PublishOutcome(
                ok=True, status="published" if was_live else "draft",
                message=message,
                raw={"dry_run": False, "draft": True, "mode": "draft",
                     "message": message})
        # Pre-publish checklist: catch everything eBay would reject BEFORE the
        # round-trip, with field-targeted fixes. Only gates a real (connected)
        # live publish — dry-runs and drafts stay permissive. Env credentials
        # no longer publish, so gating on them blocked the DRY RUN they now
        # produce, which is the one thing an unconnected deployment can do.
        if mode == "live" and creds:
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
                    # Remembering the key saves a lookup next time; the
                    # publish below works without it, and the seller is told
                    # nothing about it either way.
                    db.save_ebay_account_best_effort(
                        creds["_uid"], merchant_location_key=key)
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
                issues = ebay_account.publish_block_issues(
                    exc, creds, listing=listing,
                    verify=listing_sync.verifier(creds["access_token"], urls,
                                                 creds))
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
            if not listing.ebay_account_id:
                listing.ebay_account_id = ((creds or {}).get("ebay_user_id")
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
                    ebay_listing_id=res["listing_id"],
                    # Read BEFORE promote() sets it: the per-listing toggle is
                    # the seller's own choice, made beside a rate slider and a
                    # fee preview. Without it, everything auto-promote touched
                    # would look like an explicit request.
                    chosen_by_seller=bool(listing.promote))
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

        # A live publish with no creds used to fall through to the env-config
        # single-tenant path, which carries the OPERATOR's refresh token and
        # policy ids. That is the right behaviour for a deployment with no
        # accounts at all, and completely wrong for a signed-in seller whose
        # token refresh happened to fail a moment ago — their listing would go
        # live on the operator's eBay account. `creds_for` returns None for
        # both, so ask which one this is before letting it through.
        if not creds and has_stored_connection(ctx.uid):
            log.warning("publish refused: session=%s eBay token refresh failed "
                        "for uid=%s", session_id, ctx.uid)
            msg = ("We couldn't reach your eBay account just now, so nothing "
                   "was published. Try again in a minute — if it keeps "
                   "happening, reconnect eBay in Settings.")
            return PublishOutcome(
                ok=False, message=msg,
                raw={"dry_run": False, "error": True, "mode": mode,
                     "message": msg})

        if not creds:
            # No eBay account anywhere: render what a publish WOULD send so the
            # payload can be inspected without one.
            return self._dry_run(ctx)

        # Connected, live, and neither a new listing (handled above) nor an
        # imported one (returned far above). What's left is a record that
        # claims to be live but carries no source="ebay" stamp — only an
        # Inventory-API listing from an older build of this app can be that.
        # eBay refuses to let the Trading API revise those and offers no way to
        # convert one, so say what actually works instead of sending a call
        # that comes back in eBay's words.
        log.warning("publish: no route for session=%s (already_live=%s "
                    "item_id=%s) — legacy inventory-managed record",
                    session_id, already_live, bool(listing.ebay_listing_id))
        db.upsert_listing(session_id, listing.model_dump(),
                          status="published" if was_live else "draft",
                          user_id=ctx.uid)
        msg = ("This listing was published by an older version of the app, and "
               "eBay won't let it be edited from here. End it on eBay, then "
               "use Relist to publish it fresh.")
        issues = [{"target": "account", "level": "error",
                   "title": "This listing can't be edited from here",
                   "fix": msg}]
        return PublishOutcome(
            ok=False, message=msg, issues=issues,
            raw={"dry_run": False, "error": True, "mode": mode,
                 "message": msg, "issues": issues})

    def _dry_run(self, ctx: PublishContext) -> PublishOutcome:
        """The request a publish would make, for a deployment with no account.

        Renders the Trading XML through the same builder create_listing uses.
        The old dry run rendered an Inventory item + offer, which after the
        Trading switch described a call this app never makes — a payload a
        seller could act on and still be surprised by the real publish.
        """
        listing = ctx.listing
        urls = ebay.image_urls_for(ctx.session_id, listing, ctx.base_url)
        call, body = ebay_trading.build_add_item(
            listing, urls,
            policies={"fulfillment_policy_id": config.EBAY_FULFILLMENT_POLICY_ID,
                      "payment_policy_id": config.EBAY_PAYMENT_POLICY_ID,
                      "return_policy_id": config.EBAY_RETURN_POLICY_ID})
        # No postal code: a dry run has no connected account to read a
        # ship-from ZIP from, and build_add_item omits the element rather than
        # inventing one. create_listing is what refuses a real publish without
        # it, so the preview stays an honest picture of an unconfigured app.
        # In production this is a FAILED publish, not a successful preview.
        #
        # "Published" is a claim about the listing being live on eBay. Nothing
        # was created here, so ok:true was untrue in the way that matters: the
        # seller is told their item is listed, closes the app, and finds out
        # later that it never was. The rendered XML and the export path made
        # it worse rather than better — neither is something a seller can act
        # on, and the path describes the server's own filesystem.
        #
        # Outside production the payload preview stays, because it is a real
        # development tool: the only way to see what a publish would send
        # without connecting an account.
        message = ("Connect your eBay account in Settings to publish. "
                   "Nothing was listed.")
        if config.EBAY_ENV == "production":
            return PublishOutcome(
                ok=False, status="draft", message=message,
                issues=[{"target": "account", "level": "error",
                         "title": "eBay isn't connected",
                         "fix": "Connect eBay in Settings, then publish again."}],
                raw={"dry_run": False, "error": True, "mode": "live",
                     "message": message})

        payload = {"call": call, "xml": body, "mode": "live"}
        export_path = storage.write_export(ctx.session_id, "ebay_payload", payload)
        dev_message = ("No eBay account connected — generated the "
                       f"{call} request instead of publishing. Connect eBay in "
                       "Settings to go live. (Server-side eBay credentials no "
                       "longer publish on their own; a listing is always created "
                       "on a connected seller's account.)")
        return PublishOutcome(
            ok=True, dry_run=True, status="dry_run", message=dev_message,
            raw={"dry_run": True, "mode": "live", "message": dev_message,
                 "export_path": str(export_path), "payload": payload})


register(EbayProvider())
