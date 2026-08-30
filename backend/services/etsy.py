"""Etsy Open API v3 client: listings, images, taxonomy, category suggestion.

Publish flow (etsy_provider drives it): createDraftListing -> upload each
image -> PATCH state=active. Etsy has no sandbox, so the provider's dry-run
mode (no connection) returns the exact payload this module would send.

Transport note: the v3 listing endpoints take x-www-form-urlencoded bodies,
not JSON (form_body does the conversion). Image upload is the exception —
that one is genuinely multipart.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

from .. import config
from ..config import log
from ..models import Listing


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}",
            "x-api-key": config.ETSY_CLIENT_ID,
            "Accept": "application/json"}


def form_body(payload: dict) -> dict:
    """Etsy's listing payload as x-www-form-urlencoded fields.

    The v3 listing endpoints declare their request bodies as
    application/x-www-form-urlencoded, NOT JSON — posting JSON gets a 400 on
    every call. Repeated-value fields (tags, materials) go as one
    comma-joined string, booleans as "true"/"false", and None is dropped
    rather than sent as the literal "None".
    """
    out: dict[str, str] = {}
    for key, value in (payload or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            joined = ",".join(str(v).strip() for v in value if str(v).strip())
            if joined:
                out[key] = joined
        else:
            out[key] = str(value)
    return out


def etsy_error_issues(resp: Optional[httpx.Response], fallback: str) -> list[dict]:
    """Etsy's {error: "..."} bodies as one UI-ready issue."""
    message = fallback
    if resp is not None:
        try:
            message = str(resp.json().get("error") or fallback)
        except Exception:  # noqa: BLE001 - non-JSON error body
            message = (resp.text or fallback)[:300]
    return [{"target": "generic", "level": "error",
             "title": "Etsy rejected the listing", "fix": message}]


class EtsyError(ValueError):
    """An Etsy API rejection, carrying UI-ready issues.

    `outcome_unknown` is False here and True on UnknownOutcome below, so any
    caller can ask an Etsy failure whether Etsy might still have acted on it.
    """

    outcome_unknown = False

    def __init__(self, message: str, issues: list[dict]):
        super().__init__(message)
        self.issues = issues


class UnknownOutcome(EtsyError):
    """The request went out and we never learned what Etsy did with it.

    The provider already handles this once a listing EXISTS: its failure path
    keeps `listing_id` so a photo upload or an activate call that fails cannot
    orphan the listing and have the retry mint a second one. The create itself
    is the gap -- there is no id to keep, so a lost answer means the retry
    creates a second draft on the seller's shop.

    And the words matter as much as the id. "Etsy rejected the listing" is
    what the fix panel and the bulk cards render, and someone who reads
    "rejected" edits a field and publishes again.
    """

    outcome_unknown = True


# Transport failures that prove the request never reached Etsy: no connection
# was established, so nothing there could have acted on it. Everything else,
# including an exception type nobody here anticipated, is unknown -- the same
# asymmetry the Trading and orders clients use, for the same reason.
_NEVER_SENT = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
    httpx.UnsupportedProtocol, httpx.InvalidURL,
)

_UNKNOWN_ISSUE = {
    "target": "generic", "level": "error",
    "title": "We could not confirm what Etsy did",
    "fix": ("The request reached Etsy and the answer didn't come back, so we "
            "can't tell whether it went through. Check your Etsy shop's "
            "drafts before trying again — retrying blind could create it "
            "twice."),
}


def _unknown(doing: str) -> "UnknownOutcome":
    log.warning("etsy: %s — no answer, outcome unknown", doing)
    return UnknownOutcome(_UNKNOWN_ISSUE["fix"], [dict(_UNKNOWN_ISSUE)])


def _raise_for_status(resp: httpx.Response, doing: str,
                      changes: bool = True) -> None:
    if resp.status_code < 400:
        return
    if changes and resp.status_code >= 500:
        # Something that already had the request in hand failed to answer for
        # it. Not a rejection.
        raise _unknown(doing)
    issues = etsy_error_issues(resp, f"{doing} failed (HTTP {resp.status_code})")
    log.warning("etsy: %s failed: HTTP %s %s", doing, resp.status_code,
                resp.text[:300])
    raise EtsyError(issues[0]["fix"], issues)


def _unreachable(doing: str, exc: Exception) -> EtsyError:
    """Etsy never saw the request, or never answered a read.

    NOT titled as a rejection: `etsy_error_issues` says "Etsy rejected the
    listing", which is a claim about something Etsy did, and here it did
    nothing -- the connection was never made. The fix panel and the bulk cards
    render the title, so getting this wrong sends the seller looking for a
    problem with their listing when the problem is the network.
    """
    message = f"Couldn't reach Etsy while {doing}."
    return EtsyError(message, [{"target": "generic", "level": "error",
                                "title": "Couldn't reach Etsy",
                                "fix": f"{message} Nothing was sent — try "
                                       "again in a moment."}])


def _send(doing: str, changes: bool, call, *args, **kwargs) -> httpx.Response:
    """Run one Etsy request, classifying a failure to get an answer.

    A write with no answer is an UNKNOWN OUTCOME; a read with no answer is an
    ordinary failure, because nothing on Etsy moved.
    """
    try:
        return call(*args, **kwargs)
    except _NEVER_SENT as exc:
        raise _unreachable(doing, exc) from exc
    except Exception as exc:  # noqa: BLE001 - sent, or sent-ness unproven
        if changes:
            raise _unknown(doing) from exc
        raise _unreachable(doing, exc) from exc


