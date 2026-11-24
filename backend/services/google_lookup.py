"""Google image search in AI Mode: what the item IS, and what it SELLS for.

This is the app's primary identifier and its primary price. It asks Google the
question a seller asks when they point the Google app at the thing in their
hand: here is a photo, what is it and what is it worth? Google answers that by
matching the PHOTO against the web — product pages, sold listings, catalogues,
collector sites — and its AI writes the answer from what it matched.

Nothing else in this app can do that first half. Every other pass reads the
photo with a vision model and answers from memory: a model can see a bowl, but
it cannot see that this exact bowl is the 1957 Pyrex Butterprint 403 that
sells for $45, so it hedges, names a lookalike, and prices the lookalike. A
seller comparing the two side by side found Google right every time and this
app's price low, which is the expensive direction of wrong: an underpriced
item sells, and sells for less than it was worth.

So the order is now Google first, everything else after:

  * main runs this at the guidance step, before anything is drafted, and the
    seller reviews Google's answer for each item (approve it, or type a
    correction and ask again);
  * the vision model then WRITES the listing — title, specifics, description,
    condition — about the item Google named (listing_prompt.google_block);
  * the price comes from Google's range for that item's condition, at the
    point of it the account's pricing strategy picks (`price_range`, `pick`);
  * the photos-only guess, the grounded research pass and the eBay keyword
    comps run only when this could not answer.

Spoken over SerpApi's `google_ai_mode` engine, which takes the photo as an
`image_url` beside the question — the same image-plus-question Google's own
AI Mode answers. Google fetches the photo itself, so it needs a URL it can
reach: main hands it one from the bucket. Same key as the Google Lens reverse
image search (services.imagesearch), and the same latch when SerpApi refuses
it: one key, one decision.

Never raises. `lookup` always returns a dict whose `status` says whether there
is an answer, because every caller has a draft to write either way and the
seller is owed a sentence about why Google is not on their card.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

from .. import config
from ..config import log
from . import imagesearch

SERPAPI_URL = "https://serpapi.com/search.json"
ENGINE = "google_ai_mode"

# AI Mode reads the photo, searches, and writes an answer — slower than a
# plain search. The bound is the seller waiting on a card, not a draft.
_TIMEOUT = config.env_float("GOOGLE_AI_MODE_TIMEOUT_SECONDS", 75.0)

# What the seller sees on the review card, and what the identify prompt is
# handed. Long enough for Google's whole answer to a "what is this" question;
# bounded because it is third-party text going into a prompt and onto a page.
SUMMARY_MAX_CHARS = 1800
MAX_SOURCES = 5
HINT_MAX_CHARS = 300

# The labelled lines the question asks Google to end on. Read case-blind and
# with or without markdown around them; see `_labelled`.
_LABELS = ("ITEM", "BRAND", "USED PRICE", "NEW PRICE", "CONFIDENCE")


def enabled() -> bool:
    """Configured, and the key not refused. Whether a photo URL can be had
    (R2) is the caller's to check, since only the caller has the photo."""
    return config.google_ai_mode_ready() and imagesearch.enabled()


def question(hint: str = "") -> str:
    """What Google is asked about the photo.

    Plain words first, because that is what AI Mode answers best: what is it,
    and what does it sell for — used AND new, since the condition is the
    vision pass's to read and comes after this. Then the seller's own line,
    when they have corrected an earlier answer. Then the labelled lines, which
    are what make the answer machine-readable without a second model.
    """
    ask = ("What exactly is the item in this photo? Name the brand or maker, "
           "the model or product name, and the variant, edition, pattern, "
           "size or year where it can be told. Then price it for resale in US "
           "dollars: what it actually SELLS for used (recent sold listings on "
           "eBay and similar sites), and what it sells for new.")
    hint = " ".join(str(hint or "").split())[:HINT_MAX_CHARS]
    if hint:
        ask += f" The owner says: {hint}"
    ask += (" End your answer with exactly these lines: "
            "ITEM: <full name> | BRAND: <brand or maker> | "
            "USED PRICE: $<low> - $<high> | NEW PRICE: $<low> - $<high> or none | "
            "CONFIDENCE: high, medium or low")
    return ask


