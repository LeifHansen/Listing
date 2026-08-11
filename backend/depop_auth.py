"""'Sign in with Depop' — OAuth 2.0 Authorization Code (Depop Selling API).

Depop's Selling API is partner-gated (partnerapi.depop.com; access via
Depop's partnerships team), so the authorize/token endpoints are env vars
(DEPOP_AUTH_URL / DEPOP_TOKEN_URL) supplied with the partner credentials —
endpoint corrections need zero code changes. Nothing here runs until
config.depop_oauth_ready() is true.
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

import httpx

from . import config


def authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": config.DEPOP_CLIENT_ID,
        "redirect_uri": config.DEPOP_REDIRECT_URI,
        "scope": config.DEPOP_SCOPES,
        "state": state,
    }
    return f"{config.DEPOP_AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    resp = httpx.post(
        config.DEPOP_TOKEN_URL,
        data=data,
        auth=(config.DEPOP_CLIENT_ID, config.DEPOP_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code(code: str) -> dict:
    body = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.DEPOP_REDIRECT_URI,
    })
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 3600)),
    }


def refresh_access_token(refresh_token: str) -> dict:
    body = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    return {
        "access_token": body["access_token"],
        # Tolerate rotation if Depop does it; empty means "unchanged".
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 3600)),
    }
