"""Claude-powered image identification and listing content generation.

This is the "lens": images are sent to Claude's vision model, which identifies
the item and drafts a full eBay listing. A second call refines the draft from
free-form user instructions.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

from .. import config
from ..config import log
from . import taxonomy
from .listing_prompt import (
    EBAY_CONDITIONS,
    LISTING_SCHEMA,
    REFINE_ORDER_RULE,
    expected_item_count,
    group_notes_block,
    identify_notes_block,
)
from ..models import TITLE_MAX_CHARS, IdentifyResult, ItemSpecific, Listing
from ..money import charm_price


# One shared client, built on first use. Every call used to construct its own,
# which threw away the underlying HTTP connection pool — so each vision request
# paid a fresh TLS handshake, and a bulk batch paid one per photo. The SDK's
# client is thread-safe, which is what the bulk/identify worker threads need.
_CLIENT: Optional[Anthropic] = None
_CLIENT_LOCK = threading.Lock()

# The SDK defaults to a 600s timeout with 2 retries — worst case ~30 minutes on
# a wedged request, far longer than any caller waits (the client gives up on a
# job at 240s) and long enough to pin a worker thread the whole time. These
# bound it to something a seller might actually still be waiting for.
_AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "120") or 120)
_AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2") or 2)


def _client() -> Anthropic:
    global _CLIENT
    if not config.anthropic_ready():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                    timeout=_AI_TIMEOUT_SECONDS,
                                    max_retries=_AI_MAX_RETRIES)
    return _CLIENT


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
    if isinstance(exc, json.JSONDecodeError):
        # Its own message ("Expecting ',' delimiter: line 1 column 3644") is
        # about a document the seller never sees. What they can do is retry.
        return 502, ("The AI's answer couldn't be read this time — try it "
                     "again.")
    return 502, f"AI request failed: {str(exc)[:200]}"


def _image_block(path: Path) -> dict:
    # Whole-frame vision payloads ride as ~1092px cached copies: identical
    # reads at roughly half the image tokens and upload bytes of the full
    # 1600px listing photo (see images.vision_copy). Tag close-ups don't come
    # through here — they crop from the full-size photo.
    from . import images
    path = images.vision_copy(path)
    media_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _log_usage(tag: str, resp) -> None:
    """One line of token accounting per call — cache_read > 0 is the proof the
    prompt-cache breakpoints are landing (zero across repeated calls means a
    silent invalidator crept into the system blocks)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return
    log.info("ai %s: in=%s out=%s cache_read=%s cache_write=%s", tag,
             getattr(u, "input_tokens", None), getattr(u, "output_tokens", None),
             getattr(u, "cache_read_input_tokens", None),
             getattr(u, "cache_creation_input_tokens", None))


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
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # The model's answer, not ours, and it reads a listing: a description
        # that says the jeans are 34" x 28" has put two unescaped quotes in
        # the middle of a JSON string, and a batch item died on 'Expecting
        # ',' delimiter: line 1 column 3644'. Repair what a person would
        # repair before giving up.
        repaired = _repair_json(text)
        if repaired == text:
            raise
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            raise exc from None
        log.info("ai: repaired the model's JSON (%s)", exc.msg)
        return data


_CLOSERS = frozenset(",}]:")


def _repair_json(text: str) -> str:
    """The most common ways a model breaks its own JSON, undone.

    A quote INSIDE a string that was never escaped -- the inch mark in
    34" x 28", a quoted name in a description -- is told apart from the
    string's closing quote by what follows it: a real closing quote is
    followed (after whitespace) by a comma, a colon, a closing bracket or the
    end; an inner one by prose. A raw newline or tab inside a string is
    escaped. A trailing comma before a closing bracket is dropped. Everything
    else is left exactly as it was, and the caller decides.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j >= n or text[j] in _CLOSERS:
                    out.append(ch)
                    in_string = False
                else:
                    out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\r":
                pass
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
            elif ch == ",":
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in "}]":
                    pass                     # trailing comma
                else:
                    out.append(ch)
            else:
                out.append(ch)
        i += 1
    return "".join(out)


# Public aliases for other services (orient.py) — cross-module callers
# shouldn't have to reach for underscored internals.
client = _client
extract_json = _extract_json


# Things the ACCOUNT settles once, not the seller per listing: where it ships
# from, which policies apply, how fast it goes out. The model likes to add
# these to missing_info because a listing does carry them, but asking the
# seller to type their own city on every item is noise — the app already has
# it from Settings and sends it at publish time.
_ACCOUNT_LEVEL_FIELDS = (
    "location", "ship from", "ship-from", "shipping origin", "zip", "postal",
    "handling time", "dispatch time", "return policy", "payment policy",
    "shipping policy", "seller name", "store name",
)


def _drop_account_level(entries: list) -> list[str]:
    """missing_info minus anything Settings already answers."""
    out = []
    for raw in entries:
        text = str(raw).strip()
        if not text:
            continue
        if any(term in text.lower() for term in _ACCOUNT_LEVEL_FIELDS):
            continue
        out.append(text)
    return out


def _to_listing(data: dict, image_names: list[str]) -> Listing:
    # The model sometimes returns null (not []) for these, or entries that
    # aren't dicts — coerce defensively so a stray shape can't crash identify.
    raw_specs = data.get("item_specifics") or []
    specifics = [
        ItemSpecific(
            name=str(s.get("name", "")), value=str(s.get("value", "")),
            confidence=("high" if str(s.get("confidence", "")).strip().lower() == "high"
                        else "medium"))
        for s in raw_specs
        if isinstance(s, dict) and s.get("name")
    ]
    raw_missing = data.get("missing_info") or []
    cond = str(data.get("condition", "USED_EXCELLENT")).upper()
    if cond not in EBAY_CONDITIONS:
        cond = "USED_EXCELLENT"
    title = (data.get("title") or "").strip()[:TITLE_MAX_CHARS]
    # Every price this app chooses ends in .99 (money.charm_price): an item the
    # AI values at about $25 lists at $24.99, the way a seller would have
    # written it themselves. `refine` puts back a price the seller set, so this
    # only ever shapes a number the AI picked.
    price = charm_price(data.get("price"))
    # Price read off a store sticker in the photos (what the seller would PAY,
    # not the resale suggestion). Optional; feeds profit-per-item once sold.
    purchase_price = data.get("purchase_price")
    try:
        purchase_price = (round(float(purchase_price), 2)
                          if purchase_price is not None else None)
        if purchase_price is not None and purchase_price <= 0:
            purchase_price = None
    except (TypeError, ValueError):
        purchase_price = None
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
        purchase_price=purchase_price,
        currency=config.EBAY_CURRENCY,
        quantity=quantity,
        package_weight_lb=weight_lb,
        package_weight_oz=weight_oz,
        package_length_in=_f("package_length_in"),
        package_width_in=_f("package_width_in"),
        package_height_in=_f("package_height_in"),
        item_specifics=specifics,
        images=image_names,
        missing_info=_drop_account_level(raw_missing),
    )


# The seller's account-level pricing strategy, folded into the identify
# prompt so the AI's suggested price lands where they want on the market range.
_PRICING_STRATEGY_HINTS = {
    "quick_flip": (
        "\nPricing strategy — QUICK FLIP: the seller wants a FAST sale. "
        "Suggest a price at the LOW end of the realistic market range for this "
        "item and condition (around the 25th percentile of comparable "
        "listings) — attractive enough to move within days."),
    "median": (
        "\nPricing strategy — MEDIAN: suggest the typical middle-of-market "
        "price for this item and condition (around the median of comparable "
        "listings)."),
    "long_sale": (
        "\nPricing strategy — LONG SALE: the seller is patient and wants to "
        "maximize the sale price. Suggest a price at the HIGH end of the "
        "realistic market range (around the 75th percentile of comparable "
        "listings) — still defensible, not fantasy."),
}


# The static half of the identify prompt, kept byte-identical across calls so
# it prompt-caches: identical system blocks are served from cache (~10x cheaper,
# faster time-to-first-token) on every identify within the cache TTL — which is
# every item of a bulk batch. Volatile content (the photos, the per-account
# pricing strategy) rides in the user message, after the cached prefix.
_IDENTIFY_SYSTEM = (
    "You are an expert eBay reseller and product cataloguer. Examine the "
    "product photos and produce a complete, accurate eBay listing draft.\n\n"
    + LISTING_SCHEMA)


def identify(image_paths: list[Path], image_names: list[str],
             strategy: str = "", notes: str = "") -> IdentifyResult:
    """Identify the item(s) in the images and draft a full listing.
    `strategy` (optional): quick_flip | median | long_sale — tilts the
    suggested price toward that end of the market range.
    `notes` (optional): the seller's own comma-separated hints about what is
    in the photos — a brand the camera never caught, a variant only the owner
    knows. Rides the user message, never the cached system prefix, because it
    changes per upload.

    The result also carries `tags`: bounding boxes of tags/labels the model
    spotted while examining the photos, for the zoom-and-transcribe pass —
    free here, where a separate locate call used to pay for its own look at
    every photo."""
    client = _client()
    content: list[dict] = []
    for p in image_paths[:8]:  # cap images per request
        content.append(_image_block(p))
    content.append(
        {
            "type": "text",
            "text": (
                "These are the product photos for one listing. Draft it."
                + _PRICING_STRATEGY_HINTS.get(strategy, "")
                + identify_notes_block(notes)
            ),
        }
    )

    resp = client.messages.create(
        model=config.VISION_MODEL,
        # The description alone is now a 300-600 word SEO body, and it shares
        # the budget with the specifics, the tag boxes and the observations.
        # A draft that runs past the cap is not a truncated description — it
        # is unparseable JSON, raised below as an error the seller has to
        # retry, so the headroom is worth more than the unused tokens (only
        # what is generated is billed).
        max_tokens=8192,
        system=[{"type": "text", "text": _IDENTIFY_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    _log_usage("identify", resp)
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
        # Raw tag boxes; tag_crops() validates each entry when cropping.
        tags=[t for t in (data.get("tags") or []) if isinstance(t, dict)][:6],
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
  backs, close-ups of tags/labels/flaws, the item on a hanger vs laid flat).
- Every photo index appears in EXACTLY ONE group. A group may have 1 photo.
- Sellers photograph one item completely before moving to the next, so photo
  ORDER is strong evidence: consecutive photos usually belong to the same item.
  A close-up (tag, label, texture, flaw) almost always belongs to the item in
  the surrounding overview shots — never make a close-up its own item.
- IDENTITY EVIDENCE OUTRANKS LOOKS. A size tag, a care label, a brand patch
  (a Levi's leather patch with its lot number and W/L size), a model or serial
  number, a distinctive flaw: two photos showing DIFFERENT such marks are two
  items, even when the garments look identical. Two pairs of jeans in the
  same wash are two listings. Count the tags and patches you can see and
  expect at least that many items. A seller who shoots look-alikes shoots
  each one's tag right after its overview: the tag belongs to the overview
  before it, and the next overview starts the next item.
- Split when two photos show different physical items (a different
  garment/object, a clearly different color or print, or different identity
  marks as above). When nothing tells two look-alikes apart -- no tag, patch
  or label reads differently -- KEEP THEM TOGETHER: a duplicate listing of
  one item is a worse mistake than one extra photo on a listing, and the
  seller can drag a photo out later.
- Order each group's indices with the best overview shot first.
"""


