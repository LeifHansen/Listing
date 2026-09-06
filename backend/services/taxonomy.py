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
from ..models import ItemSpecific
from . import barcodes

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


# eBay conditionId -> the condition enum this app stores on Listing.condition.
# The single source of truth for that direction: ebay_trading imports it for
# import/GetItem too. A second copy there disagreed on 2750, so a "Like New"
# listing imported as USED_EXCELLENT and every revise pushed 3000 back to eBay.
CONDITION_ID_TO_ENUM = {
    "1000": "NEW", "1500": "NEW_OTHER", "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED", "2010": "CERTIFIED_REFURBISHED",
    "2020": "SELLER_REFURBISHED", "2030": "SELLER_REFURBISHED",
    "2500": "SELLER_REFURBISHED", "2750": "LIKE_NEW",
    # 2990/3000/3010 are the pre-owned grades eBay rolled out across the
    # Pre-loved Apparel categories (Excellent / Good / Fair). Without them
    # here, `item_conditions` DROPPED them from a clothing category's allowed
    # list, so the editor offered a used seller three "New" options and one
    # "Used" — and any grade the AI picked below that came back from eBay as
    # error 25021.
    "2990": "PRE_OWNED_EXCELLENT",
    "3000": "USED_EXCELLENT",  # "Used" everywhere else, "Pre-owned - Good" in apparel
    "3010": "PRE_OWNED_FAIR",
    "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}

# How much wear each grade promises a buyer, all on ONE scale so grades from
# different category families can be compared. eBay does not offer the same
# ladder everywhere: 4000/5000/6000 (Very Good / Good / Acceptable) exist only
# in media categories, 2990/3010 only in apparel, and most of the rest of the
# site offers a bare "Used" (3000). A number per grade is what lets a
# condition a category refuses be replaced with the CLOSEST one it allows,
# instead of the first one in eBay's list — which is "New", and putting a worn
# t-shirt up as New is worse than the publish error it replaced.
CONDITION_QUALITY = {
    "NEW": 100,
    "NEW_OTHER": 90,
    "NEW_WITH_DEFECTS": 80,
    "CERTIFIED_REFURBISHED": 75,
    "SELLER_REFURBISHED": 70,
    "LIKE_NEW": 65,
    "PRE_OWNED_EXCELLENT": 60,
    "USED_EXCELLENT": 55,
    "USED_VERY_GOOD": 48,
    "USED_GOOD": 40,
    "PRE_OWNED_FAIR": 20,
    "USED_ACCEPTABLE": 20,
    "FOR_PARTS_OR_NOT_WORKING": 0,
}

# Which side of the new/used line each grade sits on. A substitution never
# crosses it: a used item silently relabelled "New" is a buyer complaint and a
# return, and a new one relabelled "Used" is money off the price. When the
# category allows nothing in the item's own family, there is no honest
# substitute and the answer is "we can't fit this" — which the preflight
# reports rather than the code guessing.
CONDITION_FAMILY = {
    "NEW": "new", "NEW_OTHER": "new", "NEW_WITH_DEFECTS": "new",
    "CERTIFIED_REFURBISHED": "refurbished", "SELLER_REFURBISHED": "refurbished",
    "LIKE_NEW": "used", "PRE_OWNED_EXCELLENT": "used", "USED_EXCELLENT": "used",
    "USED_VERY_GOOD": "used", "USED_GOOD": "used", "PRE_OWNED_FAIR": "used",
    "USED_ACCEPTABLE": "used", "FOR_PARTS_OR_NOT_WORKING": "used",
}


def nearest_allowed_condition(current: str, allowed) -> Optional[str]:
    """The condition to use when `current` isn't one the category accepts.

    Returns `current` when it is already allowed, the closest allowed grade in
    the same family when it isn't, and None when the category allows nothing
    in that family — a "new only" category has no honest home for a used item,
    and picking one anyway is how a listing goes live lying about what it is.

    Ties go to the LOWER grade: understating wear costs a few dollars,
    overstating it costs the sale and the feedback.
    """
    cur = (current or "").strip().upper()
    allowed = [str(c or "").strip().upper() for c in (allowed or [])]
    allowed = [c for c in allowed if c]
    if not allowed or not cur or cur in allowed:
        return cur or None
    family = CONDITION_FAMILY.get(cur)
    want = CONDITION_QUALITY.get(cur)
    if family is None or want is None:
        return None
    pool = [c for c in allowed
            if CONDITION_FAMILY.get(c) == family and c in CONDITION_QUALITY]
    if not pool:
        return None
    # abs(distance) first, then the lower grade — sorting on the grade itself
    # ascending makes the second key do exactly that.
    return min(pool, key=lambda c: (abs(CONDITION_QUALITY[c] - want),
                                    CONDITION_QUALITY[c]))


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
    unknown = []
    for pol in policies:
        for c in pol.get("itemConditions", []) or []:
            cid = str(c.get("conditionId", ""))
            enum = CONDITION_ID_TO_ENUM.get(cid)
            if not enum:
                # A grade eBay has and this app cannot name. Said out loud
                # rather than dropped in silence: an id missing from the map
                # shrinks the seller's choices and can make a condition that
                # IS allowed look forbidden, and the only way anyone finds out
                # is a log line naming the id to add.
                if cid:
                    unknown.append(cid)
                continue
            if enum in seen:
                continue
            seen.add(enum)
            conditions.append({
                "enum": enum,
                "id": cid,
                "label": c.get("conditionDescription", "") or enum.replace("_", " ").title(),
            })
    if unknown:
        log.info("item-conditions(cat=%s): eBay offers condition id(s) %s "
                 "that CONDITION_ID_TO_ENUM doesn't name", category_id,
                 ", ".join(sorted(set(unknown))))
    result = {"conditions": conditions}
    _cache_put(_CONDITIONS_CACHE, cache_key, result)
    return result


def allowed_condition_enums(category_id: str, access_token: Optional[str] = None,
                            marketplace_id: Optional[str] = None) -> list[str]:
    """Just the enums from `item_conditions` — what a caller fitting a stored
    condition to a category needs, without the labels the dropdown wants."""
    got = item_conditions(category_id, access_token=access_token,
                          marketplace_id=marketplace_id)
    return [c["enum"] for c in got.get("conditions", []) if c.get("enum")]


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
        # Which OTHER aspect's answers each of those values is legal beside.
        #
        # eBay does not validate Size and Size Type as two independent boxes:
        # it publishes, per value, the values of a controlling aspect that
        # value may be paired with, and refuses the rest at publish time with
        # "“Regular” is not a valid Size Type for the Size “33”. Select a
        # compatible Size Type and Size combination." — after the photos have
        # uploaded, with no listing. Dropping this on the way in is what left
        # the app guessing at a pairing eBay had already spelled out.
        pairs = _value_constraints(a.get("aspectValues") or [])
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
            # {value: {controlling aspect: [values it may be paired with]}} —
            # empty for the aspects eBay places no such restriction on, which
            # is most of them.
            "pairs_with": pairs,
        })
    # Required first, then by name, so the UI can show must-haves up top.
    aspects.sort(key=lambda x: (not x["required"], x["name"].lower()))
    result = {"aspects": aspects}
    _cache_put(_ASPECTS_CACHE, cache_key, result, bound=300)
    return result


