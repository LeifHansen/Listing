"""Claude-powered image identification and listing content generation.

This is the "lens": images are sent to Claude's vision model, which identifies
the item and drafts a full eBay listing. A second call refines the draft from
free-form user instructions.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

from .. import config
from ..models import IdentifyResult, ItemSpecific, Listing

# eBay's well-known condition enum values (subset most listings use).
EBAY_CONDITIONS = [
    "NEW",
    "NEW_OTHER",
    "NEW_WITH_DEFECTS",
    "CERTIFIED_REFURBISHED",
    "SELLER_REFURBISHED",
    "USED_EXCELLENT",
    "USED_VERY_GOOD",
    "USED_GOOD",
    "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
]

_LISTING_SCHEMA = """
Return ONLY a JSON object (no markdown fences) with this exact shape:
{
  "title": "string, <= 80 chars, keyword-rich eBay title",
  "subtitle": "always the empty string \\"\\" (eBay charges an extra fee for subtitles; the seller adds one manually if they want)",
  "brand": "string",
  "condition": "one of: %s",
  "condition_description": "string describing visible wear/flaws",
  "category_suggestion": "human-readable eBay category path",
  "description": "string, 2-4 short paragraphs, buyer-friendly, no false claims",
  "price": number or null (suggested USD price based on item & condition),
  "quantity": integer (default 1),
  "package_weight_oz": number (estimated TOTAL shipping weight in ounces, packed; best-effort estimate the seller can correct),
  "package_length_in": number (estimated SHIPPING BOX length in inches, packed),
  "package_width_in": number (estimated SHIPPING BOX width in inches, packed),
  "package_height_in": number (estimated SHIPPING BOX height in inches, packed),
  "item_specifics": [{"name": "string", "value": "string"}],
  "missing_info": ["names of fields a human should verify/fill, e.g. 'exact model number', 'size'"],
  "confidence": "low|medium|high",
  "raw_observations": "brief notes on what you actually see in the photos"
}
Rules:
- Only state facts you can see or reasonably infer. Never invent serial numbers,
  authenticity guarantees, or specs you cannot verify; put those in missing_info.
- Title must be <= 80 characters and front-load the most searched keywords.
- ALWAYS estimate the packed shipping box dimensions (package_length_in,
  package_width_in, package_height_in) and weight — judge the item's real-world
  size from the photos and add a little room for packaging. Never leave the
  dimensions at 0; a reasonable estimate the seller can correct is required so
  shipping calculates. (e.g. a t-shirt ≈ 10×8×2 in; a coffee mug ≈ 6×5×5 in;
  a paperback ≈ 8×6×1 in; a pair of shoes in-box ≈ 13×8×5 in.)
- item_specifics: be thorough. Fill EVERY standard eBay item specific you can
  see or confidently infer, using eBay's exact aspect names as "name" (these
  populate the listing's item specifics, so more accurate entries = far better
  search visibility). Give ONE value per name; never guess. Common names by
  category:
  * Clothing: Department, Type, Style, Size, Size Type, Color, Material,
    Pattern, Sleeve Length, Fit, Neckline, Closure, Occasion, Season, Theme,
    Features, Country/Region of Manufacture, Vintage.
  * Shoes: Department, Type, Style, US Shoe Size, Color, Upper Material.
  * Trading cards: Game, Set, Card Name, Card Number, Language, Rarity, Finish,
    Features, Grade.
  * Collectibles/other: Type, Character, Material, Color, Theme, Year
    Manufactured, Country/Region of Manufacture.
  Use the canonical value eBay expects (e.g. Color "Red", Department "Men",
  Size "L"). For a field with two values, return it twice as separate entries
  (e.g. {"name":"Season","value":"Spring"} and {"name":"Season","value":"Summer"})
  rather than one comma-joined value. Put anything you cannot verify in
  missing_info instead of guessing.
