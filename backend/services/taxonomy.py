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

import re
import threading
import time
from typing import Optional

import httpx

from .. import config
from ..config import log

# Simple in-process caches (token + per-marketplace tree id).
_token_cache: dict = {"token": None, "expires_at": 0.0}
_tree_cache: dict = {}

# One lock for every TTL cache below: identify jobs, bulk workers, and request
# threads all read/write them concurrently. The lock only guards the dict —
# never held across an HTTP call, so a slow eBay response can't serialize
# unrelated lookups (two threads racing the same cold key just fetch twice).
_CACHE_LOCK = threading.Lock()


def _cache_get(cache: dict, key, ttl: float):
    with _CACHE_LOCK:
        hit = cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_put(cache: dict, key, value, bound: int = 500) -> None:
    with _CACHE_LOCK:
        if len(cache) > bound:  # tiny bound; whole-cache clear is fine
            cache.clear()
        cache[key] = (time.time(), value)


# Category suggestions barely change for a given query, but they were fetched
# live on EVERY identify (and every click of "Suggest categories") — one eBay
# round trip per listing that a bulk batch of similar items repeats endlessly.
_SUGGEST_TTL = 24 * 3600
_SUGGEST_CACHE: dict[tuple, tuple[float, dict]] = {}

# Item conditions are per-category metadata (same answer for every seller),
# fetched on every editor session open. As static as aspects — cache alike.
_CONDITIONS_TTL = 24 * 3600
_CONDITIONS_CACHE: dict[tuple, tuple[float, dict]] = {}


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

    cache_key = (" ".join(query.lower().split()), marketplace_id or "", limit)
    cached = _cache_get(_SUGGEST_CACHE, cache_key, _SUGGEST_TTL)
    if cached is not None:
        return cached

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
    result = {"query": query, "tree_id": tree_id, "suggestions": suggestions}
    _cache_put(_SUGGEST_CACHE, cache_key, result)
    return result


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
    # Cached per category, not per seller: the allowed conditions are category
    # metadata — the same answer whichever token asked for it.
    cache_key = (category_id, marketplace_id)
    cached = _cache_get(_CONDITIONS_CACHE, cache_key, _CONDITIONS_TTL)
    if cached is not None:
        return cached
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
    result = {"conditions": conditions}
    _cache_put(_CONDITIONS_CACHE, cache_key, result)
    return result


# Aspects rarely change, but they're fetched on every preflight AND every
# publish for the same category — cache them for a few hours so a bulk batch
# of N same-category items costs one Taxonomy call, not 2N.
_ASPECTS_TTL = 6 * 3600
_ASPECTS_CACHE: dict[str, tuple[float, dict]] = {}

# eBay's Taxonomy API marks these physical item-dimension aspects "required"
# for many categories, but its publish flow does NOT actually enforce them —
# gating our UI on them blocks listings eBay would happily accept. Treat them
# as recommended: still shown, never a hard publish blocker. If eBay ever does
# reject on one, the post-publish error surfaces it field-targeted anyway.
_NON_BLOCKING_ASPECTS = {
    "item height", "item length", "item width", "item depth",
    "item diameter", "item weight",
}