def cached_item_aspects(category_id: str,
                        marketplace_id: Optional[str] = None) -> Optional[dict]:
    """`item_aspects` for a category ALREADY in the cache, else None — never a
    live call.

    The Taxonomy API runs on one application-wide allowance shared by every
    seller (see test_taxonomy_quota_guard), so a screen that wants aspects for
    a whole store's worth of categories cannot simply ask for them. This lets
    such a caller take what is already paid for and spend its own small budget
    of live lookups deliberately on the rest.
    """
    if not category_id:
        return None
    return _cache_get(_ASPECTS_CACHE, f"{category_id}|{marketplace_id or ''}",
                      _ASPECTS_TTL)


def _value_constraints(aspect_values: list[dict]) -> dict:
    """eBay's valueConstraints, as {value: {controlling aspect: [values]}}.

    Kept only where eBay actually names a controlling aspect and values for
    it; an unconstrained value carries no entry at all, so "not in here" reads
    as "eBay places no restriction on this", never as "nothing is allowed".
    """
    out: dict[str, dict[str, list[str]]] = {}
    for v in aspect_values:
        label = (v.get("localizedValue") or "").strip()
        if not label:
            continue
        for c in (v.get("valueConstraints") or []):
            name = (c.get("applicableForLocalizedAspectName") or "").strip()
            allowed = [str(x).strip() for x in
                       (c.get("applicableForLocalizedAspectValues") or [])
                       if str(x).strip()]
            if name and allowed:
                out.setdefault(label, {}).setdefault(name, []).extend(allowed)
    return out


def compatible_values(aspects: list[dict], aspect_name: str,
                      value: str) -> dict:
    """{controlling aspect: [values]} this aspect's `value` may be paired with.

    Empty when eBay named no constraint for it — which is the common case, and
    means every answer is legal as far as the pairing rules go.
    """
    want = (aspect_name or "").strip().lower()
    label = (value or "").strip().lower()
    for a in aspects or []:
        if (a.get("name") or "").strip().lower() != want:
            continue
        for known, pairs in (a.get("pairs_with") or {}).items():
            if known.strip().lower() == label:
                return {k: list(dict.fromkeys(v)) for k, v in pairs.items()}
    return {}


