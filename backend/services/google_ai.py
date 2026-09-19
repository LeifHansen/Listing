"""Google Gemini as the item identifier: photos in, a priced listing out.

The identify pass used to be Claude's alone, and it was asked to do two jobs
at once — recognise the item AND say what it is worth — from the photos and
nothing else. The second job has no answer in a photo. A model can see that a
jacket is a Carhartt Detroit; what that jacket SELLS for lives on the open
market and changes weekly, so a price from a model's memory is a price from
whenever its training stopped.

So this backend splits them and gives each one what it needs:

  * `identify()` sends the photos to Gemini's vision model under the SAME
    listing schema Claude drafts against (services.listing_prompt), so the
    draft that comes back is the same shape the whole app already consumes —
    same fields, same conditions enums, same tag boxes, same barcodes.
  * `research_item()` runs the pricing and verification pass with GOOGLE
    SEARCH GROUNDING switched on. Gemini searches before it answers and hands
    back the pages it used, so a price comes with a source instead of a
    vibe — the one thing the photos-only pass structurally cannot do.

Everything is spoken over the REST API with httpx, which the app already
depends on, rather than pulling in another vendor SDK for two endpoints.
"""
from __future__ import annotations

import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .. import config
from ..config import log
from ..models import IdentifyResult, Listing
from . import barcodes

