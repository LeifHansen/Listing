"""eBay orders (Sell Fulfillment API): what happens AFTER an item sells.

1. Orders: getOrders lists the seller's recent orders — most usefully the
   ones still awaiting shipment, with the buyer's ship-to address, which is
   what the label workflow needs. Requires the sell.fulfillment scope
   (connections made before it was added must reconnect once — same story
   as sell.marketing).

2. mark_shipped: createShippingFulfillment attaches the tracking number a
   label carries to the order. That is what flips it to "shipped" on eBay and
   emails the buyer. Labels themselves are bought through the seller's own
   EasyPost account (services/easypost.py); this module only tells eBay.

Everything raises OrdersError with a user-facing message; no raw eBay JSON
escapes this module.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .. import config
from ..config import log

_TIMEOUT = 30
_FULFILLMENT = "/sell/fulfillment/v1"


class OrdersError(ValueError):
    """An orders/shipping call failed — carries a user-facing reason.

    `outcome_unknown` is False here and True on UnknownOutcome below, so a
    caller can ask any orders failure whether eBay might still have acted on
    it.
    """

    outcome_unknown = False


class UnknownOutcome(OrdersError):
    """The request went out and we never learned what eBay did with it.

    Raised only for the call that CHANGES something and cannot be repeated
    for free: filing a shipping fulfillment tells eBay the order shipped and
    emails the buyer the tracking. It used to answer a lost response with
    "Couldn't reach eBay", which reads as "nothing happened" -- so the seller
    filed a second fulfillment against one order.

    The reads are deliberately not in this class: asking again is free and
    there is no outcome to be in doubt about.
    """

    outcome_unknown = True


# Transport failures that prove the request never reached eBay: no connection
# was established, so nothing there could have acted on it. Everything else --
# including an exception type nobody here anticipated -- is treated as
# unknown, because being wrong in the "nothing happened" direction is what
# costs the seller a second charge.
_NEVER_SENT = (
    httpx.ConnectError,        # DNS failure, connection refused, TLS refused
    httpx.ConnectTimeout,      # gave up before the connection was made
    httpx.PoolTimeout,         # never got a connection out of the pool
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)


def _lost(exc: Exception, unknown: str) -> OrdersError:
    """The error for a change-making call whose answer never arrived.

    `unknown` is what to tell the seller when eBay may already have acted. A
    connection that was never made gets the ordinary "couldn't reach eBay"
    instead -- sending someone to check their postage every time their wifi
    drops before the request leaves would train them to ignore the warning
    that matters.
    """
    if isinstance(exc, _NEVER_SENT):
        return OrdersError(f"Couldn't reach eBay: {exc}")
    return UnknownOutcome(unknown)


# What the seller is told when a fulfillment may have landed. It never says
# "nothing happened", which is the one thing that cannot be justified here
# and the one thing that leads to doing it twice.
_FULFILLMENT_UNKNOWN = (
    "We lost contact with eBay while marking this order shipped, so we can't "
    "tell whether it went through. Check the order on eBay before marking it "
    "again.")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json",
            "Content-Type": "application/json"}


def _scope_missing(resp: httpx.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    body = resp.text.lower()
    return "insufficient" in body and "scope" in body


def _get(token: str, path: str, params: Optional[dict] = None) -> dict:
    try:
        resp = httpx.get(f"{config.EBAY_API_BASE}{path}", headers=_headers(token),
                         params=params, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise OrdersError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code != 200:
        if _scope_missing(resp):
            raise OrdersError(
                "eBay didn't allow reading your orders — reconnect eBay in "
                "Settings to grant the new permission, then try again.")
        raise OrdersError(f"eBay returned {resp.status_code} reading orders.")
    return resp.json()


# --- orders (Fulfillment API) -----------------------------------------------

def _order_to_dict(o: dict) -> dict:
    """Flatten one Fulfillment-API order to what the shipping UI needs."""
    line_items = []
    for li in o.get("lineItems") or []:
        line_items.append({
            "line_item_id": str(li.get("lineItemId") or ""),
            "legacy_item_id": str(li.get("legacyItemId") or ""),
            "title": li.get("title") or "",
            "quantity": int(li.get("quantity") or 1),
            "total": ((li.get("total") or {}).get("value") or ""),
        })
    ship_to = {}
    for fsi in o.get("fulfillmentStartInstructions") or []:
        step = fsi.get("shippingStep") or {}
        contact = step.get("shipTo") or {}
        addr = contact.get("contactAddress") or {}
        if addr:
            ship_to = {
                "name": contact.get("fullName") or "",
                "company": contact.get("companyName") or "",
                "email": contact.get("email") or "",
                "phone": ((contact.get("primaryPhone") or {}).get("phoneNumber")
                          or ""),
                "address1": addr.get("addressLine1") or "",
                "address2": addr.get("addressLine2") or "",
                "city": addr.get("city") or "",
                "state": addr.get("stateOrProvince") or "",
                "postal_code": addr.get("postalCode") or "",
                "country": addr.get("countryCode") or "",
            }
            break
    total = (o.get("pricingSummary") or {}).get("total") or {}
    return {
        "order_id": str(o.get("orderId") or ""),
        "created": o.get("creationDate") or "",
        "buyer_username": ((o.get("buyer") or {}).get("username") or ""),
        "fulfillment_status": o.get("orderFulfillmentStatus") or "",
        "total": total.get("value") or "",
        "currency": total.get("currency") or "",
        "line_items": line_items,
        "ship_to": ship_to,
    }


def awaiting_page(token: str, limit: int = 50) -> dict:
    """One page of orders still waiting to ship, and how many there are.

    {orders, total, partial}. `total` is eBay's own count for the filter, and
    `partial` says the page does not cover it — which the caller needs,
    because this is the list a seller reads to decide what still has to be
    packed. A page of 50 out of 80 used to be indistinguishable from 50 out of
    50, and eBay measures late dispatch: the thirty invisible orders cost the
    seller's standing, not just their afternoon.

    `total` is never invented. When eBay omits it, it falls back to what this
    page actually holds and `partial` stays False — the honest reading of "we
    were not told".
    """
    data = _get(token, f"{_FULFILLMENT}/order", params={
        "filter": "orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}",
        "limit": str(max(1, min(limit, 200))),
    })
    orders = [_order_to_dict(o) for o in data.get("orders") or []]
    try:
        total = int(data.get("total"))
    except (TypeError, ValueError):
        total = len(orders)
    return {"orders": orders, "total": total,
            "partial": total > len(orders)}


def awaiting_shipment(token: str, limit: int = 50) -> list[dict]:
    """The seller's orders still waiting to ship, newest first.

    The bare list, for callers that only need to find one order in it (see
    order_for_item). Anything SHOWING the pile should use awaiting_page, which
    also says whether the page is the whole of it.
    """
    return awaiting_page(token, limit)["orders"]


def orders_total(token: str) -> Optional[int]:
    """eBay's own count of EVERY order in the default 90-day window, whatever
    its status. One call at limit=1, no filter.

    This is what turns an empty awaiting-shipment list into a sentence a
    seller can act on: "N orders, all already shipped" is a different fact
    from "no orders at all on this account", and the sandbox environment is a
    third. None when eBay omitted `total` -- never invented.
    """
    data = _get(token, f"{_FULFILLMENT}/order", params={"limit": "1"})
    try:
        return int(data.get("total"))
    except (TypeError, ValueError):
        return None


def get_order(token: str, order_id: str) -> dict:
    """One order, flattened. Raises OrdersError if it can't be read."""
    if not order_id:
        raise OrdersError("No order id given.")
    return _order_to_dict(_get(token, f"{_FULFILLMENT}/order/{order_id}"))