def fit_paired_aspects(listing, aspects: list[dict]) -> list[tuple]:
    """Make every item specific legal beside the ones it is paired with.

    eBay's own answer to "which Size Type goes with Size 33" is in the
    category's aspect list; this applies it. For each specific the seller (or
    the AI) has answered, the controlling aspect eBay names for that answer is
    set to a value eBay lists as compatible — replacing one that isn't, and
    filling one that is blank. A required Size Type that nothing filled is
    therefore answered from the Size rather than defaulted to "Regular" and
    refused.

    Returns [(aspect, was, now)] for what it changed, so a caller can log or
    say what it did. Never raises, never invents a value eBay did not list,
    and never touches an aspect already holding a compatible answer.
    """
    changed: list[tuple[str, str, str]] = []
    if not aspects:
        return changed
    specifics = list(getattr(listing, "item_specifics", None) or [])
    for row in list(specifics):
        name = (getattr(row, "name", "") or "").strip()
        value = (getattr(row, "value", "") or "").strip()
        if not name or not value:
            continue
        for controlling, allowed in compatible_values(aspects, name, value).items():
            if not allowed:
                continue
            current = ""
            holder = None
            for other in specifics:
                if (getattr(other, "name", "") or "").strip().lower() \
                        == controlling.strip().lower():
                    val = (getattr(other, "value", "") or "").strip()
                    if val:
                        current, holder = val, other
                        break
                    holder = holder or other
            if current and any(current.lower() == a.lower() for a in allowed):
                continue  # already a pairing eBay accepts
            # One compatible answer is eBay's answer. Several means the Size
            # alone does not decide it, and picking one at random would be the
            # same guess that put "Regular" beside a 33 — so an answer the
            # seller already gave is left for them to correct, and only a
            # blank is filled, with the first value eBay offers.
            if len(allowed) > 1 and current:
                continue
            pick = allowed[0]
            if holder is not None:
                holder.value = pick
            else:
                specifics.append(type(row)(name=controlling, value=pick))
            changed.append((controlling, current, pick))
    if changed:
        listing.item_specifics = specifics
    return changed


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

# Waist-by-inseam, the way a jeans tag prints it versus the way eBay spells
# it. A tag says "W33 L34"; eBay's Size list says "33x34" — and the two never
# matched, so the value went out verbatim and came back as "\"W33 L34\" is not
# a valid value for Size. Select a value from the available options", which is
# a rejected listing, not a warning.
#
# Every pattern must match the WHOLE value. A pair found loose inside a longer
# string is not a size: "50/50 Cotton Poly" is a blend, and rewriting it to
# "50x50" would corrupt a Material that had nothing wrong with it.
_SIZE_PAIR_RES = (
    # "W33 L34", "W33L34", "W33xL34"
    re.compile(r"W\s*(\d{1,2})\s*[X/]?\s*L\s*(\d{1,2})"),
    # "33W 34L", "33W x 34L"
    re.compile(r"(\d{1,2})\s*W\s*[X/]?\s*(\d{1,2})\s*L"),
    # "Waist 33 Inseam 34" / "Waist 33 Length 34"
    re.compile(r"WAIST\s*(\d{1,2})\s*(?:INSEAM|LENGTH|L)?\s*(\d{1,2})"),
    # "33x34", "33 x 34", "33/34"
    re.compile(r"(\d{1,2})\s*[X/]\s*(\d{1,2})"),
)

# Units a tag tacks on the end and eBay's value never carries.
_SIZE_UNIT_RE = re.compile(r"\s*(?:IN|INCH|INCHES|\"|”)\.?$")


def size_pair(s: str) -> Optional[tuple[str, str]]:
    """(waist, inseam) when `s` is a waist-by-inseam size in ANY of the ways a
    tag or a marketplace spells one, else None. Whole-value match only — see
    the note above _SIZE_PAIR_RES."""
    v = _SIZE_UNIT_RE.sub("", (s or "").strip().upper())
    if not v:
        return None
    for pattern in _SIZE_PAIR_RES:
        m = pattern.fullmatch(v)
        if m:
            return m.group(1), m.group(2)
    return None


def _norm_value(s: str) -> str:
    # A waist-by-inseam size collapses to one spelling FIRST, so that both
    # sides of a comparison reach it from wherever they started: the tag's
    # "W33 L34" and eBay's "33x34" are the same size and now normalize alike.
    # (Where eBay's Size is the WAIST ALONE — which is the usual shape for
    # men's bottoms — split_size_pair below has already reduced the value to
    # the waist before anything gets here.)
    pair = size_pair(s)
    if pair:
        return "x".join(pair)
    s = s.lower()
    for k, v in _VALUE_SYNONYMS.items():
        s = s.replace(k, v)
    return "".join(ch for ch in s if ch.isalnum() or ch == ".")


