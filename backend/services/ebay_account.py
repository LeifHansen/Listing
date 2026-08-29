"""Which eBay account is connected, and what follows from that.

Two questions live here, both of which used to be answered inside request
handlers where nothing could test them:

  * On a reconnect, which of the seller's saved settings still apply? Business
    policy ids and location keys are minted by ONE eBay seller account and
    rejected outright on another, so carrying them across a switch publishes
    listings that fail for a reason no field on screen explains.

  * When eBay refuses to list ANYTHING (error 240), is it something the seller
    can see and clear? The code says only "the listing or seller may be in
    violation of eBay policy", which is four causes in a trench coat. Two of
    them the account APIs will name; for the rest, `probe_block_scope` gets
    eBay to say whether it is refusing the account or the listing's words.

Deliberately importable without backend.main — same rule as sync_guard: the CI
`checks` job installs no image/AI stack, so anything that reaches for main
(and through it Pillow, anthropic, rembg) is a test that cannot run there.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, NamedTuple, Optional

from .. import config, ebay_auth, ebay_errors
from ..config import log

# The saved eBay settings that belong to one account. Each is an id eBay
# minted for a specific seller; on any other account it is not just wrong but
# rejected.
ACCOUNT_SCOPED = ("fulfillment_policy_id", "payment_policy_id",
                  "return_policy_id", "merchant_location_key")


class Reconciled(NamedTuple):
    """What one pass against eBay learned about the account-scoped settings.

    changes    -- what to store (empty when everything already matches AND when
                  eBay could not be read; `conclusive` is what tells them apart)
    conclusive -- eBay answered for every account-scoped field
    absent     -- fields eBay positively has NONE of. Nothing can be synced into
                  these; only the seller can create them, on eBay.
    valid      -- {kind: {policy ids this account actually has}}, straight from
                  the lookup this pass already paid for. The account-scoped
                  fields above are not the only place a policy id is stored:
                  a DRAFT carries its own shipping choice, and that one is
                  reconciled nowhere. Handing the set back lets the publish
                  path check it without buying the lookup a second time.
    """
    changes: dict
    conclusive: bool
    absent: list
    valid: dict = {}


def reconcile_account_settings(
        access: str, existing: dict, discovered: dict, *,
        policy_ids: Optional[Callable[[str], dict]] = None,
        location_keys: Optional[Callable[[str], Optional[set]]] = None,
        fill_blanks: bool = True) -> "Reconciled":
    """(what to store for this account, did eBay actually answer).

    A saved choice survives only if it still EXISTS on this account; otherwise
    the auto-discovered default for that slot takes over.

    This used to be decided from the account NAME, which kept every saved id
    whenever the name was unreadable — the common case, because a connection
    made before the identity scope was granted 403s on the identity call. A
    seller switching accounts therefore carried the previous store's shipping,
    payment, return and location ids straight into the new one.

    When eBay can't say what exists (a kind that failed to fetch comes back
    absent, not empty), the saved value is left alone: an outage must never
    silently re-pick a seller's shipping.

    `fill_blanks` also fills an EMPTY slot from the discovered default. That is
    right while connecting — a fresh account needs somewhere to start — and
    wrong everywhere else, because "— none —" is a choice the Settings screen
    offers for all three business policies. A seller who deliberately turns
    their return policy off must not have eBay's first one written back over it
    by a later pass.

    The second return value is the one the caller usually gets wrong. Neither
    lookup RAISES on failure — policy_ids_on_account skips a kind it couldn't
    fetch, location_keys_on_account returns None — so a total eBay outage is
    indistinguishable, from the changes alone, from "everything already
    matches": both produce {}. It is False unless every account-scoped field
    got a definite answer, and only a True licenses a caller to record that
    this account has been checked.
    """
    valid_policies = (policy_ids or ebay_auth.policy_ids_on_account)(access)
    valid_locations = (location_keys or ebay_auth.location_keys_on_account)(access)
    out: dict = {}
    absent: list[str] = []
    conclusive = valid_locations is not None
    for field in ACCOUNT_SCOPED:
        saved = (existing.get(field) or "").strip()
        kind = field[: -len("_policy_id")] if field.endswith("_policy_id") else ""
        known = valid_policies.get(kind) if kind else valid_locations
        if known is None:
            conclusive = False
        elif not known:
            # eBay answered, and the answer is "this account has none of these".
            # Distinct from an unreadable lookup: there is nothing to sync from,
            # and no amount of retrying will produce one. Only the seller can,
            # on eBay.
            absent.append(field)
        if saved and known is not None and saved not in known:
            out[field] = discovered.get(field, "")  # gone from this account
        elif (not saved and fill_blanks and discovered.get(field)
                and (known is None or discovered[field] in known)):
            # Fill the gap -- but never with an id this account provably does
            # not have. `discovered` is a separate lookup and can disagree with
            # `known` (it is derived per-kind and a partial failure leaves a
            # stale value in it); writing one in would recreate, from inside
            # the repair itself, exactly the foreign-id state the repair exists
            # to remove. When known is None we have no opinion, which is the
            # long-standing connect-time behaviour.
            out[field] = discovered[field]
    return Reconciled(out, conclusive, absent, valid_policies)


# How long a verified set of account-scoped ids is trusted before the next
# publish re-checks them against eBay. The check costs three account-API calls;
# at this interval that is a rounding error next to what a publish already
# spends, and it bounds how long a stale id can survive.
VERIFY_TTL = config.env_float("EBAY_POLICY_VERIFY_TTL", 600.0)

_verified: dict[str, float] = {}
_verified_lock = threading.Lock()


def verify_due(uid: str, now: Optional[float] = None,
               ttl: float = VERIFY_TTL) -> bool:
    """Has this user's account gone long enough without a check?

    Deliberately a plain time gate rather than anything cleverer: the cost of
    checking too often is three cheap API calls, and the cost of checking too
    rarely is every publish rejected by eBay for a reason no field on screen
    explains.
    """
    now = time.time() if now is None else now
    with _verified_lock:
        return (now - _verified.get(uid, 0.0)) >= ttl


def note_verified(uid: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _verified_lock:
        _verified[uid] = now


# The policy ids each account actually has, from the last verify pass. This
# exists because ACCOUNT_SCOPED is not the whole story: a DRAFT stores its own
# `fulfillment_policy_id` (the editor's and the bulk card's Shipping dropdown),
# and the repair pass has never looked at it. A draft created while another
# eBay account was connected therefore carries that account's policy id for
# ever, and publishes it on every attempt — every listing failing identically,
# on an account whose own API checks all come back clean.
_valid_policies: dict[str, tuple[float, dict]] = {}


def remember_valid_policies(uid: str, valid: dict,
                            now: Optional[float] = None) -> None:
    """Keep what the verify pass already learned, for the publish path."""
    if not uid or not valid:
        return
    with _verified_lock:
        _valid_policies[uid] = (time.time() if now is None else now, valid)


def known_policy_ids(uid: str, kind: str,
                     now: Optional[float] = None) -> Optional[set]:
    """The ids of `kind` this account has, or None for "we don't know".

    None is the important half: an unread or expired answer must leave a saved
    id alone. Replacing a policy the seller chose because we could not reach
    eBay is the same class of bug as keeping a foreign one.
    """
    if not uid:
        return None
    now = time.time() if now is None else now
    with _verified_lock:
        at, valid = _valid_policies.get(uid, (0.0, {}))
    if not valid or (now - at) >= VERIFY_TTL:
        return None
    known = valid.get(kind)
    return known if known else None


def usable_policy_id(uid: str, kind: str, wanted: str, fallback: str) -> str:
    """`wanted` if this account really has it, else `fallback`.

    Only ever downgrades to the account default, and only on a definite
    answer: an id we cannot check is passed through untouched.
    """
    wanted = (wanted or "").strip()
    if not wanted:
        return fallback
    known = known_policy_ids(uid, kind)
    if known is None or wanted in known:
        return wanted
    log.warning("ebay: draft carried a %s policy id this account does not "
                "have (%s) — falling back to the account default", kind, wanted)
    return fallback


# Which account-scoped settings eBay says this seller has NONE of, from the
# last conclusive pass. Cached beside the verify clock and for the same reason:
# it is the answer to "why is this slot still empty after a sync", and the
# publish checklist needs it to tell the seller the truth. "Choose one in
# Settings" is a dead end when the dropdown it points at is empty.
_absent: dict[str, list] = {}


def note_absent(uid: str, fields: list) -> None:
    with _verified_lock:
        _absent[uid] = list(fields)


def absent_for(uid: str) -> list:
    """Fields eBay had none of at the last conclusive pass. Empty when we have
    not looked, which reads the same as "nothing missing" on purpose: an
    unknown must never produce a scary message we cannot stand behind."""
    with _verified_lock:
        return list(_absent.get(uid, ()))


def forget_verified(uid: Optional[str] = None) -> None:
    """Drop the "checked recently" memory — on disconnect, so the next connect
    re-verifies immediately instead of trusting the previous account's pass."""
    with _verified_lock:
        if uid is None:
            _verified.clear()
            _absent.clear()
            _block_scope.clear()
            _valid_policies.clear()
        else:
            _verified.pop(uid, None)
            _absent.pop(uid, None)
            # A hold belongs to the account that was connected, never to the
            # next one: carrying the verdict across a switch would tell a
            # healthy account it is blocked.
            _block_scope.pop(uid, None)
            # Policy ids belong to the account that was connected even more
            # literally: kept across a switch, they would vet the NEW
            # account's drafts against the OLD account's policies — rewriting
            # a valid choice to a foreign default, which is the exact bug the
            # check exists to prevent, inverted.
            _valid_policies.pop(uid, None)


