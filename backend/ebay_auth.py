"""'Sign in with eBay' OAuth (Authorization Code grant) + auto-discovery of
business policies and inventory location via the Account/Inventory APIs.

This is what removes the manual publish secrets: the seller clicks "Connect
eBay", grants access, and we store a refresh token + their policy IDs.
"""
from __future__ import annotations

import time
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
        "name": "Thryft ship-from location",
    }
    r = httpx.post(f"{base}/sell/inventory/v1/location/{key}",
                   headers=headers, json=body, timeout=30)
    if r.status_code == 409:
        # Already exists (maybe with a stale/missing address) — force it to the
        # current ZIP+country, and surface it if the repair itself fails.
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
                out[key].append({
                    "id": p.get(id_field, ""),
                    "name": p.get("name", "") or p.get(id_field, ""),
                    "summary": _policy_summary(key, p),
                })
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
            opts = p.get("shippingOptions") or []
            svcs = (opts[0].get("shippingServices") if opts else []) or []
            if svcs:
                cost = (svcs[0].get("shippingCost") or {}).get("value")
                if cost in ("0.0", "0.00", 0, "0"):
                    return "Free shipping"
                return f"Flat/calculated shipping" if cost else "Shipping configured"
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
                out[f"{kind}_policy_id"] = items[0].get(id_field, "")
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