def match_selection_value(value: str, allowed: list[str],
                          allow_partial: bool = True) -> Optional[str]:
    """Map a value onto eBay's exact allowed value for a fixed-choice
    (SELECTION_ONLY) aspect. Returns the canonical allowed string or None.
    Conservative order: exact > normalized-equal > unambiguous containment
    (only when exactly one allowed value overlaps), so it never silently picks
    between rivals like Cotton vs Cotton Blend.

    `allow_partial=False` stops before that last step, leaving only the two
    steps that mean "this IS that value, spelled differently". Containment is
    the right call for a fixed-choice aspect, where the alternative to an
    imperfect match is dropping the value entirely — but on a free-text
    aspect, where the value survives either way, it is pure loss: the
    suggestion list holds "Nike", the seller wrote "Nike Air", and one
    contains the other. Nothing is gained by trading what they wrote for
    something shorter.
    """
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
    if not allow_partial:
        return None
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
    - Any OTHER aspect that still ships a value list: map onto it where the
      value fits, and keep the seller's own wording where it doesn't.
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
    elif aspect.get("values"):
        fixed = aspect.get("mode") == "SELECTION_ONLY"
        matched = match_selection_value(v, aspect["values"],
                                        allow_partial=fixed)
        if matched is not None:
            v = matched
        elif fixed:
            # A fixed-choice aspect takes one of eBay's strings or nothing.
            return None
        # Otherwise the value list is only a set of SUGGESTIONS, and a value
        # that isn't on it can still be perfectly legal — a brand eBay hasn't
        # heard of, a seller's own wording. Canonicalizing to eBay's spelling
        # when we CAN is still worth doing, though, and this used to be the
        # SELECTION_ONLY branch alone: an aspect eBay reported as free text
        # was never compared against its own list at all. eBay's publish
        # validation does not always agree with the mode its Taxonomy API
        # reports — a jeans "Size" came back FREE_TEXT and then rejected the
        # listing with "Select a value from the available options" — so the
        # tag's "W33 L34" went out verbatim against a list that spelled the
        # same size "33x34". Matching here is what turns that into a listing.
    max_len = int(aspect.get("max_length") or 0) or 65
    return v[:max_len]


# --- the Size aspect ---------------------------------------------------------
# eBay's Size for men's bottoms is the WAIST ALONE: "33". The inseam is its
# own aspect ("Inseam"), and a fit word like "Regular" is a Size Type, not a
# size. Send anything else and the listing does not go live —
#
#   "W33 L34" is not a valid value for Size. Select a value from the
#   available options.
#   "Regular" is not a valid value for Size. Select a value from the
#   available options.
#
# — after the photos have uploaded. Both came straight off the draft: the tag
# prints "W33 L34" and the vision pass reads it correctly, and "Regular" is
# the word on the label of nearly every pair of straight-leg jeans made.
# Neither is wrong about the item; both are in the wrong box.
#
# So the Size field is put right before anything is sent, and what is taken
# out of it is put where it belongs rather than thrown away.

SIZE_ASPECT = "size"
INSEAM_ASPECTS = ("inseam", "inside leg", "leg length")
# Where a fit word belongs instead. In eBay's order of preference.
FIT_ASPECTS = ("size type", "fit", "style")

# Words that describe the CUT, not the measurement. Every one of these is a
# legal value somewhere on a jeans listing — just never under Size.
FIT_WORDS = {
    "regular", "standard", "classic", "slim", "slim fit", "skinny",
    "relaxed", "relaxed fit", "loose", "straight", "straight leg",
    "bootcut", "boot cut", "tapered", "athletic", "carpenter",
    "big", "tall", "big & tall", "big and tall", "husky",
    "plus", "petite", "juniors", "maternity", "one size", "regular size",
}


def is_fit_word(value: str) -> bool:
    """Whether a value describes the cut rather than the measurement."""
    return (value or "").strip().lower() in FIT_WORDS


def _aspect_named(aspects: list[dict], *names: str) -> Optional[dict]:
    """The first of `names` the category actually offers, as its aspect."""
    by_key = {(a.get("name") or "").strip().lower(): a for a in (aspects or [])}
    for name in names:
        if name in by_key:
            return by_key[name]
    return None


