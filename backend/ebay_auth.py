"""'Sign in with eBay' OAuth (Authorization Code grant) + auto-discovery of
business policies and inventory location via the Account/Inventory APIs.

This is what removes the manual publish secrets: the seller clicks "Connect
eBay", grants access, and we store a refresh token + their policy IDs.
"""
from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from . import config
from .config import log


def authorize_url(state: str) -> str:
    params = {
        "client_id": config.EBAY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.EBAY_RUNAME,
        "scope": " ".join(config.EBAY_OAUTH_SCOPES),
        "state": state,
    }
    return f"{config.EBAY_AUTH_BASE}/oauth2/authorize?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        data=data,
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code(code: str) -> dict:
    """Trade an authorization code for access + refresh tokens."""
    body = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.EBAY_RUNAME,
        }
    )
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 7200)),
    }


def refresh_access_token(refresh_token: str) -> dict:
    # Deliberately omit `scope`: a refresh grant may only request scopes that
    # were in the original consent, so sending the full (possibly newly-widened)
    # list would break connections made before a scope was added. Omitting it
    # returns a token with exactly the scopes the user originally granted.
    body = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    return {
        "access_token": body["access_token"],
        "expires_at": time.time() + float(body.get("expires_in", 7200)),
    }


def fetch_user_identity(access_token: str) -> dict:
    """The connected seller's identity (username, email). Requires the
    commerce.identity.readonly scope — connections made before that scope was
    added won't have it and this will 403 until the user reconnects."""
    # The Identity API lives on the apiz.* host, not api.*.
    base = config.EBAY_API_BASE.replace("://api.", "://apiz.")
    resp = httpx.get(
        f"{base}/commerce/identity/v1/user/",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def identity_display(identity: dict) -> dict:
    """Flatten the Identity API response to {username, email} for the UI."""
    acct = identity.get("individualAccount") or identity.get("businessAccount") or {}
    return {
        "username": identity.get("username") or "",
        "email": acct.get("email") or "",
    }


def _account_get(path: str, access_token: str) -> dict:
    resp = httpx.get(
        f"{config.EBAY_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={"marketplace_id": config.EBAY_MARKETPLACE_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_payments_program(access_token: str) -> dict:
    """Payments-program opt-in status for the connected seller.

    Bank accounts are linked on eBay's side (Seller Hub -> Payments); the
    closest signal the API exposes is the payments-program status, which is
    OPTED_IN once payout setup (including a bank account) is complete.
    """
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/sell/account/v1/payments_program/"
        f"{config.EBAY_MARKETPLACE_ID}/EBAY_PAYMENTS",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def ensure_inventory_location(access_token: str, postal_code: str,
                              country: str = "US") -> str:
    """Ensure our own ship-from location holds exactly the seller's ZIP+country
    and return its key. publishOffer needs the location's address to carry a
    country ('Item.Country empty' otherwise). We always use our own key rather
    than reusing an arbitrary existing location, so the ZIP the seller entered
    is the one eBay actually ships from (and can't silently diverge)."""
    base = config.EBAY_API_BASE
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }
    key = "thryft-loc-1"
    addr = {"country": country, "postalCode": postal_code}
    body = {
        "location": {"address": addr},
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
        # Display name only — the location is found by its key ("thryft-loc-1"
        # above), never by name, so this is safe to keep in step with the brand.
        "name": "Thryft Shop ship-from location",
    }
    r = httpx.post(f"{base}/sell/inventory/v1/location/{key}",
                   headers=headers, json=body, timeout=30)
    # "Already exists" arrives as 409 per the docs but as 400 (error 25801) in
    # practice — treating only 409 as exists made every re-save of Settings
    # fail on an account that already had the location.
    already_exists = r.status_code == 409 or (
        r.status_code == 400 and ("already exists" in r.text.lower()
                                  or "25801" in r.text))
    if already_exists:
        # Force it to the current ZIP+country (it may hold a stale/missing
        # address), and surface it if the repair itself fails.
        u = httpx.post(
            f"{base}/sell/inventory/v1/location/{key}/update_location_details",
            headers=headers, json={"location": {"address": addr}}, timeout=30)
        if u.status_code not in (200, 204):
            log.warning("update_location_details(%s) -> %s %s",
                        key, u.status_code, u.text[:200])
    elif r.status_code not in (200, 204):
        r.raise_for_status()
    return key


_POLICY_SPECS = {
    "fulfillment": ("/sell/account/v1/fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
    "payment": ("/sell/account/v1/payment_policy", "paymentPolicies", "paymentPolicyId"),
    "return": ("/sell/account/v1/return_policy", "returnPolicies", "returnPolicyId"),
}


def list_business_policies(access_token: str) -> dict:
    """All of the seller's eBay business policies, grouped by type, as
    [{id, name, summary}] so the user can pick a default for each."""
    out = {"fulfillment": [], "payment": [], "return": []}
    for key, (path, list_field, id_field) in _POLICY_SPECS.items():
        try:
            data = _account_get(path, access_token)
            for p in data.get(list_field, []):
                entry = {
                    "id": p.get(id_field, ""),
                    "name": p.get("name", "") or p.get(id_field, ""),
                    "summary": _policy_summary(key, p),
                }
                # The shipping-service picker needs to know what each
                # fulfillment policy actually ships with.
                if key == "fulfillment":
                    entry["services"] = [s["code"] for s in _policy_services(p)]
                out[key].append(entry)
        except Exception:  # noqa: BLE001 - best effort per type
            pass
    return out


def _policy_summary(kind: str, p: dict) -> str:
    """A short human hint about what a policy does, for the picker."""
    try:
        if kind == "return":
            if not p.get("returnsAccepted", False):
                return "No returns"
            days = (p.get("returnPeriod") or {}).get("value")
            return f"{days}-day returns" if days else "Returns accepted"
        if kind == "fulfillment":
            codes = [s_["code"] for s_ in _policy_services(p)]
            if codes:
                pretty = ", ".join(_friendly_service(c) for c in codes[:3])
                return pretty + ("…" if len(codes) > 3 else "")
            return "Shipping configured"
        if kind == "payment":
            return "Managed payments"
    except Exception:  # noqa: BLE001
        pass
    return ""


def fetch_policies_and_location(access_token: str) -> dict:
    """Best-effort auto-discovery of the seller's default policies + location.

    Returns whatever it can find; missing pieces stay empty (the seller may
    need to create business policies in Seller Hub first).
    """
    out = {
        "fulfillment_policy_id": "",
        "payment_policy_id": "",
        "return_policy_id": "",
        "merchant_location_key": "",
    }
    for kind, (path, list_field, id_field) in _POLICY_SPECS.items():
        try:
            data = _account_get(path, access_token)
            items = data.get(list_field, [])
            if items:
                # Fulfillment: prefer a USPS Ground Advantage policy (cheapest
                # broadly-applicable service) over whatever happens to be first.
                pick = items[0]
                if kind == "fulfillment":
                    pick = next((p for p in items if _is_ground_policy(p)), pick)
                out[f"{kind}_policy_id"] = pick.get(id_field, "")
        except Exception:  # noqa: BLE001 - best effort
            pass
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        locs = resp.json().get("locations", [])
        if locs:
            out["merchant_location_key"] = locs[0].get("merchantLocationKey", "")
    except Exception:  # noqa: BLE001
        pass
    return out


def ship_from_postal(access_token: str, merchant_location_key: str = "") -> str:
    """The postal code of the seller's ship-from location, read off eBay.

    Publishing through the Trading API has to state where the item ships from
    — eBay rejects the listing outright with "Your item's location was not
    filled in" otherwise. The app usually has the ZIP saved, but a seller who
    connected before we started storing it (or who set their location up on
    eBay directly) has only a merchantLocationKey, so fetch the address behind
    it. Returns "" when it can't be determined; never raises."""
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""
        locs = resp.json().get("locations", []) or []
    except Exception:  # noqa: BLE001 - best-effort lookup
        return ""
    if not locs:
        return ""
    match = next((loc for loc in locs
                  if loc.get("merchantLocationKey") == merchant_location_key),
                 locs[0])
    address = (match.get("location") or {}).get("address") or {}
    return str(address.get("postalCode") or "").strip()


def account_overview(access_token: str) -> dict:
    """Mirror the seller's most-updated eBay account settings, best-effort.
    Every section is fetched independently, so one failing leaves the rest
    intact (e.g. a seller with no business policies still gets locations)."""
    out: dict = {"policies": {"fulfillment": [], "payment": [], "return": []},
                 "locations": [], "programs": [], "payments": {}}
    try:
        out["policies"] = list_business_policies(access_token)
    except Exception:  # noqa: BLE001
        pass
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            out["locations"] = resp.json().get("locations", []) or []
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _account_get("/sell/account/v1/program/get_opted_in_programs", access_token)
        out["programs"] = [p.get("programType") for p in (data.get("programs") or [])
                           if p.get("programType")]
    except Exception:  # noqa: BLE001
        pass
    try:
        out["payments"] = fetch_payments_program(access_token)
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# Shipping services (per-listing selection + USPS Ground Advantage default)
# ---------------------------------------------------------------------------

# These policy names show up in the seller's own eBay account, so they carry
# the app's real brand. They are display-only: find_policy_for_service matches
# on the shipping SERVICE CODE, never the name, so renaming them cannot orphan
# a policy an existing seller already has.
GROUND_POLICY_NAME = "USPS Ground Advantage (Thryft Shop)"

# The eBay (EBAY_US) shipping services a seller can one-tap into a fulfillment
# policy. Codes are eBay ShippingServiceCodeType values; keep to well-known,
# calculated-cost-friendly services so policy creation can't send an invalid
# combo. The picker shows label + note; `cap_oz` mirrors preflight's weight
# caps where a service silently kills a publish.
SHIPPING_SERVICES = [
    {"code": "USPSGroundAdvantage", "carrier": "USPS",
     "label": "USPS Ground Advantage", "note": "Cheapest for most packages — up to 70 lb"},
    {"code": "USPSPriority", "carrier": "USPS",
     "label": "USPS Priority Mail", "note": "1–3 business days"},
    {"code": "USPSPriorityExpress", "carrier": "USPS",
     "label": "USPS Priority Mail Express", "note": "Overnight to 2-day"},
    {"code": "USPSMedia", "carrier": "USPS",
     "label": "USPS Media Mail", "note": "Books & media only — slow but cheap"},
    {"code": "US_eBayStandardEnvelope", "carrier": "USPS",
     "label": "eBay Standard Envelope", "note": "Cards & flat items, 3 oz max", "cap_oz": 3},
    {"code": "UPSGround", "carrier": "UPS",
     "label": "UPS Ground", "note": "1–5 business days, good for heavy items"},
    {"code": "UPS3DaySelect", "carrier": "UPS",
     "label": "UPS 3 Day Select", "note": "3 business days"},
    {"code": "UPS2ndDay", "carrier": "UPS",
     "label": "UPS 2nd Day Air", "note": "2 business days"},
    {"code": "FedExHomeDelivery", "carrier": "FEDEX",
     "label": "FedEx Home Delivery", "note": "1–5 business days, residential"},
]


def service_by_code(code: str) -> Optional[dict]:
    norm = (code or "").lower().replace("_", "")
    for svc in SHIPPING_SERVICES:
        if svc["code"].lower().replace("_", "") == norm:
            return svc
    return None


def _policy_services(p: dict) -> list[dict]:
    """[{code, name}] for every shipping service in a fulfillment policy."""
    services = []
    for opt in p.get("shippingOptions") or []:
        for svc in opt.get("shippingServices") or []:
            code = svc.get("shippingServiceCode", "") or ""
            if code:
                services.append({
                    "code": code,
                    "name": svc.get("shippingCarrierCode", "") or "",
                })
    return services


def _is_ground_policy(p: dict) -> bool:
    return any("groundadvantage" in s["code"].lower().replace("_", "")
               for s in _policy_services(p))


def find_policy_for_service(access_token: str, service_code: str) -> Optional[dict]:
    """The seller's first fulfillment policy already shipping `service_code`."""
    norm = (service_code or "").lower().replace("_", "")
    path, list_field, id_field = _POLICY_SPECS["fulfillment"]
    try:
        data = _account_get(path, access_token)
    except Exception:  # noqa: BLE001
        return None
    for p in data.get(list_field, []):
        if any(s["code"].lower().replace("_", "") == norm
               for s in _policy_services(p)):
            return {"id": p.get(id_field, ""), "name": p.get("name", "")}
    return None


def ensure_service_policy(access_token: str, svc: dict) -> dict:
    """Find — or create — a fulfillment policy that ships `svc` (an entry from
    SHIPPING_SERVICES): calculated cost, domestic, 2-day handling. Returns
    {id, name, created}."""
    existing = find_policy_for_service(access_token, svc["code"])
    if existing and existing["id"]:
        return {**existing, "created": False}
    name = (f"{svc['label']} (Thryft Shop)"
            if svc["code"] != "USPSGroundAdvantage" else GROUND_POLICY_NAME)
    body = {
        "name": name,
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": 2, "unit": "DAY"},
        "shippingOptions": [{
            "costType": "CALCULATED",
            "optionType": "DOMESTIC",
            "shippingServices": [{
                "sortOrder": 1,
                "shippingCarrierCode": svc["carrier"],
                "shippingServiceCode": svc["code"],
            }],
        }],
    }
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/sell/account/v1/fulfillment_policy",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    created = resp.json()
    log.info("ebay: created %s fulfillment policy %s", svc["code"],
             created.get("fulfillmentPolicyId", ""))
    return {"id": created.get("fulfillmentPolicyId", ""),
            "name": created.get("name", name), "created": True}


def fulfillment_policy_services(access_token: str, policy_id: str) -> list[dict]:
    """[{code, name}] for one fulfillment policy (empty on any failure —
    preflight treats unknown services as unconstrained)."""
    if not policy_id:
        return []
    try:
        p = _account_get(f"/sell/account/v1/fulfillment_policy/{policy_id}",
                         access_token)
        return _policy_services(p)
    except Exception:  # noqa: BLE001
        return []


_SERVICE_FRIENDLY = [
    ("standardenvelope", "eBay Standard Envelope (3 oz max)"),
    ("groundadvantage", "USPS Ground Advantage"),
    ("firstclass", "USPS First Class (<1 lb)"),
    ("uspspriorityflatrate", "USPS Priority Flat Rate"),
    ("uspspriorityexpress", "USPS Priority Express"),
    ("uspspriority", "USPS Priority"),
    ("mediamail", "USPS Media Mail"),
    ("upsground", "UPS Ground"),
    ("fedex", "FedEx"),
]


def _friendly_service(code: str) -> str:
    c = (code or "").lower().replace("_", "")
    for frag, name in _SERVICE_FRIENDLY:
        if frag in c:
            return name
    return code
