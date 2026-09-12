"""A browser leaving mid-upload is not this server failing.

Starlette raises ClientDisconnect while READING a request body that stops
arriving. In this app that body is nearly always a pile of photos, so a phone
that loses signal partway up, or a tab closed on a slow upload, produces one
in the ordinary course of a seller's day.

With no handler it reached the catch-all and was dressed as a crash. The error
feed carried the result on 2026-09-12: `ClientDisconnect` x3, and because the
exception carries no message of its own the row read "ClientDisconnect:" with
nothing after it. The daily triage graded it worth a pull request — against a
row naming nothing to fix, for a seller who was no longer connected to read
the support reference it had just minted for them.

Two properties, and the second is the one that would rot quietly: the response
is not a 500, and NOTHING is recorded. A handler that answered 499 while the
row still landed would look fixed from the outside and leave the feed exactly
as noisy as before.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from backend import main
from backend.services import errorlog


@pytest.fixture()
def disconnecting(dbmod, monkeypatch):
    """A route that raises ClientDisconnect, and one that really crashes.

    Both are inserted at the front of the table because main mounts
    StaticFiles at "/", which answers anything registered after it.
    """
    monkeypatch.setattr(main, "db", dbmod)

    def went_away(session_id: str):
        raise ClientDisconnect()

    def broke(session_id: str):
        raise RuntimeError("a real fault")

    routes = [
        APIRoute("/api/_test_gone/{session_id}", went_away, methods=["POST"]),
        APIRoute("/api/_test_broke/{session_id}", broke, methods=["POST"]),
    ]
    for route in routes:
        main.app.router.routes.insert(0, route)
    try:
        yield TestClient(main.app, raise_server_exceptions=False), dbmod
    finally:
        for route in routes:
            main.app.router.routes.remove(route)


def test_a_disconnect_is_not_answered_as_a_server_fault(disconnecting):
    client, _db = disconnecting
    res = client.post("/api/_test_gone/abc")

    assert res.status_code == 499, "499 is the client having gone, not a 500"


def test_a_disconnect_leaves_no_row_in_the_error_feed(disconnecting):
    """The whole point. A 499 with the row still recorded fixes nothing."""
    client, db = disconnecting
    client.post("/api/_test_gone/abc")
    errorlog.flush()

    assert db.error_events_list() == [], (
        "a seller's phone losing signal is not a bug in this tree, and a row "
        "for it is a pull request the daily triage will offer to open")


def test_a_disconnect_spends_no_support_reference(disconnecting):
    """Nobody is on the other end to quote one back to us."""
    client, _db = disconnecting
    res = client.post("/api/_test_gone/abc")

    assert "X-Request-Id" not in res.headers or not res.content


def test_a_real_crash_still_answers_500_and_is_still_recorded(disconnecting):
    """The guard is narrow: it must not have swallowed the catch-all.

    ClientDisconnect is checked by type, so a genuine fault keeps every
    property test_an_unhandled_error_answers_with_a_reference.py pins.
    """
    client, db = disconnecting
    res = client.post("/api/_test_broke/abc")
    errorlog.flush()

    assert res.status_code == 500
    assert res.headers["X-Request-Id"] in res.json()["detail"]
    rows = db.error_events_list()
    assert len(rows) == 1
    assert rows[0]["exc_type"] == "RuntimeError"
