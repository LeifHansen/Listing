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
        "name": "QuickFlip ship-from location",
    }
    r = httpx.post(f"{base}/sell/inventory/v1/location/{key}",
                   headers=headers, json=body, timeout=30)
    if r.status_code in (200, 204):
        return key
    # Create failed — usually because the location already exists. eBay signals
    # that as 409 OR as 400 with errorId 25803, so don't try to distinguish:
    # attempt the address update first.
    log.info("ebay location create(%s) -> %s; trying update: %s",
             key, r.status_code, r.text[:200])
    u = httpx.post(
        f"{base}/sell/inventory/v1/location/{key}/update_location_details",
        headers=headers, json={"location": {"address": addr}}, timeout=30)
    if u.status_code in (200, 204):
        return key
    log.warning("update_location_details(%s) -> %s %s", key, u.status_code, u.text[:300])
    # Last resort: an existing location created by an older/buggy version can be
    # un-updatable (missing country, wrong type). Delete it and create fresh.
    d = httpx.delete(f"{base}/sell/inventory/v1/location/{key}",
                     headers=headers, timeout=30)
    log.info("ebay location delete(%s) -> %s", key, d.status_code)
    if d.status_code in (200, 204, 404):
        r2 = httpx.post(f"{base}/sell/inventory/v1/location/{key}",
                        headers=headers, json=body, timeout=30)
        if r2.status_code in (200, 204):
            log.info("ebay location recreated cleanly (%s)", key)
            return key
        log.warning("ebay location recreate(%s) -> %s %s",
                    key, r2.status_code, r2.text[:300])
        r = r2  # surface the recreate error below
    detail = _ebay_error_message(u.text) or _ebay_error_message(r.text) \
        or f"HTTP {u.status_code}"
    raise RuntimeError(f"eBay couldn't save that ship-from location: {detail}")


def _ebay_error_message(body: str) -> str:
    """The human part of an eBay error body, if there is one."""
    try:
        import json as _json
        errs = _json.loads(body).get("errors") or []
        return "; ".join(e.get("message", "") for e in errs if e.get("message"))[:300]
    except Exception:  # noqa: BLE001
        return ""


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
                elif kind == "payment":
                    pick = next((p for p in items
                                 if "managed" in (p.get("name", "") or "").lower()), pick)
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


# ---------------------------------------------------------------------------
# Shipping services (per-listing selection + USPS Ground Advantage default)
# ---------------------------------------------------------------------------

GROUND_POLICY_NAME = "USPS Ground Advantage (QuickFlip)"


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


def list_fulfillment_policies(access_token: str) -> list[dict]:
    """Fulfillment policies with their shipping services, for the per-listing
    shipping-service picker: [{id, name, services: [code, ...]}]."""
    path, list_field, id_field = _POLICY_SPECS["fulfillment"]
    data = _account_get(path, access_token)
    out = []
    for p in data.get(list_field, []):
        out.append({
            "id": p.get(id_field, ""),
            "name": p.get("name", "") or p.get(id_field, ""),
            "services": [s["code"] for s in _policy_services(p)],
        })
    return out


def find_ground_policy(access_token: str) -> Optional[dict]:
    """The seller's first fulfillment policy that ships USPS Ground Advantage."""
    path, list_field, id_field = _POLICY_SPECS["fulfillment"]
    try:
        data = _account_get(path, access_token)
    except Exception:  # noqa: BLE001
        return None
    for p in data.get(list_field, []):
        if _is_ground_policy(p):
            return {"id": p.get(id_field, ""), "name": p.get("name", "")}
    return None


def ensure_ground_policy(access_token: str) -> dict:
    """Find — or create — a fulfillment policy that ships USPS Ground Advantage
    (calculated cost, up to 70 lb; the cheapest broadly-applicable USPS
    service). Returns {id, name, created}."""
    existing = find_ground_policy(access_token)
    if existing and existing["id"]:
        return {**existing, "created": False}
    body = {
        "name": GROUND_POLICY_NAME,
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": 2, "unit": "DAY"},
        "shippingOptions": [{
            "costType": "CALCULATED",
            "optionType": "DOMESTIC",
            "shippingServices": [{
                "sortOrder": 1,
                "shippingCarrierCode": "USPS",
                "shippingServiceCode": "USPSGroundAdvantage",
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
    log.info("ebay: created Ground Advantage fulfillment policy %s",
             created.get("fulfillmentPolicyId", ""))
    return {"id": created.get("fulfillmentPolicyId", ""),
            "name": created.get("name", GROUND_POLICY_NAME), "created": True}


def fulfillment_policy_services(access_token: str, policy_id: str,
                                timeout: float = 30) -> list[dict]:
    """[{code, name}] for one fulfillment policy (empty on any failure —
    preflight treats unknown services as unconstrained)."""
    if not policy_id:
        return []
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/account/v1/fulfillment_policy/{policy_id}",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            params={"marketplace_id": config.EBAY_MARKETPLACE_ID},
            timeout=timeout,
        )
        resp.raise_for_status()
        return _policy_services(resp.json())
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


PAYMENT_POLICY_NAME = "eBay Managed Payments (QuickFlip)"


def ensure_payment_policy(access_token: str) -> dict:
    """Find — or create — a payment policy, preferring one named for managed
    payments (which is what every policy is in practice). {id, name, created}."""
    path, list_field, id_field = _POLICY_SPECS["payment"]
    try:
        items = _account_get(path, access_token).get(list_field, [])
    except Exception:  # noqa: BLE001
        items = []
    if items:
        pick = next((p for p in items
                     if "managed" in (p.get("name", "") or "").lower()), items[0])
        return {"id": pick.get(id_field, ""),
                "name": pick.get("name", ""), "created": False}
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/sell/account/v1/payment_policy",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "name": PAYMENT_POLICY_NAME,
            "marketplaceId": config.EBAY_MARKETPLACE_ID,
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    created = resp.json()
    log.info("ebay: created payment policy %s", created.get("paymentPolicyId", ""))
    return {"id": created.get("paymentPolicyId", ""),
            "name": created.get("name", PAYMENT_POLICY_NAME), "created": True}


RETURN_POLICY_NAME = "30-day returns (QuickFlip)"


def ensure_return_policy(access_token: str) -> dict:
    """Find — or create — a return policy (30-day, buyer pays return shipping,
    the common resale default). {id, name, created}."""
    path, list_field, id_field = _POLICY_SPECS["return"]
    try:
        items = _account_get(path, access_token).get(list_field, [])
    except Exception:  # noqa: BLE001
        items = []
    if items:
        pick = items[0]
        return {"id": pick.get(id_field, ""),
                "name": pick.get("name", ""), "created": False}
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/sell/account/v1/return_policy",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "name": RETURN_POLICY_NAME,
            "marketplaceId": config.EBAY_MARKETPLACE_ID,
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "returnsAccepted": True,
            "returnPeriod": {"value": 30, "unit": "DAY"},
            "returnShippingCostPayer": "BUYER",
            "refundMethod": "MONEY_BACK",
        },
        timeout=30,
    )
    resp.raise_for_status()
    created = resp.json()
    log.info("ebay: created return policy %s", created.get("returnPolicyId", ""))
    return {"id": created.get("returnPolicyId", ""),
            "name": created.get("name", RETURN_POLICY_NAME), "created": True}