_GROUP_VERIFY_SCHEMA = """
Return ONLY a JSON object (no markdown fences): {"merge": [[0, 2]]}
- Each inner list = the numbered GROUPS that actually show the SAME physical
  item or set, and must become ONE listing. Groups you don't mention stay as
  they are.
- A close-up, back view, tag/label shot, or ONE COMPONENT OF A SET (e.g. a
  single print from a print set, one shoe of a pair) belongs with its set's
  overview group — those are the classic accidental splits.
- Keep genuinely different physical products separate, even from the same
  brand or artist. Two groups whose tags or patches read differently (a
  different size, lot or model number) are two items however alike they
  look; never merge look-alikes on looks alone.
- Nothing to merge? Return {"merge": []}.
"""


# --- the split check: one item, or two of the same? -------------------------
#
# The first pass is told to keep look-alikes together when unsure, and the
# verify pass above only ever merges. Nothing corrected the opposite mistake,
# and it happened: two pairs of Levi's, each with its own patch and tag,
# drafted as one listing. So a group big enough to be two items -- or, when
# the seller's notes promised more items than the grouping found, the biggest
# groups -- gets a second look with all of its photos, and is split only on
# identity evidence the model can point at. Angles are never evidence.
#
# One garment gets three to five photos: front, back, tag, a detail. Two of
# the same garment merged into one group is the six-to-ten-photo group.
GROUP_SPLIT_MIN_PHOTOS = int(os.getenv("BULK_SPLIT_CHECK_MIN_PHOTOS", "6") or "6")
# Below this a group cannot be two items with a tag each; not worth a call.
_GROUP_SPLIT_FLOOR = 3

_GROUP_SPLIT_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{"items": [{"name": "short name telling this one apart", "indices": [3, 4, 5], "evidence": "what makes this a separate physical item"}]}
- These numbered photos were grouped as ONE item for sale. Decide whether
  they show one physical item, or MORE THAN ONE of the same kind (two pairs
  of the same jeans, two of the same shirt in different sizes).
- Split ONLY on identity evidence you can point at: a different size on the
  tag or patch, a different lot, model or serial number, a different care
  label, a different flaw or wear pattern, a clearly different wash or color.
  Quote the tag or patch text where you can read it.
- Angles, lighting, a hanger vs laid flat, a close-up vs an overview, front
  vs back are NOT evidence of a second item. A close-up of a tag belongs
  with the overview shots taken around it.
- Every photo index appears in exactly one item. One item: return a single
  entry with every index and evidence "".
