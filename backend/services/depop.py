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
    """A Depop API rejection, carrying UI-ready issues.

    `outcome_unknown` is False here and True on UnknownOutcome below, so any
    caller can ask a Depop failure whether Depop might still have acted on it.
    """

    outcome_unknown = False

    def __init__(self, message: str, issues: list[dict]):
        super().__init__(message)
        self.issues = issues


class UnknownOutcome(DepopError):
    """The request went out and we never learned what Depop did with it.

    "Depop rejected the listing" is the title the fix panel and the bulk cards
    render, and it was what a lost answer said. Someone who reads "rejected"
    edits a field and publishes again, which is a second product on their
    shop; on a DELETE it is worse in the other direction, because the retry
    reports "no such product" and the seller concludes it never worked.
    """

    outcome_unknown = True


# Transport failures that prove the request never reached Depop. Everything
# else -- including an exception type nobody here anticipated -- is unknown,
# the same asymmetry the eBay and Etsy clients use.
_NEVER_SENT = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
    httpx.UnsupportedProtocol, httpx.InvalidURL,
)

# The methods that can change something over there. `_request` is the single
# choke point for every Depop call and already takes the method, so this needs
# no table of call names and cannot go stale as calls are added.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_UNKNOWN_ISSUE = {
    "target": "generic", "level": "error",
    "title": "We could not confirm what Depop did",
    "fix": ("The request reached Depop and the answer didn't come back, so we "
            "can't tell whether it went through. Check your Depop shop before "
            "trying again — retrying blind could do it twice."),
}


def _unknown(method: str, path: str) -> "UnknownOutcome":
    log.warning("depop: %s %s — no answer, outcome unknown", method, path)
    return UnknownOutcome(_UNKNOWN_ISSUE["fix"], [dict(_UNKNOWN_ISSUE)])


def _unreachable(exc: Exception) -> DepopError:
    message = f"Couldn't reach Depop: {exc}"
    return DepopError(message, [{"target": "generic", "level": "error",
                                 "title": "Couldn't reach Depop",
                                 "fix": message}])


def _request(method: str, path: str, access_token: str, *,
             json_body: Optional[dict] = None, timeout: int = 60) -> dict:
    changes = method.upper() in _WRITE_METHODS
    try:
        resp = httpx.request(
            method, f"{config.DEPOP_API_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            json=json_body, timeout=timeout)
    except _NEVER_SENT as exc:
        # No connection, so Depop never saw this. A definite failure even for
        # a write.
        raise _unreachable(exc) from exc
    except Exception as exc:  # noqa: BLE001 - sent, or sent-ness unproven
        if changes:
            raise _unknown(method, path) from exc
        raise _unreachable(exc) from exc
    if changes and resp.status_code >= 500:
        # Something that already had the request in hand failed to answer for
        # it. Not a rejection.
        raise _unknown(method, path)
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
