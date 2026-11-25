"""Suggestions are ranked over the listings they can be made for.

The dashboard's suggestions, "Finish everything" and "Send offers to all"
read the newest LIST_CAP records of EVERY status, while the recommender only
ever suggests something for a live, published or unlisted one. On a store
whose sold archive had grown past the cap, the older live listings were never
read at all -- no suggestion, and never reached by "Finish everything".
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402


def test_an_old_live_listing_behind_newer_sales_still_gets_a_suggestion(
        dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main, "LIST_CAP", 2)
    monkeypatch.setattr(main, "_metrics_by_record_id", lambda creds, items: {})
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup", json={
        "email": "archive@example.com", "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("archive@example.com")["id"]

    # Written first, so it is the OLDEST row.
    assert dbmod.upsert_listing("ready1", {"title": "Ready to go"},
                                status="unlisted", user_id=uid)
    for i in range(3):
        assert dbmod.upsert_listing(f"sold{i}", {"title": f"Sold {i}"},
                                    status="sold", user_id=uid)

    recs = client.get("/api/insights").json()["recommendations"]

    assert any(r["listing_id"] == "ready1" for r in recs), recs