""" % ", ".join(EBAY_CONDITIONS)


def _client() -> Anthropic:
    if not config.anthropic_ready():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


def ai_error_message(exc: Exception) -> tuple[int, str]:
    """Map an Anthropic/API error to (http_status, user-facing message).

    Turns raw SDK errors into a clear reason the UI can show — especially the
    two limits a seller actually hits: rate limits and an exhausted credit
    balance. Uses status_code + message text so it survives SDK version drift.
    """
    status = getattr(exc, "status_code", None)
    body = str(getattr(exc, "message", "") or exc).lower()
    if status == 429 or "rate limit" in body or "overloaded" in body:
        return 429, ("The AI is rate-limited or busy right now — wait a few "
                     "seconds and try again.")
    if (status == 402 or "credit balance" in body or "insufficient" in body
            or "billing" in body or "quota" in body):
        return 402, ("The Anthropic account is out of credits. Top it up at "
                     "console.anthropic.com to keep generating listings.")
    if status in (401, 403) or "authentication" in body or "x-api-key" in body:
        return 400, ("The AI credentials on the server are missing or invalid "
                     "(check ANTHROPIC_API_KEY).")
    if "connection" in body or "timed out" in body or "timeout" in body:
        return 503, ("Couldn't reach the AI service — check the connection and "
                     "try again in a moment.")
    return 502, f"AI request failed: {str(exc)[:200]}"


def _image_block(path: Path) -> dict:
    media_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    # Be forgiving: grab the outermost { ... }.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _to_listing(data: dict, image_names: list[str]) -> Listing:
    # The model sometimes returns null (not []) for these, or entries that
    # aren't dicts — coerce defensively so a stray shape can't crash identify.
    raw_specs = data.get("item_specifics") or []
    specifics = [
        ItemSpecific(name=str(s.get("name", "")), value=str(s.get("value", "")))
        for s in raw_specs
        if isinstance(s, dict) and s.get("name")
    ]
    raw_missing = data.get("missing_info") or []
    cond = str(data.get("condition", "USED_EXCELLENT")).upper()
    if cond not in EBAY_CONDITIONS:
        cond = "USED_EXCELLENT"
    title = (data.get("title") or "").strip()[:80]
    price = data.get("price")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None
    try:
        quantity = max(1, int(float(data.get("quantity") or 1)))
    except (TypeError, ValueError):
        quantity = 1
    def _f(key: str) -> float:
        try:
            return max(0.0, float(data.get(key) or 0))
        except (TypeError, ValueError):
            return 0.0
    # identify returns a single estimated total (package_weight_oz); refine
    # echoes the split fields (package_weight_lb + package_weight_oz). Detect
    # which shape we got so a refine round-trip doesn't drop the pounds.
    if "package_weight_lb" in data:
        weight_lb = int(_f("package_weight_lb"))
        weight_oz = round(_f("package_weight_oz"), 1)
    else:
        total_oz = _f("package_weight_oz")
        weight_lb = int(total_oz // 16)
        weight_oz = round(total_oz - weight_lb * 16, 1)
    return Listing(
        title=title,
        subtitle=(data.get("subtitle") or "").strip(),
        brand=(data.get("brand") or "").strip(),
        condition=cond,
        condition_description=(data.get("condition_description") or "").strip(),
        category_suggestion=(data.get("category_suggestion") or "").strip(),
        description=(data.get("description") or "").strip(),
        price=price,
        currency=config.EBAY_CURRENCY,
        quantity=quantity,
        package_weight_lb=weight_lb,
        package_weight_oz=weight_oz,
        package_length_in=_f("package_length_in"),
        package_width_in=_f("package_width_in"),
        package_height_in=_f("package_height_in"),
        item_specifics=specifics,
        images=image_names,
        missing_info=[str(m) for m in raw_missing if m],
    )


def identify(image_paths: list[Path], image_names: list[str]) -> IdentifyResult:
    """Identify the item(s) in the images and draft a full listing."""
    client = _client()
    content: list[dict] = []
    for p in image_paths[:8]:  # cap images per request
        content.append(_image_block(p))
    content.append(
        {
            "type": "text",
            "text": (
                "You are an expert eBay reseller and product cataloguer. "
                "Examine these product photos and produce a complete, accurate "
                "eBay listing draft.\n\n" + _LISTING_SCHEMA
            ),
        }
    )

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the AI response was too long and got cut off; "
                           "try again or use fewer photos")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    listing = _to_listing(data, image_names)
    # eBay charges extra for subtitles - never auto-fill one; the seller can
    # add it in the editor if it's worth the fee. (refine() keeps whatever
    # the seller typed, so only clear it here at first draft.)
    listing.subtitle = ""
    # Constrain confidence to the known set — it's rendered into the UI, so a
    # free-form (prompt-injected) value must never reach the DOM.
    conf = str(data.get("confidence", "medium")).lower().strip()
    if conf not in ("low", "medium", "high"):
        conf = "medium"
    return IdentifyResult(
        listing=listing,
        confidence=conf,
        raw_observations=str(data.get("raw_observations", "")),
    )


_GROUP_SCHEMA = """
Return ONLY a JSON object (no markdown fences) with this shape:
{
  "groups": [
    {"name": "short item name", "indices": [0, 3, 4]}
  ]
}
Rules:
- The numbered photos are one bulk upload containing MULTIPLE distinct items
  for sale. Group photos that show the SAME physical item (different angles,
  close-ups of tags/labels, flaws).
