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
  "item_specifics": [{"name": "string", "value": "string"}],
  "missing_info": ["names of fields a human should verify/fill, e.g. 'exact model number', 'size'"],
  "confidence": "low|medium|high",
  "raw_observations": "brief notes on what you actually see in the photos",
  "image_orientations": [one integer PER PHOTO, in the same order shown: how many degrees CLOCKWISE to rotate that photo so the item appears upright. 0, 90, 180, or 270 only]
}
Rules:
- Only state facts you can see or reasonably infer. Never invent serial numbers,
  authenticity guarantees, or specs you cannot verify; put those in missing_info.
- Title must be <= 80 characters and front-load the most searched keywords.
- item_specifics should include relevant fields (Brand, Model, Color, Size,
  Material, Type, etc.) where determinable.
- image_orientations must have exactly one entry per photo provided, in order.
  Be conservative — only flag a rotation when the correct upright is obvious
  (readable text/logo, a person or mannequin, a clearly top-heavy object). When
  there is no obvious upright (e.g. a garment laid flat and shot from above) or
  you are unsure, use 0. The seller can rotate manually if you get it wrong.
""" % ", ".join(EBAY_CONDITIONS)


def _client() -> Anthropic:
    if not config.anthropic_ready():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


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
    # Per-image auto-orientation, aligned to the images we actually sent (capped
    # at 8). Clamp to the four valid quarter-turns; anything odd becomes 0 (leave
    # as-is) so a malformed value can never rotate a photo wrongly.
    sent = min(len(image_paths), 8)
    raw_orient = data.get("image_orientations")
    orientations = []
    if isinstance(raw_orient, list):
        for v in raw_orient[:sent]:
            try:
                deg = int(v) % 360
            except (TypeError, ValueError):
                deg = 0
            orientations.append(deg if deg in (90, 180, 270) else 0)
    return IdentifyResult(
        listing=listing,
        confidence=conf,
        raw_observations=str(data.get("raw_observations", "")),
        orientations=orientations,
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
    # _to_listing only parses the fields the identify schema defines — fields
    # managed by the UI (category picker, shipping policy, Best Offer, Promote)
    # aren't among them and would silently reset to defaults on EVERY refine
    # ("lower the price" used to wipe the category and turn Promote off).
    # Carry them over from the seller's current listing.
    for field in ("category_id", "fulfillment_policy_id",
                  "best_offer_enabled", "best_offer_min", "best_offer_accept",
                  "promote_enabled", "promote_recommended", "promote_percent"):
        setattr(updated, field, getattr(listing, field))
    return updated
