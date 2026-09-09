"""EasyPost shipping labels: rates, purchase, void.

The seller's own EasyPost account buys the postage — the API key is theirs,
pasted once in Settings and held encrypted in marketplace_accounts — so every
function here takes the key first and the app never holds a key of its own.

Three calls matter:
  - create_shipment: POST /shipments rates a parcel between the order's
    ship-to and the seller's ship-from. It creates a Shipment object on the
    seller's account but buys nothing and reserves nothing, so a lost answer
    is simply asked again.
  - buy_label: POST /shipments/{id}/buy spends the seller's money. A lost
    answer here is an UnknownOutcome, never "nothing happened" — the same
    rule as every other money-bearing call in this app (see
    test_every_external_write_is_classified). main.py reconciles one through
    retrieve_shipment before it says anything to the seller.
  - refund_label: POST /shipments/{id}/refund asks the carrier to void.
    Money comes back, never goes away, and EasyPost refuses a second request
    on the same shipment, so a repeat is harmless.

Everything raises EasyPostError with a user-facing message; no raw EasyPost
JSON escapes this module, and the API key never appears in one.
"""
from __future__ import annotations

from typing import Optional

import httpx

from ..config import log

_API = "https://api.easypost.com/v2"
_TIMEOUT = 30


class EasyPostError(ValueError):
    """An EasyPost call failed — carries a user-facing reason.

    `outcome_unknown` is False here and True on UnknownOutcome below, so a
    caller can ask any EasyPost failure whether money may already have moved.
    """

    outcome_unknown = False


class UnknownOutcome(EasyPostError):
    """The request went out and we never learned what EasyPost did with it.

    Raised only for the purchase: it charges the seller's wallet, and a
    seller told "nothing happened" buys again and pays for two labels.
    """

    outcome_unknown = True


# Transport failures that prove the request never reached EasyPost: no
# connection was established, so nothing there could have acted on it.
# Everything else -- including an exception type nobody here anticipated --
# is treated as unknown, because being wrong in the "nothing happened"
# direction is what costs the seller a second charge.
_NEVER_SENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)

_BUY_UNKNOWN = (
    "We lost contact with EasyPost while buying this label, so we can't tell "
    "whether it went through. Reopen this order — a bought label shows here — "
    "before buying again, or you may be charged for two.")


def _lost(exc: Exception, unknown: str) -> EasyPostError:
    """The error for a change-making call whose answer never arrived."""
    if isinstance(exc, _NEVER_SENT):
        return EasyPostError(f"Couldn't reach EasyPost: {exc}")
    return UnknownOutcome(unknown)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _error_message(resp: httpx.Response, doing: str) -> str:
    """EasyPost's own reason, in a sentence. Never the key, never raw JSON."""
    if resp.status_code == 401:
        return "EasyPost rejected the API key — reconnect it in Settings."
    detail = ""
    try:
        err = resp.json().get("error") or {}
        parts = [err.get("message") or ""]
        for sub in err.get("errors") or []:
            if isinstance(sub, dict):
                field = sub.get("field") or ""
                msg = sub.get("message") or ""
                parts.append(f"{field}: {msg}" if field else msg)
            elif isinstance(sub, str):
                parts.append(sub)
        detail = "; ".join(p for p in parts if p)
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    return (f"EasyPost couldn't {doing} ({resp.status_code})"
            + (f": {detail[:300]}" if detail else "."))


# --- keys -------------------------------------------------------------------

def key_is_test(api_key: str) -> bool:
    """EasyPost test keys start EZTK and buy free sample labels; production
    keys start EZAK and buy real postage."""
    return (api_key or "").strip().startswith("EZTK")


def key_looks_valid(api_key: str) -> bool:
    """A local shape check so a pasted password or a blank never reaches the
    network. Real keys are EZTK…/EZAK… and comfortably longer than 20 chars."""
    key = (api_key or "").strip()
    return key[:4] in ("EZTK", "EZAK") and len(key) >= 20