"""


def _split_candidates(groups: list[dict], expected: int = 0) -> list[int]:
    """Which groups get the second look: every group at or above
    GROUP_SPLIT_MIN_PHOTOS, and -- when the seller's notes named more items
    than the grouping found -- the biggest groups down to the floor, until
    the count could come out right. Largest first: the merged pair is the
    biggest group in the pile."""
    order = sorted(range(len(groups)),
                   key=lambda gi: -len(groups[gi].get("indices") or []))
    picked = [gi for gi in order
              if len(groups[gi].get("indices") or []) >= GROUP_SPLIT_MIN_PHOTOS]
    short = max(0, expected - len(groups))
    for gi in order:
        if short <= 0:
            break
        if gi in picked:
            continue
        if len(groups[gi].get("indices") or []) < _GROUP_SPLIT_FLOOR:
            break
        picked.append(gi)
        short -= 1
    return picked


def _apply_split(group: dict, items) -> list[dict]:
    """The verifier's answer for one group, applied deterministically: the
    group is split only into items that partition its photos exactly and
    each carry evidence. Anything else -- one item, a photo dropped or used
    twice, a split with nothing to point at -- keeps the group as it was."""
    want = list(group.get("indices") or [])
    if not isinstance(items, list) or len(items) < 2:
        return [group]
    parts: list[dict] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            return [group]
        idxs: list[int] = []
        for i in item.get("indices") or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                return [group]
            if i in seen or i not in want:
                return [group]
            seen.add(i)
            idxs.append(i)
        if not idxs or not str(item.get("evidence") or "").strip():
            return [group]
        parts.append({"indices": idxs,
                      "name": str(item.get("name") or "").strip()})
    if seen != set(want):
        return [group]
    base = group.get("name") or "Item"
    out = []
    for k, part in enumerate(parts, start=1):
        name = part["name"] or (base if k == 1 else f"{base} ({k})")
        out.append({"name": name, "indices": part["indices"]})
    return out


def _check_splits(client, images: list[bytes], groups: list[dict],
                  expected: int = 0) -> list[dict]:
    """Second look at the groups that could be two items. Best-effort per
    group: a failed call keeps that group as it was."""
    candidates = _split_candidates(groups, expected)
    if not candidates:
        return groups
    result: dict[int, list[dict]] = {}
    for gi in candidates:
        group = groups[gi]
        content: list[dict] = []
        for i in group["indices"]:
            content.append({"type": "text", "text": f"Photo {i}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(images[i]).decode("ascii")}})
        content.append({"type": "text", "text": (
            f'These photos were grouped as one item, "{group.get("name", "")}", '
            "from a reseller's bulk photo dump. Is it one item?\n\n"
            + _GROUP_SPLIT_SCHEMA)})
        try:
            resp = client.messages.create(
                model=config.VISION_MODEL, max_tokens=800,
                messages=[{"role": "user", "content": content}])
            text = "".join(b.text for b in resp.content if b.type == "text")
            parts = _apply_split(group, _extract_json(text).get("items"))
        except Exception as exc:  # noqa: BLE001 - an assist, never a gate
            log.info("bulk split check skipped for %r: %s",
                     group.get("name", ""), exc)
            continue
        if len(parts) > 1:
            log.info("bulk split check: %r -> %d items", group.get("name", ""),
                     len(parts))
            result[gi] = parts
    if not result:
        return groups
    out: list[dict] = []
    for gi, group in enumerate(groups):
        out.extend(result.get(gi, [group]))
    return out


def _apply_group_merges(groups: list[dict], merge_lists) -> list[dict]:
    """Deterministically apply the verifier's merge instructions.

    Groups named together become ONE group, and the lists are read
    TRANSITIVELY: [[0, 1], [1, 2]] is one item spread over three groups, not
    a merge plus a leftover. They used to be applied one list at a time
    against a table of already-absorbed groups, which dropped any group named
    in an earlier list -- leaving the second list with a single group, and
    merging nothing. The order of the lists decided the answer: [[1, 2],
    [0, 1]] lost the 0-1 merge outright. A model untangling a badly
    over-split pile names the same group in several lists, so that path was
    live exactly when the merge mattered most.

    The lowest-numbered group of a union survives, keeping the merged item's
    place in the queue, and takes the name of whichever group in the union
    starts earliest in the pile -- the seller shoots the overview first, so
    that is the group named after the item rather than after a close-up.
    Photos come out in the pile's own order, which puts that overview first.
    """
    n = len(groups)
    parent = list(range(n))

    def root(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    merged = False
    for lst in (merge_lists or []):
        if not isinstance(lst, list):
            continue
        idxs = []
        for i in lst:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n and i not in idxs:
                idxs.append(i)
        if len(idxs) < 2:
            continue
        for a, b in zip(idxs, idxs[1:]):
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
                merged = True
    if not merged:
        return groups
    out: list[dict] = []
    for gi, g in enumerate(groups):
        if root(gi) != gi:
            continue
        union = [k for k in range(gi, n) if root(k) == gi]
        combined = sorted(i for k in union for i in groups[k]["indices"])
        first = min(union, key=lambda k: min(groups[k]["indices"], default=n))
        out.append({"name": groups[first]["name"], "indices": combined})
    return out


# What the pile itself says about an over-split, handed to the verify pass.
# On 2026-09-03 a seller's stained glass suncatcher came out of bulk mode as
# one listing PER PHOTO: every group held a single photo, and the verify pass
# — which sees exactly those photos — merged none of them. Nothing in the
# request said the shape of the answer was itself suspicious, and the
# seller's own note naming one item was read only by the pass that SPLITS.
_ALL_SINGLES_HINT = (
    "\n\nEVERY group below holds exactly ONE photo. A reseller's pile almost "
    "never looks like that — one item takes three to five shots (front, back, "
    "a tag, a detail) — so this shape usually means angles of the SAME item "
    "were read as separate items. An item whose colour changes with the light "
    "(glass, gemstone, anything shot both backlit and flat) is the classic "
    "case. Look hard for the sets before you answer."
)


def _count_hint(expected: int, found: int) -> str:
    """The gap between the seller's own inventory and what grouping found.

    The notes count already sends the biggest groups for a split check when
    it promises MORE items than were found. The opposite gap — the seller
    naming one suncatcher and the grouping producing six groups — was read by
    nobody, though it is the same evidence pointing the other way.
    """
    if not expected or found <= expected:
        return ""
    return (
        f"\n\nThe seller's own notes name {expected} item(s) in this pile and "
        f"the grouping came out with {found} groups. That gap is strong "
        "evidence the same item was split across several of them. The count "
        "is a hint and not a quota: merge the groups you can SEE are one "
        "item, and leave the rest alone."
    )


def _verify_groups(client, images: list[bytes], groups: list[dict],
                   expected: int = 0) -> list[dict]:
    """Second-pass duplicate check: show ONE representative photo per group and
    ask which groups are actually the same item. This catches the split the
    single-pass grouping keeps making (overview vs close-up / component of a
    set becoming two 'items'). Best-effort — any failure keeps the original
    grouping rather than blocking the batch.

    `expected`: how many items the seller's own notes name, so a grouping that
    came out with more groups than that can be told so."""
    if len(groups) < 2:
        return groups
    content: list[dict] = []
    for gi, g in enumerate(groups):
        rep = images[g["indices"][0]]
        content.append({"type": "text", "text": f'Group {gi} — "{g["name"]}":'})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(rep).decode("ascii")}})
    hint = _count_hint(expected, len(groups))
    if all(len(g["indices"]) == 1 for g in groups):
        hint += _ALL_SINGLES_HINT
    content.append({"type": "text", "text": (
        "These are the item groups made from ONE reseller's bulk photo dump. "
        "Some may accidentally be the SAME physical item split in two — that "
        "creates duplicate eBay listings, which is the worst outcome."
        + hint + "\n\n" + _GROUP_VERIFY_SCHEMA)})
    resp = client.messages.create(
        model=config.VISION_MODEL,
        # Room to answer for the pile in front of it. A flat 400 was enough
        # for the handful of merges a good grouping needs, and far too little
        # for the badly split pile this pass exists to rescue: 60 groups to
        # untangle is a longer answer than that, and a cut-off one is
        # unparseable JSON — which failed silently, leaving every accidental
        # split standing.
        max_tokens=max(400, min(2000, 24 * len(groups))),
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            f"the merge answer for {len(groups)} groups was cut off")
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text)
    return _apply_group_merges(groups, data.get("merge"))


# The keys the grouping answer may arrive under. The SAME model is asked for
# {"groups": [{"indices": ...}]} here and {"items": [{"indices": ...}]} by the
# split check a few hundred tokens later, and it does sometimes answer with
# the other pass's word — or with the plain-English "photos". An unrecognised
# key used to leave every group empty, and the straggler repair below then
# gave EVERY photo its own item: a listing per photo, each one drafted and
# billed as a separate identify call. Reading the aliases costs nothing.
_GROUP_LIST_KEYS = ("groups", "items")
_INDEX_KEYS = ("indices", "indexes", "photos", "photo_indices", "index")


def _index_list(group: dict) -> list:
    """The photo numbers in one group of the model's answer, under whichever
    of the known keys it used. A bare number counts as a group of one."""
    for key in _INDEX_KEYS:
        if key not in group:
            continue
        value = group[key]
        if isinstance(value, list):
            return value
        if isinstance(value, (int, str)):
            return [value]
    return []


def _parse_groups(data: dict, n: int) -> list[dict]:
    """The model's grouping answer as [{"name", "indices"}] over 0..n-1.

    Covers a SUBSET of the photos: a number that is out of range or used
    twice is dropped here, and the caller decides what to do about anything
    left over. Returns [] when nothing usable came back at all, which the
    caller must not confuse with "the model saw no items".
    """
    entries: list = []
    for key in _GROUP_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list) and value:
            entries = [g for g in value if isinstance(g, dict)]
            break
    raw = [(str(g.get("name", "")).strip(), _index_list(g)) for g in entries]

    numbers: list[int] = []
    for _, idxs in raw:
        for i in idxs:
            try:
                numbers.append(int(i))
            except (TypeError, ValueError):
                continue
    # A wholly 1-based answer ("Photo 1" read as the first photo). The
    # signature has to be one that cannot occur 0-based: nobody claimed photo
    # 0 and somebody claimed photo n. Without this the last photo was dropped
    # for being out of range and the first became an item of its own.
    shift = 1 if numbers and 0 not in numbers and max(numbers) == n else 0

    groups: list[dict] = []
    seen: set[int] = set()
    for name, idxs in raw:
        placed: list[int] = []
        for i in idxs:
            try:
                i = int(i) - shift
            except (TypeError, ValueError):
                continue
            if 0 <= i < n and i not in seen:
                seen.add(i)
                placed.append(i)
        if placed:
            groups.append({"name": name or f"Item {len(groups) + 1}",
                           "indices": placed})
    if shift:
        log.info("bulk grouping: the answer numbered photos from 1; shifted")
    return groups


def group_photos(images: list[bytes], notes: str = "") -> dict:
    """Bulk mode: split a pile of photos into per-item groups, then a second
    verification pass re-checks the result for the same item accidentally
    split into two groups (the duplicate-listing bug) and merges those.

    `notes` (optional): the seller's comma-separated inventory of the pile.
    This pass is where it is worth the most — the seller stating "two lacoste
    polos different size color" answers the one question grouping keeps
    getting wrong, in the seller's own words, before it is asked.

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
        "to list on eBay.\n\n" + _GROUP_SCHEMA + group_notes_block(notes))})

    n = len(images)

    def ask() -> tuple[list[dict], dict]:
        """One grouping call: the groups it placed, and the raw answer (which
        says what the model called things when it placed nothing)."""
        # Headroom matters at the batch cap: a 100-photo chunk that turns out
        # to be ~80 distinct items needs well over 1500 tokens of JSON, and a
        # cut-off response aborts the whole bulk job.
        resp = client.messages.create(
            model=config.VISION_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
        )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("grouping response was cut off; try fewer photos")
        text = "".join(b.text for b in resp.content if b.type == "text")
        answer = _extract_json(text)
        return _parse_groups(answer, n), answer

    groups, answer = ask()
    if not groups:
        # Valid JSON that placed no photo at all. The repair below would turn
        # that into one item per photo — a listing per photo, each drafted and
        # charged for, handed to the seller as if it were the answer. Ask once
        # more, and say so plainly rather than bill for the misreading: the
        # pile is still staged and the batch can simply be run again.
        log.warning("bulk grouping: no photo placed for %d photo(s) "
                    "(answer keys: %s) — asking once more",
                    n, ", ".join(sorted(map(str, answer))) or "none")
        groups, answer = ask()
        if not groups:
            raise RuntimeError(
                "the AI's answer for how to split these photos into items "
                "could not be read; please run this batch again")
    # Any photo the model missed becomes its own item rather than vanishing.
    seen = {i for g in groups for i in g["indices"]}
    missing = [i for i in range(n) if i not in seen]
    if missing:
        log.info("bulk grouping: %d of %d photo(s) were left unplaced; each "
                 "becomes an item of its own", len(missing), n)
    for i in missing:
        groups.append({"name": f"Item {len(groups) + 1}", "indices": [i]})
    expected = expected_item_count(notes)
    # Duplicate check: a cheap second look (one photo per group) that merges
    # groups which are actually the same item. Best-effort — the batch always
    # proceeds with the first-pass grouping if this errs, but never silently:
    # a swallowed failure here is a pile of duplicate listings with nothing
    # in the log to say why.
    try:
        groups = _verify_groups(client, images, groups, expected=expected)
    except Exception as exc:  # noqa: BLE001 - verification is an assist, never a gate
        log.warning("bulk grouping: the merge check did not run (%s); "
                    "keeping %d group(s) as first grouped", exc, len(groups))
    # The opposite mistake, checked AFTER the merges so nothing it separates
    # is put back together: a group big enough to be two of the same thing,
    # or the biggest groups when the seller's notes promised more items than
    # were found, is looked at photo by photo and split on identity evidence.
    try:
        groups = _check_splits(client, images, groups, expected=expected)
    except Exception as exc:  # noqa: BLE001 - an assist, never a gate
        log.warning("bulk grouping: the split check did not run (%s)", exc)
    # The one line that says what this pass decided. Without it a seller
    # reporting "it made a listing per photo" left nothing to read back.
    log.info("bulk grouping: %d photo(s) -> %d item(s)%s", n, len(groups),
             f"; the seller's notes name {expected}" if expected else "")
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


