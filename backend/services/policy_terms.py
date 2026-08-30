"""Plain-English terms for the eBay business policies this app would create.

A business policy is not a setting. It is a public promise attached to every
listing that references it: how fast the seller dispatches, whether returns
are accepted and for how long, who pays the return postage, whether the buyer
must pay at checkout. eBay shows those terms to buyers, measures the seller
against them, and penalises the ones they miss.

"Create my policies" chose all of that and made it real on the seller's own
eBay account. This module is what lets the seller read the terms first.

Every description is derived from the REQUEST BODY the create would send
(`ebay_auth.*_body`), not written a second time beside it. That is the whole
point of the split: a hand-copied summary agrees with the code on the day it
is written and drifts silently afterwards, and a preview that has drifted is
worse than none — it is a consent screen for terms nobody is going to send.
"""
from __future__ import annotations

from typing import Optional

from backend import ebay_auth

# The service used when the caller names none. Ground Advantage is USPS's
# cheapest tracked domestic option, which is the one a small seller wants.
FALLBACK_SERVICE = "USPSGroundAdvantage"

_PAYER_WORDS = {
    "BUYER": "The buyer pays return postage",
    "SELLER": "You pay return postage",
}


def _days(period: dict, word: str = "") -> str:
    value = period.get("value", 0)
    unit = word or str(period.get("unit", "DAY")).lower()
    plural = "" if value == 1 else "s"
    return f"{value} {unit}{plural}"


def _fulfillment(body: dict, svc: dict) -> list[dict]:
    option = (body.get("shippingOptions") or [{}])[0]
    handling = body.get("handlingTime", {})
    calculated = option.get("costType") == "CALCULATED"
    domestic = option.get("optionType") == "DOMESTIC"
    return [
        {"label": "Carrier and service",
         "value": svc.get("label", svc.get("code", "")),
         "detail": svc.get("note", "")},
        {"label": "Dispatch time",
         "value": _days(handling, "business day") + " after payment",
         "detail": "eBay measures this. Dispatching later than you promised "
                   "counts against your seller standing, so pick a window you "
                   "can keep on a bad week."},
        {"label": "Postage cost",
         "value": ("Calculated by eBay from the package and the buyer's "
                   "address" if calculated else "Flat rate"),
         "detail": "You are not offering free postage. The buyer is charged, "
                   "and the amount depends on the weight and dimensions you "
                   "enter on each listing."},
        {"label": "Where you post to",
         "value": "The United States only" if domestic else "International",
         "detail": "You can add international postage later in Seller Hub."},
    ]


def _payment(body: dict) -> list[dict]:
    immediate = bool(body.get("immediatePay"))
    return [
        {"label": "How you get paid",
         "value": "eBay managed payments",
         "detail": "eBay collects from the buyer and pays out to the bank "
                   "account on your eBay profile. This app never touches it."},
        {"label": "Immediate payment",
         "value": "Required at checkout" if immediate else "Not required",
         "detail": ("The item stays on sale until the buyer actually pays, so "
                    "an unpaid bid cannot hold it. It also means a buyer who "
                    "cannot pay right away will not commit."
                    if immediate else
                    "A buyer can commit to the item before paying for it.")},
    ]


def _returns(body: dict) -> list[dict]:
    accepted = bool(body.get("returnsAccepted"))
    payer = str(body.get("returnShippingCostPayer", ""))
    if not accepted:
        return [{"label": "Returns", "value": "Not accepted", "detail": ""}]
    return [
        {"label": "Returns", "value": "Accepted",
         "detail": "Refusing returns outright limits how your listings are "
                   "shown and ranked on eBay."},
        {"label": "Return window",
         "value": f"{_days(body.get('returnPeriod', {}))} from delivery",
         "detail": "The buyer can start a return for any reason inside this "
                   "window."},
        {"label": "Return postage",
         "value": _PAYER_WORDS.get(payer, payer),
         "detail": ("You still pay it when the item arrives damaged or is not "
                    "as described — eBay overrides the policy in those cases."
                    if payer == "BUYER" else
                    "This costs you money on every return, including changes "
                    "of mind.") if payer in _PAYER_WORDS else ""},
        {"label": "Refund",
         "value": "Money back",
         "detail": "eBay no longer allows exchange-only or credit-only "
                   "returns, so this is the only option."},
    ]


def describe(*, service_code: str = "",
             return_days: Optional[int] = None,
             return_payer: str = "",
             immediate_pay: bool = True) -> dict:
    """What creating the three policies would commit the seller to.

    Pure: it reads constants and builds request bodies. It makes no network
    call, so it cannot fail because eBay is unreachable and it costs no part
    of the account's daily quota — which matters, because the whole reason
    this exists is to be shown BEFORE the seller decides to spend either.
    """
    svc = (ebay_auth.service_by_code(service_code)
           or ebay_auth.service_by_code(FALLBACK_SERVICE))
    days = int(return_days if return_days is not None
               else ebay_auth.DEFAULT_RETURN_DAYS)
    payer = (return_payer or ebay_auth.DEFAULT_RETURN_PAYER).upper()

    fulfillment = ebay_auth.fulfillment_body(svc)
    payment = ebay_auth.payment_body(immediate_pay)
    returns = ebay_auth.return_body(days, payer)
    return {
        "kinds": {
            "fulfillment": {"title": "Postage",
                            "name": fulfillment["name"],
                            "terms": _fulfillment(fulfillment, svc),
                            "body": fulfillment},
            "payment": {"title": "Payment",
                        "name": payment["name"],
                        "terms": _payment(payment),
                        "body": payment},
            "return": {"title": "Returns",
                       "name": returns["name"],
                       "terms": _returns(returns),
                       "body": returns},
        },
        "options": {"service_code": svc["code"], "return_days": days,
                    "return_payer": payer, "immediate_pay": immediate_pay},
    }
