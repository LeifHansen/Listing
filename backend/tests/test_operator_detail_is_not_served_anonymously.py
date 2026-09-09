"""What the operator has and has not configured is not for anonymous callers.

Three things this pins, each of which /api/health had already learned:

* /docs, /redoc and /openapi.json are off. The schema enumerated every admin
  and ops route to anyone, under the product's pre-rebrand name.
* /api/ebay/status names the OAuth variables that are unset — to a signed-in
  seller, whose Settings page renders them, and to nobody else.
* /api/listings answers anonymously (that is how the sign-in prompt knows
  there is nothing to show) and carried db_status() whole, `error` included:
  the raw driver text, which on an auth failure names the Neon host and role.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main  # noqa: E402


@pytest.fixture()
def client(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_the_api_schema_is_not_published(client, path):
    res = client.get(path)
    assert res.status_code == 404 or "openapi" not in res.text.lower()
    assert "/api/admin/diagnostics" not in res.text


def test_missing_oauth_variable_names_need_a_sign_in(client, monkeypatch):
    monkeypatch.setattr(config, "EBAY_CLIENT_ID", "")
    body = client.get("/api/ebay/status").json()
    assert body["oauth_missing"] == []


def test_the_database_error_text_is_not_in_the_anonymous_listing_answer(
        client, dbmod, monkeypatch):
    monkeypatch.setattr(dbmod, "db_status", lambda refresh=False: {
        "configured": True, "connected": False,
        "error": 'password authentication failed for user "neondb_owner" '
                 'at ep-secret-host.neon.tech'})
    body = client.get("/api/listings").json()
    assert body["db"] == {"configured": True, "connected": False}
    assert "neon" not in str(body).lower()