def settings_were_dropped(save_kwargs: dict, existing: dict) -> bool:
    """True when a saved account-scoped id didn't survive the reconnect.

    That is the tell-tale of a different eBay account even when the account
    name is unreadable — ids are per-account, so losing one means the store
    behind the connection changed.
    """
    return any(existing.get(f) and save_kwargs.get(f, existing.get(f)) != existing.get(f)
               for f in ACCOUNT_SCOPED)


def releasable(data: dict, connected: str,
               include_unowned: bool = False) -> bool:
    """Should release-foreign-listings unlink this record?

    A record STAMPED with some other account (the previous-account sentinel
    included) always releases: its item id belongs to another store. A record
    with NO owner recorded is the hard case — it predates ownership stamping,
    so after a switch the app cannot tell it from the connected account's own
    imports. Only the seller knows whether the store behind it is still the
    one connected, so those release only on their explicit say-so
    (`include_unowned`), and only when the record actually carries an eBay
    identity — a plain local draft has nothing to unlink.
    """
    owner = (data.get("ebay_account") or "").strip()
    if owner and owner != (connected or "").strip():
        return True
    if owner or not include_unowned:
        return False
    return bool((data.get("ebay_listing_id") or "").strip()
                or (data.get("marketplaces") or {}).get("ebay"))


