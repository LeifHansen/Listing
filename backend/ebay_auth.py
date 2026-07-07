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
    body = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(config.EBAY_OAUTH_SCOPES),
        }
    )
    return {
        "access_token": body["access_token"],
        "expires_at": time.time() + float(body.get("expires_in", 7200)),
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
    fetchers = {
        "fulfillment_policy_id": ("/sell/account/v1/fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
        "payment_policy_id": ("/sell/account/v1/payment_policy", "paymentPolicies", "paymentPolicyId"),
        "return_policy_id": ("/sell/account/v1/return_policy", "returnPolicies", "returnPolicyId"),
    }
    for key, (path, list_field, id_field) in fetchers.items():
        try:
            data = _account_get(path, access_token)
            items = data.get(list_field, [])
            if items:
                out[key] = items[0].get(id_field, "")
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