def size_for_aspect(value: str, aspect: Optional[dict]) -> tuple[str, str]:
    """(what belongs in Size, what belongs in Inseam) for a raw size value.

    A waist-by-inseam value is split, because eBay's Size for bottoms is the
    waist on its own — "W33 L34" becomes ("33", "34"). The combined spelling
    is used only where the category's own list actually carries it (some
    categories do list "33x34"), which is checked against `aspect` and never
    assumed. Anything that isn't a pair comes back unchanged.
    """
    pair = size_pair(value)
    if not pair:
        return (value or "").strip(), ""
    waist, inseam = pair
    values = [v for v in ((aspect or {}).get("values") or []) if v]
    if values:
        # The waist alone is what eBay wants and what it lists; only fall back
        # to the combined form where THIS category spells it that way.
        for candidate in (waist, f"{waist}x{inseam}"):
            matched = match_selection_value(candidate, values,
                                            allow_partial=False)
            if matched:
                return matched, inseam
        return waist, inseam
    return waist, inseam


def fix_size_specifics(listing, aspects: Optional[list[dict]] = None) -> list[str]:
    """Put the Size aspect right, in place. Returns what it did, for the log.

    Two corrections, both of which were rejected listings:

    - A waist-by-inseam size ("W33 L34") is reduced to the waist eBay asks
      for, and the inseam is written to the Inseam aspect rather than
      discarded — the measurement is real, it just belongs one box over.
    - A fit word ("Regular", "Slim", "Bootcut") is moved out of Size and into
      Size Type or Fit where the category offers one and it is still empty.
      Size is then left blank, which the preflight reports as a required
      specific to fill — a question the seller can answer in a second, rather
      than a rejection they get after the upload.

    Best-effort: no aspects, no Size aspect, or nothing to fix all leave the
    listing untouched.
    """
    specifics = list(getattr(listing, "item_specifics", None) or [])
    if not specifics:
        return []
    if aspects is None:
        cid = (getattr(listing, "category_id", "") or "").strip()
        if not cid:
            return []
        try:
            aspects = item_aspects(cid).get("aspects", [])
        except Exception as exc:  # noqa: BLE001 - the fix is best-effort
            log.info("size fix skipped (cat=%s): %s", cid, exc)
            return []
    size_aspect = _aspect_named(aspects, SIZE_ASPECT)
    if size_aspect is None:
        return []
    row = next((s for s in specifics
                if (s.name or "").strip().lower() == SIZE_ASPECT
                and (s.value or "").strip()), None)
    if row is None:
        return []

    raw = row.value.strip()
    done: list[str] = []

    # A cut, not a measurement. Move it to the first aspect where it is
    # actually LEGAL — not merely the first one the category happens to
    # offer. "Regular" is a Size Type and "Slim" is a Fit; stopping at
    # whichever aspect came first threw one of them away.
    if is_fit_word(raw):
        moved_to = ""
        for name in FIT_ASPECTS:
            target = _aspect_named(aspects, name)
            if target is None:
                continue
            legal = coerce_aspect_value(raw, target)
            if not legal:
                continue
            canonical = (target.get("name") or "").strip()
            held = next((s for s in specifics
                         if (s.name or "").strip().lower() == canonical.lower()),
                        None)
            if held is not None and (held.value or "").strip():
                continue  # already answered — don't overwrite the seller
            if held is None:
                listing.item_specifics = [
                    *listing.item_specifics,
                    ItemSpecific(name=canonical, value=legal,
                                 confidence=row.confidence or "medium"),
                ]
            else:
                held.value = legal
            moved_to = canonical
            break
        row.value = ""
        done.append(f"Size {raw!r} is a fit, not a measurement — "
                    + (f"moved to {moved_to}" if moved_to else "cleared"))
        log.info("size fix: %s", done[-1])
        return done

    # The L eBay wants on an extra-large size, before the waist/inseam split
    # below: a bare "4X" is not a measurement pair and never will be.
    spelled = xl_size_spelling(raw, size_aspect, aspects)
    if spelled and spelled != raw:
        row.value = spelled
        done.append(f"Size {raw!r} -> {spelled!r} (eBay spells it with the L)")
        raw = spelled

    size, inseam = size_for_aspect(raw, size_aspect)
    if size != raw:
        row.value = size
        done.append(f"Size {raw!r} -> {size!r} (eBay's Size is the waist)")
    if inseam:
        target = _aspect_named(aspects, *INSEAM_ASPECTS)
        if target is not None:
            name = (target.get("name") or "").strip()
            held = next((s for s in specifics
                         if (s.name or "").strip().lower() == name.lower()), None)
            legal = coerce_aspect_value(inseam, target)
            if legal and (held is None or not (held.value or "").strip()):
                if held is None:
                    listing.item_specifics = [
                        *listing.item_specifics,
                        ItemSpecific(name=name, value=legal,
                                     confidence=row.confidence or "medium"),
                    ]
                else:
                    held.value = legal
                done.append(f"{name} {legal!r} (from the same tag)")
    for line in done:
        log.info("size fix: %s", line)
    return done