def _same_money(a, b) -> bool:
    """Two amounts that are the same to the cent (either may be unreadable)."""
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


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
        + REFINE_ORDER_RULE
        + "Return ONLY the JSON, no markdown."
    )
    resp = client.messages.create(
        model=config.CONTENT_MODEL,
        # A refine echoes the WHOLE listing back, long description included,
        # so it needs at least as much room as the draft that produced it.
        max_tokens=8192,
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
    # What the seller PAID is a fact, not listing copy — a refine must never
    # rewrite or drop it (the model may not echo the field back).
    if listing.purchase_price is not None:
        updated.purchase_price = listing.purchase_price
    # Nor may it move a price the seller set. A refine echoes the WHOLE draft
    # back, so without this an instruction about the title would take a $25.00
    # they typed and hand it back as $24.99 — the charm rule reaching a number
    # it does not own. Echoed unchanged means unchanged; a price the
    # instruction actually moved is the AI's and keeps its .99.
    if listing.price is not None and _same_money(data.get("price"), listing.price):
        updated.price = listing.price
    return updated


# ---------------------------------------------------------------------------
# Tag targeting — the fix for "the AI can't read clothing sizes".
#
# A size/care tag in a normal product photo is tiny (often <150px across once
# the shot is downscaled), so a single-pass model read comes back blank or
# guessed. This two-step pass works the way a person does: first FIND every
# tag across the photos, then ZOOM INTO each one and transcribe it. The
# transcript is handed to the specifics fill as ground truth, so Size /
# Material / Country / MPN / UPC come off the actual tag instead of being
# inferred.
# ---------------------------------------------------------------------------

_TAG_SCAN_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "tags": [ {"photo": <1-based photo number>,
             "box": [x0, y0, x1, y1],
             "kind": "size|care|brand|model|barcode|other"} ] }
Rules:
- Find every TAG, LABEL, STAMP, or PRINTED MARKING that could carry item
  facts: neck labels, waistband tags, care tags, shoe tongue/heel labels,
  hang tags, box text, model plates, barcodes.
- box is the tag's bounding region as FRACTIONS of that photo's width/height
  (x0,y0 = top-left, x1,y1 = bottom-right), padded a little so nothing is
  cut off.
- Include a tag even if you can't read it at this size — it will be zoomed.
- At most 6 entries, best candidates first. No tags at all -> {"tags": []}.
"""


def _pil_block(img) -> dict:
    """An Anthropic image block from an in-memory PIL image."""
    from io import BytesIO
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(buf.getvalue()).decode("ascii")}}


def tag_crops(image_paths: list[Path], tags: list[dict]) -> list[dict]:
    """Zoomed-in image blocks for tag bounding boxes ({photo, box, kind} with
    1-based photo numbers into image_paths, box as width/height fractions).

    CPU-only — no API call. Each box is cropped from the FULL-SIZE photo (plus
    a margin) and upscaled so small print reads at presentation size. Invalid
    entries are dropped, never guessed at."""
    from PIL import Image
    paths = [p for p in image_paths[:8] if p.is_file()]
    crops: list[dict] = []
    for t in (tags or [])[:6]:
        if not isinstance(t, dict):
            continue
        try:
            idx = int(t.get("photo", 0)) - 1
            x0, y0, x1, y1 = [float(v) for v in (t.get("box") or [])[:4]]
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(paths)) or not (x0 < x1 and y0 < y1):
            continue
        with Image.open(paths[idx]) as im:
            w, h = im.size
            mx, my = (x1 - x0) * 0.08, (y1 - y0) * 0.08
            box = (max(0, int((x0 - mx) * w)), max(0, int((y0 - my) * h)),
                   min(w, int((x1 + mx) * w)), min(h, int((y1 + my) * h)))
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                continue
            crop = im.crop(box)
            if max(crop.size) < 1100:
                scale = 1100 / max(crop.size)
                crop = crop.resize((round(crop.width * scale),
                                    round(crop.height * scale)), Image.LANCZOS)
            crops.append(_pil_block(crop))
    return crops


def read_tag_text(image_paths: list[Path]) -> str:
    """Locate tags/labels across the photos, zoom into each, and transcribe.

    Returns a plain-text transcript ("" when no tags were found) for use as
    ground-truth context in fill_aspects/identify. Two vision calls: a cheap
    low-res scan to get tag bounding boxes, then one call with the high-res
    crops. Raises on API failure — callers treat tag reading as best-effort.
    """
    from PIL import Image
    paths = [p for p in image_paths[:8] if p.is_file()]
    if not paths:
        return ""
    client = _client()

    # Pass 1 — find the tags. Low-res copies are plenty for locating.
    content: list[dict] = []
    for p in paths:
        with Image.open(p) as im:
            im.thumbnail((768, 768), Image.LANCZOS)
            content.append(_pil_block(im))
    content.append({"type": "text", "text": (
        f"These are photos 1 to {len(paths)} of one secondhand item being "
        "listed for sale. Locate every tag/label worth reading up close.\n"
        + _TAG_SCAN_SCHEMA)})
    resp = client.messages.create(model=config.VISION_MODEL, max_tokens=600,
                                  messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        tags = [t for t in (_extract_json(text).get("tags") or [])
                if isinstance(t, dict)][:6]
    except Exception:  # noqa: BLE001 - a malformed scan just means "no tags"
        tags = []
    if not tags:
        return ""

    # Pass 2 — zoom in and transcribe.
    crops = tag_crops(paths, tags)
    if not crops:
        return ""
    crops.append({"type": "text", "text": (
        "These are zoomed-in crops of the tags/labels on that same item. "
        "Transcribe ALL text you can read on them, exactly as printed. Then, "
        "on separate lines, state what the tags establish (only if actually "
        "readable): SIZE (the exact marking, e.g. 'L', 'W32 L34', 'EU 42', "
        "'US 10.5 M', and the size system), BRAND, MATERIAL percentages, "
        "COUNTRY of manufacture, MODEL/STYLE number, RN number, and the "
        "digits under any BARCODE (UPC/EAN). If a crop is unreadable, say "
        "so — never fill in what you can't see. Plain text only.")})
    resp = client.messages.create(model=config.VISION_MODEL, max_tokens=900,
                                  messages=[{"role": "user", "content": crops}])
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    return out[:2000]


_ASPECTS_FILL_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "specifics": [ {"name": "<exact aspect name>", "value": "<value>",
                  "confidence": "high|medium"} ] }
Rules:
- GOAL: fill as many of the listed eBay item specifics as you legitimately
  can — every filled specific is a search filter buyers use. Aim for all of
  the required ones and nearly all of the recommended ones. Leave one out
  only when you truly cannot tell.
- Read EVERYTHING in the photos first: care tags, sewn labels, printed marks,
  stamps, box/packaging text, model plates — and the human-readable DIGITS
  printed under any barcode (that's the UPC/EAN; also look there for MPN and
  model numbers). Exact text you can read is your best source. When the
  context includes TAG TEXT (transcribed from zoomed tag close-ups), treat it
  as ground truth — values taken from it are confidence "high".
- Clothing/shoe SIZE comes from the size tag, not from guessing: neck label,
  waistband tag, shoe tongue/heel label, or the care tag (the size often
  follows "SIZE" there). Report the marking in the aspect's expected form
  (e.g. "L", "32", "10.5"). Sizes read off a tag or TAG TEXT are "high";
  a size judged from proportions or measurements is "medium".
- confidence per specific:
  * "high" — you can literally see/read it (tag, label, print, barcode
    digits) or it is unambiguous from the photos (an obvious color, an
    obvious type).
  * "medium" — a reasonable inference from what the item clearly is (e.g.
    Department from the garment's cut, Style from the design, Theme,
    Occasion, estimated sizes). Fill these — a good inference beats a blank —
    the seller is shown a "review" flag on them.
- Use the aspect's EXACT name as given.
- FIXED-CHOICE aspects come in two shapes, and a value for either MUST be
  copied VERBATIM from that aspect's allowed list — the verbatim copy is the
  only thing that makes eBay's fixed-value specifics actually get selected:
  * "(choose exactly one of: ...)" — eBay's dropdown. Give one value.
  * "(CHECKBOXES - ...)" — eBay's multi-select tick boxes, and the specifics
    that ship empty most often, so treat them as a priority: emit a SEPARATE
    entry (same name, different value) for EVERY listed value that genuinely
    applies — typically two to four. One is fine when only one applies; none
    only when the item truly matches none. Never comma-join them into one
    value, and never stop at the first match when others also apply.
  When a listed value is only a near fit, prefer the closest defensible one at
  "medium" over leaving the aspect blank; omit it only when nothing fits.
- An aspect shown as "(plain number)" takes ONLY a number like "14" (one
  decimal at most, no words or units); "(4-digit year)" takes a year like
  "1985". If you can't tell, omit it — text there gets the listing rejected.
- For "(free text)" aspects, give the single best concise value eBay expects.
- DESCRIPTIVE aspects are inference-friendly — fill them, at "medium", from
  what the item clearly shows. These are the ones eBay's own suggester fills
  and buyers filter by, and the ones that reach live listings blank most
  often: Subject (what the item DEPICTS or is about — Bowling, Floral,
  Nautical), Character (a named character on or as the item, a generic figure
  included: "Bowler", "Santa Claus"), Era / Time Period Manufactured (from
  style, construction and marks, in the allowed list's own wording:
  "Mid 20th Century (1941-1969)"), Occasion (what it is bought or given for —
  "All Occasions" is a real answer for an item that suits any of them),
  Packaging (whether the item is boxed IN THESE PHOTOS — "Unboxed" for a loose
  item), Accents (e.g. Logo), Theme (e.g. 90s, Sports), Season (from the
  garment's weight/type), Style, Features, Performance/Activity, Garment Care
  (from the care tag), Product Line (the brand's line, e.g. "Tommy Jeans"),
  Pattern, Fit, Neckline, Closure. An honest inference here beats a blank:
  almost every one of these has a true answer for almost every item, and a
  blank one is a filter this listing never appears in.
- A free-text aspect shown as "(may repeat: several values allowed)" works the
  same way — e.g. Season as both "Winter" and "Spring". Repeat the name in a
  new entry per value; never comma-join values.
- Never invent identifiers: a UPC/EAN/ISBN/MPN/serial you cannot actually
  read must be omitted, and never fill any aspect with "Not Specified"/
  "Does Not Apply"/"Unknown" just to have an answer.
- Physical size aspects (Item Height, Item Length, Item Width, Item Depth,
  Item Diameter, Item Weight): when listed, ALWAYS provide a best-effort
  ESTIMATE of the actual item's dimensions (not the shipping box) judged from
  the photos' real-world scale, as a number with unit (e.g. "7 in",
  "1.5 lb") at "medium" confidence — some categories refuse to publish
  without these, and the seller can correct an estimate.
- One entry per VALUE, not per aspect: a single-value aspect appears once, a
  repeatable one (CHECKBOXES, "may repeat") appears once per value that
  applies.
"""