def create_draft_listing(access_token: str, shop_id: str, payload: dict) -> dict:
    doing = "creating the Etsy listing"
    resp = _send(doing, True, httpx.post,
                 f"{config.ETSY_API_BASE}/application/shops/{shop_id}/listings",
                 headers=_headers(access_token), data=form_body(payload),
                 timeout=60)
    _raise_for_status(resp, doing)
    body = resp.json()
    return {"listing_id": str(body.get("listing_id", "")),
            "url": body.get("url") or "", "state": body.get("state", "draft")}


def update_listing(access_token: str, shop_id: str, listing_id: str,
                   patch: dict) -> dict:
    doing = "updating the Etsy listing"
    resp = _send(
        doing, True, httpx.patch,
        f"{config.ETSY_API_BASE}/application/shops/{shop_id}/listings/{listing_id}",
        headers=_headers(access_token), data=form_body(patch), timeout=60)
    _raise_for_status(resp, doing)
    body = resp.json()
    return {"listing_id": str(body.get("listing_id", listing_id)),
            "url": body.get("url") or "", "state": body.get("state", "")}


def get_listing(access_token: str, listing_id: str) -> dict:
    doing = "reading the Etsy listing"
    resp = _send(doing, False, httpx.get,
                 f"{config.ETSY_API_BASE}/application/listings/{listing_id}",
                 headers=_headers(access_token), timeout=30)
    _raise_for_status(resp, doing, changes=False)
    return resp.json()


def upload_listing_image(access_token: str, shop_id: str, listing_id: str,
                         image_bytes: bytes, filename: str, rank: int) -> None:
    doing = f"uploading photo {rank}"
    resp = _send(
        doing, True, httpx.post,
        f"{config.ETSY_API_BASE}/application/shops/{shop_id}/listings/{listing_id}/images",
        headers=_headers(access_token),
        files={"image": (filename, image_bytes, "image/jpeg")},
        data={"rank": str(rank)},
        timeout=120)
    _raise_for_status(resp, doing)


# --- seller taxonomy --------------------------------------------------------
# The full tree is ~3k nodes and changes rarely; cache it in-process for a
# day. Fetching needs only the app keystring (no user OAuth).

_TAXONOMY_CACHE: dict = {"at": 0.0, "nodes": None}
_TAXONOMY_TTL = 86400
_TAXONOMY_LOCK = threading.Lock()


def taxonomy_nodes() -> list[dict]:
    with _TAXONOMY_LOCK:
        if (_TAXONOMY_CACHE["nodes"] is not None
                and time.time() - _TAXONOMY_CACHE["at"] < _TAXONOMY_TTL):
            return _TAXONOMY_CACHE["nodes"]
        resp = httpx.get(
            f"{config.ETSY_API_BASE}/application/seller-taxonomy/nodes",
            headers={"x-api-key": config.ETSY_CLIENT_ID,
                     "Accept": "application/json"},
            timeout=60)
        resp.raise_for_status()
        nodes = resp.json().get("results", [])
        _TAXONOMY_CACHE.update(at=time.time(), nodes=nodes)
        return nodes


def _flatten_taxonomy(nodes: list[dict], trail: tuple = (),
                      out: Optional[list] = None) -> list[dict]:
    """The tree as leaf paths: [{id, path: "Clothing > Men's > Shirts"}]."""
    if out is None:
        out = []
    for n in nodes:
        path = trail + ((n.get("name") or "").strip(),)
        children = n.get("children") or []
        if children:
            _flatten_taxonomy(children, path, out)
        else:
            out.append({"id": int(n.get("id") or 0), "path": " > ".join(path)})
    return out


def taxonomy_paths() -> list[dict]:
    return _flatten_taxonomy(taxonomy_nodes())


def suggest_taxonomy(listing: Listing) -> dict:
    """Pick the best Etsy category for this listing: cheap keyword filter down
    to a shortlist, then one small Claude call to choose (the same shape as
    the eBay taxonomy suggestion flow). Returns {taxonomy_id, path}."""
    # Imported here, not at module scope: this is the only function that needs
    # the Anthropic SDK, and hoisting it made the whole Etsy transport layer
    # unimportable wherever that dependency isn't installed (CI's minimal
    # install, which exists to unit-test exactly these pure request bodies).
    from . import claude_ai

    paths = taxonomy_paths()
    text = " ".join(filter(None, [
        listing.title, listing.brand, listing.category_suggestion,
        " ".join(s.value for s in listing.item_specifics)])).lower()
    words = {w for w in text.replace("/", " ").replace("-", " ").split()
             if len(w) > 2}

    def score(p: dict) -> int:
        path_words = set(p["path"].lower().replace(">", " ").split())
        return len(words & path_words)

    shortlist = sorted(paths, key=score, reverse=True)[:30]
    if not shortlist:
        return {"taxonomy_id": 0, "path": ""}
    if not config.anthropic_ready():
        best = shortlist[0]
        return {"taxonomy_id": best["id"], "path": best["path"]}
    client = claude_ai._client()
    options = "\n".join(f"{p['id']}: {p['path']}" for p in shortlist)
    resp = client.messages.create(
        model=config.CONTENT_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": (
            "Pick the single best Etsy category for this item.\n\n"
            f"Item: {listing.title}\nBrand: {listing.brand}\n"
            f"Details: {'; '.join(f'{s.name}: {s.value}' for s in listing.item_specifics[:10])}\n\n"
            f"Categories (id: path):\n{options}\n\n"
            'Reply with JSON only: {"taxonomy_id": <id>}')}],
    )
    text_out = "".join(b.text for b in resp.content if b.type == "text")
    try:
        picked = int(claude_ai._extract_json(text_out).get("taxonomy_id") or 0)
    except Exception:  # noqa: BLE001 - fall back to the keyword winner
        picked = 0
    match = next((p for p in shortlist if p["id"] == picked), shortlist[0])
    return {"taxonomy_id": match["id"], "path": match["path"]}