- Every photo index appears in EXACTLY ONE group. A group may have 1 photo.
- When unsure whether two photos show the same item, prefer keeping them
  together only if strong visual evidence matches (same color/pattern/brand);
  otherwise split them.
- Order each group's indices with the best overview shot first.
"""


def group_photos(images: list[bytes]) -> dict:
    """Bulk mode: split a pile of photos into per-item groups.

    Returns {"groups": [{"name", "indices"}]} covering every input index
    exactly once (indices the model dropped/duplicated are repaired here).
    """
    client = _client()
    content: list[dict] = []
    for i, data in enumerate(images):
        content.append({"type": "text", "text": f"Photo {i}:"})
        b64 = base64.standard_b64encode(data).decode("ascii")
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": (
        "You are sorting a reseller's bulk photo dump into individual items "
        "to list on eBay.\n\n" + _GROUP_SCHEMA)})

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("grouping response was cut off; try fewer photos")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)

    n = len(images)
    groups: list[dict] = []
    seen: set[int] = set()
    for g in (data.get("groups") or []):
        if not isinstance(g, dict):
            continue
        idxs = []
        for i in (g.get("indices") or []):
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n and i not in seen:
                seen.add(i)
                idxs.append(i)
        if idxs:
            groups.append({"name": str(g.get("name", "")).strip() or f"Item {len(groups) + 1}",
                           "indices": idxs})
    # Any photo the model missed becomes its own item rather than vanishing.
    for i in range(n):
        if i not in seen:
            groups.append({"name": f"Item {len(groups) + 1}", "indices": [i]})
    return {"groups": groups}


_SHELF_SCHEMA = """
Return ONLY a JSON object (no markdown fences) with this shape:
{
  "items": [
    {
      "name": "short name of the item you spotted",
      "reason": "one concise phrase on why it could be worth reselling",
      "location": "rough position so the shopper can find it (e.g. 'top shelf, left')",
      "confidence": "low|medium|high"
    }
  ]
}
Rules:
- These frames are sampled from a video panning across a shelf/rack/table at a
  thrift store, estate sale, or garage sale. GOAL: narrow the shopper's search —
  flag the few items MOST likely to be worth reselling (brand names, vintage,
  collectibles, electronics, designer, unusual/quality pieces).
- Do NOT estimate prices. Do NOT invent brands or details you can't see.
- Skip obvious low-value clutter. Prefer 3-8 of the strongest candidates.
- If nothing stands out, return an empty items array.
"""


def scan_shelf(images: list[bytes]) -> dict:
    """Triage a shelf: look across sampled video frames and flag items that
    might be worth reselling. No pricing — just narrows where to look."""
    client = _client()
    content: list[dict] = []
    for data in images[:8]:
        b64 = base64.standard_b64encode(data).decode("ascii")
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": (
        "You are an expert reseller scanning a shelf for hidden gems. The images "
        "are frames from one video panning across the same shelf.\n\n" + _SHELF_SCHEMA)})

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the scan returned too much; try a shorter video")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    items = []
    for it in (data.get("items") or []):
        if not isinstance(it, dict) or not it.get("name"):
            continue
        conf = str(it.get("confidence", "medium")).lower().strip()
        if conf not in ("low", "medium", "high"):
            conf = "medium"
        items.append({
            "name": str(it.get("name", "")).strip(),
            "reason": str(it.get("reason", "")).strip(),
            "location": str(it.get("location", "")).strip(),
            "confidence": conf,
        })
    return {"items": items}


def refine(listing: Listing, prompt: str) -> Listing:
    """Apply a free-form user instruction to an existing listing draft."""
    client = _client()
    current = listing.model_dump()
    # Don't let the model rewrite the image list.
    current.pop("images", None)

    msg = (
        "You are editing an eBay listing draft. Here is the current draft as "
        "JSON:\n\n" + json.dumps(current, indent=2) + "\n\n"
        "Apply this instruction from the seller:\n\"" + prompt + "\"\n\n"
        "Return the FULL updated listing as a single JSON object with the same "
        "fields (title <= 80 chars, condition must be one of: "
        + ", ".join(EBAY_CONDITIONS)
        + "). Only change what the instruction asks for; keep everything else. "
        "Return ONLY the JSON, no markdown."
    )
    resp = client.messages.create(
        model=config.CONTENT_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": msg}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the AI response was too long and got cut off; "
                           "try a shorter instruction or trim the description")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    updated = _to_listing(data, listing.images)
    # Preserve images explicitly.
    updated.images = listing.images
    return updated


_ASPECTS_FILL_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "specifics": [ {"name": "<exact aspect name>", "value": "<value>"} ] }
Rules:
- Fill each listed eBay item specific you can SEE in the photos or confidently
  infer. Use the aspect's EXACT name as given.
- For an aspect shown as "(choose one of: ...)", the value MUST be exactly one
  of those allowed values, copied verbatim (this is how eBay's fixed-value /
  checkbox specifics are matched). If none fits, omit that aspect.
- For "(free text)" aspects, give the single best concise value eBay expects.
- Omit any aspect you cannot determine — never guess or invent.
- One value per aspect name.
"""