# Static role text for the specifics fill, cached with _ASPECTS_FILL_SCHEMA as
# one system block; the per-category aspect list gets a second cached block so
# same-category listings (a bulk batch's common case) reuse both.
_ASPECTS_SYSTEM = (
    "You are cataloguing an item for eBay. Using the product photos and the "
    "context provided, fill in the given eBay item specifics as accurately as "
    "possible.\n\n" + _ASPECTS_FILL_SCHEMA)


# How many of a fixed-choice aspect's allowed values to show the model. The old
# cap of 40 hid the rest silently, so anything past it could never be picked —
# on the long lists (Country/Region of Manufacture runs ~250) that made whole
# aspects unfillable. Allowed values are short strings and the aspect block is
# prompt-cached per category, so a generous cap costs little; beyond it the line
# says so and the model may answer off-list, which coerce_aspect_value still
# matches against the FULL list.
_MAX_SHOWN_VALUES = 150


def _choice_line(a: dict, multi: bool) -> str:
    """The prompt line for a fixed-choice aspect. MULTI ones are eBay's
    multi-select tick boxes — the "Item Specifics checkboxes" — and are labelled
    as such so the model ticks every value that applies instead of one."""
    values = a["values"]
    shown = ", ".join(values[:_MAX_SHOWN_VALUES])
    if len(values) > _MAX_SHOWN_VALUES:
        shown += (f", ... (+{len(values) - _MAX_SHOWN_VALUES} more allowed values "
                  "not shown - if the right one is missing here, give it verbatim "
                  "anyway)")
    if multi:
        return (f'- "{a["name"]}" (CHECKBOXES - tick every value that applies by '
                f'repeating this aspect once per value; allowed values: {shown})')
    return f'- "{a["name"]}" (choose exactly one of: {shown})'


def _aspect_lines(named: list[dict]) -> str:
    lines = []
    for a in named:
        dtype = (a.get("data_type") or "STRING").upper()
        fmt = (a.get("format") or "").lower()
        is_multi = (a.get("cardinality") or "SINGLE") == "MULTI"
        multi = " (may repeat: several values allowed)" if is_multi else ""
        if a.get("mode") == "SELECTION_ONLY" and a.get("values"):
            lines.append(_choice_line(a, is_multi))
        elif dtype == "DATE" or "yyyy" in fmt:
            lines.append(f'- "{a["name"]}" (4-digit year)')
        elif dtype == "NUMBER":
            lines.append(f'- "{a["name"]}" (plain number)')
        else:
            lines.append(f'- "{a["name"]}" (free text){multi}')
    return "\n".join(lines)


# Enough of the description to carry the Key Details block, where the values
# an aspect fill is looking for (material, colour, size, year, markings) are
# spelled out. 500 characters stopped inside the opening paragraph.
_CONTEXT_DESCRIPTION_CHARS = 2_000


def _listing_context(listing: Listing) -> str:
    return (f"Title: {listing.title}\nBrand: {listing.brand}\n"
            f"Category: {listing.category_suggestion}\n"
            f"Description: {(listing.description or '')[:_CONTEXT_DESCRIPTION_CHARS]}")


# Separators a model reaches for when it comma-joins a repeatable aspect's
# values despite being told not to ("Winter, Spring", "Cotton / Polyester").
_MULTI_JOIN_RE = re.compile(r"\s*[,;|]\s*|\s+/\s+")

# eBay's own ceiling on values per aspect (mirrored in services/ebay.py).
_MAX_ASPECT_VALUES = 30


def _legal_values(value: str, aspect: dict, is_multi: bool) -> list[str]:
    """Every eBay-legal value `value` yields for `aspect`: one for a
    single-value aspect, and possibly several for a repeatable (checkbox) one
    that arrived comma-joined. Joining is what the prompt forbids, but when it
    happens anyway the joined string matches no allowed value and the whole
    aspect used to be dropped — the checkboxes went untouched. Splitting
    recovers the individual ticks."""
    allowed = aspect.get("values") or []
    # A fixed-choice value can legitimately contain a comma ("Shirt, Blouse"),
    # so an exact match on the whole string always wins over splitting it.
    exact = (taxonomy.match_selection_value(value, allowed)
             if aspect.get("mode") == "SELECTION_ONLY" and allowed else None)
    if is_multi and exact is None and _MULTI_JOIN_RE.search(value):
        pieces: list[str] = []
        for piece in _MULTI_JOIN_RE.split(value):
            legal = taxonomy.coerce_aspect_value(piece, aspect)
            if legal is not None and legal not in pieces:
                pieces.append(legal)
        if pieces:
            return pieces
    legal = taxonomy.coerce_aspect_value(value, aspect)
    return [legal] if legal is not None else []


def _validate_specifics(data: dict, named: list[dict]) -> list[ItemSpecific]:
    """The model's raw specifics, coerced to eBay-legal ItemSpecifics."""
    by_name = {a["name"].strip().lower(): a for a in named}
    out: list[ItemSpecific] = []
    seen: set[tuple[str, str]] = set()   # (name, value) — exact repeats only
    single_used: set[str] = set()
    counts: dict[str, int] = {}
    for s in (data.get("specifics") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", "")).strip()
        value = str(s.get("value", "")).strip()
        key = name.lower()
        if not name or not value or key not in by_name:
            continue
        a = by_name[key]
        # A SINGLE-cardinality aspect takes its first value only; MULTI ones
        # (Season, Features, Theme...) may repeat — eBay accepts a value list
        # and buyers filter on each. Exact duplicates are dropped either way.
        if key in single_used:
            continue
        is_multi = (a.get("cardinality") or "SINGLE") == "MULTI"
        conf = str(s.get("confidence", "")).strip().lower()
        # Coerce to the aspect's own constraints (fixed choices matched to
        # eBay's exact wording, numbers stripped to plain numbers, years to
        # YYYY) — an illegal value is dropped rather than rejected later.
        for legal in _legal_values(value, a, is_multi):
            if (key, legal.lower()) in seen:
                continue
            if counts.get(key, 0) >= _MAX_ASPECT_VALUES:
                break
            seen.add((key, legal.lower()))
            counts[key] = counts.get(key, 0) + 1
            out.append(ItemSpecific(
                name=a["name"], value=legal,
                # Anything not explicitly "high" gets the review flag — safer
                # to over-flag than to show an inference as read-off-the-tag
                # fact.
                confidence="high" if conf == "high" else "medium"))
            if not is_multi:
                single_used.add(key)
                break
    return out


def _aspects_system_blocks(named: list[dict]) -> list[dict]:
    return [
        {"type": "text", "text": _ASPECTS_SYSTEM,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": "EBAY ITEM SPECIFICS TO FILL:\n" + _aspect_lines(named),
         "cache_control": {"type": "ephemeral"}},
    ]


def fill_aspects(image_paths: list[Path], listing: Listing,
                 aspects: list[dict], tag_text: str = "") -> list[ItemSpecific]:
    """Fill eBay's category item specifics from the product photos. `aspects`
    is the taxonomy list [{name, required, mode, values, cardinality}]. Returns
    validated ItemSpecifics — SELECTION_ONLY values are matched to eBay's
    allowed list so the fixed-value specifics actually populate, and a MULTI
    one (eBay's checkboxes) comes back as one entry per value ticked.
    `tag_text` (from read_tag_text) is passed as ground-truth context so Size/
    Material/Country/MPN come off the actual tags."""
    named = [a for a in aspects if a.get("name")]
    if not named or not image_paths:
        return []
    client = _client()
    context = _listing_context(listing)
    if tag_text:
        context += ("\nTAG TEXT (transcribed from zoomed close-ups of this "
                    "item's tags/labels — treat as ground truth):\n" + tag_text)
    content: list[dict] = [_image_block(p) for p in image_paths[:8]]
    content.append({"type": "text", "text": "CONTEXT:\n" + context})

    # Big clothing categories run 30+ aspects; a tight cap used to cut the
    # response off, which raised and silently skipped the WHOLE enrichment —
    # the "eBay suggests way more specifics than we fill" gap.
    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=4000,
        system=_aspects_system_blocks(named),
        messages=[{"role": "user", "content": content}],
    )
    _log_usage("fill_aspects", resp)
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the item-specifics response was cut off; try again")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _validate_specifics(_extract_json(text), named)