def item_aspects(category_id: str, marketplace_id: Optional[str] = None) -> dict:
    """The item specifics (aspects) eBay defines for a leaf category, so the UI
    can show exactly which fields are required vs recommended and whether each
    is free-text or a fixed set of values.

    Returns {"aspects": [{name, required, mode, values}]} where mode is
    "SELECTION_ONLY" (must pick from `values`) or "FREE_TEXT".
    """
    if not category_id:
        return {"aspects": []}
    cache_key = f"{category_id}|{marketplace_id or ''}"
    cached = _cache_get(_ASPECTS_CACHE, cache_key, _ASPECTS_TTL)
    if cached is not None:
        return cached
    tree_id = default_tree_id(marketplace_id)
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/commerce/taxonomy/v1/category_tree/{tree_id}"
        "/get_item_aspects_for_category",
        params={"category_id": category_id},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    aspects = []
    for a in data.get("aspects", []):
        constraint = a.get("aspectConstraint", {}) or {}
        mode = constraint.get("aspectMode", "FREE_TEXT")
        values = [v.get("localizedValue", "")
                  for v in (a.get("aspectValues") or []) if v.get("localizedValue")]
        # For SELECTION_ONLY aspects the value MUST come from this list, so keep
        # it whole — capping it drops valid choices (e.g. Country/Region of
        # Manufacture has ~250 entries and was truncating at "Dominica"). For
        # FREE_TEXT the values are only suggestions, so cap them to stay lean.
        capped = values if mode == "SELECTION_ONLY" else values[:60]
        name = a.get("localizedAspectName", "")
        required = bool(constraint.get("aspectRequired"))
        if name.strip().lower() in _NON_BLOCKING_ASPECTS:
            required = False  # eBay over-reports these; don't gate on them
        # SINGLE vs MULTI: most aspects accept only one value, and sending two
        # (e.g. a comma-joined string) makes eBay reject the whole publish with
        # "<Aspect> should contain only one value". Publishing uses this to
        # decide which aspects may hold several values.
        cardinality = constraint.get("itemToAspectCardinality") or "SINGLE"
        aspects.append({
            "name": name,
            "required": required,
            "mode": mode,
            "values": capped,
            "cardinality": cardinality,
            # Value-format constraints. eBay enforces these at publish with
            # errors like "Fabric weight must be greater than 0. Enter up to 1
            # number after the decimal" — so senders must too, or one chatty
            # value kills the whole listing.
            "data_type": (constraint.get("aspectDataType") or "STRING").upper(),
            "format": constraint.get("aspectFormat") or "",
            "max_length": int(constraint.get("aspectMaxLength") or 0),
        })
    # Required first, then by name, so the UI can show must-haves up top.
    aspects.sort(key=lambda x: (not x["required"], x["name"].lower()))
    result = {"aspects": aspects}
    _cache_put(_ASPECTS_CACHE, cache_key, result, bound=300)
    return result


# --- aspect-value validation -------------------------------------------------
# eBay validates item-specific VALUES against the aspect's constraints at
# publish time, and one bad value rejects the whole listing ("Fabric weight
# must be greater than 0. Enter up to 1 number after the decimal"). Everything
# below makes a value legal for its aspect — or says it can't be.

# Values that mean "no real answer". Legitimate in a free-text STRING aspect
# (eBay itself suggests "Does Not Apply" for MPN/UPC); poison in a NUMBER,
# DATE, or fixed-choice aspect.
NA_SENTINELS = {"does not apply", "not specified", "n/a", "na", "none",
                "unknown", "unbranded"}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


# Size tags print fractions and separators that eBay's canonical values spell
# differently — "16½" vs "16.5", "32 x 34" vs "32x34". Translate before
# normalizing so a correctly-read tag value doesn't get dropped as unmatched
# (a dropped Size is a publish blocker in most clothing categories).
_VALUE_SYNONYMS = {
    "½": ".5", "¼": ".25", "¾": ".75", "⅓": ".33", "⅔": ".66",
    " 1/2": ".5", " 1/4": ".25", " 3/4": ".75",
    "-1/2": ".5", "-1/4": ".25", "-3/4": ".75",
}


def _norm_value(s: str) -> str:
    s = s.lower()
    for k, v in _VALUE_SYNONYMS.items():
        s = s.replace(k, v)
    return "".join(ch for ch in s if ch.isalnum() or ch == ".")


def match_selection_value(value: str, allowed: list[str]) -> Optional[str]:
    """Map a value onto eBay's exact allowed value for a fixed-choice
    (SELECTION_ONLY) aspect. Returns the canonical allowed string or None.
    Conservative order: exact > normalized-equal > unambiguous containment
    (only when exactly one allowed value overlaps), so it never silently picks
    between rivals like Cotton vs Cotton Blend."""
    v = (value or "").strip()
    if not v:
        return None
    for a in allowed:
        if a.strip().lower() == v.lower():
            return a
    nv = _norm_value(v)
    if not nv:
        return None
    for a in allowed:
        if _norm_value(a) == nv:
            return a
    hits = [a for a in allowed
            if _norm_value(a) and (_norm_value(a) in nv or nv in _norm_value(a))]
    return hits[0] if len(hits) == 1 else None