def lookup(image_url: str, hint: str = "") -> dict:
    """Ask Google about the photo at `image_url`. Never raises.

    {"status": "done", "identified", "brand", "summary", "used_low",
     "used_high", "new_low", "new_high", "confidence", "sources", "hint",
     "at"} when Google answered, or {"status": "failed", "reason", "hint",
    "at"} when it could not be asked or had nothing to say. `reason` is a
    sentence for the seller.
    """
    hint = " ".join(str(hint or "").split())[:HINT_MAX_CHARS]
    if not enabled():
        return failed("Google image search isn't set up on this server.", hint)
    if not image_url:
        return failed("Couldn't hand Google this photo.", hint)
    started = time.monotonic()
    try:
        resp = httpx.get(SERPAPI_URL, params={
            "engine": ENGINE, "q": question(hint), "image_url": image_url,
            "hl": "en", "gl": "us", "api_key": config.SERPAPI_KEY,
        }, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - the draft is worth more
        # The type and status only: httpx puts the request URL in its
        # messages, and the request URL carries the key.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            imagesearch.refuse_key(status)
            return failed("Google image search isn't available right now.", hint)
        log.warning("google lookup: failed (%s%s)", type(exc).__name__,
                    f" {status}" if status else "")
        return failed("Couldn't reach Google — try again in a moment.", hint)
    found = parse(data, hint)
    log.info("google lookup: %s in %.1fs -> %r (used %s-%s, new %s-%s, %s)",
             found["status"], time.monotonic() - started,
             found.get("identified", "")[:80], found.get("used_low"),
             found.get("used_high"), found.get("new_low"),
             found.get("new_high"), found.get("confidence"))
    return found


def failed(reason: str, hint: str = "") -> dict:
    return {"status": "failed", "reason": reason, "hint": hint,
            "at": time.time()}


# --- reading the answer -----------------------------------------------------
#
# Defensive about shape on purpose. SerpApi hands back the answer two ways —
# `reconstructed_markdown`, the whole answer as one markdown string, and
# `text_blocks`, the same answer as typed blocks (paragraph, heading, list,
# table, code) — and neither Google's layout nor the scraper's field names
# are a contract. The markdown is preferred; the blocks are walked for any
# text they hold when it is missing.

# Keys inside a block whose values are never answer text.
_NOT_TEXT = {"reference_indexes", "snippet_highlighted_words", "link",
             "thumbnail", "image", "images", "source_icon", "favicon",
             "serpapi_link", "type", "index", "position"}


def _walk(node, out: list[str]) -> None:
    if isinstance(node, str):
        text = node.strip()
        if text:
            out.append(text)
        return
    if isinstance(node, list):
        # A table arrives as rows of cells; one row, one line.
        if node and all(isinstance(r, list) for r in node):
            for row in node:
                cells = [str(c).strip() for c in row if isinstance(c, (str, int, float))]
                if any(cells):
                    out.append(" | ".join(cells))
            return
        for child in node:
            _walk(child, out)
        return
    if not isinstance(node, dict):
        return
    # Title before body, then whatever the block nests (a list's items, a
    # table's rows).
    for key in ("title", "snippet", "text", "code"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    for key, value in node.items():
        if key in _NOT_TEXT or key in ("title", "snippet", "text", "code"):
            continue
        if isinstance(value, (list, dict)):
            _walk(value, out)


def answer_text(data) -> str:
    """Google's answer as one string: the markdown when SerpApi sent it, the
    text of the blocks otherwise. "" when there is no answer."""
    if not isinstance(data, dict):
        return ""
    markdown = data.get("reconstructed_markdown")
    if isinstance(markdown, str) and markdown.strip():
        return markdown.strip()
    out: list[str] = []
    _walk(data.get("text_blocks") or [], out)
    return "\n".join(out).strip()


def sources(data) -> list[dict]:
    """The pages Google's answer cites, as [{"title", "link", "source"}]."""
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for ref in data.get("references") or []:
        if not isinstance(ref, dict):
            continue
        link = str(ref.get("link") or "").strip()
        if not link.startswith(("https://", "http://")):
            continue
        out.append({"title": " ".join(str(ref.get("title") or "").split())[:140],
                    "link": link[:400],
                    "source": " ".join(str(ref.get("source") or "").split())[:60]})
        if len(out) >= MAX_SOURCES:
            break
    return out


_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*([kK])?\b"
_RANGE = re.compile(r"\$\s?" + _AMOUNT + r"\s*(?:-|–|—|to)\s*\$?\s?" + _AMOUNT)
_SINGLE = re.compile(r"\$\s?" + _AMOUNT)


def _number(whole: str, cents: Optional[str], thousands: Optional[str]) -> Optional[float]:
    try:
        value = float(whole.replace(",", "") + (f".{cents}" if cents else ""))
    except ValueError:
        return None
    if thousands:
        value *= 1000
    return value if 0 < value < 10_000_000 else None


def price_span(text: str) -> Optional[tuple[float, float]]:
    """The first dollar range in `text` ("$40 - $60", "$1.2k to $2k"), or a
    single dollar amount as a range of one. None when there is no price, or
    the text says there is none."""
    if not text or re.search(r"\b(none|n/?a|unknown|not sold new)\b", text, re.I) \
            and "$" not in text:
        return None
    m = _RANGE.search(text)
    if m:
        low = _number(m.group(1), m.group(2), m.group(3))
        high = _number(m.group(4), m.group(5), m.group(6))
        if low and high:
            return (min(low, high), max(low, high))
    m = _SINGLE.search(text)
    if m:
        value = _number(m.group(1), m.group(2), m.group(3))
        if value:
            return (value, value)
    return None


def _split_labels(text: str) -> str:
    """The answer with markdown emphasis dropped ("**ITEM:** Pyrex 403" is the
    usual rendering) and every labelled field on a line of its own, because an
    answer that ran them together on one line, pipes and all, is the other
    usual rendering. Case-sensitive on purpose: the labels are asked for in
    capitals, and "Item:" in Google's own prose is prose."""
    flat = text.replace("**", "").replace("__", "")
    labels = "|".join(_LABELS)
    flat = re.sub(r"[ \t]*\|[ \t]*(?=(?:%s)\s*:)" % labels, "\n", flat)
    return re.sub(r"(?<=\S)[ \t]+(?=(?:%s)\s*:)" % labels, "\n", flat)


def _labelled(text: str) -> dict[str, str]:
    """The `LABEL: value` lines the question asked Google to end on."""
    found: dict[str, str] = {}
    for line in _split_labels(text).splitlines():
        m = re.match(r"^[\s>*\-•#\d.)]*(%s)\s*:\s*(.+?)\s*$" % "|".join(_LABELS),
                     line, re.I)
        if m:
            # The LAST one wins: the labelled block is what the answer was
            # asked to end on, and an "Item:" earlier in Google's own prose is
            # the line most likely to be something else.
            found[m.group(1).upper()] = m.group(2).strip(" |*_")
    return found


_USED_WORDS = re.compile(r"\b(used|pre-?owned|second-?hand|sold|resale|resell|"
                         r"ebay|vintage|secondhand|thrift)\b", re.I)
_NEW_WORDS = re.compile(r"\b(new|retail|msrp|brand-?new|sealed|nwt)\b", re.I)


def _prose_prices(text: str) -> tuple[Optional[tuple], Optional[tuple]]:
    """(used, new) ranges out of an answer that did not label them: each range
    goes to whichever word family sits closest before it in its sentence."""
    used = new = None
    for m in _RANGE.finditer(text):
        span = price_span(m.group(0))
        if not span:
            continue
        before = text[max(0, m.start() - 160):m.start()]
        sentence = re.split(r"[.!?\n]", before)[-1]
        if _NEW_WORDS.search(sentence) and not _USED_WORDS.search(sentence):
            new = new or span
        else:
            used = used or span
        if used and new:
            break
    return used, new


def _first_sentence(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"^[#>*\-•\s]+", "", line).replace("**", "").strip()
        if line:
            return re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0][:140]
    return ""


def _display(text: str) -> str:
    """The answer as the seller reads it: Google's prose, without the
    labelled lines (they are on the card already, as fields) and without the
    markdown punctuation a plain-text card would print literally."""
    lines: list[str] = []
    for raw in _split_labels(text).splitlines():
        line = raw.rstrip()
        bare = line.strip()
        if re.match(r"^[\s>*\-•#\d.)]*(%s)\s*:" % "|".join(_LABELS), bare, re.I):
            continue
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^(\s*)[*\-]\s+", r"\1• ", line)
        # Citation markers like [1] or [2, 3] point at a list the card does
        # not print inline.
        line = re.sub(r"\s*\[\d+(?:,\s*\d+)*\]", "", line)
        lines.append(line)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(out) <= SUMMARY_MAX_CHARS:
        return out
    cut = out[:SUMMARY_MAX_CHARS]
    stop = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[:stop + 1] if stop > SUMMARY_MAX_CHARS // 2 else cut).rstrip() + " …"


def parse(data, hint: str = "") -> dict:
    """A SerpApi google_ai_mode payload as this app's lookup dict.

    Split from the fetch so the shape can be pinned without a live key — the
    same split services/imagesearch.parse_leads makes.
    """
    if isinstance(data, dict) and data.get("error") and not data.get("text_blocks") \
            and not data.get("reconstructed_markdown"):
        log.info("google lookup: no answer (%s)", str(data.get("error"))[:160])
        return failed("Google didn't have an answer for this photo.", hint)
    text = answer_text(data)
    if not text:
        return failed("Google didn't have an answer for this photo.", hint)
    labelled = _labelled(text)
    identified = labelled.get("ITEM", "") or _first_sentence(text)
    used = price_span(labelled.get("USED PRICE", ""))
    new = price_span(labelled.get("NEW PRICE", ""))
    if not used and not new and "USED PRICE" not in labelled:
        used, new = _prose_prices(text)
    confidence = labelled.get("CONFIDENCE", "").strip().lower().split(" ")[0] \
        if labelled.get("CONFIDENCE") else ""
    if confidence not in ("high", "medium", "low"):
        confidence = "medium" if (identified and (used or new)) else "low"
    return {
        "status": "done",
        "identified": " ".join(identified.split())[:160],
        "brand": " ".join(labelled.get("BRAND", "").split())[:65],
        "summary": _display(text),
        "used_low": used[0] if used else None,
        "used_high": used[1] if used else None,
        "new_low": new[0] if new else None,
        "new_high": new[1] if new else None,
        "confidence": confidence,
        "sources": sources(data),
        "hint": hint,
        "at": time.time(),
    }


# --- the price --------------------------------------------------------------

def price_range(found: Optional[dict], new: bool) -> Optional[tuple[float, float]]:
    """Google's range for an item in this condition, or None.

    A NEW item takes the new range, and the used one when Google gave no new
    price (a new one is worth at least what used ones sell for). A USED item
    takes the used range only: pricing a worn thing off what it costs in a
    shop is the opposite mistake, so without a used range the price is left to
    the fallbacks.
    """
    if not found or found.get("status") != "done":
        return None

    def span(lo_key: str, hi_key: str) -> Optional[tuple[float, float]]:
        try:
            low = float(found.get(lo_key) or 0)
            high = float(found.get(hi_key) or 0) or low
        except (TypeError, ValueError):
            return None
        if low <= 0:
            return None
        return (min(low, high), max(low, high))

    used = span("used_low", "used_high")
    if new:
        return span("new_low", "new_high") or used
    return used


def pick(low: float, high: float, strategy: str = "") -> float:
    """Where in Google's range the listing price lands, by the account's
    pricing strategy: the low end for a quick flip, the high end for a
    patient sale, the middle otherwise."""
    if strategy == "quick_flip":
        return low
    if strategy == "long_sale":
        return high
    return (low + high) / 2


def usable(found: Optional[dict]) -> bool:
    """Whether a lookup is an answer the draft can be built on."""
    return bool(found and found.get("status") == "done"
                and (found.get("identified") or found.get("summary")))