def order_for_item(token: str, ebay_listing_id: str) -> Optional[dict]:
    """The most recent awaiting-shipment order containing this eBay item
    (None when it isn't in the awaiting pile — already shipped, or too old)."""
    if not ebay_listing_id:
        return None
    for order in awaiting_shipment(token):
        for li in order["line_items"]:
            if li["legacy_item_id"] == str(ebay_listing_id):
                return order
    return None


def mark_shipped(token: str, order_id: str, tracking_number: str,
                 carrier_code: str, line_items: Optional[list[dict]] = None) -> dict:
    """Attach a tracking number to an order (createShippingFulfillment) —
    this is what flips it to 'shipped' on eBay and emails the buyer.

    `carrier_code` is sent as given: eBay spells its carriers USPS, UPS,
    FedEx, DHL and links the tracking number to the carrier's site by that
    exact string (easypost.ebay_carrier_code produces it). Upper-casing it,
    as this once did, turned FedEx into FEDEX."""
    if not order_id or not tracking_number:
        raise OrdersError("An order id and tracking number are required.")
    items = line_items
    if not items:
        order = get_order(token, order_id)
        items = [{"lineItemId": li["line_item_id"], "quantity": li["quantity"]}
                 for li in order["line_items"]]
    body = {
        "lineItems": items,
        "trackingNumber": tracking_number.strip(),
        "shippingCarrierCode": (carrier_code or "USPS").strip(),
    }
    try:
        resp = httpx.post(
            f"{config.EBAY_API_BASE}{_FULFILLMENT}/order/{order_id}/shipping_fulfillment",
            headers=_headers(token), json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - sent, or sent-ness unproven
        raise _lost(exc, _FULFILLMENT_UNKNOWN) from exc
    if resp.status_code >= 500:
        # Something that already had the request in hand failed to answer for
        # it. Not a refusal.
        raise UnknownOutcome(_FULFILLMENT_UNKNOWN)
    if resp.status_code not in (200, 201):
        if _scope_missing(resp):
            raise OrdersError(
                "eBay didn't allow updating the order — reconnect eBay in "
                "Settings to grant the new permission, then try again.")
        raise OrdersError(
            f"eBay rejected the tracking number ({resp.status_code}): "
            f"{resp.text[:200]}")
    log.info("orders: tracking added to order %s", order_id)
    return {"ok": True, "order_id": order_id}