# The top-level fields that mirror eBay's entry in `marketplaces`. Cleared
# alongside it, because `source` is what routes a later edit down the Trading
# path and `ebay_listing_id` is what it would aim at.
_EBAY_PROJECTION = ("ebay_account", "ebay_listing_id", "source", "view_url",
                    "sku")


def unlink_ebay(data: dict) -> dict:
    """Strip this record's eBay identity, leaving every other marketplace
    exactly as it was. Mutates and returns `data`.

    The eBay entry is removed from `marketplaces` by key. Dropping the whole
    map — which is what this used to do — also deleted the seller's Etsy and
    Depop ids, URLs and statuses. Nothing changes on those marketplaces, so
    the listings stay live and the app simply forgets them; the record still
    looks intact, and the loss only surfaces later as a duplicate publish or
    an update that goes nowhere.
    """
    data.update({name: "" for name in _EBAY_PROJECTION})
    data["image_urls"] = data.get("image_urls") or []
    markets = data.get("marketplaces")
    if isinstance(markets, dict):
        # By key, so a marketplace added after this was written survives too.
        data["marketplaces"] = {k: v for k, v in markets.items() if k != "ebay"}
    return data


# eBay's "this account can't list right now" code. It carries no field to fix
# and repeats on every listing, so the only useful follow-up is to ask what
# the hold actually is.
BLOCKED_CODE = "240"


