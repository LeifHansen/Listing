"""Verify that a notification really came from eBay.

eBay signs every marketplace notification. The signature is an ECDSA
signature over the RAW request body, carried in the `x-ebay-signature`
header as base64-encoded JSON:

    {"alg": "ecdsa", "kid": "<public key id>", "signature": "<base64>",
     "digest": "SHA1"}

The public key is fetched from eBay's Notification API by `kid`, using an
application (client-credentials) token, and cached — keys rotate, but slowly.

Why this must exist before the endpoint deletes anything: the account-deletion
endpoint is a public URL with no authentication of its own. An unverified
handler that erases data is a remote, unauthenticated account-wipe primitive —
anyone who knows the URL could post a body naming any seller. Verification and
erasure therefore have to land together, never erasure first.

Contract: https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion
"""
from __future__ import annotations

import base64
import json
import threading
import time
from typing import Optional

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .. import config
from ..config import log

# kid -> (public key object, fetched_at). eBay's keys are stable for long
# stretches; the TTL is here so a rotation cannot pin a stale key forever.
_KEY_CACHE: dict[str, tuple[object, float]] = {}
_KEY_TTL_SECONDS = 24 * 3600
_KEY_LOCK = threading.Lock()

# A verification failure must be cheap to distinguish from a transport
# problem: the first is a forged or stale notice (refuse it), the second is
# eBay being unreachable (ask eBay to send it again).
class KeyUnavailable(RuntimeError):
    """eBay's public key could not be fetched, so nothing can be verified."""


def _app_token() -> str:
    """A client-credentials token for the Notification API public-key call."""
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        data={"grant_type": "client_credentials",
              "scope": "https://api.ebay.com/oauth/api_scope"},
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not resp.is_success:
        raise KeyUnavailable(
            f"eBay refused an application token ({resp.status_code})")
    token = (resp.json() or {}).get("access_token") or ""
    if not token:
        raise KeyUnavailable("eBay returned no application token")
    return token


def _fetch_public_key(kid: str):
    """eBay's public key for this key id, as a cryptography key object."""
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/commerce/notification/v1/public_key/{kid}",
        headers={"Authorization": f"Bearer {_app_token()}",
                 "Accept": "application/json"},
        timeout=30,
    )
    if not resp.is_success:
        raise KeyUnavailable(
            f"eBay returned {resp.status_code} for public key {kid}")
    pem = ((resp.json() or {}).get("key") or "").strip()
    if not pem:
        raise KeyUnavailable(f"eBay returned no key material for {kid}")
    if "BEGIN PUBLIC KEY" not in pem:
        # eBay hands back bare base64 for some keys; PEM-wrap it so
        # cryptography can load it.
        pem = ("-----BEGIN PUBLIC KEY-----\n"
               + "\n".join(pem[i:i + 64] for i in range(0, len(pem), 64))
               + "\n-----END PUBLIC KEY-----\n")
    return serialization.load_pem_public_key(pem.encode("ascii"))


def public_key_for(kid: str):
    """Cached public key lookup."""
    now = time.time()
    with _KEY_LOCK:
        hit = _KEY_CACHE.get(kid)
        if hit and (now - hit[1]) < _KEY_TTL_SECONDS:
            return hit[0]
    key = _fetch_public_key(kid)
    with _KEY_LOCK:
        _KEY_CACHE[kid] = (key, now)
    return key


def _parse_header(signature_header: str) -> Optional[dict]:
    try:
        decoded = base64.b64decode(signature_header, validate=True)
        parsed = json.loads(decoded)
    except Exception:  # noqa: BLE001 - malformed header is simply not eBay
        return None
    return parsed if isinstance(parsed, dict) else None


def verify(raw_body: bytes, signature_header: str) -> bool:
    """True when `raw_body` really carries eBay's signature.

    `raw_body` must be the EXACT bytes received. Verifying a re-serialized
    dict verifies nothing: json.dumps of a parsed body differs from what was
    signed in whitespace, key order and unicode escaping, so it would fail
    for good notices and, worse, invite someone to "fix" it by skipping the
    check.

    Raises KeyUnavailable when eBay's key could not be fetched — that is not
    a forged notice and must not be answered as one.
    """
    if not signature_header or not raw_body:
        return False
    header = _parse_header(signature_header)
    if not header:
        return False
    kid = str(header.get("kid") or "")
    signature_b64 = str(header.get("signature") or "")
    if not kid or not signature_b64:
        return False
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:  # noqa: BLE001
        return False

    key = public_key_for(kid)  # may raise KeyUnavailable
    if not isinstance(key, ec.EllipticCurvePublicKey):
        log.warning("ebay notify: unexpected key type for kid=%s", kid)
        return False

    # eBay documents SHA1 for this signature. SHA1 is weak in general, but the
    # algorithm is eBay's to choose and the check is still what proves the
    # notice came from them; the alternative is no verification at all.
    digest = (str(header.get("digest") or "SHA1")).upper()
    algorithm = hashes.SHA1() if digest == "SHA1" else hashes.SHA256()
    try:
        key.verify(signature, raw_body, ec.ECDSA(algorithm))
        return True
    except InvalidSignature:
        return False
    except Exception as exc:  # noqa: BLE001 - malformed signature encoding
        log.warning("ebay notify: signature check errored: %s", exc)
        return False
