"""'Sign in with Etsy' — OAuth 2.0 Authorization Code with PKCE (Etsy v3).

Etsy never sees a client secret: the app keystring (ETSY_CLIENT_ID) plus a
per-flow PKCE verifier is the whole handshake, and every API call carries the
keystring again as the x-api-key header.

Two Etsy quirks shape this module:
- Refresh tokens ROTATE: every refresh returns a new ~90-day refresh token
  and invalidates the old one. Callers must persist the rotated token
  immediately (etsy_provider.creds_for does, under a per-user lock).
- There is no sandbox; the dry-run path in the provider is the test story.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from . import config


def make_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) for one authorization flow."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": config.ETSY_CLIENT_ID,
        "redirect_uri": config.ETSY_REDIRECT_URI,
        "scope": config.ETSY_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{config.ETSY_AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    resp = httpx.post(
        config.ETSY_TOKEN_URL,
        json=data,
        headers={"x-api-key": config.ETSY_CLIENT_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code(code: str, code_verifier: str) -> dict:
    body = _token_request({
        "grant_type": "authorization_code",
        "client_id": config.ETSY_CLIENT_ID,
        "redirect_uri": config.ETSY_REDIRECT_URI,
        "code": code,
        "code_verifier": code_verifier,
    })
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 3600)),
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Returns a fresh access token AND the rotated refresh token — persist
    the rotation or the connection dies when the old token expires."""
    body = _token_request({
        "grant_type": "refresh_token",
        "client_id": config.ETSY_CLIENT_ID,
        "refresh_token": refresh_token,
    })
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 3600)),
    }


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}",
            "x-api-key": config.ETSY_CLIENT_ID,
            "Accept": "application/json"}


def fetch_me(access_token: str) -> dict:
    """The connected user's ids: {user_id, shop_id}."""
    resp = httpx.get(f"{config.ETSY_API_BASE}/application/users/me",
                     headers=_headers(access_token), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_shop(access_token: str, shop_id: str) -> dict:
    resp = httpx.get(f"{config.ETSY_API_BASE}/application/shops/{shop_id}",
                     headers=_headers(access_token), timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_shipping_profiles(access_token: str, shop_id: str) -> list[dict]:
    """[{id, name}] for the Settings picker."""
    resp = httpx.get(
        f"{config.ETSY_API_BASE}/application/shops/{shop_id}/shipping-profiles",
        headers=_headers(access_token), timeout=30)
    resp.raise_for_status()
    return [{"id": str(p.get("shipping_profile_id", "")),
             "name": p.get("title") or f"Profile {p.get('shipping_profile_id')}"}
            for p in resp.json().get("results", [])]


def list_return_policies(access_token: str, shop_id: str) -> list[dict]:
    """[{id, name}] — Etsy return policies have no titles, so synthesize a
    readable one from what the policy does."""
    resp = httpx.get(
        f"{config.ETSY_API_BASE}/application/shops/{shop_id}/policies/return",
        headers=_headers(access_token), timeout=30)
    resp.raise_for_status()
    out = []
    for p in resp.json().get("results", []):
        if p.get("accepts_returns") or p.get("accepts_exchanges"):
            what = " & ".join(w for w, on in (
                ("returns", p.get("accepts_returns")),
                ("exchanges", p.get("accepts_exchanges"))) if on)
            name = f"Accepts {what} within {p.get('return_deadline') or '?'} days"
        else:
            name = "No returns or exchanges"
        out.append({"id": str(p.get("return_policy_id", "")), "name": name})
    return out