# The maker ask appended to the consolidated enrichment call (chain v2). Kept
# in the user text so the cached system blocks stay byte-identical whether or
# not the maker hunt is needed.
_MAKER_ASK = """
ADDITIONALLY, identify the item's MAKER (brand/manufacturer). Inspect every
photo for maker evidence — logos, embossed or printed marks, sewn tags,
labels, stamps, hallmarks, signatures, serial/model plates, packaging text,
and distinctive design signatures (typography, colorways, stitching,
hardware, patterns) you recognize — and work like a reverse-image search,
matching what you see against known makers in this product category. Add a
top-level "maker" key to the SAME JSON object:
"maker": {"maker": "<the exact maker name, or \\"\\" if none is identifiable>",
          "evidence": "<concise: what in the photos supports this>",
          "confidence": "low|medium|high"}
Name the MOST SPECIFIC maker the evidence supports (e.g. "Fenton" not "a
glass maker"). If the photos genuinely show no identifiable maker, return ""
— a wrong maker is worse than a blank one.
"""


def fill_aspects_combined(
        image_paths: list[Path], listing: Listing, aspects: list[dict],
        tag_crop_blocks: Optional[list[dict]] = None,
        want_maker: bool = False) -> tuple[list[ItemSpecific], Optional[dict]]:
    """Chain v2's single enrichment call: item specifics + (optionally) a
    maker candidate, with zoomed tag crops included as ground truth instead of
    a separate transcription round-trip.

    Returns (specifics, maker_candidate) where maker_candidate is
    {"maker", "evidence", "confidence"} or None. The candidate is only a
    proposal — verify_maker() must confirm it before anything is written."""
    named = [a for a in aspects if a.get("name")]
    if not named or not image_paths:
        return [], None
    client = _client()
    content: list[dict] = [_image_block(p) for p in image_paths[:8]]
    if tag_crop_blocks:
        content.append({"type": "text", "text": (
            "Zoomed-in crops of this item's tags/labels follow. Read them "
            "closely and treat what they say as ground truth — values taken "
            "from them are confidence \"high\".")})
        content.extend(tag_crop_blocks)
    tail = "CONTEXT:\n" + _listing_context(listing)
    if want_maker:
        tail += "\n" + _MAKER_ASK
    content.append({"type": "text", "text": tail})

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=4500,  # fill_aspects' 4000 + headroom for the maker section
        system=_aspects_system_blocks(named),
        messages=[{"role": "user", "content": content}],
    )
    _log_usage("fill_aspects_combined", resp)
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the item-specifics response was cut off; try again")
    data = _extract_json("".join(b.text for b in resp.content if b.type == "text"))
    specifics = _validate_specifics(data, named)
    maker = None
    if want_maker and isinstance(data.get("maker"), dict):
        m = data["maker"]
        name = str(m.get("maker", "")).strip()
        if name and len(name) <= 65:
            maker = {"maker": name,
                     "evidence": str(m.get("evidence", "")).strip(),
                     "confidence": str(m.get("confidence", "low")).strip().lower()}
    return specifics, maker


# ---------------------------------------------------------------------------
# The coverage pass — going back for the specifics the first fill skipped.
#
# fill_aspects/fill_aspects_combined hand the model thirty-odd aspects at once
# and ask it to fill what it can. What it does with the ones it is unsure of
# is silently nothing, and nothing downstream ever noticed: a listing reached
# eBay with Subject, Era, Occasion, Packaging and Character blank while eBay's
# own suggester offered all five, from the same photos and the same title, on
# the listing form the seller opened next. Every blank is a filter a buyer can
# use that this listing does not appear in.
#
# So the blanks get a second, narrower ask. It is a different question from
# the first pass — not "read this item" but "you have already read it; what is
# it obviously ABOUT" — and it is asked of a short list instead of thirty, so
# there is nowhere for an aspect to quietly go missing. The identifiers are
# not on that list at all: see taxonomy.is_identifier_aspect for why an
# instruction to prefer an inference must never be able to reach a UPC box.
# ---------------------------------------------------------------------------

_COVERAGE_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "specifics": [ {"name": "<exact aspect name>", "value": "<value>",
                  "confidence": "high|medium"} ] }
These are the item specifics eBay lists for this category that the listing
STILL HAS BLANK. You have already read this item once; this pass is not about
reading it again. It is about what the item plainly IS and what it is
obviously ABOUT — the things a person holding it could answer in a second and
eBay's own suggester answers from these same photos.
Rules:
- ANSWER EVERY ASPECT YOU HONESTLY CAN. A blank helps nobody: buyers filter on
  these, and an aspect left empty is a search this listing never appears in.
  "medium" confidence is the expected answer here — the seller is shown a
  review flag on it and can change it in one click. Omit an aspect only when
  you genuinely cannot tell, not merely because you are not certain.
- Every value must still be TRUE of THIS item. An inference is a reading of
  what you can see; it is not a guess, and it is never a placeholder.
- Most of these have an answer for almost any item, and they are the ones that
  ship blank most often. What they mean:
  * Subject / Theme / Franchise — what the item DEPICTS or is about (Bowling,
    Floral, Nautical, Christmas, a sport, a place, a named property).
  * Character — a named character shown on or as the item (including a generic
    figure the piece is of, e.g. "Bowler", "Santa Claus").
  * Era / Time Period Manufactured / Age / Vintage — when it was made, in the
    wording the allowed list uses ("Mid 20th Century (1941-1969)", "1970s").
    Judge it from style, construction, materials, marks and wear.
  * Occasion — what it is bought or given for. "All Occasions" is a real and
    usually correct answer for an item that suits any of them.
  * Packaging — whether the item is in its box/packaging IN THESE PHOTOS
    ("Unboxed" for a loose item, "Boxed"/"Original Box" when the box is shown).
  * Style / Type / Product Line / Features / Accents / Pattern / Finish —
    what the design plainly is.
  * Material / Color / Shape / Size — what the item is made of and looks like.
  * Country/Region of Manufacture — only from a mark, label or stamp that
    says so; never from where a brand is headquartered.
  * Year Manufactured — only a year the item, its mark or its packaging
    actually supports; leave blank rather than round an era to a year.
  * Original/Reproduction, Signed, Handmade, Antique — answer these from the
    evidence you can see, and never downgrade the item to be safe: an unmarked
    piece is not a "Reproduction", it is an item whose aspect stays blank.
- FIXED-CHOICE aspects: the value MUST be copied VERBATIM from that aspect's
  allowed list, exactly as spelled there — a near miss is dropped and the box
  stays empty. When the list holds nothing that is true of this item, leave the
  aspect out.
  * "(choose exactly one of: ...)" — one value.
  * "(CHECKBOXES - ...)" — tick EVERY listed value that applies, as a separate
    entry with the same name. Two to four is normal.
- "(plain number)" takes a bare number, "(4-digit year)" a year like "1985".
  Text in either gets the listing rejected — omit instead.
- Physical size aspects (Item Height, Item Length, Item Width, Item Depth,
  Item Diameter, Item Weight) take a best-effort ESTIMATE of the ITEM (not its
  shipping box) judged from the photos, as a number with unit ("7 in").
- NEVER fill an aspect with "Not Specified", "Does Not Apply", "Unknown",
  "N/A", "Various" or "Other" — an empty box says the same thing honestly and
  those answers show up on the live listing.