API_ROOT = os.getenv(
    "GOOGLE_AI_API_ROOT",
    "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

# Same bounds the Anthropic client runs under, and for the same reason: a
# wedged request must not outlive the seller waiting on it (the client gives
# up on a job at 240s) or pin a bulk worker thread while it does.
_TIMEOUT_SECONDS = config.env_float("AI_TIMEOUT_SECONDS", 120.0)
_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2") or 2)
# Seconds before the second attempt, doubling after it. A module constant so
# the tests of the retry can pin it to zero instead of sleeping through it.
_RETRY_BACKOFF_SECONDS = config.env_float("GOOGLE_RETRY_BACKOFF_SECONDS", 2.0)

# Identify drafts a 300-600 word description alongside the specifics, the tag
# boxes and the observations — and on a 2.5 model the thinking tokens come out
# of this same budget. A draft that runs past the cap is not a short
# description, it is unparseable JSON, so the headroom is worth more than the
# unused tokens (only what is generated is billed).
_IDENTIFY_MAX_TOKENS = int(os.getenv("GOOGLE_IDENTIFY_MAX_TOKENS", "16384") or 16384)
_RESEARCH_MAX_TOKENS = int(os.getenv("GOOGLE_RESEARCH_MAX_TOKENS", "8192") or 8192)

# Photos per request, matching the Claude path so the two backends read the
# same pile and draft from the same evidence.
_MAX_IDENTIFY_IMAGES = 8
_MAX_RESEARCH_IMAGES = 4


# The model actually in use, once a call has proved the configured one wrong
# and discovery has picked a replacement. Process-wide: one lookup, not one
# per photo.
_RESOLVED_MODEL: Optional[str] = None
_MODEL_LOCK = threading.Lock()

# Names that answer generateContent but are not what "look at these photos"
# means. Filtered out of discovery so a fallback can never land on an
# embedder or an image generator.
_NOT_A_VISION_MODEL = ("embedding", "embed", "aqa", "imagen", "veo", "tts",
                       "image-generation", "-image", "live", "native-audio",
                       "computer-use", "robotics", "learnlm", "gemma")


class GoogleAIError(RuntimeError):
    """A Gemini call that failed, carrying the HTTP status it failed with.

    Exists so `claude_ai.ai_error_message` can say the same specific things
    about Google's limits (quota, a bad key, a safety block) that it already
    says about Anthropic's, instead of falling through to a raw string.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status_code = status


class GoogleAIRefused(GoogleAIError):
    """Gemini's safety filter stopped the answer; there is no text to read.

    Retrying does not change the photos, so this is said as what it is rather
    than as "the AI's answer couldn't be read — try again".
    """


def ready() -> bool:
    return config.google_ai_ready()


def error_message(exc: Exception) -> Optional[tuple[int, str]]:
    """(http_status, user-facing sentence) for a Gemini failure, or None.

    None means "not one of mine" — the caller falls through to its own
    mapping. Same contract as claude_ai.ai_error_message minus the fallback,
    which stays with the caller so there is only one of them.
    """
    if not isinstance(exc, GoogleAIError):
        return None
    if isinstance(exc, GoogleAIRefused):
        return 422, str(exc)
    status = exc.status_code
    body = str(exc).lower()
    if status == 429 or "rate limit" in body or "quota" in body:
        return 429, ("Google's AI is rate-limited or over quota right now — "
                     "wait a few seconds and try again.")
    if status in (401, 403) or "api key" in body or "permission" in body:
        return 400, ("The Google AI credentials on the server are missing or "
                     "invalid (check GOOGLE_API_KEY).")
    if status in (500, 502, 503, 504) or "overloaded" in body or "unavailable" in body:
        return 503, ("Google's AI is busy or unreachable — try again in a "
                     "moment.")
    if "timed out" in body or "timeout" in body or "connect" in body:
        return 503, ("Couldn't reach Google's AI — check the connection and "
                     "try again in a moment.")
    return 502, f"Google AI request failed: {str(exc)[:200]}"


# --- which model ------------------------------------------------------------
#
# Google turns Gemini model ids over fast, and retires the old ones: a name
# that is right today answers 404 NOT_FOUND in a year, and a default nobody
# has touched is exactly the name that goes stale. That failure is silent
# until a seller presses Identify, and then it is total.
#
# So the configured name is a preference, not a contract. When a call says
# that model does not exist, the key's OWN model list decides the
# replacement — discovery, once, cached for the process — and the log says
# what it picked. An operator who wants a specific model still pins
# GOOGLE_VISION_MODEL and gets it; what they cannot get is a dead app
# because a name expired.

def _model_missing(status: Optional[int], message: str) -> bool:
    body = (message or "").lower()
    return status == 404 or "not found" in body or "is not supported" in body


def _version_key(name: str) -> tuple:
    """Sort key that puts the newest, most finished model first.

    Sorted DESCENDING, so bigger is better: the version number first, then a
    stable release ahead of a preview or an experiment of the same version.
    """
    digits = re.findall(r"\d+", name)
    version = tuple(int(d) for d in digits[:2]) or (0,)
    settled = 0 if any(w in name for w in ("preview", "exp", "latest")) else 1
    return (version, settled)


def _rank(names: list[str], prefer_tier: str) -> list[str]:
    """The candidates, best first: right tier, then newest, then settled."""
    def key(name: str) -> tuple:
        return (1 if prefer_tier and prefer_tier in name else 0,
                *_version_key(name))
    return sorted(names, key=key, reverse=True)


def _discover_model() -> Optional[str]:
    """Ask the key what it can actually call, and pick the best fit.

    Best-effort: returns None when the list cannot be read, and the caller
    then fails with the original error — which is the honest outcome. A
    clear "that model does not exist" beats a silent switch to something
    nobody chose.
    """
    try:
        resp = httpx.get(f"{API_ROOT}/models",
                         params={"pageSize": 1000},
                         headers={"x-goog-api-key": config.GOOGLE_API_KEY},
                         timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        listed = resp.json().get("models") or []
    except Exception as exc:  # noqa: BLE001 - discovery is a rescue, not a step
        log.warning("google: could not list models (%s)", exc)
        return None

    names = []
    for entry in listed:
        name = str(entry.get("name") or "").removeprefix("models/")
        methods = entry.get("supportedGenerationMethods") or []
        if not name.startswith("gemini"):
            continue
        if methods and "generateContent" not in methods:
            continue
        if any(bad in name for bad in _NOT_A_VISION_MODEL):
            continue
        names.append(name)
    if not names:
        return None

    # Follow the tier the deployment asked for: a server configured for pro
    # must not silently drop to flash because flash happens to sort first.
    wanted = (config.GOOGLE_VISION_MODEL or "").lower()
    tier = "pro" if "pro" in wanted else ("flash" if "flash" in wanted else "")
    return _rank(names, tier)[0]


def _model() -> str:
    """The model id to call: the configured one until it is proved gone."""
    return _RESOLVED_MODEL or config.GOOGLE_VISION_MODEL


def _replace_missing_model(missing: str) -> Optional[str]:
    """Pick a stand-in for a model that no longer exists. None if we cannot."""
    global _RESOLVED_MODEL
    with _MODEL_LOCK:
        if _RESOLVED_MODEL and _RESOLVED_MODEL != missing:
            return _RESOLVED_MODEL  # another thread already found one
        found = _discover_model()
        if not found or found == missing:
            return None
        _RESOLVED_MODEL = found
        log.warning(
            "google: %r is not available to this key; using %r instead. "
            "Set GOOGLE_VISION_MODEL to pin a model yourself.", missing, found)
        return found


# --- the wire ---------------------------------------------------------------

def _thinking_config() -> Optional[dict]:
    """How hard to let the model think, when the deployment pinned it.

    Two model generations spell this differently — 2.5 takes a token budget
    (`thinkingBudget`), 3.x takes a level (`thinking_level`: low/high) — and
    sending the wrong one is a 400 on every draft. So the setting takes
    either form and the shape follows what was written: a number is a budget,
    a word is a level.

    Unset by default, which is the one value that is correct on every model:
    each picks its own. Capping the thinking to save a few seconds is exactly
    the trade that produced the identifier this replaces.
    """
    raw = (config.GOOGLE_THINKING_BUDGET or "").strip()
    if not raw or raw == "-1":
        return None
    try:
        return {"thinkingBudget": int(raw)}
    except ValueError:
        pass
    level = raw.lower()
    if level in ("minimal", "low", "medium", "high"):
        return {"thinking_level": level}
    log.info("GOOGLE_THINKING_BUDGET=%r is neither a token count nor a level "
             "(low/high); letting the model choose", raw)
    return None


def _image_part(path: Path) -> dict:
    # Same ~1092px cached copies the Claude path sends (images.vision_copy):
    # identical reads at roughly half the image tokens and upload bytes of the
    # full 1600px listing photo. The base64 of them comes off the same
    # mtime-keyed cache too — one listing goes through several calls over the
    # same eight photos, and re-encoding them per call is the cost that cache
    # exists to stop. Which backend asked does not change the bytes.
    from . import claude_ai, images
    path = images.vision_copy(path)
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return {"inline_data": {"mime_type": mime,
                            "data": claude_ai._image_b64(path)}}


def _log_usage(tag: str, payload: dict) -> None:
    usage = payload.get("usageMetadata") or {}
    if not usage:
        return
    log.info("ai %s (google): in=%s out=%s thoughts=%s", tag,
             usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
             usage.get("thoughtsTokenCount"))


def _blocked(payload: dict) -> None:
    """Raise when the safety filter, not the model, ended the turn."""
    reason = ((payload.get("promptFeedback") or {}).get("blockReason") or "")
    if not reason:
        for cand in payload.get("candidates") or []:
            finish = str(cand.get("finishReason") or "")
            if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST",
                          "SPII", "IMAGE_SAFETY"):
                reason = finish
                break
    if reason:
        raise GoogleAIRefused(
            "Google's AI declined to work on these photos. Make sure they "
            "show only the item for sale, then try again.", 422)


def _answer_text(payload: dict) -> str:
    """The model's text, with the thinking parts left behind.

    A 2.5 response carries its reasoning as parts flagged `thought`. Those are
    not JSON and must never reach the parser — concatenating them is how a
    perfectly good answer becomes a decode error.
    """
    out = []
    for cand in payload.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            if part.get("thought"):
                continue
            text = part.get("text")
            if text:
                out.append(text)
    return "".join(out)


def _truncated(payload: dict) -> bool:
    return any(str(c.get("finishReason") or "") == "MAX_TOKENS"
               for c in payload.get("candidates") or [])


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "")[:300]
    return str(body)[:300]


def _generate(tag: str, parts: list[dict], system: str = "",
              max_tokens: int = 4096, json_only: bool = True,
              search: bool = False, temperature: float = 0.2) -> dict:
    """One generateContent call. Returns the decoded response body.

    `json_only` asks the API itself for JSON, which is the reliable way to get
    it — but the API refuses that alongside a tool, so the grounded pass
    (`search=True`) asks for JSON in the prompt and leans on the caller's
    extractor instead.
    """
    if not config.google_ai_ready():
        raise GoogleAIError(
            "GOOGLE_API_KEY is not set. Add it to your .env file.", 400)

    generation: dict = {"temperature": temperature,
                        "maxOutputTokens": max_tokens}
    thinking = _thinking_config()
    if thinking:
        generation["thinkingConfig"] = thinking
    if json_only and not search:
        generation["responseMimeType"] = "application/json"

    body: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body["tools"] = [{"google_search": {}}]

    # The key rides a header, never the query string: a URL is what ends up in
    # proxy logs and exception messages.
    headers = {"x-goog-api-key": config.GOOGLE_API_KEY,
               "Content-Type": "application/json"}
    model = _model()
    swapped = False  # a retired model id is worth exactly one second chance

    # `attempt` counts RETRIES of the same call. Swapping a retired model id
    # is not one of those — it is a different call — so it neither spends an
    # attempt nor waits out a backoff it has no reason to wait out.
    last: Optional[Exception] = None
    attempt = 0
    while attempt <= _MAX_RETRIES:
        if attempt:
            # Backing off matters most on the one status that asks for it: an
            # instant retry into a 429 is two failures where one wait would
            # have been one success.
            time.sleep(min(_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), 8.0))
        try:
            resp = httpx.post(f"{API_ROOT}/models/{model}:generateContent",
                              json=body, headers=headers,
                              timeout=_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            last = GoogleAIError(f"could not reach Google AI: {exc}", 503)
            attempt += 1
            continue
        if resp.status_code < 400:
            try:
                payload = resp.json()
            except ValueError as exc:
                raise GoogleAIError(
                    f"Google AI returned a body that is not JSON: {exc}",
                    502) from exc
            _log_usage(tag, payload)
            _blocked(payload)
            return payload
        message = _error_text(resp) or resp.reason_phrase
        last = GoogleAIError(message, resp.status_code)
        # The model id expired. Retrying it is pointless — the same name
        # fails the same way forever — so find the one this key does have.
        if not swapped and _model_missing(resp.status_code, message):
            swapped = True
            replacement = _replace_missing_model(model)
            if replacement:
                model = replacement
                continue
        # 429 and 5xx are worth another go; a bad key or a malformed request
        # will fail identically every time, so those stop here.
        if resp.status_code not in (408, 429, 500, 502, 503, 504):
            raise last
        attempt += 1
    raise last or GoogleAIError("Google AI request failed", 502)


# --- identify ---------------------------------------------------------------

def identify(image_paths: list[Path], image_names: list[str],
             strategy: str = "", notes: str = "",
             item_notes: str = "", subject=None) -> IdentifyResult:
    """Identify the item(s) in the photos and draft a full listing.

    Signature, prompt and result are claude_ai.identify's — the two backends
    are interchangeable on purpose, so routing between them is a config
    setting and not a second code path through the app. See that docstring
    for what `strategy`, `notes` and `item_notes` mean; the schema, the expert
    routing and the parsing are literally the same code, imported here.
    """
    # Imported inside the call: claude_ai routes INTO this module, and the
    # shared prompt/parse halves live over there.
    from . import claude_ai

    parts: list[dict] = [_image_part(p) for p in image_paths[:_MAX_IDENTIFY_IMAGES]]
    parts.append({"text": (
        "These are the product photos for one listing. Draft it."
        + claude_ai._PRICING_STRATEGY_HINTS.get(strategy, "")
        + claude_ai.identify_notes_block(notes)
        + claude_ai.item_notes_block(item_notes)
    )})

    payload = _generate("identify", parts,
                        system=claude_ai._identify_system(subject),
                        max_tokens=_IDENTIFY_MAX_TOKENS)
    if _truncated(payload):
        raise GoogleAIError(
            "the AI response was too long and got cut off; try again or use "
            "fewer photos", 502)
    data = claude_ai._extract_json(_answer_text(payload))

    listing = claude_ai._to_listing(data, image_names)
    # eBay charges extra for subtitles — never auto-fill one.
    listing.subtitle = ""
    # Constrain confidence to the known set: it is rendered into the UI, so a
    # free-form (prompt-injected) value must never reach the DOM.
    conf = str(data.get("confidence", "medium")).lower().strip()
    if conf not in ("low", "medium", "high"):
        conf = "medium"
    return IdentifyResult(
        listing=listing,
        confidence=conf,
        raw_observations=str(data.get("raw_observations", "")),
        tags=[t for t in (data.get("tags") or []) if isinstance(t, dict)][:6],
        identifiers=barcodes.from_scan(data.get("identifiers")),
    )


# --- the grounded pricing lookup -------------------------------------------

def _grounding_sources(payload: dict) -> list[str]:
    """The pages Gemini actually searched, off the grounding metadata.

    Taken from the metadata rather than from the model's own `sources` field
    because this list is the one the API vouches for — a URL the model typed
    into its JSON is as inventable as the price it came with.
    """
    urls: list[str] = []
    for cand in payload.get("candidates") or []:
        meta = cand.get("groundingMetadata") or {}
        for chunk in meta.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            uri = web.get("uri") or ""
            if uri and uri not in urls:
                urls.append(uri)
    return urls[:12]


def _search_queries(payload: dict) -> list[str]:
    out: list[str] = []
    for cand in payload.get("candidates") or []:
        meta = cand.get("groundingMetadata") or {}
        for q in meta.get("webSearchQueries") or []:
            if q and q not in out:
                out.append(str(q))
    return out


def research_item(image_paths: list[Path], listing: Listing,
                  observations: str = "") -> Optional[dict]:
    """Look the drafted item up on Google and price it against what it finds.

    This is the pass the photos-only draft cannot do for itself, and the whole
    reason the identifier moves to Gemini: search grounding is switched on, so
    the model queries Google before it answers and the response carries the
    pages it used. The returned dict is claude_ai.research_item's, field for
    field, because main._research_draft folds them in under rules that must
    not care which backend found them.

    Best-effort by contract: every caller treats a failure as "no research",
    so this raises nothing.
    """
    if not image_paths:
        return None
    from . import claude_ai

    try:
        specifics = "; ".join(
            f"{s.name}: {s.value}" for s in (listing.item_specifics or [])[:12]
            if (s.value or "").strip())
        context = (
            "A first-pass AI drafted this listing FROM THE PHOTOS ALONE, with "
            "no lookup of any kind. Check it.\n\n"
            f"Drafted title: {listing.title}\n"
            f"Drafted brand: {listing.brand or '(none)'}\n"
            f"Drafted price: "
            f"{'(none)' if listing.price is None else f'${listing.price:.2f}'}\n"
            f"Category: {listing.category_suggestion or '(unknown)'}\n"
            f"Specifics: {specifics or '(none)'}\n"
            f"What the first pass saw: {(observations or '')[:600]}\n\n"
            "Establish what this actually is and what it is actually worth. "
            "Search Google before you answer — for the maker and the work, "
            "for the identifying marks, and above all for what comparable "
            "examples have SOLD for recently."
            + claude_ai._RESEARCH_SCHEMA)
        parts = [_image_part(p) for p in image_paths[:_MAX_RESEARCH_IMAGES]]
        parts.append({"text": context})
        payload = _generate("research", parts, max_tokens=_RESEARCH_MAX_TOKENS,
                            json_only=False, search=True, temperature=0.1)
        data = claude_ai._extract_json(_answer_text(payload))
    except Exception as exc:  # noqa: BLE001 - a draft is worth more than a lookup
        log.info("research skipped: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    # The grounded URLs replace whatever the model typed into `sources`: these
    # are the pages the API says it actually read.
    grounded = _grounding_sources(payload)
    if grounded:
        data["sources"] = grounded
    queries = _search_queries(payload)
    log.info("research (google): %d searches -> %r (confidence=%s, %s-%s)",
             len(queries), str(data.get("identified", ""))[:80],
             data.get("confidence"), data.get("value_low"),
             data.get("value_high"))
    return data
