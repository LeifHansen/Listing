"""A wrong operator token is a 401, whatever bytes it carries.

secrets.compare_digest raises TypeError on a str with a character outside
ASCII, and Starlette decodes headers as latin-1 — so one probe with a byte
>= 0x80 in x-admin-token or x-error-feed-token was a 500 and an error_events
row, where a wrong token is a 401. Neither door is open either way; the
difference is a crash report about a script trying passwords.
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
    monkeypatch.setattr(config, "ERROR_FEED_TOKEN", "feed-token")
    monkeypatch.setattr(config, "ADMIN_TOKEN", "admin-token")
    return TestClient(main.app)


@pytest.mark.parametrize("path,header", [
    ("/api/ops/error-feed", b"x-error-feed-token"),
    ("/api/admin/diagnostics", b"x-admin-token"),
])
def test_a_non_ascii_token_is_a_401(client, path, header):
    res = client.get(path, headers={header: b"\xe9\xe9-not-the-token"})
    assert res.status_code == 401


def test_the_right_token_still_opens_the_door(client):
    assert client.get("/api/ops/error-feed",
                      headers={"x-error-feed-token": "feed-token"}).status_code == 200