- One entry per VALUE, not per aspect.
"""

_COVERAGE_SYSTEM = (
    "You are finishing an eBay listing that has already been drafted. The "
    "item has been identified and described; what is left is the item "
    "specifics nobody filled in.\n\n" + _COVERAGE_SCHEMA)

# The first pass gets eight photos because it is reading the item. This one
# already knows what the item is — it is carrying the finished title,
# description and specifics as context — so it needs enough frames to judge
# style, material and whether a box is in shot, and no more. Four keeps the
# marginal cost of going back for the blanks well under the first pass.
_COVERAGE_PHOTOS = 4


def _coverage_context(listing: Listing) -> str:
    """What the first pass already settled, so this one argues from it rather
    than starting over — and so a value it proposes has to agree with the
    listing the seller is going to publish."""
    held = [f"{s.name}: {s.value}" for s in (listing.item_specifics or [])
            if (s.name or "").strip() and (s.value or "").strip()]
    context = _listing_context(listing)
    if held:
        context += ("\nItem specifics ALREADY FILLED (do not repeat these; "
                    "your answers must agree with them):\n- "
                    + "\n- ".join(held[:60]))
    return context


def fill_missing_aspects(image_paths: list[Path], listing: Listing,
                         blanks: list[dict]) -> list[ItemSpecific]:
    """Second look at the item specifics `blanks` left empty. `blanks` is the
    taxonomy aspect list narrowed by taxonomy.fillable_blanks — identifiers
    are already gone from it, and this function does not put them back.

    Returns validated ItemSpecifics, the same shape fill_aspects returns, so
    the caller merges both through one path."""
    named = [a for a in blanks if a.get("name")]
    if not named or not image_paths:
        return []
    client = _client()
    content: list[dict] = [_image_block(p)
                           for p in image_paths[:_COVERAGE_PHOTOS]]
    content.append({"type": "text", "text": "CONTEXT:\n"
                    + _coverage_context(listing)})
    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=3000,
        system=[
            {"type": "text", "text": _COVERAGE_SYSTEM,
             "cache_control": {"type": "ephemeral"}},
            # Not cached: the blanks are what THIS listing is missing, so the
            # block changes listing to listing and a breakpoint here would
            # write a cache entry nothing ever reads.
            {"type": "text",
             "text": "ITEM SPECIFICS STILL BLANK:\n" + _aspect_lines(named)},
        ],
        messages=[{"role": "user", "content": content}],
    )
    _log_usage("fill_missing_aspects", resp)
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("the item-specifics response was cut off; try again")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _validate_specifics(_extract_json(text), named)

# ---------------------------------------------------------------------------
# Maker / manufacturer identification — a double-layer check.
#
# The generic identify pass rarely commits to a maker (it's told never to
# guess), so Brand/Maker/Manufacturer often ship empty. This dedicated pass
# behaves like a reverse-image lookup: layer 1 HUNTS for maker evidence in the
# photos and names a candidate; layer 2 independently tries to KNOCK IT DOWN.
# Only a candidate that survives both layers is written to the listing —
# accuracy over coverage, since a wrong maker is worse than a blank one.
# ---------------------------------------------------------------------------

# --- research: look the item UP, instead of remembering it ------------------
#
# Everything above this point asks a vision model what it thinks an item is.
# That is a memory test, and the two ways it fails both cost the seller the
# item: a piece it half-recognizes gets hedged into a lookalike ("Fanch Ledan
# STYLE lithograph", a genuine butcher cover called a "replica"), and a piece
# it cannot value gets a low number. Neither failure announces itself — the
# draft reads exactly as confident as a correct one.
#
# So the app now looks things up. This pass runs Claude with Anthropic's
# server-side WEB SEARCH tool over the same photos plus the draft, and its job
# is the one the identify pass cannot do from memory: is this a CATALOGUED
# thing with a name, is there a valuable variant it could be, and what do real
# examples actually go for. The answer is advice, not authority — main.py
# merges it under rules that can raise a price and name a work, and can never
# downgrade an item or lower a price.
#
# The tool type is pinned but overridable: web_search_20260209 needs Opus
# 4.6+/5 or Sonnet 4.6+ (which VISION_MODEL is by default). A deployment on an
# older model sets WEB_SEARCH_TOOL=web_search_20250305 rather than losing the
# pass to a 400.
WEB_SEARCH_TOOL = os.getenv("WEB_SEARCH_TOOL", "web_search_20260209").strip()
# How many searches one item may run, and how many times a paused server-tool
# turn may be resumed. Both bound the cost of a pass that is otherwise open.
RESEARCH_MAX_SEARCHES = int(os.getenv("RESEARCH_MAX_SEARCHES", "6") or "6")
_RESEARCH_MAX_RESUMES = 2

_RESEARCH_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{
  "identified": "one line: what this item specifically IS, or \"\" if the search could not settle it",
  "maker": "artist, author, manufacturer or brand — \"\" if unresolved",
  "work": "the name of the specific work, pattern, model or release — \"\" if unresolved",
  "variant": "the specific edition/state/pressing/printing this appears to be, with what in the photos says so — \"\" if unresolved",
  "title": "an eBay title <= 80 chars for what you actually established, or \"\" to leave the draft's alone",
  "value_low": number or null,
  "value_high": number or null,
  "value_basis": "what those figures come from — sold listings, auction records, dealer prices — and their date if you saw one",
  "high_value_variant": "if a MORE VALUABLE variant of this item exists and the photos cannot rule it out: name it, what it is worth, and what physically distinguishes it. \"\" when there is no such trap",
  "verify": ["what the SELLER must physically check to settle the above — specific, checkable things"],
  "sources": ["urls you actually used"],
  "confidence": "low|medium|high"
}
Rules:
- SEARCH before you answer. You are here because memory alone already got this
  wrong. Search the maker and the work, the identifying marks, and what
  comparable examples SOLD for. Prefer sold/auction records over asking prices.
- Your job is the name and the money, in that order. Collectors buy the name:
  a named work ("Three Matisses"), a state or pressing (a first-state stereo
  butcher cover), a printing or error variant, a production year. Find it.
- NEVER resolve a doubt downward. You may not conclude "replica", "reprint",
  "style of", "tribute" or "not authentic" from the photos and a search — the
  seller has the item in their hands and you do not. When you cannot confirm,
  leave the fields "" and put what to check in verify. Absence of proof is not
  proof, and calling a real thing a copy is the expensive direction of wrong.
- high_value_variant is the most important field. Ask: what is the version of
  this item that is worth 10x or 100x, and could THIS be it? A genuine Beatles
  butcher cover called a "replica" sold for $22 and was worth over $7,000 —
  that is the failure this field exists to prevent. Fill it whenever the trap
  exists, even at low confidence, and say what would settle it.
- value_low/value_high describe what THIS item is worth as best you can
  establish, in USD. If the range depends on which variant it is, use the range
  for the variant the photos support and name the other one in
  high_value_variant. null when the search genuinely could not price it —
  never a placeholder number.
- Cite what you used in sources. A finding with no source is a guess wearing a
  suit, and it will be treated as one.
"""


def research_item(image_paths: list[Path], listing: Listing,
                  observations: str = "") -> Optional[dict]:
    """Look the drafted item up on the web. Returns the parsed research dict,
    or None when the pass did not run or produced nothing usable.

    Best-effort by contract: every caller treats a failure as "no research".
    Raises nothing.
    """
    if not image_paths:
        return None
    try:
        client = _client()
        imgs = [_image_block(p) for p in image_paths[:4]]
        specifics = "; ".join(
            f"{s.name}: {s.value}" for s in (listing.item_specifics or [])[:12]
            if (s.value or "").strip())
        context = (
            "A first-pass AI drafted this listing FROM THE PHOTOS ALONE, with no "
            "lookup of any kind. Check it.\n\n"
            f"Drafted title: {listing.title}\n"
            f"Drafted brand: {listing.brand or '(none)'}\n"
            f"Drafted price: {'(none)' if listing.price is None else f'${listing.price:.2f}'}\n"
            f"Category: {listing.category_suggestion or '(unknown)'}\n"
            f"Specifics: {specifics or '(none)'}\n"
            f"What the first pass saw: {(observations or '')[:600]}\n\n"
            "Establish what this actually is and what it is actually worth."
            + _RESEARCH_SCHEMA)
        messages = [{"role": "user", "content": imgs + [{"type": "text",
                                                         "text": context}]}]
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search",
                  "max_uses": RESEARCH_MAX_SEARCHES}]
        resp = client.messages.create(
            model=config.VISION_MODEL, max_tokens=4096,
            tools=tools, messages=messages)
        # A server-tool loop that hits its iteration limit comes back paused,
        # not finished. Resending the turn resumes it; the API needs no extra
        # user message and adding one would derail it.
        resumes = 0
        while resp.stop_reason == "pause_turn" and resumes < _RESEARCH_MAX_RESUMES:
            resumes += 1
            messages = messages + [{"role": "assistant", "content": resp.content}]
            resp = client.messages.create(
                model=config.VISION_MODEL, max_tokens=4096,
                tools=tools, messages=messages)
        _log_usage("research", resp)
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = _extract_json(text)
    except Exception as exc:  # noqa: BLE001 - a draft is worth more than a lookup
        log.info("research skipped: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    searches = sum(1 for b in resp.content
                   if getattr(b, "type", "") == "server_tool_use")
    log.info("research: %d searches -> %r (confidence=%s, %s-%s)", searches,
             str(data.get("identified", ""))[:80], data.get("confidence"),
             data.get("value_low"), data.get("value_high"))
    return data


