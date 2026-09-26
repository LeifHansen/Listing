"""eBay Negotiation API — "Send offers to interested buyers".

A buyer who watched a listing and didn't buy is the warmest lead a seller has,
and eBay will carry a private discount to all of them at once: the public price
never moves, and only the people already interested see the offer. That is the
whole feature. Two calls do it:

  GET  /sell/negotiation/v1/find_eligible_items
  POST /sell/negotiation/v1/send_offer_to_interested_buyers

Both run on the `sell.inventory` scope this app has always requested, so no
seller has to reconnect for it — see the OAuth scopes in config.py.

Three facts about eBay's contract shape everything below, and each of them is
a thing the obvious implementation gets wrong:

  * ONE LISTING PER CALL. `offeredItems` is an array and it is documented as
    holding exactly one element ("the service does not currently support the
    creation of multiple offers with a single call"); eBay refuses more with
    error 150005. So a bulk send is a loop, and it is the caller's job to
    bound it — see main.BULK_OFFER_CAP.
  * ELIGIBILITY IS EBAY'S TO DECIDE, not ours. find_eligible_items returns
    only the listings that actually have interested buyers right now. Sending
    blind means spending a call per listing to be told 150020 ("no interested
    buyers"), so the sweep runs first and the send only visits what it named.
  * THE DURATION IS NOT OURS TO SET. eBay's offer window is site-specific (4
    days on EBAY_US and EBAY_GB, 2 on most others) and it rejects anything but
    that site's own value with 150027. So this sends no `offerDuration` at all
    and lets each marketplace apply its own — a field we cannot get right for
    every site is worse than a field we don't send.

There is also no third call: the Negotiation API has these two methods and
nothing that reads back the offers a seller has already sent. So the record of
a send is ours to keep — `Listing.offer_sent_at`, written by the caller once
eBay confirms — and that is the only thing standing between the dashboard and
suggesting the same offer again tomorrow.

Everything here is fail-soft in the same way promotions.py is: a listing that
eBay refuses comes back as a skip with eBay's own reason in seller language,
and it never sinks the rest of the run.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from .. import config
from .ebay import is_scope_error, rest_headers as _headers

log = logging.getLogger("thryft.offers")

_NEGOTIATION = "/sell/negotiation/v1"

# eBay's own floor: an offer must take at least 5% off the listed price
# (OfferedItem.discountPercentage, "Minimum: 5"), and a smaller one is refused
# with 150008 rather than rounded up.
MIN_DISCOUNT = 5.0
# Ours, not eBay's. The API sets no ceiling, but a bulk discount this far below
# the asking price is far more likely to be a slipped decimal point than an
# intention — and unlike a price drop, this one is accepted by a buyer at the
# click of a button with no second chance to look at it. The listing editor is
# still there for a genuine clearance.
MAX_DISCOUNT = 50.0

# eBay's limit on the covering note (error 150010), which also refuses HTML and
# its own list of blocked words. The length is ours to keep inside; the word
# list is not published, so a message eBay dislikes comes back as that
# listing's skip reason rather than being guessed at here.
MESSAGE_MAX = 2000

# How many listings one eligibility sweep reads. eBay pages this 200 at a time
# (`limit`, max 200), and the sweep exists to narrow a bulk run that is itself
# capped an order of magnitude below this — so one page is the whole budget,
# and a store with more eligible listings than that has more than any single
# run would reach anyway.
ELIGIBLE_LIMIT = 200

# How long the dashboard reuses one seller's eligibility sweep. The dashboard
# asks on every load that has a watched listing on it (see eligible_cached),
# and a store's interested buyers do not change by the minute — while eBay's
# call allowance is shared by the whole app. Same shape as the metrics cache.
_ELIGIBLE_TTL = 300
_ELIGIBLE_CACHE: dict[str, tuple[float, frozenset]] = {}
_ELIGIBLE_CACHE_MAX = 200


class ScopeError(Exception):
    """The token can't use the Negotiation API — the seller must reconnect.

    Same shape and same purpose as promotions._ScopeError: it is the one
    failure a seller can actually do something about, so it is told apart from
    every other refusal instead of arriving as "eBay said no".
    """


class OfferRefused(Exception):
    """eBay refused this one listing, with a reason worth reading.

    Carries `.code` (eBay's numeric errorId) alongside the sentence, because
    the caller sorts the refusals into "skip, that listing was never going to
    take an offer" and "fail, something went wrong" by code and not by prose.
    """

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


# eBay's documented refusals, in seller language. The ones NOT here are the
# ones this app would be at fault for (150001 missing headers, 150006 empty
# request, 150007 both price and percentage) — those arrive as the raw message
# so a bug in this file is legible in the logs rather than dressed up as a
# listing problem.
_REASONS = {
    150011: "That listing has ended.",
    150012: "eBay doesn't have that listing under this account.",
    150014: "Fewer items are left than the offer is for.",
    150015: "eBay's minimum offer price for that listing isn't met.",
    150016: "That discount doesn't come out below the listing price.",
    150017: "That listing doesn't take offers.",
    150018: "A buyer already has an offer in on that listing — answer it first.",
    150019: "An offer is already out to that listing's buyers.",
    150020: "Nobody is watching that listing yet.",
    150022: "eBay's limit on open offers for that listing is reached.",
    150023: "eBay's daily limit on offers to buyers is reached — try again tomorrow.",
    150026: "eBay doesn't allow seller offers on Motors listings.",
}

# Refusals that mean "this listing was never going to take this offer", as
# opposed to "the send went wrong". A skip is not a failure: bulk runs are
# built over a suggestion group that was computed minutes ago, and a listing
# that has since sold, ended, or already had an offer sent to it is an
# expected member of that group rather than an error to alarm the seller with.
# 150023 is deliberately NOT here — hitting the account's daily offer ceiling
# stops the run being what the seller asked for, and saying so once is the
# point.
_SKIPPABLE = {150011, 150012, 150014, 150015, 150016, 150017, 150018,
              150019, 150020, 150022, 150026}


def clean_message(text: Optional[str]) -> str:
    """The seller's covering note, trimmed to what eBay will take.

    eBay refuses HTML outright (150010), so angle brackets go rather than
    being escaped: the note is one sentence to a buyer, and there is nothing
    in it that a `<` was meant to say.
    """
    note = " ".join(str(text or "").split())
    note = note.replace("<", "").replace(">", "")
    return note[:MESSAGE_MAX]


def validate_discount(percent) -> float:
    """The requested discount, or ValueError with something a seller can act on.

    Rounded to whole percent because that is what eBay's field takes; the
    bounds are checked on the number the seller typed, so a 4.4% ask is
    refused rather than quietly rounded up to the 5% minimum and sent.
    """
    try:
        value = float(percent)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a discount percentage to offer.") from exc
    if value < MIN_DISCOUNT:
        raise ValueError(
            f"eBay's smallest offer is {MIN_DISCOUNT:g}% off — offer at least that.")
    if value > MAX_DISCOUNT:
        raise ValueError(
            f"That's {value:g}% off — offers to buyers are capped at "
            f"{MAX_DISCOUNT:g}%. Open a listing to discount it further.")
    return float(round(value))


def _first_error(resp: httpx.Response) -> tuple[int, str]:
    """(errorId, message) from eBay's error envelope — (0, "") when unreadable.

    eBay answers a refusal with {"errors":[{"errorId":150018,"message":"…"}]}.
    A body that isn't that shape (a gateway's HTML, a truncated reply) still
    has to say something, so the caller falls back to the raw text.
    """
    try:
        errs = (resp.json() or {}).get("errors") or []
    except (ValueError, json.JSONDecodeError):
        return 0, ""
    if not errs or not isinstance(errs[0], dict):
        return 0, ""
    first = errs[0]
    try:
        code = int(first.get("errorId") or 0)
    except (TypeError, ValueError):
        code = 0
    return code, str(first.get("message") or "")


def _refusal(resp: httpx.Response) -> OfferRefused:
    code, message = _first_error(resp)
    return OfferRefused(
        _REASONS.get(code) or message or f"eBay refused the offer ({resp.status_code}).",
        code)


def skippable(exc: OfferRefused) -> bool:
    """Whether this refusal is a listing that can't take the offer (skip) as
    opposed to a send that went wrong (fail)."""
    return exc.code in _SKIPPABLE


def eligible_items(creds: Optional[dict], client: Optional[httpx.Client] = None
                   ) -> set[str]:
    """eBay listing ids that have interested buyers right now.

    This is eBay's answer, not ours: watchers are only one of the signals it
    counts, and it is the only party that knows the rest. Raises ScopeError
    when the token can't make the call, because "reconnect eBay" is a
    different thing to tell a seller than "nobody is watching anything".

    A 204 means eBay has nothing eligible — an empty set, not a failure.
    """
    token = (creds or {}).get("access_token")
    if not token:
        return set()
    owned = client is None
    client = client or httpx.Client(timeout=30)
    try:
        r = client.get(
            f"{config.EBAY_API_BASE}{_NEGOTIATION}/find_eligible_items",
            headers={**_headers(token),
                     "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID},
            params={"limit": str(ELIGIBLE_LIMIT), "offset": "0"})
        if r.status_code == 204:
            return set()
        if r.status_code != 200:
            if is_scope_error(r):
                raise ScopeError()
            raise RuntimeError(
                f"eligible items failed ({r.status_code}): {r.text[:200]}")
        items = (r.json() or {}).get("eligibleItems") or []
        return {str(i.get("listingId")) for i in items
                if isinstance(i, dict) and i.get("listingId")}
    finally:
        if owned:
            client.close()


def eligible_cached(creds: Optional[dict]) -> Optional[frozenset]:
    """eligible_items for deciding what to SUGGEST — None when it can't be read.

    The "Send offers" suggestion was built off the watch count alone, and a
    watcher is not what eBay counts: its sweep leaves out listings whose
    watchers it will not carry an offer to. The send has always trusted the
    sweep and skipped those, so the group kept suggesting listings the button
    could only ever skip: pressed, it reported them skipped, and the group
    came back unchanged — a button that does not work. Asked here too, the
    suggestion and the button give the same answer.

    None, never an empty set, when eBay could not be asked (no token, no
    scope, a failed call), because the caller then falls back to the watch
    count. Reading an unreadable sweep as "nothing is eligible" would hide
    every suggestion on a store that has interested buyers.
    """
    token = (creds or {}).get("access_token")
    if not token:
        return None
    key = token[-12:]
    now = time.time()
    hit = _ELIGIBLE_CACHE.get(key)
    if hit and now - hit[0] < _ELIGIBLE_TTL:
        return hit[1]
    try:
        found = frozenset(eligible_items(creds))
    except Exception as exc:  # noqa: BLE001 - a suggestion never breaks a page
        log.info("eligible-items sweep unavailable for suggestions: %s", exc)
        return None
    if len(_ELIGIBLE_CACHE) >= _ELIGIBLE_CACHE_MAX:
        for stale in [k for k, (at, _) in _ELIGIBLE_CACHE.items()
                      if now - at >= _ELIGIBLE_TTL]:
            _ELIGIBLE_CACHE.pop(stale, None)
        while len(_ELIGIBLE_CACHE) >= _ELIGIBLE_CACHE_MAX:
            _ELIGIBLE_CACHE.pop(min(_ELIGIBLE_CACHE,
                                    key=lambda k: _ELIGIBLE_CACHE[k][0]), None)
    _ELIGIBLE_CACHE[key] = (now, found)
    return found


def send_offer(creds: Optional[dict], listing_id: str, percent: float,
               message: str = "", quantity: int = 1,
               client: Optional[httpx.Client] = None) -> dict:
    """Offer ONE listing to its interested buyers at `percent` off.

    Returns {"offer_id": str, "status": str} on success. Raises ScopeError
    when the token can't make the call, OfferRefused with eBay's reason when
    eBay says no, and httpx's own errors when the call doesn't complete —
    which the bulk runner turns into that listing's failure and nothing more.
    """
    token = (creds or {}).get("access_token")
    if not token:
        raise ScopeError()
    item: dict = {"listingId": str(listing_id),
                  # eBay's offer is all-or-nothing on the quantity named, so
                  # this is 1 unless a caller has a reason: an offer for the
                  # seller's whole stack of five is one a buyer who wants one
                  # of them cannot take.
                  "quantity": max(int(quantity or 1), 1),
                  # Percentage, never `price`. eBay takes one or the other and
                  # refuses both together (150007); the percentage is also the
                  # only one of the two that a bulk run can name once and mean
                  # correctly across listings at different prices.
                  "discountPercentage": f"{float(percent):g}"}
    body: dict = {"offeredItems": [item],
                  # Documented as "currently, you must set this field to
                  # false" — counter-offers are not in this release of eBay's
                  # API. Sent explicitly so that when eBay does allow them,
                  # turning it on is a decision made here rather than a
                  # default quietly changing under the seller.
                  "allowCounterOffer": False}
    note = clean_message(message)
    if note:
        body["message"] = note
    owned = client is None
    client = client or httpx.Client(timeout=30)
    try:
        r = client.post(
            f"{config.EBAY_API_BASE}{_NEGOTIATION}/send_offer_to_interested_buyers",
            headers={**_headers(token),
                     "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID},
            json=body)
        if r.status_code in (200, 201):
            offers = (r.json() or {}).get("offers") or []
            first = offers[0] if offers and isinstance(offers[0], dict) else {}
            return {"offer_id": str(first.get("offerId") or ""),
                    "status": str(first.get("offerStatus") or "")}
        if is_scope_error(r):
            raise ScopeError()
        raise _refusal(r)
    finally:
        if owned:
            client.close()
