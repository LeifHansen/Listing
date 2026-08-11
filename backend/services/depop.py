"""Depop Selling API client (partner-gated).

The transport layer for Depop products: one _request helper against
config.DEPOP_API_BASE. Paths follow the partner API's documented product
surface and — like everything Depop — get confirmed against the partner
docs on the first credentialed run; corrections land here and in
mapping_depop.py only.
"""
from __future__ import annotations

from typing import Optional

import httpx

from .. import config
from ..config import log


class DepopError(ValueError):
    """A Depop API rejection, carrying UI-ready issues."""

    def __init__(self, message: str, issues: list[dict]):
        super().__init__(message)
        self.issues = issues


def _request(method: str, path: str, access_token: str, *,
             json_body: Optional[dict] = None, timeout: int = 60) -> dict:
    resp = httpx.request(
        method, f"{config.DEPOP_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}",
                 "Accept": "application/json"},
        json=json_body, timeout=timeout)
    if resp.status_code >= 400:
        try:
            message = str(resp.json().get("message")
                          or resp.json().get("error") or "")
        except Exception:  # noqa: BLE001 - non-JSON error body
            message = ""
        message = message or f"Depop request failed (HTTP {resp.status_code})"
        log.warning("depop: %s %s failed: HTTP %s %s", method, path,
                    resp.status_code, resp.text[:300])
        raise DepopError(message, [{
            "target": "generic", "level": "error",
            "title": "Depop rejected the listing", "fix": message}])
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def create_product(access_token: str, payload: dict) -> dict:
    body = _request("POST", "/v1/products", access_token, json_body=payload)
    return {"listing_id": str(body.get("id") or body.get("product_id") or ""),
            "url": str(body.get("url") or body.get("permalink") or "")}


def update_product(access_token: str, product_id: str, payload: dict) -> dict:
    body = _request("PUT", f"/v1/products/{product_id}", access_token,
                    json_body=payload)
    return {"listing_id": str(body.get("id") or product_id),
            "url": str(body.get("url") or body.get("permalink") or "")}


def delete_product(access_token: str, product_id: str) -> None:
    _request("DELETE", f"/v1/products/{product_id}", access_token)