# --- size type ---------------------------------------------------------------
# eBay files a men's garment sized past XL under Size Type "Big & Tall", and
# refuses the pairing it knows is wrong: Size "XXL" with Size Type "Regular"
# comes back as an item-specifics error and no listing at all. Nothing here
# ever asked, because the two values arrive from different places — Size is
# read off the tag, Size Type off whatever the category defaults to — so they
# disagreed on every big shirt that went out.
#
# The rule below is deliberately narrow. It fires only where eBay's OWN aspect
# list for the category offers a Big & Tall value, which is what tells a men's
# category from a women's one: there XXL means Size Type "Plus", "Regular" is
# perfectly legal, and stamping "Big & Tall" on it would be the same mistake
# in the other direction.

SIZE_TYPE_ASPECT = "size type"

# How the value gets spelled, best first. eBay says "Big & Tall"; the rest are
# here so a category that punctuates it differently still matches.
BIG_AND_TALL_VALUES = ("Big & Tall", "Big and Tall", "Big-Tall", "Big/Tall")

# The Size Types this rule may overwrite. "Big", "Tall" and "Big & Tall" are
# all answers a seller can honestly mean on a 2XL shirt, so they stand as
# entered; "Regular" is the one eBay refuses, and a blank has nothing to lose.
_OVERRIDABLE_SIZE_TYPES = {"", "regular", "standard", "regular size"}

# Every way a tag spells a size past XL: "2XL", "XXL", "XXX-Large", "4X",
# "3XLT". The multiplier is the digit, or the number of X's. Both patterns
# refuse to start or end mid-word, which is what keeps "XXS", "MAXX" and the
# waist-by-inseam form "32 X 34" out of a rule about extra-larges.
_DIGIT_XL_RE = re.compile(r"(?<![\w.])(\d)\s*X(?:\s*-?\s*L(?:ARGE)?)?T?(?!\w)(?!\s*\d)")
_REPEAT_XL_RE = re.compile(r"(?<!\w)(X{2,6})(?:\s*-?\s*L(?:ARGE)?)?T?(?!\w)")


def size_multiplier(value: str) -> int:
    """How many X's a size carries: 2 for XXL and 2XL, 3 for XXXL and 3XL, 0
    for XL and everything below it (and for sizes that aren't X-sizes at all).

    Reads the LARGEST answer in the value, because tags and eBay values pair
    the spellings — "XXL/2XL" is a 2, "XXL (3XL)" a 3.
    """
    v = (value or "").strip().upper()
    if not v:
        return 0
    best = 0
    for m in _DIGIT_XL_RE.finditer(v):
        best = max(best, int(m.group(1)))
    for m in _REPEAT_XL_RE.finditer(v):
        best = max(best, len(m.group(1)))
    return best


def is_big_and_tall_size(value: str) -> bool:
    """True when the size is XXL or larger — the point past which eBay wants
    Size Type "Big & Tall" rather than "Regular" on a men's garment."""
    return size_multiplier(value) >= 2


def _is_size_aspect(name: str) -> bool:
    """Whether an aspect name holds the item's SIZE ("Size", "Men's Size",
    "Shirt Size", "Size (Men's)") as opposed to describing it ("Size Type").
    Neighbours like "Neck Size" are in — they never carry an X-size, so
    reading them costs nothing and missing the real one costs the listing."""
    n = (name or "").strip().lower()
    return n != SIZE_TYPE_ASPECT and bool(re.search(r"\bsize\b", n))


def offered_big_and_tall(aspect: dict) -> str:
    """The category's own spelling of Big & Tall for a Size Type `aspect`, or
    "" when it doesn't offer one.

    That absence is the men's-vs-women's test this rule leans on: eBay lists
    Big & Tall in men's apparel and Plus / Petite / Maternity in women's, so a
    category with no Big & Tall value is one where this rule has no business
    firing. "" is also the answer when the category offers "Big" and "Tall"
    only separately — there is no way to pick between them honestly.
    """
    values = [v for v in (aspect.get("values") or []) if v]
    if not values:
        return ""
    for spelling in BIG_AND_TALL_VALUES:
        matched = match_selection_value(spelling, values)
        if matched:
            return matched
    return ""


