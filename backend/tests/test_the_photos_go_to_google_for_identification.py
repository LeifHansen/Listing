"""The item identifier runs on Google's vision model when a key is set.

Identifying a second-hand item and pricing it are two different jobs, and the
photos-only pass could only ever do the first: a model can see a Carhartt
Detroit jacket, but what the jacket SELLS for is not in the picture. This is
the switch to Gemini — the photos go to Google, and the pricing pass goes to
Google Search grounding, which answers with the pages it actually read.

What is pinned here is the seam, not the model: both backends draft against
the same schema and return the same IdentifyResult, so routing between them
stays a config setting and never becomes a second path through the app.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("httpx")

from backend.models import Listing
from backend.services import claude_ai, google_ai


# --- the routing ------------------------------------------------------------

def test_a_google_key_is_the_whole_switch(fresh_config):
    # No second setting to remember: the key IS the opt-in, and a deployment
    # that never adds one is untouched.
    cfg = fresh_config(ANTHROPIC_API_KEY="sk-ant-x")
    assert cfg.identify_provider() == "anthropic"
    cfg = fresh_config(ANTHROPIC_API_KEY="sk-ant-x", GOOGLE_API_KEY="g-key")
    assert cfg.identify_provider() == "google"


def test_googles_key_answers_to_the_names_google_hands_it_out_under(fresh_config):
    assert fresh_config(GEMINI_API_KEY="g-key").identify_provider() == "google"
    assert fresh_config(GOOGLE_AI_API_KEY="g-key").identify_provider() == "google"


def test_a_pin_to_a_backend_with_no_key_still_drafts(fresh_config):
    # Failing closed here means a seller pressing Identify gets a 400 about a
    # server setting they cannot see. It fails open instead — and says so,
    # because a silent fallback reads exactly like a working pin.
    cfg = fresh_config(IDENTIFY_PROVIDER="google", ANTHROPIC_API_KEY="sk-ant-x")
    assert cfg.identify_provider() == "anthropic"
    assert any("IDENTIFY_PROVIDER=google" in w for w in cfg.config_warnings())


def test_the_identifier_can_be_pinned_back_to_claude(fresh_config):
    cfg = fresh_config(IDENTIFY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-x",
                       GOOGLE_API_KEY="g-key")
    assert cfg.identify_provider() == "anthropic"


def test_either_backend_is_enough_to_identify(fresh_config):
    assert fresh_config().vision_ready() is False
    assert fresh_config(GOOGLE_API_KEY="g-key").vision_ready() is True
    assert fresh_config(ANTHROPIC_API_KEY="sk-ant-x").vision_ready() is True


def test_identify_goes_to_google_and_never_to_claude(monkeypatch, fresh_config):
    fresh_config(ANTHROPIC_API_KEY="sk-ant-x", GOOGLE_API_KEY="g-key")
    seen = {}
    monkeypatch.setattr(google_ai, "identify",
                        lambda *a, **k: seen.update(args=a, kw=k) or "drafted")
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: pytest.fail("Claude was called on the Google path"))

    out = claude_ai.identify([], ["a.jpg"], strategy="median", notes="n",
                             item_notes="i")
    assert out == "drafted"
    assert seen["args"] == ([], ["a.jpg"])
    assert seen["kw"] == {"strategy": "median", "notes": "n", "item_notes": "i"}


def test_the_pricing_lookup_goes_to_google_too(monkeypatch, fresh_config):
    # The lookup is the half that most needs the switch: Claude's pass asks a
    # model what things cost, Gemini's searches for what they sold for.
    fresh_config(ANTHROPIC_API_KEY="sk-ant-x", GOOGLE_API_KEY="g-key")
    seen = {}
    monkeypatch.setattr(google_ai, "research_item",
                        lambda *a, **k: seen.update(kw=k) or {"identified": "x"})
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: pytest.fail("Claude was called on the Google path"))

    assert claude_ai.research_item(["p.jpg"], Listing(title="t"),
                                   observations="saw a mark") == {"identified": "x"}
    assert seen["kw"] == {"observations": "saw a mark"}


def test_a_batch_on_google_does_not_warm_claudes_cache(monkeypatch, fresh_config):
    # The warm-up writes an Anthropic prompt-cache prefix. On the Google path
    # there is no prefix to write, and paying a live Anthropic call to warm
    # nothing is the kind of cost that hides in a bulk batch.
    fresh_config(ANTHROPIC_API_KEY="sk-ant-x", GOOGLE_API_KEY="g-key")
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: pytest.fail("Claude was called on the Google path"))
    assert claude_ai.warm_identify_cache() is False


# --- the call Google actually receives --------------------------------------

class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.reason_phrase = "err"
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _answer(text: str, **extra) -> dict:
    candidate = {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
    candidate.update(extra)
    return {"candidates": [candidate], "usageMetadata": {"promptTokenCount": 9}}


_DRAFT = json.dumps({
    "title": "Carhartt Detroit Jacket Blanket Lined Duck Canvas Mens L Brown",
    "brand": "Carhartt",
    "condition": "used - excellent",
    "description": "A well-kept Detroit jacket.",
    "price": 118.0,
    "confidence": "high",
    "item_specifics": [{"name": "Size", "value": "L", "confidence": "high"}],
    "raw_observations": "union-made label, J97 tag",
})


@pytest.fixture(autouse=True)
def _no_model_carried_between_tests(monkeypatch):
    """A discovered model is cached for the PROCESS, not for a test."""
    monkeypatch.setattr(google_ai, "_RESOLVED_MODEL", None)


@pytest.fixture
def posted(monkeypatch, fresh_config, tmp_path):
    """Stand in for Google, and hand the test what was sent."""
    fresh_config(GOOGLE_API_KEY="g-key", GOOGLE_VISION_MODEL="gemini-x-pro")
    calls: list[dict] = []
    reply = {"body": _answer(_DRAFT)}

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        return _Resp(reply["body"], reply.get("status", 200))

    monkeypatch.setattr(google_ai.httpx, "post", _post)
    # The real backoff is seconds long; the suite does not need to live it.
    monkeypatch.setattr(google_ai, "_RETRY_BACKOFF_SECONDS", 0.0)
    # vision_copy resizes a real photo; these tests are about the request.
    photo = tmp_path / "front.jpg"
    photo.write_bytes(b"\xff\xd8\xff-not-a-real-jpeg")
    monkeypatch.setattr("backend.services.images.vision_copy", lambda p: p)
    return calls, reply, photo


def test_the_photos_ride_the_request_as_images(posted):
    calls, _reply, photo = posted
    google_ai.identify([photo], ["front.jpg"])

    body = calls[0]["body"]
    parts = body["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[0]["inline_data"]["data"], "the photo itself must be sent"
    assert "Draft it." in parts[-1]["text"]


def test_the_key_never_rides_the_url(posted):
    # A URL is what ends up in proxy logs and exception messages.
    calls, _reply, photo = posted
    google_ai.identify([photo], ["front.jpg"])
    assert "g-key" not in calls[0]["url"]
    assert calls[0]["headers"]["x-goog-api-key"] == "g-key"
    assert "gemini-x-pro:generateContent" in calls[0]["url"]


def test_the_draft_comes_back_in_the_shape_the_app_already_reads(posted):
    _calls, _reply, photo = posted
    result = google_ai.identify([photo], ["front.jpg"])

    assert result.confidence == "high"
    assert result.listing.brand == "Carhartt"
    assert result.listing.images == ["front.jpg"]
    # Parsed through the same helpers Claude's drafts go through: the charm
    # price and the condition enum are the proof.
    assert result.listing.price == 117.99
    assert result.listing.condition == "USED_EXCELLENT"
    assert result.listing.item_specifics[0].value == "L"
    # eBay charges for a subtitle; a first draft never spends one.
    assert result.listing.subtitle == ""


def test_a_made_up_confidence_never_reaches_the_page(posted):
    calls, reply, photo = posted
    reply["body"] = _answer(json.dumps(
        {"title": "t", "confidence": "<img src=x onerror=alert(1)>"}))
    assert google_ai.identify([photo], ["front.jpg"]).confidence == "medium"
    assert calls  # it really did go through the stand-in


def test_the_models_thinking_is_not_fed_to_the_parser(posted):
    # A 2.5 answer carries its reasoning as parts flagged `thought`. They are
    # not JSON, and concatenating them turns a good answer into a decode error.
    _calls, reply, photo = posted
    reply["body"] = {"candidates": [{"content": {"parts": [
        {"text": "Let me look at the tag first...", "thought": True},
        {"text": _DRAFT},
    ]}, "finishReason": "STOP"}]}
    assert google_ai.identify([photo], ["front.jpg"]).listing.brand == "Carhartt"


def test_an_answer_cut_off_mid_json_is_not_reported_as_a_bad_photo(posted):
    _calls, reply, photo = posted
    reply["body"] = {"candidates": [{"content": {"parts": [{"text": '{"title": "t'}],
                                    }, "finishReason": "MAX_TOKENS"}]}
    with pytest.raises(google_ai.GoogleAIError) as exc:
        google_ai.identify([photo], ["front.jpg"])
    assert "cut off" in str(exc.value)


def test_a_safety_block_is_said_as_what_it_is(posted):
    # Retrying does not change the photos, so "try again" is the one thing
    # that cannot help. 422, not a 5xx.
    _calls, reply, photo = posted
    reply["body"] = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(google_ai.GoogleAIRefused) as exc:
        google_ai.identify([photo], ["front.jpg"])
    code, message = claude_ai.ai_error_message(exc.value)
    assert code == 422
    assert "declined" in message


@pytest.mark.parametrize("status,expect_code,expect_word", [
    (429, 429, "rate-limited"),
    (403, 400, "GOOGLE_API_KEY"),
    (400, 502, "Google AI request failed"),
])
def test_googles_limits_get_their_own_sentence(posted, status, expect_code,
                                               expect_word):
    # The seller is told which of the two AI accounts is the one with the
    # problem — "AI request failed" sent them to check the wrong key.
    _calls, reply, photo = posted
    reply["body"] = {"error": {"message": "quota exceeded for this project"
                                          if status == 429 else "bad request"}}
    reply["status"] = status
    with pytest.raises(google_ai.GoogleAIError) as exc:
        google_ai.identify([photo], ["front.jpg"])
    code, message = claude_ai.ai_error_message(exc.value)
    assert (code, expect_word in message) == (expect_code, True), message
    assert claude_ai.is_ai_error(exc.value)


# --- a model id that expired ------------------------------------------------
#
# Google retires Gemini ids on its own schedule. A pinned default is a name
# that works the day it is written and 404s every draft some months later,
# with nothing but a seller's bug report to say so.

def test_a_retired_model_is_replaced_with_one_the_key_actually_has(posted, monkeypatch):
    calls, reply, photo = posted
    listed = {"models": [
        {"name": "models/gemini-4-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]}
    monkeypatch.setattr(google_ai.httpx, "get",
                        lambda url, **kw: _Resp(listed))

    # First call: the configured model is gone. Second: the replacement.
    reply["body"] = {"error": {"message": "models/gemini-x-pro is not found "
                                          "for API version v1beta"}}
    reply["status"] = 404

    def _after_swap(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        if "gemini-x-pro" in url:
            return _Resp({"error": {"message": "is not found"}}, 404)
        return _Resp(_answer(_DRAFT))

    monkeypatch.setattr(google_ai.httpx, "post", _after_swap)
    result = google_ai.identify([photo], ["front.jpg"])

    assert result.listing.brand == "Carhartt"
    # The newest PRO, because the deployment asked for a pro. Dropping to
    # flash would be a quality change nobody asked for.
    assert "gemini-4-pro:generateContent" in calls[-1]["url"]
    # And it is remembered, so the next photo does not re-learn it.
    assert google_ai._RESOLVED_MODEL == "gemini-4-pro"


def test_the_swap_is_not_one_of_the_retries(posted, monkeypatch):
    # A retired name fails the same way forever, so spending a retry on it
    # would cost a real attempt at the model that does work. Here the
    # replacement is merely BUSY, and it must still get the whole budget.
    calls, _reply, photo = posted
    monkeypatch.setattr(google_ai.httpx, "get", lambda url, **kw: _Resp(
        {"models": [{"name": "models/gemini-9-pro",
                     "supportedGenerationMethods": ["generateContent"]}]}))

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        if "gemini-x-pro" in url:
            return _Resp({"error": {"message": "is not found"}}, 404)
        return _Resp({"error": {"message": "backend overloaded"}}, 503)

    monkeypatch.setattr(google_ai.httpx, "post", _post)
    with pytest.raises(google_ai.GoogleAIError):
        google_ai.identify([photo], ["front.jpg"])
    # One call on the dead name, then the full retry budget on the live one.
    assert sum("gemini-x-pro" in c["url"] for c in calls) == 1
    assert sum("gemini-9-pro" in c["url"] for c in calls) == google_ai._MAX_RETRIES + 1


def test_discovery_that_fails_leaves_the_real_error_standing(posted, monkeypatch):
    # Better a clear "that model does not exist" than a silent switch to
    # something nobody chose.
    _calls, reply, photo = posted
    monkeypatch.setattr(google_ai.httpx, "get", lambda url, **kw: _Resp({}, 500))
    reply["body"] = {"error": {"message": "models/gemini-x-pro is not found"}}
    reply["status"] = 404
    with pytest.raises(google_ai.GoogleAIError) as exc:
        google_ai.identify([photo], ["front.jpg"])
    assert "not found" in str(exc.value)


def test_a_fallback_is_never_an_embedder_or_an_image_generator(posted, monkeypatch):
    calls, reply, photo = posted
    monkeypatch.setattr(google_ai.httpx, "get", lambda url, **kw: _Resp({"models": [
        {"name": "models/gemini-9-embedding", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9-pro-image-generation", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemma-9-it", "supportedGenerationMethods": ["generateContent"]},
    ]}))
    reply["body"] = {"error": {"message": "is not found"}}
    reply["status"] = 404

    def _after_swap(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        if "gemini-x-pro" in url:
            return _Resp({"error": {"message": "is not found"}}, 404)
        return _Resp(_answer(_DRAFT))

    monkeypatch.setattr(google_ai.httpx, "post", _after_swap)
    google_ai.identify([photo], ["front.jpg"])
    assert "gemini-2-pro:generateContent" in calls[-1]["url"]


@pytest.mark.parametrize("setting,expected", [
    ("", None),
    ("-1", None),
    ("2048", {"thinkingBudget": 2048}),
    ("0", {"thinkingBudget": 0}),
    ("high", {"thinking_level": "high"}),
    ("nonsense", None),
])
def test_the_thinking_knob_speaks_both_generations(fresh_config, setting,
                                                   expected):
    # 2.5 takes a token budget, 3.x takes a level, and sending the wrong one
    # is a 400 on every draft. Unset sends neither, which is right on both.
    fresh_config(GOOGLE_API_KEY="g-key", GOOGLE_THINKING_BUDGET=setting)
    assert google_ai._thinking_config() == expected


def test_a_settled_release_beats_a_preview_of_the_same_version():
    assert google_ai._rank(
        ["gemini-5-pro-preview", "gemini-5-pro", "gemini-4-pro"], "pro"
    )[0] == "gemini-5-pro"


# --- the grounded pricing pass ----------------------------------------------

_RESEARCH = json.dumps({
    "identified": "Carhartt J97 Detroit jacket, pre-2000 union label",
    "value_low": 140, "value_high": 190,
    "sources": ["https://example.invalid/made-this-up"],
    "confidence": "high",
})


def test_the_lookup_actually_searches_google(posted):
    calls, reply, photo = posted
    reply["body"] = _answer(_RESEARCH)
    google_ai.research_item([photo], Listing(title="brown jacket"))

    body = calls[0]["body"]
    assert body["tools"] == [{"google_search": {}}]
    # The API refuses a JSON response type alongside a tool, so the grounded
    # pass must ask for JSON in the prompt instead of in the config.
    assert "responseMimeType" not in body["generationConfig"]
    assert "SOLD for" in body["contents"][0]["parts"][-1]["text"]


def test_the_sources_are_the_pages_google_says_it_read(posted):
    # A URL the model typed into its own JSON is as inventable as the price it
    # came with. The grounding metadata is the half the API vouches for.
    _calls, reply, photo = posted
    reply["body"] = _answer(_RESEARCH, groundingMetadata={
        "webSearchQueries": ["carhartt j97 detroit jacket sold"],
        "groundingChunks": [
            {"web": {"uri": "https://ebay.example/sold/1"}},
            {"web": {"uri": "https://ebay.example/sold/2"}},
        ],
    })
    found = google_ai.research_item([photo], Listing(title="brown jacket"))
    assert found["sources"] == ["https://ebay.example/sold/1",
                                "https://ebay.example/sold/2"]
    assert found["value_low"] == 140


def test_a_lookup_that_fails_is_not_a_draft_that_fails(posted):
    # Every caller treats a failure as "no research"; it must never raise.
    _calls, reply, photo = posted
    reply["body"] = {"error": {"message": "quota exceeded"}}
    reply["status"] = 429
    assert google_ai.research_item([photo], Listing(title="t")) is None


def test_a_busy_moment_is_retried_and_a_bad_key_is_not(posted):
    # 429 and 5xx pass on their own; a rejected key fails the same way every
    # time, and spending three calls to learn that delays the sentence that
    # tells the operator which key to fix.
    calls, reply, photo = posted
    reply["body"] = {"error": {"message": "backend overloaded"}}
    reply["status"] = 503
    with pytest.raises(google_ai.GoogleAIError):
        google_ai.identify([photo], ["front.jpg"])
    assert len(calls) == google_ai._MAX_RETRIES + 1

    calls.clear()
    reply["body"] = {"error": {"message": "API key not valid"}}
    reply["status"] = 403
    with pytest.raises(google_ai.GoogleAIError):
        google_ai.identify([photo], ["front.jpg"])
    assert len(calls) == 1
