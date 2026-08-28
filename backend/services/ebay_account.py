"""Which eBay account is connected, and what follows from that.

Two questions live here, both of which used to be answered inside request
handlers where nothing could test them:

  * On a reconnect, which of the seller's saved settings still apply? Business
    policy ids and location keys are minted by ONE eBay seller account and
    rejected outright on another, so carrying them across a switch publishes
    listings that fail for a reason no field on screen explains.

  * When eBay refuses to list ANYTHING (error 240), is it something the seller
    can see and clear? The code says only "the listing or seller may be in
    violation of eBay policy", which is four causes in a trench coat.

Deliberately importable without backend.main — same rule as sync_guard: the CI
`checks` job installs no image/AI stack, so anything that reaches for main
(and through it Pillow, anthropic, rembg) is a test that cannot run there.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, NamedTuple, Optional

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
    """
    changes: dict
    conclusive: bool
    absent: list


def reconcile_account_settings(
        access: str, existing: dict, discovered: dict, *,
        policy_ids: Optional[Callable[[str], dict]] = None,
        location_keys: Optional[Callable[[str], Optional[set]]] = None,
        fill_blanks: bool = True) -> tuple[dict, bool]:
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
    return Reconciled(out, conclusive, absent)


def carry_over_settings(access: str, existing: dict, discovered: dict,
                        **kwargs) -> dict:
    """reconcile_account_settings' changes alone, for callers that don't need
    to know whether eBay answered."""
    return reconcile_account_settings(access, existing, discovered, **kwargs)[0]


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
        else:
            _verified.pop(uid, None)
            _absent.pop(uid, None)


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


# eBay's "this account can't list right now" code. It carries no field to fix
# and repeats on every listing, so the only useful follow-up is to ask what
# the hold actually is.
BLOCKED_CODE = "240"


def publish_block_issues(exc: Exception, creds: Optional[dict], *,
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

    Both run on the failure path only, where a round-trip costs nothing and a
    blind seller costs everything. Every check here is silent on failure and
    ADDITIVE: a diagnosis must never replace the rejection the seller actually
    needs to read, and one lookup failing must not skip the others.
    """
    issues = ebay_errors.from_trading_error(exc)
    if str(getattr(exc, "code", "")) != BLOCKED_CODE or not creds:
        return issues
    token = creds.get("access_token") or ""

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
        issues.append({
            "target": "account", "level": "error",
            "title": "This eBay account hasn't finished payments setup",
            "fix": ("eBay reports the account as “" + status.replace("_", " ").lower()
                    + "” for managed payments, and it won't accept new listings "
                    "until that's done. Finish it on eBay under Seller Hub → "
                    "Payments (bank details and identity verification), then "
                    "publish again — the listing itself is fine."),
        })

    if priv is not None and not priv.get("registration_complete"):
        issues.append({
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
        issues.append({
            "target": "account", "level": "error",
            "title": "This account is at its eBay selling limit",
            "fix": ("eBay caps what a new account may list — this one is at "
                    + _limit_words(limit) + ". New listings are refused until "
                    "the cap rises or current listings end. You can ask eBay "
                    "to raise it from My eBay → Selling → Monthly limits."),
        })
    return issues


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