# eBay spells a men's extra-large size with the L on the end. Its Size list
# for a big & tall category carries "4XL" and "5XL" and no bare "4X" / "5X",
# so a tag read as "Mens 4X" goes out as a value eBay refuses and the whole
# listing comes back an item-specifics error.
#
# The bare form is not wrong everywhere, which is why this is not a blanket
# rewrite: women's PLUS sizes are spelled exactly that way — "1X", "2X" and
# "3X" are eBay's own values there. So the category decides. Where it
# publishes a Size list, a value that list already carries is left alone and
# anything else is rewritten to whichever spelling the list does carry ("2X"
# -> "XXL" in a category that says it that way). With no list to consult, the
# rewrite fires only where Size Type offers Big & Tall — the same men's test
# the rule below leans on.
_BARE_X_SIZE_RE = re.compile(r"^(\d)\s*X(T)?$", re.I)


def xl_size_spelling(value: str, size_aspect: Optional[dict],
                     aspects: Optional[list[dict]] = None) -> str:
    """eBay's spelling for an X-size written without the L ("4X" -> "4XL"), or
    "" when the value isn't one, is already legal here, or is a size this
    category genuinely spells bare."""
    m = _BARE_X_SIZE_RE.match((value or "").strip())
    if not m:
        return ""
    count, tall = int(m.group(1)), bool(m.group(2))
    # "1X" is a women's plus size in its own right, not an XL missing its L.
    if count < 2:
        return ""
    spellings = [f"{count}XL", "X" * count + "L"]
    if tall:
        spellings = [f"{s}T" for s in spellings] + spellings
    values = [v for v in ((size_aspect or {}).get("values") or []) if v]
    if values:
        if match_selection_value(value, values, allow_partial=False):
            return ""      # the category lists it exactly as written
        for spelling in spellings:
            matched = match_selection_value(spelling, values, allow_partial=False)
            if matched:
                return matched
        return ""          # not a spelling this category knows — leave it be
    size_type = _aspect_named(aspects or [], SIZE_TYPE_ASPECT)
    if size_type is None or not offered_big_and_tall(size_type):
        return ""
    return spellings[0]


def apply_big_and_tall(listing, aspects: Optional[list[dict]] = None) -> str:
    """Default Size Type to Big & Tall on a listing whose size is XXL or
    larger, in place. Returns the value written, or "" when nothing changed.

    eBay rejects "Size: XXL" + "Size Type: Regular" outright, so this runs on
    the draft (the seller sees the answer and can still change it) and again
    just before publish (where it is the difference between a listing and an
    error). Only a blank or "Regular" is overwritten — a seller who picked
    "Big" or "Tall" has already answered the question.

    `aspects` is the category's aspect list when the caller already has it;
    otherwise it is looked up. Best-effort throughout: no category, no Size
    Type aspect, or a Taxonomy call that fails all mean the listing passes
    through untouched.
    """
    specifics = list(getattr(listing, "item_specifics", None) or [])
    if not specifics:
        return ""
    if aspects is None:
        cid = (getattr(listing, "category_id", "") or "").strip()
        if not cid:
            return ""
        try:
            aspects = item_aspects(cid).get("aspects", [])
        except Exception as exc:  # noqa: BLE001 - the rule is best-effort
            log.info("size type skipped (cat=%s): %s", cid, exc)
            return ""
    aspect = next((a for a in (aspects or [])
                   if (a.get("name") or "").strip().lower() == SIZE_TYPE_ASPECT),
                  None)
    if aspect is None:
        return ""
    big_and_tall = offered_big_and_tall(aspect)
    if not big_and_tall:
        return ""
    size = next((s.value for s in specifics
                 if _is_size_aspect(s.name) and is_big_and_tall_size(s.value)), "")
    if not size:
        return ""
    canonical = (aspect.get("name") or "Size Type").strip()
    row = next((s for s in specifics
                if (s.name or "").strip().lower() == SIZE_TYPE_ASPECT), None)
    if row is not None:
        if (row.value or "").strip().lower() not in _OVERRIDABLE_SIZE_TYPES:
            return ""
        row.name, row.value = canonical, big_and_tall
        # "high": this isn't a guess about the item, it's what the size the
        # seller can read on the tag means in eBay's own vocabulary.
        row.confidence = "high"
    else:
        listing.item_specifics = [
            *specifics,
            ItemSpecific(name=canonical, value=big_and_tall, confidence="high"),
        ]
    log.info("size type: %r is XXL or larger — %s is %r, not Regular "
             "(eBay rejects that pairing)", size, canonical, big_and_tall)
    return big_and_tall


