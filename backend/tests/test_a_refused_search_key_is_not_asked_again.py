"""A search key SerpAPI has refused is not tried again on every draft.

Production's error feed carried "image search: lookup failed (HTTPStatusError
401)" 23 times in a day: SERPAPI_KEY had expired, and every artwork draft still
presigned its photo in the bucket, called SerpAPI, got refused, and wrote a
warning row. A 401 or 403 is about the key, not the request, so it holds for
every call until the key is replaced — which needs a restart, since the key is
read at boot, and a restart is what clears this. A 429 or 5xx is SerpAPI having
a bad moment and stays retried.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from backend.services import imagesearch


@pytest.fixture(autouse=True)
def _keyed(monkeypatch):
    monkeypatch.setattr(imagesearch.config, "serpapi_ready", lambda: True)
    monkeypatch.setattr(imagesearch, "_key_refused", False)


def _answering(monkeypatch, status):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return httpx.Response(status, request=httpx.Request("GET", url),
                              json={"error": "Invalid API key."})
    monkeypatch.setattr(imagesearch.httpx, "get", fake_get)
    return calls


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_turns_the_lookup_off(monkeypatch, caplog, status):
    calls = _answering(monkeypatch, status)
    with caplog.at_level(logging.INFO, logger="thryft"):
        assert imagesearch.reverse_image("https://r2.example/p.jpg") == []
        assert imagesearch.enabled() is False
        assert imagesearch.reverse_image("https://r2.example/q.jpg") == []
    assert len(calls) == 1, "a refused key was sent again"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "refused the key" in warnings[0].getMessage()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_bad_moment_at_serpapi_is_still_retried(monkeypatch, status):
    calls = _answering(monkeypatch, status)
    imagesearch.reverse_image("https://r2.example/p.jpg")
    assert imagesearch.enabled() is True
    imagesearch.reverse_image("https://r2.example/q.jpg")
    assert len(calls) == 2
