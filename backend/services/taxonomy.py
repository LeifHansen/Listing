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

import time
from typing import Optional

import httpx

from .. import config

# Simple in-process caches (token + per-marketplace tree id).
_token_cache: dict = {"token": None, "expires_at": 0.0}
_tree_cache: dict = {}


def _app_token() -> str:
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
