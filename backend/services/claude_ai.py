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
  "subtitle": "string, optional short subtitle, can be empty",
  "brand": "string",
  "condition": "one of: %s",
  "condition_description": "string describing visible wear/flaws",
  "category_suggestion": "human-readable eBay category path",
  "description": "string, 2-4 short paragraphs, buyer-friendly, no false claims",
  "price": number or null (suggested USD price based on item & condition),
  "quantity": integer (default 1),
  "item_specifics": [{"name": "string", "value": "string"}],
  "missing_info": ["names of fields a human should verify/fill, e.g. 'exact model number', 'size'"],
  "confidence": "low|medium|high",
  "raw_observations": "brief notes on what you actually see in the photos"
}
Rules:
- Only state facts you can see or reasonably infer. Never invent serial numbers,
  authenticity guarantees, or specs you cannot verify; put those in missing_info.
- Title must be <= 80 characters and front-load the most searched keywords.
- item_specifics should include relevant fields (Brand, Model, Color, Size,
  Material, Type, etc.) where determinable.
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
    specifics = [
        ItemSpecific(name=str(s.get("name", "")), value=str(s.get("value", "")))
        for s in data.get("item_specifics", [])
        if s.get("name")
    ]
    cond = str(data.get("condition", "USED_EXCELLENT")).upper()
    if cond not in EBAY_CONDITIONS:
        cond = "USED_EXCELLENT"
    title = (data.get("title") or "").strip()[:80]
    price = data.get("price")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None
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
        quantity=int(data.get("quantity") or 1),
        item_specifics=specifics,
        images=image_names,
        missing_info=[str(m) for m in data.get("missing_info", [])],
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
        max_tokens=2000,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    listing = _to_listing(data, image_names)
    return IdentifyResult(
        listing=listing,
        confidence=str(data.get("confidence", "medium")),
        raw_observations=str(data.get("raw_observations", "")),
    )


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
        max_tokens=2000,
        messages=[{"role": "user", "content": msg}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    updated = _to_listing(data, listing.images)
    # Preserve images explicitly.
    updated.images = listing.images
    return updated