def sanitize_specifics(listing) -> None:
    """Rewrite listing.item_specifics into publish-safe form, in place:
    canonical aspect names for the category (case drift and the "Height" vs
    "Item Height" alias both reject publishes), values coerced to each
    aspect's constraints via coerce_aspect_value, one value per SINGLE-
    cardinality aspect, and unfixable values dropped — a busy specific must
    never sink the listing. Then the size-type rule (apply_big_and_tall), on
    the cleaned values and last, because this is the final thing that touches
    a listing before eBay does. Best-effort: without a category (or with the
    Taxonomy API down) the specifics pass through untouched."""
    if not listing.category_id:
        return
    try:
        aspects = item_aspects(listing.category_id).get("aspects", [])
    except Exception as exc:  # noqa: BLE001 - sanitizing is best-effort
        log.info("sanitize_specifics skipped (cat=%s): %s", listing.category_id, exc)
        return
    # BEFORE the per-aspect pass: get the Size field into the shape eBay
    # asks for (waist alone, no fit words) so the coercion below is validating
    # a value that can actually match, and the inseam it splits off lands in
    # its own aspect and gets validated too.
    fix_size_specifics(listing, aspects)
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
        # A product identifier the AI read but that fails its own check digit
        # is a misread, and this is the last place before eBay sees it. eBay
        # matches a UPC against its CATALOGUE, so a wrong one does not fail
        # loudly — it succeeds, and puts another company's product page,
        # photos and price history behind this listing. `confidence` is what
        # keeps this off the seller's own work: "" means they entered or
        # confirmed the value, and their number stands even when it looks
        # wrong to us.
        if (spec.confidence and is_identifier_aspect(name)
                and barcodes.looks_like_a_code(value)
                and not barcodes.verified(value)):
            log.info("specifics: dropped %r=%r (the check digit says that is "
                     "not a valid code)", name, value)
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
    # Last, on the cleaned values: a men's top sized XXL or larger goes out as
    # Size Type "Big & Tall". eBay rejects the whole listing when that one says
    # "Regular", and this is the last place before it is sent.
    apply_big_and_tall(listing, aspects)


# --- what the AI fill can still be asked for --------------------------------

# Aspects whose value is an IDENTIFIER: a code printed on the item, its box or
# its plate. There is no such thing as inferring one — a UPC is read off the
# barcode or it is wrong, and a wrong one puts someone else's product on the
# listing. Every pass that fills specifics is told never to invent these; the
# COVERAGE pass (claude_ai.fill_missing_aspects), whose whole licence is "a
# defensible inference beats a blank", must not even be shown them, because
# that instruction and an empty UPC box in the same prompt is how a model
# talks itself into twelve digits.
#
# Matched as whole words against the aspect name, plus anything ending in
# "Number" — which catches Model Number, Style Number, Card Number and the
# rest of the family without listing them, and leaves the plain names beside
# them (Model, Style, Card Name) alone.
_IDENTIFIER_WORDS = {
    "upc", "ean", "isbn", "gtin", "mpn", "sku", "asin", "issn",
    "serial", "barcode", "vin",
}


def is_identifier_aspect(name: str) -> bool:
    """Whether `name` is an aspect nothing may fill by inference."""
    text = (name or "").strip().lower()
    if not text:
        return False
    words = re.findall(r"[a-z0-9]+", text)
    if not words:
        return False
    return bool(_IDENTIFIER_WORDS & set(words)) or words[-1] == "number"


def _aspect_keys(name: str) -> set[str]:
    """The names a listing might legitimately be holding this aspect under.

    eBay publishes "Item Height" and sellers (and the identify pass) write
    "Height"; sanitize_specifics already treats the two as one aspect, and a
    coverage check that did not would keep asking for a specific the listing
    plainly holds.
    """
    key = (name or "").strip().lower()
    if not key:
        return set()
    return {key, key[5:] if key.startswith("item ") else f"item {key}"}


def fillable_blanks(listing, aspects: list[dict]) -> list[dict]:
    """The category's aspects this listing holds no value for and the AI could
    still be asked to answer — every blank except the identifiers above.

    This is what "Fill in details" is actually about, and until it existed the
    app had no way to say it: the dashboard's group was built from the
    free-text `missing_info` notes instead, which is a different question with
    a different answer. A listing can carry no notes at all and still reach
    eBay with Subject, Era, Occasion, Packaging and Character blank — eBay's
    own suggester offers exactly those, from the same photos, and a buyer
    filtering on any of them never sees the listing.
    """
    held = {(s.name or "").strip().lower()
            for s in (getattr(listing, "item_specifics", None) or [])
            if (s.value or "").strip()}
    if (getattr(listing, "brand", "") or "").strip():
        held.add("brand")
    out = []
    for a in aspects:
        name = (a.get("name") or "").strip()
        if not name or is_identifier_aspect(name):
            continue
        if _aspect_keys(name) & held:
            continue
        out.append(a)
    return out