def verify_key(api_key: str) -> dict:
    """Prove the key works before it is stored: one read that EasyPost refuses
    with a 401 for a bad key. Returns {"test", "carriers"}."""
    try:
        resp = httpx.get(f"{_API}/carrier_accounts", headers=_headers(api_key),
                         timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise EasyPostError(f"Couldn't reach EasyPost: {exc}") from exc
    if resp.status_code != 200:
        raise EasyPostError(_error_message(resp, "check the API key"))
    carriers = []
    try:
        for acct in resp.json() or []:
            name = (acct.get("readable") or acct.get("type") or "").strip()
            if name and name not in carriers:
                carriers.append(name)
    except Exception:  # noqa: BLE001 - an unexpected body is not a bad key
        carriers = []
    return {"test": key_is_test(api_key), "carriers": carriers}


# --- shaping ----------------------------------------------------------------

def weight_ounces(package: dict) -> float:
    """The dialog collects pounds and ounces; EasyPost wants ounces."""
    try:
        lb = float(package.get("weight_lb") or 0)
    except (TypeError, ValueError):
        lb = 0.0
    try:
        oz = float(package.get("weight_oz") or 0)
    except (TypeError, ValueError):
        oz = 0.0
    return lb * 16.0 + oz


def parcel(package: dict) -> dict:
    """Weight always; dimensions only when all three are given, because a
    parcel with a length and no height is a rate EasyPost cannot compute."""
    out = {"weight": round(weight_ounces(package), 2)}
    dims = {}
    for key in ("length_in", "width_in", "height_in"):
        try:
            val = float(package.get(key) or 0)
        except (TypeError, ValueError):
            val = 0.0
        if val <= 0:
            return out
        dims[key.split("_")[0]] = val
    out.update(dims)
    return out


_ADDRESS_FIELDS = (
    ("name", "name"), ("company", "company"), ("street1", "address1"),
    ("street2", "address2"), ("city", "city"), ("state", "state"),
    ("zip", "postal_code"), ("country", "country"), ("phone", "phone"),
    ("email", "email"),
)


def address(a: dict) -> dict:
    """Our address shape (the orders module's ship_to, the prefs ship_from)
    to EasyPost's. Blanks are omitted rather than sent as empty strings."""
    out = {}
    for theirs, ours in _ADDRESS_FIELDS:
        val = str((a or {}).get(ours) or "").strip()
        if val:
            out[theirs] = val
    out.setdefault("country", "US")
    return out


def _rate_to_dict(r: dict) -> dict:
    return {
        "rate_id": r.get("id") or "",
        "carrier": r.get("carrier") or "",
        "service": r.get("service") or "",
        "cost": r.get("rate") or "",
        "currency": r.get("currency") or "USD",
        "delivery_days": r.get("delivery_days") or r.get("est_delivery_days"),
    }


def _messages(shipment: dict) -> list[dict]:
    out = []
    for m in shipment.get("messages") or []:
        if isinstance(m, dict) and m.get("message"):
            out.append({"carrier": m.get("carrier") or "",
                        "message": str(m.get("message"))[:300]})
    return out


def create_shipment(api_key: str, order: dict, package: dict,
                    ship_from: dict) -> dict:
    """Rate one order's package. Refuses, before any network, the things
    EasyPost would refuse less clearly.

    Returns {"shipment_id", "rates": [...cheapest first], "messages": [...]}.
    """
    ship_to = order.get("ship_to") or {}
    if not ship_to.get("address1"):
        raise EasyPostError("This order has no ship-to address yet — eBay may "
                            "still be finalizing payment.")
    if weight_ounces(package) <= 0:
        raise EasyPostError("Enter the package weight first.")
    missing = [k for k in ("address1", "city", "state", "postal_code")
               if not str(ship_from.get(k) or "").strip()]
    if missing:
        raise EasyPostError("Fill in your ship-from address first (street, "
                            "city, state and ZIP).")
    from_country = (ship_from.get("country") or "US").strip().upper()
    to_country = (ship_to.get("country") or "").strip().upper()
    if to_country and to_country != from_country:
        raise EasyPostError(
            "International labels need customs forms, which this app doesn't "
            "fill in yet — buy this one on easypost.com.")
    order_id = str(order.get("order_id") or "")
    body = {"shipment": {
        "to_address": address(ship_to),
        "from_address": address(ship_from),
        "parcel": parcel(package),
        "options": {
            "label_format": "PDF",
            "label_size": "4x6",
            "invoice_number": order_id,
            "print_custom_1": order_id,
        },
        "reference": order_id,
    }}
    try:
        resp = httpx.post(f"{_API}/shipments", headers=_headers(api_key),
                          json=body, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - a quote is free to ask again
        raise EasyPostError(f"Couldn't reach EasyPost: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise EasyPostError(_error_message(resp, "quote shipping rates"))
    data = resp.json()
    rates = [_rate_to_dict(r) for r in data.get("rates") or []]
    rates.sort(key=lambda r: float(r["cost"] or 1e9))
    return {"shipment_id": data.get("id") or "", "rates": rates,
            "messages": _messages(data)}


def _label_from(shipment: dict) -> dict:
    """The parts of a Shipment the dialog and the label record need. The same
    reader serves the purchase and the reconcile, so both agree."""
    label = shipment.get("postage_label") or {}
    rate = shipment.get("selected_rate") or {}
    tracker = shipment.get("tracker") or {}
    carrier = rate.get("carrier") or ""
    return {
        "shipment_id": shipment.get("id") or "",
        "purchased": bool(label.get("label_url")),
        "tracking_number": shipment.get("tracking_code") or "",
        "label_url": label.get("label_url") or "",
        "carrier": carrier,
        "service": rate.get("service") or "",
        "cost": rate.get("rate") or "",
        "currency": rate.get("currency") or "USD",
        "tracker_url": tracker.get("public_url") or "",
        "ebay_carrier": ebay_carrier_code(carrier),
        "refund_status": shipment.get("refund_status") or "",
    }


def buy_label(api_key: str, shipment_id: str, rate_id: str) -> dict:
    """Buy the chosen rate. Spends the seller's postage money."""
    if not shipment_id or not rate_id:
        raise EasyPostError("Pick a shipping rate first.")
    try:
        resp = httpx.post(f"{_API}/shipments/{shipment_id}/buy",
                          headers=_headers(api_key),
                          json={"rate": {"id": rate_id}}, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - sent, or sent-ness unproven
        raise _lost(exc, _BUY_UNKNOWN) from exc
    if resp.status_code >= 500:
        # Something that already had the request in hand failed to answer for
        # it. Not a refusal.
        raise UnknownOutcome(_BUY_UNKNOWN)
    if resp.status_code not in (200, 201):
        raise EasyPostError(_error_message(resp, "buy the label"))
    out = _label_from(resp.json())
    log.info("easypost: label bought for shipment %s (%s %s)", shipment_id,
             out["carrier"], out["service"])
    return out


def retrieve_shipment(api_key: str, shipment_id: str) -> dict:
    """One shipment as it stands — how a lost purchase answer is settled:
    `purchased` says whether a label exists on it."""
    if not shipment_id:
        raise EasyPostError("No shipment id given.")
    try:
        resp = httpx.get(f"{_API}/shipments/{shipment_id}",
                         headers=_headers(api_key), timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - a read
        raise EasyPostError(f"Couldn't reach EasyPost: {exc}") from exc
    if resp.status_code != 200:
        raise EasyPostError(_error_message(resp, "look up the shipment"))
    return _label_from(resp.json())


def refund_label(api_key: str, shipment_id: str) -> dict:
    """Ask the carrier to void the label. EasyPost answers "submitted" and
    settles it later; a second request on the same shipment is refused."""
    if not shipment_id:
        raise EasyPostError("No shipment id given.")
    try:
        resp = httpx.post(f"{_API}/shipments/{shipment_id}/refund",
                          headers=_headers(api_key), json={}, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - money comes back, never away
        raise EasyPostError(f"Couldn't reach EasyPost: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise EasyPostError(_error_message(resp, "void the label"))
    data = resp.json()
    return {"refund_status": data.get("refund_status") or "submitted"}


# --- carriers ---------------------------------------------------------------

# EasyPost names its carrier accounts; eBay's createShippingFulfillment wants
# the carrier as eBay spells it (USPS, UPS, FedEx, DHL — case matters, eBay
# links the tracking number to the carrier's site by it).
_EBAY_CARRIERS = {
    "usps": "USPS",
    "ups": "UPS", "upsdap": "UPS", "upsmailinnovations": "UPS",
    "upssurepost": "UPS",
    "fedex": "FedEx", "fedexdefault": "FedEx", "fedexsmartpost": "FedEx",
    "fedexcrossborder": "FedEx",
    "dhlexpress": "DHL", "dhlecommerce": "DHL", "dhlecs": "DHL",
    "dhlecommercesolutions": "DHL", "dhlglobalmail": "DHL",
}


def ebay_carrier_code(carrier: Optional[str]) -> str:
    key = (carrier or "").strip().replace(" ", "").replace("_", "").lower()
    return _EBAY_CARRIERS.get(key, (carrier or "").strip())
