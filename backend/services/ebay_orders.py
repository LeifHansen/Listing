"""eBay orders + shipping labels (Sell Fulfillment API + Sell Logistics API).

Two jobs live here, both about what happens AFTER an item sells:

1. Orders: getOrders (Fulfillment API) lists the seller's recent orders —
   most usefully the ones still awaiting shipment, with the buyer's ship-to
   address. That address is what both label workflows below need. Requires
   the sell.fulfillment scope (connections made before it was added must
   reconnect once — same story as sell.marketing).

2. Labels, two ways:
   - eBay's own labels (Logistics API): createShippingQuote returns live
     eBay-negotiated rates for a package, createFromShippingQuote buys one
     and returns a label download URL; eBay uploads the tracking number to
     the order automatically. NOTE: the Logistics API is a LIMITED RELEASE
     API — eBay must enable it for your application keyset, and it needs the
     sell.logistics scope. Every call degrades into a clear "not available
     for this app yet" message instead of a stack trace, so the UI can fall
     back to Pirate Ship.
   - Pirate Ship: there is no public Pirate Ship API, so the integration is
     their spreadsheet-import flow — pirate_ship_rows() shapes awaiting-
     shipment orders into the columns Pirate Ship's importer maps
     (recipient address + per-row weight/dimensions), main.py serves it as
     a CSV download, and the tracking number the user gets back is posted
     to the order via mark_shipped() (createShippingFulfillment).

Everything raises OrdersError with a user-facing message; no raw eBay JSON
escapes this module.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

import httpx

from .. import config
from ..config import log

_TIMEOUT = 30
_FULFILLMENT = "/sell/fulfillment/v1"
_LOGISTICS = "/sell/logistics/v1_beta"


class OrdersError(ValueError):
    """An orders/shipping call failed — carries a user-facing reason."""


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


def awaiting_shipment(token: str, limit: int = 50) -> list[dict]:
    """The seller's orders still waiting to ship, newest first."""
    data = _get(token, f"{_FULFILLMENT}/order", params={
        "filter": "orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}",
        "limit": str(max(1, min(limit, 200))),
    })
    return [_order_to_dict(o) for o in data.get("orders") or []]


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
    this is what flips it to 'shipped' on eBay and emails the buyer. Used for
    labels bought outside eBay (Pirate Ship); eBay-bought labels upload their
    tracking automatically."""
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
        "shippingCarrierCode": (carrier_code or "USPS").strip().upper(),
    }
    try:
        resp = httpx.post(
            f"{config.EBAY_API_BASE}{_FULFILLMENT}/order/{order_id}/shipping_fulfillment",
            headers=_headers(token), json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise OrdersError(f"Couldn't reach eBay: {exc}") from exc
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


# --- shipping labels (Logistics API — limited release) ----------------------

_LOGISTICS_UNAVAILABLE = (
    "eBay label purchasing isn't enabled for this app yet — eBay's Logistics "
    "API is a limited-release API that eBay has to approve per application. "
    "Use the Pirate Ship option instead, then paste the tracking number back "
    "here.")


def _logistics_error(resp: httpx.Response, doing: str) -> OrdersError:
    if resp.status_code in (401, 403, 404):
        # 404s here usually mean the API isn't provisioned for the keyset at
        # all; 401/403 the scope. Either way the seller's fix is the same.
        return OrdersError(_LOGISTICS_UNAVAILABLE)
    detail = ""
    try:
        errs = resp.json().get("errors") or []
        if errs:
            detail = errs[0].get("message") or ""
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    return OrdersError(
        f"eBay couldn't {doing} ({resp.status_code})"
        + (f": {detail[:200]}" if detail else "."))


def create_shipping_quote(token: str, order: dict, package: dict,
                          ship_from: dict) -> dict:
    """Live eBay-negotiated rates for one order's package.

    `package`: {weight_lb, weight_oz, length_in, width_in, height_in}
    `ship_from`: {name, address1, city, state, postal_code, country, phone}
    Returns {"shipping_quote_id", "rates": [{rate_id, carrier, service,
    cost, currency, delivery_est}]}.
    """
    ship_to = order.get("ship_to") or {}
    if not ship_to.get("address1"):
        raise OrdersError("This order has no ship-to address yet — eBay may "
                          "still be finalizing payment.")
    weight_oz = (float(package.get("weight_lb") or 0) * 16.0
                 + float(package.get("weight_oz") or 0))
    if weight_oz <= 0:
        raise OrdersError("Enter the package weight first.")
    dims = {}
    if all(float(package.get(k) or 0) > 0
           for k in ("length_in", "width_in", "height_in")):
        dims = {"dimensions": {
            "length": float(package["length_in"]),
            "width": float(package["width_in"]),
            "height": float(package["height_in"]),
            "unit": "INCH",
        }}
    body = {
        "orders": [{"orderId": order["order_id"], "channel": "EBAY"}],
        "packageSpecification": {
            "weight": {"value": round(weight_oz / 16.0, 2), "unit": "POUND"},
            **dims,
        },
        "shipFrom": {
            "fullName": ship_from.get("name") or "Seller",
            "contactAddress": {
                "addressLine1": ship_from.get("address1") or "",
                "city": ship_from.get("city") or "",
                "stateOrProvince": ship_from.get("state") or "",
                "postalCode": ship_from.get("postal_code") or "",
                "countryCode": ship_from.get("country") or "US",
            },
            **({"primaryPhone": {"phoneNumber": ship_from["phone"]}}
               if ship_from.get("phone") else {}),
        },
        "shipTo": {
            "fullName": ship_to.get("name") or "",
            "contactAddress": {
                "addressLine1": ship_to.get("address1") or "",
                **({"addressLine2": ship_to["address2"]}
                   if ship_to.get("address2") else {}),
                "city": ship_to.get("city") or "",
                "stateOrProvince": ship_to.get("state") or "",
                "postalCode": ship_to.get("postal_code") or "",
                "countryCode": ship_to.get("country") or "US",
            },
            **({"primaryPhone": {"phoneNumber": ship_to["phone"]}}
               if ship_to.get("phone") else {}),
        },
    }
    try:
        resp = httpx.post(f"{config.EBAY_API_BASE}{_LOGISTICS}/shipping_quote",
                          headers=_headers(token), json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise OrdersError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise _logistics_error(resp, "quote shipping rates")
    data = resp.json()
    rates = []
    for r in data.get("rates") or []:
        cost = r.get("totalShippingCost") or r.get("baseShippingCost") or {}
        rates.append({
            "rate_id": r.get("rateId") or "",
            "carrier": r.get("shippingCarrierName")
                       or r.get("shippingCarrierCode") or "",
            "service": r.get("shippingServiceName")
                       or r.get("shippingServiceCode") or "",
            "cost": cost.get("value") or "",
            "currency": cost.get("currency") or "USD",
            "delivery_est": r.get("maxEstimatedDeliveryDate") or "",
        })
    rates.sort(key=lambda r: float(r["cost"] or 1e9))
    return {"shipping_quote_id": data.get("shippingQuoteId") or "",
            "rates": rates}


def purchase_label(token: str, shipping_quote_id: str, rate_id: str) -> dict:
    """Buy the selected rate. eBay generates the label AND uploads tracking to
    the order itself — no mark_shipped needed on this path."""
    if not shipping_quote_id or not rate_id:
        raise OrdersError("Pick a shipping rate first.")
    body = {"shippingQuoteId": shipping_quote_id, "rateId": rate_id,
            "labelSize": "4\"x6\""}
    try:
        resp = httpx.post(
            f"{config.EBAY_API_BASE}{_LOGISTICS}/shipment/create_from_shipping_quote",
            headers=_headers(token), json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise OrdersError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise _logistics_error(resp, "purchase the label")
    data = resp.json()
    rate = data.get("rate") or {}
    cost = rate.get("totalShippingCost") or {}
    return {
        "shipment_id": data.get("shipmentId") or "",
        "tracking_number": data.get("shipmentTrackingNumber") or "",
        "label_url": data.get("labelDownloadUrl") or "",
        "carrier": rate.get("shippingCarrierName")
                   or rate.get("shippingCarrierCode") or "",
        "service": rate.get("shippingServiceName")
                   or rate.get("shippingServiceCode") or "",
        "cost": cost.get("value") or "",
        "currency": cost.get("currency") or "USD",
    }


def download_label(token: str, shipment_id: str) -> bytes:
    """The label file (PDF) for a purchased shipment."""
    if not shipment_id:
        raise OrdersError("No shipment id given.")
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}{_LOGISTICS}/shipment/{shipment_id}/download_label_file",
            headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT,
            follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        raise OrdersError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code != 200:
        raise _logistics_error(resp, "download the label")
    return resp.content


# --- Pirate Ship (spreadsheet import) ---------------------------------------

# Column headers Pirate Ship's importer auto-maps. One row per order; weights
# in ounces and dimensions in inches ride each row so mixed packages price
# correctly in one upload.
PIRATE_SHIP_COLUMNS = [
    "Order ID", "Recipient Name", "Company", "Email", "Phone",
    "Address 1", "Address 2", "City", "State", "Zipcode", "Country",
    "Weight (oz)", "Length (in)", "Width (in)", "Height (in)",
    "Item Title", "Rubber Stamp",
]


def pirate_ship_csv(orders: list[dict],
                    packages: Optional[dict[str, dict]] = None) -> str:
    """A Pirate Ship-importable CSV for these orders.

    `packages` optionally maps order_id -> {weight_lb, weight_oz, length_in,
    width_in, height_in} (taken from the matching listing's package fields);
    rows without one just leave those cells blank and the user fills the
    weight in on Pirate Ship's side.
    """
    packages = packages or {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PIRATE_SHIP_COLUMNS)
    for order in orders:
        ship_to = order.get("ship_to") or {}
        if not ship_to.get("address1"):
            continue  # no address yet — can't ship it from anywhere
        pkg = packages.get(order.get("order_id") or "") or {}
        weight_oz = (float(pkg.get("weight_lb") or 0) * 16.0
                     + float(pkg.get("weight_oz") or 0))
        titles = "; ".join(li["title"] for li in order.get("line_items") or []
                           if li.get("title"))[:120]
        dims = [pkg.get("length_in") or "", pkg.get("width_in") or "",
                pkg.get("height_in") or ""]
        writer.writerow([
            order.get("order_id") or "",
            ship_to.get("name") or "",
            ship_to.get("company") or "",
            ship_to.get("email") or "",
            ship_to.get("phone") or "",
            ship_to.get("address1") or "",
            ship_to.get("address2") or "",
            ship_to.get("city") or "",
            ship_to.get("state") or "",
            ship_to.get("postal_code") or "",
            ship_to.get("country") or "US",
            f"{weight_oz:g}" if weight_oz > 0 else "",
            *[f"{float(d):g}" if d else "" for d in dims],
            titles,
            (order.get("order_id") or "")[:20],  # printed on the label corner
        ])
    return buf.getvalue()
