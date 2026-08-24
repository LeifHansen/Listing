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

from typing import Callable, Optional

from .. import ebay_auth, ebay_errors
from ..config import log

# The saved eBay settings that belong to one account. Each is an id eBay
# minted for a specific seller; on any other account it is not just wrong but
# rejected.
ACCOUNT_SCOPED = ("fulfillment_policy_id", "payment_policy_id",
                  "return_policy_id", "merchant_location_key")


def carry_over_settings(access: str, existing: dict, discovered: dict, *,
                        policy_ids: Optional[Callable[[str], dict]] = None,
                        location_keys: Optional[Callable[[str], Optional[set]]] = None,
                        ) -> dict:
    """What to store for the account that just connected.

    A saved choice survives only if it still EXISTS on this account; otherwise
    the auto-discovered default for that slot takes over, and an empty slot is
    filled from the same place.

    This used to be decided from the account NAME, which kept every saved id
    whenever the name was unreadable — the common case, because a connection
    made before the identity scope was granted 403s on the identity call. A
    seller switching accounts therefore carried the previous store's shipping,
    payment, return and location ids straight into the new one.

    When eBay can't say what exists (a kind that failed to fetch comes back
    absent, not empty), the saved value is left alone: an outage must never
    silently re-pick a seller's shipping.
    """
    valid_policies = (policy_ids or ebay_auth.policy_ids_on_account)(access)
    valid_locations = (location_keys or ebay_auth.location_keys_on_account)(access)
    out: dict = {}
    for field in ACCOUNT_SCOPED:
        saved = (existing.get(field) or "").strip()
        kind = field[: -len("_policy_id")] if field.endswith("_policy_id") else ""
        known = valid_policies.get(kind) if kind else valid_locations
        if saved and known is not None and saved not in known:
            out[field] = discovered.get(field, "")  # gone from this account
        elif not saved and discovered.get(field):
            out[field] = discovered[field]          # fill the gap
    return out


def settings_were_dropped(save_kwargs: dict, existing: dict) -> bool:
    """True when a saved account-scoped id didn't survive the reconnect.

    That is the tell-tale of a different eBay account even when the account
    name is unreadable — ids are per-account, so losing one means the store
    behind the connection changed.
    """
    return any(existing.get(f) and save_kwargs.get(f, existing.get(f)) != existing.get(f)
               for f in ACCOUNT_SCOPED)


# eBay's "this account can't list right now" code. It carries no field to fix
# and repeats on every listing, so the only useful follow-up is to ask what
# the hold actually is.
BLOCKED_CODE = "240"


def publish_block_issues(exc: Exception, creds: Optional[dict], *,
                         payments: Optional[Callable[[str], dict]] = None,
                         ) -> list[dict]:
    """Issues for a failed publish, and for eBay error 240 a look at WHY.

    Payments onboarding is both the most common cause of a 240 and the only
    one the API will state plainly, so it gets one extra call — on the failure
    path only, where a round-trip costs nothing and a blind seller costs
    everything. A failing check is silent: a diagnosis must never replace the
    rejection the seller actually needs to read.
    """
    issues = ebay_errors.from_trading_error(exc)
    if str(getattr(exc, "code", "")) != BLOCKED_CODE or not creds:
        return issues
    try:
        program = (payments or ebay_auth.fetch_payments_program)(creds["access_token"])
        status = str(program.get("status", "")).upper()
    except Exception as exc2:  # noqa: BLE001 - a diagnosis, never a blocker
        log.info("ebay: payments-program check after a 240 failed: %s", exc2)
        return issues
    log.warning("ebay: publish blocked by error %s; payments program = %s",
                BLOCKED_CODE, status or "unknown")
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
    return issues