def publish_block_issues(exc: Exception, creds: Optional[dict], *,
                         listing=None,
                         verify: Optional[Callable[[Any], None]] = None,
                         payments: Optional[Callable[[str], dict]] = None,
                         privileges: Optional[Callable[[str], Optional[dict]]] = None,
                         ) -> list[dict]:
    """Issues for a failed publish, and for eBay error 240 a look at WHY.

    A 240 carries no field to fix and repeats on every listing. eBay's own
    wording — "the listing or seller may be in violation of eBay policy" — is
    four causes in a trench coat, and the account-level ones are far more
    common than the wording one. So on a 240 only, ask the two APIs that will
    state a cause plainly:

      * the payments program (onboarding not finished);
      * selling privileges (registration not finished, or a selling limit).

    When neither names a cause the seller is still where they started, so a
    third question goes to eBay itself: `probe_block_scope` re-puts the listing
    with plain wording as a dry run, which creates nothing and settles whether
    a 240 belongs to the account or to this listing's words.

    All of it runs on the failure path only, where a round-trip costs nothing
    and a blind seller costs everything. Every check here is silent on failure:
    a lookup that fails must never skip the others, and nothing invented is
    ever reported as a finding.

    A diagnosis never replaces the rejection — but it does lead it. eBay's 240
    issue is a placeholder ("refused, cause unstated"), and the surfaces that
    have room for one line (a bulk card, the publish toast) show the FIRST
    issue: with the placeholder first, everything learned here was rendered
    invisible exactly where it was needed most.
    """
    issues = ebay_errors.from_trading_error(exc)
    if str(getattr(exc, "code", "")) != BLOCKED_CODE or not creds:
        return issues
    token = creds.get("access_token") or ""
    found: list[dict] = []

    status = ""
    try:
        program = (payments or ebay_auth.fetch_payments_program)(token)
        status = str(program.get("status", "")).upper()
    except Exception as exc2:  # noqa: BLE001 - a diagnosis, never a blocker
        # Falls through to the privileges check rather than returning: this
        # used to `return issues` here, so an unreadable payments call meant
        # the seller learned nothing about registration or limits either.
        log.info("ebay: payments-program check after a 240 failed: %s", exc2)

    try:
        priv = (privileges or ebay_auth.fetch_privileges)(token)
    except Exception as exc3:  # noqa: BLE001 - fetch_privileges already
        log.info("ebay: privileges check after a 240 failed: %s", exc3)
        priv = None

    log.warning("ebay: publish blocked by error %s; payments=%s registered=%s "
                "limit=%s", BLOCKED_CODE, status or "unknown",
                (priv or {}).get("registration_complete", "unknown"),
                (priv or {}).get("selling_limit"))

    if status and status != "OPTED_IN":
        found.append({
            "target": "account", "level": "error",
            "title": "This eBay account hasn't finished payments setup",
            "fix": ("eBay reports the account as “" + status.replace("_", " ").lower()
                    + "” for managed payments, and it won't accept new listings "
                    "until that's done. Finish it on eBay under Seller Hub → "
                    "Payments (bank details and identity verification), then "
                    "publish again — the listing itself is fine."),
        })

    if priv is not None and not priv.get("registration_complete"):
        found.append({
            "target": "account", "level": "error",
            "title": "eBay hasn't finished setting this account up to sell",
            "fix": ("eBay reports this account's seller registration as "
                    "incomplete, and it won't accept listings until that's "
                    "done. Open eBay → My eBay → Selling; it usually wants "
                    "identity or bank verification. The listing itself is "
                    "fine."),
        })

    limit = (priv or {}).get("selling_limit") or {}
    if limit and _limit_is_exhausted(limit):
        found.append({
            "target": "account", "level": "error",
            "title": "This account is at its eBay selling limit",
            "fix": ("eBay caps what a new account may list — this one is at "
                    + _limit_words(limit) + ". New listings are refused until "
                    "the cap rises or current listings end. You can ask eBay "
                    "to raise it from My eBay → Selling → Monthly limits."),
        })

    if not found:
        # Nothing eBay's account APIs will state plainly. Ask eBay directly
        # what it would accept — the last question left, and the only one that
        # separates a held account from a listing eBay dislikes the words of.
        scope = probe_block_scope(listing, verify,
                                  uid=str(creds.get("_uid") or ""))
        if scope:
            found.append(_scope_issue(scope))

    # A named cause leads; eBay's unexplained placeholder falls in behind it.
    if found and issues and issues[0].get("placeholder"):
        return found + issues
    return issues + found


# --- "is it the account, or is it this listing?" ----------------------------
#
# Error 240 is the one rejection where that question has no answer in the
# response. It does have an answer at eBay: the Verify* calls validate an item
# exactly as the real call would and create nothing, so putting the SAME
# listing twice — once with its own words, once with plain ones — makes eBay
# state, by contradiction, which half it is refusing.

# Wording with nothing in it for a filter to catch: no brand, no claim, no
# contact details, no markup. If eBay refuses even this, the words are not it.
NEUTRAL_TITLE = "Item for sale"
NEUTRAL_DESCRIPTION = "Item for sale."