def _norm_aspect_value(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _match_selection_value(value: str, allowed: list[str]) -> Optional[str]:
    """Map a model-supplied value to eBay's exact allowed value for a
    fixed-value (SELECTION_ONLY / "checkbox") aspect. Returns the canonical
    allowed string, or None when nothing fits. Only ever returns a string from
    `allowed`, so eBay can never reject it — but it's far more forgiving than an
    exact match, which used to drop things like "Machine Washable" (eBay:
    "Machine Wash") or "Spandex" (eBay: "Spandex/Elastane"), leaving the
    specific blank. Conservative order: exact > normalized-equal > unambiguous
    containment (only when exactly one allowed value overlaps)."""
    v = value.strip()
    if not v:
        return None
    for a in allowed:  # 1) exact, case-insensitive
        if a.strip().lower() == v.lower():
            return a
    nv = _norm_aspect_value(v)
    if not nv:
        return None
    for a in allowed:  # 2) same once spacing/punctuation is ignored
        if _norm_aspect_value(a) == nv:
            return a
    # 3) one contains the other, but only if a SINGLE allowed value qualifies,
    #    so we never silently pick between rival options (e.g. Cotton vs
    #    Cotton Blend both matching "cotton").
    hits = []
    for a in allowed:
        na = _norm_aspect_value(a)
        if na and (na in nv or nv in na):
            hits.append(a)
    return hits[0] if len(hits) == 1 else None


def fill_aspects(image_paths: list[Path], listing: Listing,
                 aspects: list[dict]) -> list[ItemSpecific]:
    """Fill eBay's category item specifics from the product photos. `aspects`
    is the taxonomy list [{name, required, mode, values}]. Returns validated
    ItemSpecifics — SELECTION_ONLY values are matched to eBay's allowed list so
    the fixed-value ("checkbox") specifics actually populate on eBay."""
    named = [a for a in aspects if a.get("name")]
    if not named or not image_paths:
        return []
    client = _client()
    lines = []
    for a in named:
        if a.get("mode") == "SELECTION_ONLY" and a.get("values"):
            vals = ", ".join(a["values"][:40])
            lines.append(f'- "{a["name"]}" (choose one of: {vals})')
        else:
            lines.append(f'- "{a["name"]}" (free text)')
    context = (f"Title: {listing.title}\nBrand: {listing.brand}\n"
               f"Category: {listing.category_suggestion}\n"
               f"Description: {(listing.description or '')[:500]}")
    content: list[dict] = [_image_block(p) for p in image_paths[:8]]
    content.append({"type": "text", "text": (
        "You are cataloguing an item for eBay. Using the product photos and the "
        "context below, fill in these eBay item specifics as accurately as "
        "possible.\n\nCONTEXT:\n" + context + "\n\nEBAY ITEM SPECIFICS TO FILL:\n"
        + "\n".join(lines) + "\n\n" + _ASPECTS_FILL_SCHEMA)})

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the item-specifics response was cut off; try again")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)

    by_name = {a["name"].strip().lower(): a for a in named}
    out: list[ItemSpecific] = []
    seen: set[str] = set()
    for s in (data.get("specifics") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        value = str(s.get("value", "")).strip()
        key = name.lower()
        if not name or not value or key not in by_name or key in seen:
            continue
        a = by_name[key]
        if a.get("mode") == "SELECTION_ONLY" and a.get("values"):
            match = _match_selection_value(value, a["values"])
            if not match:
                continue  # not a valid eBay value — drop rather than get rejected
            value = match  # canonical value eBay expects
        seen.add(key)
        out.append(ItemSpecific(name=a["name"], value=value))
    return out