# --- the art lookup: name the artist and the work ---------------------------
#
# A print is worth what its NAME is worth, and the identify pass can only
# read a name that is printed on it. This pass is for the print that carries
# none: read whatever text there is, recognise the image, take a reverse
# image search's matches as leads, and confirm against a catalogue, a museum
# page or an auction record before naming anyone. Same web-search tool and
# the same never-downward rule as the research pass; main.py folds the answer
# in under rules that can name an artist and a work and can never demote a
# print to a poster.
_ART_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{
  "artist": "the artist's name as catalogued, or \"\" if not established",
  "work": "the title of this specific work, or \"\" if not established",
  "kind": "original | hand-signed limited edition print | open edition print | poster or reproduction -- or \"\" if the photos cannot say",
  "year": "the year or period of the work or of this edition, or \"\"",
  "publisher": "the publisher, printer or gallery named on the print, or \"\"",
  "read_from_print": "the text you could actually read ON the print -- signature, printed title, edition number, publisher or copyright line -- or \"\"",
  "evidence": "one or two sentences: what settled the artist and the work (text on the print, a reverse-image match, a composition you recognised) and which source confirmed it",
  "title": "an eBay title <= 80 chars that LEADS with the artist's name, then the work's title, then what kind of print it is -- or \"\" if unresolved",
  "verify": ["what the SELLER must physically check: an edition number, a pencil signature, a blind stamp, a plate mark, a watermark, the paper"],
  "sources": ["urls you actually used"],
  "confidence": "low|medium|high"
}
Rules:
- READ THE PRINT FIRST. A signature, a printed title or caption, a publisher
  or gallery line, an edition number, a copyright notice: transcribe what is
  there before anything else. When the print names its artist and its work,
  that is the answer -- confirm it with one search and stop.
- When the print carries no name, IDENTIFY THE IMAGE. A well-known work is
  recognisable by its composition, palette and subject; name the work you
  recognise, then SEARCH to confirm it against the artist's catalogue, a
  museum page, or a gallery or auction record. If a reverse image search
  found matches they are listed below: read them as LEADS, not as the
  answer. A match titled for the work is strong evidence; a match to a poster
  shop says something about the edition, not about the artist.
- Tell an ORIGINAL from a hand-signed LIMITED EDITION from an OPEN EDITION
  print from a POSTER. Their prices differ by orders of magnitude. Say which
  the photos support and put what would settle it in verify. NEVER resolve a
  doubt downward: an unsigned print is not proof of a poster and a familiar
  image is not proof of a reproduction -- the seller is holding it and you
  are not.
- If nothing settled the artist, leave artist and work "" and say so with
  confidence "low". A guessed attribution is worse than a blank.
- Cite what you used in sources. A name with no source is a guess.
"""


def identify_artwork(image_paths: list[Path], listing: Listing,
                     leads: Optional[list[dict]] = None,
                     observations: str = "") -> Optional[dict]:
    """Name the artist and the work behind a print, with web search and any
    reverse-image leads the caller found. Returns the parsed dict, or None
    when the pass did not run or produced nothing usable.

    Best-effort by contract, like research_item: every caller treats a
    failure as "no answer". Raises nothing.
    """
    if not image_paths:
        return None
    try:
        client = _client()
        imgs = [_image_block(p) for p in image_paths[:4]]
        lead_lines = "\n".join(
            f"  - {lead.get('title')}"
            + (f" ({lead.get('source')})" if lead.get("source") else "")
            + (f" {lead.get('link')}" if lead.get("link") else "")
            for lead in (leads or [])[:12] if lead.get("title"))
        context = (
            "A first-pass AI drafted this listing FROM THE PHOTOS ALONE and "
            "could not name the artist or the work. Establish both.\n\n"
            f"Drafted title: {listing.title}\n"
            f"Drafted artist/brand: {listing.brand or '(none)'}\n"
            f"Category: {listing.category_suggestion or '(unknown)'}\n"
            f"What the first pass saw: {(observations or '')[:600]}\n"
            + (f"\nReverse image search matches for the first photo:\n"
               f"{lead_lines}\n" if lead_lines
               else "\nNo reverse image search was available for this photo.\n")
            + _ART_SCHEMA)
        messages = [{"role": "user", "content": imgs + [{"type": "text",
                                                         "text": context}]}]
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search",
                  "max_uses": RESEARCH_MAX_SEARCHES}]
        resp = client.messages.create(
            model=config.VISION_MODEL, max_tokens=4096,
            tools=tools, messages=messages)
        resumes = 0
        while resp.stop_reason == "pause_turn" and resumes < _RESEARCH_MAX_RESUMES:
            resumes += 1
            messages = messages + [{"role": "assistant", "content": resp.content}]
            resp = client.messages.create(
                model=config.VISION_MODEL, max_tokens=4096,
                tools=tools, messages=messages)
        _log_usage("artwork", resp)
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = _extract_json(text)
    except Exception as exc:  # noqa: BLE001 - a draft is worth more than a lookup
        log.info("art lookup skipped: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    searches = sum(1 for b in resp.content
                   if getattr(b, "type", "") == "server_tool_use")
    log.info("art lookup: %d searches -> %r by %r (confidence=%s)", searches,
             str(data.get("work", ""))[:80], str(data.get("artist", ""))[:60],
             data.get("confidence"))
    return data


_MAKER_HUNT_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{
  "maker": "the exact maker/manufacturer/brand name, or \\"\\" if none is identifiable",
  "evidence": "concise: what in the photos supports this (logo, tag text, stamp, hallmark, signature, model plate, distinctive design)",
  "confidence": "low|medium|high"
}
Rules:
- Inspect EVERY photo closely for maker evidence: logos, embossed or printed
  marks, sewn tags, labels, stamps, hallmarks, signatures, serial/model
  plates, packaging text, and distinctive design signatures (typography,
  colorways, stitching, hardware, patterns) you recognize.
- Work like a reverse-image search: match what you actually see against known
  makers in this product category.
- Name the MOST SPECIFIC maker the evidence supports (e.g. "Fenton" not
  "a glass maker"; "Pyrex" not "vintage glassware").
- If the photos genuinely show no identifiable maker, return "" — never guess.
"""

_MAKER_VERIFY_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{
  "verdict": "confirm|reject|unsure",
  "reason": "one concise sentence",
  "confidence": "low|medium|high"
}
Rules:
- You are the SECOND, INDEPENDENT layer of a two-step identification check.
  Do not take the proposal on trust — re-derive the maker from the photos
  yourself, then compare.
- confirm ONLY when the visual evidence genuinely supports this exact maker:
  the mark/logo/tag matches, and the item is consistent with that maker's
  actual product lines. Watch for lookalikes, licensed reproductions, store
  brands, and counterfeits — any of those means do NOT confirm.
- reject when the evidence contradicts the proposal or points to a different
  maker. unsure when the photos can't settle it.
"""


def verify_maker(image_paths: list[Path], listing: Listing,
                 maker: str, evidence: str = "") -> Optional[dict]:
    """The adversarial second layer of the maker check: an independent look at
    the photos that tries to knock the proposal down (lookalikes, licensed
    reproductions, store brands, counterfeits all mean no). Returns
    {"maker", "evidence", "confidence"} only when it confirms with at least
    medium confidence; None otherwise. Raises on API errors."""
    if not maker or not image_paths:
        return None
    client = _client()
    imgs = [_image_block(p) for p in image_paths[:8]]
    context = (f"Item: {listing.title}\n"
               f"Category: {listing.category_suggestion or 'unknown'}")
    verify = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": imgs + [{"type": "text", "text": (
            "An identification system proposed that this item's maker is "
            f"\"{maker}\", based on: {evidence or 'unspecified evidence'}.\n"
            "CONTEXT:\n" + context + "\n\nIndependently verify that proposal "
            "against the photos." + _MAKER_VERIFY_SCHEMA)}]}],
    )
    _log_usage("verify_maker", verify)
    vdata = _extract_json("".join(b.text for b in verify.content if b.type == "text"))
    verdict = str(vdata.get("verdict", "")).strip().lower()
    vconf = str(vdata.get("confidence", "low")).strip().lower()
    if verdict != "confirm" or vconf not in ("medium", "high"):
        return None
    return {"maker": maker, "evidence": evidence, "confidence": vconf}


def identify_maker(image_paths: list[Path], listing: Listing) -> Optional[dict]:
    """Double-layer maker/manufacturer identification from the photos.

    Layer 1 hunts for a maker candidate with evidence; layer 2 (verify_maker)
    independently verifies it. Returns {"maker", "evidence", "confidence"}
    only when the verifier confirms with at least medium confidence; None
    otherwise. Raises on API errors — the caller treats this enrichment as
    best-effort."""
    if not image_paths:
        return None
    client = _client()
    imgs = [_image_block(p) for p in image_paths[:8]]
    context = (f"Item: {listing.title}\n"
               f"Category: {listing.category_suggestion or 'unknown'}")

    hunt = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=700,
        messages=[{"role": "user", "content": imgs + [{"type": "text", "text": (
            "You are an expert product identifier and appraiser hunting for the "
            "maker of this item.\n\nCONTEXT:\n" + context + "\n" + _MAKER_HUNT_SCHEMA)}]}],
    )
    _log_usage("maker_hunt", hunt)
    data = _extract_json("".join(b.text for b in hunt.content if b.type == "text"))
    maker = str(data.get("maker", "")).strip()
    evidence = str(data.get("evidence", "")).strip()
    if not maker or len(maker) > 65:
        return None
    return verify_maker(image_paths, listing, maker, evidence)