# How long an "this account is refusing everything" verdict is reused. Bulk
# publishing is the case that matters: seven drafts failing together would
# otherwise pay for the same two probes seven times over, and the answer is a
# property of the account, not of any one listing.
BLOCK_SCOPE_TTL = config.env_float("EBAY_BLOCK_PROBE_TTL", 300.0)

_block_scope: dict[str, tuple[float, str]] = {}


def _remembered_scope(uid: str, now: Optional[float] = None) -> str:
    if not uid:
        return ""
    now = time.time() if now is None else now
    with _verified_lock:
        at, scope = _block_scope.get(uid, (0.0, ""))
    return scope if scope and (now - at) < BLOCK_SCOPE_TTL else ""


# Verdicts that are a property of the ACCOUNT rather than of one draft, and
# so are safe to reuse across a bulk run. "policies" belongs here because the
# ids come from the account, not the listing: every draft in the run is
# carrying the same three, and re-probing per draft would buy nothing.
_ACCOUNT_WIDE_SCOPES = frozenset({"account", "policies"})


def _remember_scope(uid: str, scope: str, now: Optional[float] = None) -> None:
    """Cache an account-wide verdict only. "It's this listing's title" is true
    of one listing and would be a lie about the next one."""
    if not uid or scope not in _ACCOUNT_WIDE_SCOPES:
        return
    with _verified_lock:
        _block_scope[uid] = (time.time() if now is None else now, scope)


def probe_block_scope(listing, verify: Optional[Callable[[Any], None]], *,
                      uid: str = "") -> Optional[str]:
    """What a 240 is actually about: "account", "title", "wording", or None.

    Two dry runs at most, and only after the account APIs have come up empty:

      1. the listing with plain wording. Still refused -> the ACCOUNT is
         blocked, and no amount of editing will help.
      2. otherwise the listing with its own title and plain description.
         Refused -> the TITLE carries it; accepted -> the description (or an
         item specific) does.

    None whenever eBay answered with anything other than a 240 (a validation
    error the real publish never reached, an outage, a rate limit): an
    inconclusive probe must say nothing at all rather than guess.
    """
    if listing is None or verify is None:
        return None
    remembered = _remembered_scope(uid)
    if remembered:
        return remembered
    blocked = _refused(verify, plain_wording(listing))
    if blocked is None:
        return None
    if blocked:
        # "Not the words" is NOT the same as "the account". The probe has only
        # varied the wording so far, and a publish carries plenty that isn't
        # words: the business policy ids, the photo URLs, the item specifics,
        # the condition. Any one of those can draw a 240, and calling that an
        # account hold sends the seller to argue with eBay Customer Service
        # about a listing eBay would take the moment one field changed. So
        # keep asking, one field at a time, before blaming the account.
        field = _probe_payload(listing, verify)
        if field:
            _remember_scope(uid, field)
            log.warning("ebay: 240 probe — eBay refuses the listing's %s", field)
            return field
        if field is None:
            # The walk stopped early: eBay started answering with something
            # other than a 240, so the rest of the payload went unasked. Say
            # only what was actually established — the wording is not it —
            # rather than the full "we tried everything" verdict.
            log.warning("ebay: 240 probe — wording ruled out; the rest of the "
                        "payload could not be checked")
            return "account_words"
        _remember_scope(uid, "account")
        log.warning("ebay: 240 probe — the account refuses a plain listing "
                    "with no policies, photos, specifics or condition either")
        return "account"
    # eBay would take this listing with plain wording, so the words are the
    # cause. One more probe says whether the title alone carries it.
    titled = _refused(verify, _reworded(listing, listing.title,
                                        NEUTRAL_DESCRIPTION))
    scope = "title" if titled else "wording"
    log.warning("ebay: 240 probe — the listing's %s is what eBay refuses", scope)
    return scope


def plain_wording(listing):
    """The same listing with nothing in its words for a filter to catch."""
    return _reworded(listing, NEUTRAL_TITLE, NEUTRAL_DESCRIPTION)


