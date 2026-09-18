"""The Etsy category suggestion is a Claude call, and it was the one AI
route anyone could press without signing in, as often as they liked. A
login, and a ceiling per login.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import config, ratelimit
from backend.services import etsy as etsy_service


@pytest.fixture
def api(monkeypatch, every_marketplace):
    from backend import main

    monkeypatch.setattr(config, "ETSY_CLIENT_ID", "key")
    monkeypatch.setattr(config, "ETSY_SHARED_SECRET", "secret")
    monkeypatch.setattr(config, "ETSY_REDIRECT_URI", "https://app.example/api/etsy/callback")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    asked = []
    monkeypatch.setattr(etsy_service, "suggest_taxonomy",
                        lambda listing: (asked.append(listing.title),
                                         {"taxonomy_id": 1, "path": "Home > Mugs"})[1])
    ratelimit.reset()

    def _as(uid):
        monkeypatch.setattr(main, "_uid", lambda _r: uid)
        return TestClient(main.app), asked
    return _as


def _suggest(client):
    return client.post("/api/etsy/suggest-taxonomy/s1",
                       json={"listing": {"title": "Vintage mug"}})


def test_anonymous_gets_a_401_and_no_model_call(api):
    client, asked = api(None)
    assert _suggest(client).status_code == 401
    assert asked == []


def test_a_login_reaches_the_suggestion(api):
    client, asked = api("u1")
    resp = _suggest(client)
    assert resp.status_code == 200
    assert resp.json()["taxonomy_id"] == 1
    assert asked == ["Vintage mug"]


def test_past_the_ceiling_it_is_a_429(api):
    client, asked = api("u1")
    for _ in range(ratelimit.ETSY_SUGGEST_MAX_CALLS):
        assert _suggest(client).status_code == 200
    assert _suggest(client).status_code == 429
    assert len(asked) == ratelimit.ETSY_SUGGEST_MAX_CALLS