def coerce_aspect_value(value: str, aspect: dict) -> Optional[str]:
    """The eBay-legal form of `value` for `aspect` (an item_aspects entry),
    or None when no legal form exists and the aspect should be dropped.

    - NUMBER: keep just the number ("14 oz denim" -> "14"), integers when the
      format says int; drop text/zero — that junk is a publish-killer.
    - DATE/year formats: keep a plausible 4-digit year ("1980s vintage" ->
      "1980"); drop otherwise.
    - SELECTION_ONLY: map onto the allowed list; drop when nothing matches.
    - N/A sentinels survive only where they're legal (free-text STRING).
    - Length: clip to the aspect's own max (eBay rejects over-long values).
    """
    v = (value or "").strip()
    if not v:
        return None
    data_type = (aspect.get("data_type") or "STRING").upper()
    fmt = (aspect.get("format") or "").lower()
    is_year = data_type == "DATE" or "yyyy" in fmt
    if data_type == "NUMBER" or is_year:
        if v.lower() in NA_SENTINELS:
            return None
        if is_year:
            # Digit-boundary lookarounds, not \b: "1980s vintage" must yield
            # 1980 (the "s" is a word character, so \b never matches there).
            m = re.search(r"(?<!\d)(1[6-9]\d{2}|20\d{2})(?!\d)", v)
            return m.group(1) if m else None
        m = _NUMBER_RE.search(v)
        if not m or float(m.group()) <= 0:
            return None
        number = float(m.group())
        # "int32"-style formats take whole numbers; everything else follows
        # eBay's "up to 1 number after the decimal".
        v = str(int(number)) if "int" in fmt or number == int(number) \
            else str(round(number, 1))
    elif aspect.get("mode") == "SELECTION_ONLY" and aspect.get("values"):
        matched = match_selection_value(v, aspect["values"])
        if matched is None:
            return None
        v = matched
    max_len = int(aspect.get("max_length") or 0) or 65
    return v[:max_len]


def sanitize_specifics(listing) -> None:
    """Rewrite listing.item_specifics into publish-safe form, in place:
    canonical aspect names for the category (case drift and the "Height" vs
    "Item Height" alias both reject publishes), values coerced to each
    aspect's constraints via coerce_aspect_value, one value per SINGLE-
    cardinality aspect, and unfixable values dropped — a busy specific must
    never sink the listing. Best-effort: without a category (or with the
    Taxonomy API down) the specifics pass through untouched."""
    if not listing.category_id:
        return
    try:
        aspects = item_aspects(listing.category_id).get("aspects", [])
    except Exception as exc:  # noqa: BLE001 - sanitizing is best-effort
        log.info("sanitize_specifics skipped (cat=%s): %s", listing.category_id, exc)
        return
    by_key: dict[str, dict] = {}
    for a in aspects:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        by_key[name.lower()] = a
        if name.lower().startswith("item "):
            by_key.setdefault(name.lower()[5:], a)
        else:
            by_key.setdefault(f"item {name.lower()}", a)
    cleaned, seen_single = [], set()
    for spec in listing.item_specifics:
        name = (spec.name or "").strip()
        value = (spec.value or "").strip()
        if not name or not value:
            continue
        aspect = by_key.get(name.lower())
        if aspect is None:
            cleaned.append(spec)  # seller's own free-form specific — keep as-is
            continue
        legal = coerce_aspect_value(value, aspect)
        if legal is None:
            log.info("specifics: dropped %r=%r (doesn't fit the aspect's "
                     "constraints)", name, value)
            continue
        canonical = aspect["name"]
        if (aspect.get("cardinality") or "SINGLE") != "MULTI":
            if canonical.lower() in seen_single:
                continue
            seen_single.add(canonical.lower())
        spec.name, spec.value = canonical, legal
        cleaned.append(spec)
    listing.item_specifics = cleaned