# What a publish carries besides its words, in the order worth suspecting.
# Each entry drops exactly ONE thing from an already-plain listing: eBay
# accepting the result names that thing as the cause, because it is the only
# difference between the two requests.
#
# Policies lead because they are the part of the payload that does not come
# from the draft at all — they are ids stored against the account, and an id
# that belongs to a disconnected account, or to a policy since deleted on
# eBay, is attached to every listing alike. That is exactly the shape of
# "every publish fails identically while the listings themselves are fine".
_PAYLOAD_PROBES: tuple[tuple[str, dict], ...] = (
    ("policies", {"with_policies": False}),
    ("photos", {"with_photos": False}),
    ("specifics", {"listing": {"item_specifics": []}}),
    ("condition", {"listing": {"condition_description": ""}}),
)


def _probe_payload(listing, verify: Callable[..., None]) -> Optional[str]:
    """The first non-word part of the publish eBay stops refusing without.

    Three answers, and they must stay distinct — conflating the last two is
    how an app ends up telling a seller their account is held when all it
    established was that the title was innocent:

      "policies" / "photos" / ...  eBay accepted the listing without it
      ""                           it refused every variant: the account
      None                         the walk could not finish, so the rest of
                                   the payload is simply unknown

    An inconclusive answer (eBay replying with something other than a 240,
    or a verifier too old to vary the field) ends the walk rather than moving
    on: once eBay is saying something else, the comparison this rests on no
    longer holds.
    """
    plain = plain_wording(listing)
    unanswered = False
    for field, how in _PAYLOAD_PROBES:
        candidate = plain
        overrides = how.get("listing")
        if overrides:
            try:
                candidate = plain.model_copy(update=dict(overrides))
            except Exception as exc:  # noqa: BLE001 - a diagnosis, never a blocker
                log.info("ebay: 240 probe could not vary %s: %s", field, exc)
                unanswered = True
                continue
        kwargs = {k: v for k, v in how.items() if k != "listing"}
        refused = _refused(verify, candidate, **kwargs)
        if refused is None:
            # One question eBay wouldn't answer. That is a gap in THIS
            # dimension, not a reason to abandon the others: aborting the walk
            # on the first unanswerable probe is how a seller whose photos
            # were the problem got told the cause was unknown, because the
            # policies question ahead of it came back muddled.
            unanswered = True
            continue
        if not refused:
            return field
    return None if unanswered else ""


def _reworded(listing, title: str, description: str):
    """The same listing with different words — nothing else touched, so the
    probe differs from the real publish in exactly one dimension."""
    return listing.model_copy(update={"title": title, "description": description})


