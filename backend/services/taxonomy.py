"""eBay Taxonomy API: resolve a human-readable item into a numeric category ID.

eBay requires a numeric leaf categoryId to publish a listing. The Taxonomy API
turns a free-text query (e.g. "Sony Walkman portable cassette player") into
ranked category suggestions. It uses an *application* access token
(client-credentials grant), so it works with just CLIENT_ID/CLIENT_SECRET -
no seller policies or user login required.

Endpoints used:
  GET /commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id=...
  GET /commerce/taxonomy/v1/category_tree/{treeId}/get_category_suggestions?q=...
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

from .. import config

# Simple in-process caches (token + per-marketplace tree id). Guarded by a
# lock: taxonomy runs from both the bulk daemon thread and the request
# threadpool, and an unguarded check-then-fetch near expiry produces a
# token-request stampede.
_token_cache: dict = {"token": None, "expires_at": 0.0}
_tree_cache: dict = {}
_cache_lock = threading.Lock()


def _app_token() -> str:
    with _cache_lock:
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
            return _token_cache["token"]

        resp = httpx.post(
            f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        _token_cache["token"] = body["access_token"]
        _token_cache["expires_at"] = now + float(body.get("expires_in", 7200))
        return _token_cache["token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_app_token()}", "Accept": "application/json"}


def default_tree_id(marketplace_id: Optional[str] = None) -> str:
    marketplace_id = marketplace_id or config.EBAY_MARKETPLACE_ID
    if marketplace_id in _tree_cache:
        return _tree_cache[marketplace_id]

    resp = httpx.get(
        f"{config.EBAY_API_BASE}/commerce/taxonomy/v1/get_default_category_tree_id",
        params={"marketplace_id": marketplace_id},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    tree_id = resp.json()["categoryTreeId"]
    _tree_cache[marketplace_id] = tree_id
    return tree_id


def suggest(query: str, marketplace_id: Optional[str] = None, limit: int = 5) -> dict:
    """Return ranked category suggestions for a free-text query.

    Shape:
      {
        "query": "...",
        "tree_id": "0",
        "suggestions": [
          {"category_id": "29836", "category_name": "...", "path": "A > B > C"},
          ...
        ]
      }
    """
    if not config.taxonomy_ready():
        raise RuntimeError(
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set; cannot use the "
            "Taxonomy API to resolve category IDs."
        )
    query = (query or "").strip()
    if not query:
        return {"query": query, "tree_id": None, "suggestions": []}

    tree_id = default_tree_id(marketplace_id)
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/commerce/taxonomy/v1/category_tree/{tree_id}"
        "/get_category_suggestions",
        params={"q": query},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    suggestions = []
    for s in data.get("categorySuggestions", [])[:limit]:
        cat = s.get("category", {})
        ancestors = s.get("categoryTreeNodeAncestors", []) or []
        # Ancestors are returned leaf-first; reverse for a readable root->leaf path.
        names = [a.get("categoryName", "") for a in reversed(ancestors)]
        names.append(cat.get("categoryName", ""))
        path = " > ".join(n for n in names if n)
        suggestions.append(
            {
                "category_id": cat.get("categoryId", ""),
                "category_name": cat.get("categoryName", ""),
                "path": path,
            }
        )
    return {"query": query, "tree_id": tree_id, "suggestions": suggestions}


def best_category_id(query: str, marketplace_id: Optional[str] = None) -> dict:
    """Convenience: return the single top suggestion (or empty dict)."""
    result = suggest(query, marketplace_id, limit=1)
    suggs = result.get("suggestions") or []
    return suggs[0] if suggs else {}


# eBay conditionId -> the Inventory API condition enum the offer must send.
_CONDITION_ID_TO_ENUM = {
    "1000": "NEW", "1500": "NEW_OTHER", "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED", "2010": "CERTIFIED_REFURBISHED",
    "2020": "SELLER_REFURBISHED", "2030": "SELLER_REFURBISHED",
    "2500": "SELLER_REFURBISHED", "2750": "LIKE_NEW",
    "3000": "USED_EXCELLENT", "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}


def item_conditions(category_id: str, access_token: Optional[str] = None,
                    marketplace_id: Optional[str] = None) -> dict:
    """The item conditions eBay allows for a category, so the UI can offer only
    valid choices (eBay rejects an out-of-category condition with error 25021).

    Uses the Sell Metadata API. Prefers the connected seller's token; falls back
    to the application token. Returns {"conditions": [{enum, id, label}]}.
    """
    if not category_id:
        return {"conditions": []}
    marketplace_id = marketplace_id or config.EBAY_MARKETPLACE_ID
    token = access_token or _app_token()
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/sell/metadata/v1/marketplace/{marketplace_id}"
        "/get_item_condition_policies",
        params={"filter": f"categoryIds:{{{category_id}}}"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    policies = data.get("itemConditionPolicies") or []
    conditions = []
    seen = set()
    for pol in policies:
        for c in pol.get("itemConditions", []) or []:
            cid = str(c.get("conditionId", ""))
            enum = _CONDITION_ID_TO_ENUM.get(cid)
            if not enum or enum in seen:
                continue
            seen.add(enum)
            conditions.append({
                "enum": enum,
                "id": cid,
                "label": c.get("conditionDescription", "") or enum.replace("_", " ").title(),
            })
    return {"conditions": conditions}


# Aspects are static per category; cache them so publish preflight never
# repeats the lookup (12h TTL, small bounded map).
_ASPECTS_CACHE: dict = {}
_ASPECTS_TTL_S = 12 * 3600


def item_aspects(category_id: str, marketplace_id: Optional[str] = None,
                 timeout: float = 30) -> dict:
    """The item specifics (aspects) eBay defines for a leaf category, so the UI
    can show exactly which fields are required vs recommended and whether each
    is free-text or a fixed set of values.

    Returns {"aspects": [{name, required, mode, values}]} where mode is
    "SELECTION_ONLY" (must pick from `values`) or "FREE_TEXT".
    """
    if not category_id:
        return {"aspects": []}
    cached = _ASPECTS_CACHE.get(category_id)
    if cached and time.time() - cached[0] < _ASPECTS_TTL_S:
        return cached[1]
    tree_id = default_tree_id(marketplace_id)
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/commerce/taxonomy/v1/category_tree/{tree_id}"
        "/get_item_aspects_for_category",
        params={"category_id": category_id},
        headers=_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    aspects = []
    for a in data.get("aspects", []):
        constraint = a.get("aspectConstraint", {}) or {}
        values = [v.get("localizedValue", "")
                  for v in (a.get("aspectValues") or []) if v.get("localizedValue")]
        mode = constraint.get("aspectMode", "FREE_TEXT")
        # SELECTION_ONLY aspects (e.g. Country/Region of Manufacture, ~250
        # countries) MUST keep every value — the user can only pick from the
        # list, so truncating drops valid choices. Only cap free-text enums,
        # where the list is a suggestion and typing past it is allowed.
        cap = 1000 if mode == "SELECTION_ONLY" else 60
        aspects.append({
            "name": a.get("localizedAspectName", ""),
            "required": bool(constraint.get("aspectRequired")),
            "mode": mode,
            "values": values[:cap],
        })
    # Required first, then by name, so the UI can show must-haves up top.
    aspects.sort(key=lambda x: (not x["required"], x["name"].lower()))
    result = {"aspects": aspects}
    with _cache_lock:
        if len(_ASPECTS_CACHE) > 500:  # bounded: drop everything, repopulate lazily
            _ASPECTS_CACHE.clear()
        _ASPECTS_CACHE[category_id] = (time.time(), result)
    return result