def _refused(verify: Callable[..., None], candidate, **kwargs) -> Optional[bool]:
    """Did eBay refuse this listing WITH A 240? None = it said something else.

    `kwargs` reach the verifier, which is what attaches the business policies
    and the photo URLs — the two parts of a publish that are not fields on the
    draft and so cannot be varied by editing it.
    """
    try:
        verify(candidate, **kwargs) if kwargs else verify(candidate)
    except TypeError as exc:
        # A verifier that predates the payload probes. Skip the question
        # rather than read its refusal to answer as eBay's verdict.
        log.info("ebay: 240 probe skipped (%s)", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a diagnosis, never a blocker
        code = str(getattr(exc, "code", "") or "")
        # ANY 240 in the response, not just the first error. Dropping a field
        # to ask about it routinely makes eBay add a SECOND complaint (take
        # the business policies away and it wants a shipping service), and
        # eBay is free to put that one first — at which point reading `code`
        # alone says "not a 240" about a response that plainly contains one.
        codes = getattr(exc, "codes", None)
        if code == BLOCKED_CODE or (codes and BLOCKED_CODE in codes):
            return True
        log.info("ebay: 240 probe inconclusive (code=%s): %s", code or "?", exc)
        return None
    return False


def _scope_issue(scope: str) -> dict:
    """The finding for one probe verdict. Each says what was asked and what
    eBay answered — a seller told "it's your account" while eBay's own words
    blame their title deserves to know why we contradict it."""
    if scope == "policies":
        return {
            "target": "policies", "level": "error",
            "title": "eBay is refusing this listing's business policies",
            "fix": ("eBay took this same listing the moment we sent it "
                    "WITHOUT the shipping / payment / return policies, and "
                    "refused it with them — so the policy ids this app is "
                    "attaching are the cause, not your account and not the "
                    "listing. That usually means a policy was deleted or "
                    "renamed on eBay, or the saved ids belong to an eBay "
                    "account that was connected before this one. Open "
                    "Settings → Listing settings, re-pick each of the three "
                    "policies, and publish again."),
        }
    if scope == "photos":
        return {
            "target": "photos", "level": "error",
            "title": "eBay is refusing this listing's photos",
            "fix": ("eBay took this same listing with the photos removed and "
                    "refused it with them. Either it could not fetch them or "
                    "it objects to one of them. Re-upload the photos (or drop "
                    "the last one you added) and publish again."),
        }
    if scope == "specifics":
        return {
            "target": "specifics", "level": "error",
            "title": "eBay is refusing this listing's item specifics",
            "fix": ("eBay took this same listing with the item specifics "
                    "removed and refused it with them, so one of those "
                    "name/value rows is the cause. Open Item specifics and "
                    "clear anything unusual — a value carrying a brand or "
                    "trademark name, a claim, a URL, or a row eBay doesn't "
                    "recognise for this category."),
        }
    if scope == "condition":
        return {
            "target": "condition", "level": "error",
            "title": "eBay is refusing this listing's condition note",
            "fix": ("eBay took this same listing with the condition "
                    "description removed and refused it with it. Reword the "
                    "condition note — claims about authenticity or grading, "
                    "and anything that reads as a guarantee, are the usual "
                    "cause."),
        }
    if scope == "account_words":
        return {
            "target": "account", "level": "error",
            "title": "eBay is refusing this listing, and not over its wording",
            "fix": ("eBay refused this same listing with a plain title and "
                    "description, so rewording it won't help. We couldn't get "
                    "a clear answer on the rest — eBay stopped returning this "
                    "error partway through the check — so the cause is either "
                    "a hold on the account or something the listing carries "
                    "besides its words (the business policies, the photos, "
                    "the item specifics). Press “Ask eBay why” to run the "
                    "check again; if it keeps landing here, eBay Customer "
                    "Service can say whether a restriction is on the account "
                    "— quote error 240."),
        }
    if scope == "account":
        return {
            "target": "account", "level": "error",
            "title": "eBay is refusing every listing from this account",
            "fix": ("We asked eBay to check this same listing with a plain "
                    "title and description, then again with no business "
                    "policies, no photos, no item specifics and no condition "
                    "note — and it refused every one of them. Nothing in the "
                    "listing is the cause, so editing won't help. This is an "
                    "account-level hold: open eBay → My eBay → Selling and "
                    "clear anything flagged there (registration, payments or "
                    "identity verification, a policy notice). If nothing is "
                    "flagged, only eBay Customer Service can lift it — quote "
                    "error 240 and ask which restriction is on the account."),
        }
    if scope == "title":
        return {
            "target": "title", "level": "error",
            "title": "eBay is refusing this listing's title",
            "fix": ("eBay accepted this same listing when we checked it with "
                    "a plain title, and refused it with this one — so the "
                    "title is what it objects to, not your account. Brand and "
                    "trademark names, and words that read as a claim "
                    "(“authentic”, “genuine”, “certified”, “rare”), are the "
                    "usual cause. Reword the title and publish again."),
        }
    return {
        "target": "description", "level": "error",
        "title": "eBay is refusing this listing's wording",
        "fix": ("eBay accepted this same listing when we checked it with a "
                "plain title and description, so something in the words is "
                "the cause rather than your account — and the title is not "
                "it. Look at the description and the item specifics for "
                "trademark or authenticity claims, contact details, links, or "
                "anything eBay could read as a promise about condition."),
    }


def _as_number(value) -> Optional[float]:
    """eBay sends the limit amount as a STRING ("500.0"). Anything unparseable
    is None, which every caller reads as "no usable number" rather than 0 —
    a limit we cannot read must never be reported as exhausted."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _limit_is_exhausted(limit: dict) -> bool:
    """True only when eBay gave a number and that number is zero.

    Deliberately strict. A missing or unreadable figure is not a cap of
    nothing, and telling a seller they are at a limit they are not at sends
    them to argue with eBay support over a message this app invented.
    """
    quantity = _as_number(limit.get("quantity"))
    amount = _as_number(limit.get("amount"))
    return quantity == 0 or amount == 0


def _limit_words(limit: dict) -> str:
    parts = []
    quantity = _as_number(limit.get("quantity"))
    amount = _as_number(limit.get("amount"))
    if quantity is not None:
        parts.append(f"{int(quantity)} item" + ("" if quantity == 1 else "s"))
    if amount is not None:
        parts.append(f"{amount:g} {limit.get('currency') or ''}".strip())
    return " / ".join(parts) if parts else "its cap"
