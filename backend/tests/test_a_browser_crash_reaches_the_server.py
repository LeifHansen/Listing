"""A crash in the browser is recorded, and cannot be used to flood anything.

The frontend reported nothing at all: no error boundary, no window.onerror, no
unhandledrejection handler, no ingest route. A React render crash unmounted the
tree and left a white screen — the seller saw nothing they could act on and the
server never learned it had happened.

The route that fixes that is the only UNAUTHENTICATED WRITE in the app, and it
has to be. A throw inside the app shell means there may be no session to
authenticate with, so an authenticated ingest would miss exactly the crashes
worth hearing about. Everything odd about the route follows from that:

**It always answers 202 with the same body.** Recorded, rate-limited, or
unparseable — the client cannot tell which. A 429 would advertise where the
limit is, and any answer that reads as "try again" is how a failed report
becomes the next report. The client is not being given a channel.

**It reflects nothing.** The body is attacker-controlled text that will later
be read by an automated triage job, so nothing from it comes back out, and
every field is truncated server-side as well as in the browser. The browser's
caps are a courtesy; these are the control.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import config, main, ratelimit
from backend.services import errorlog

CRASH = {"kind": "react", "name": "TypeError",
         "message": "Cannot read properties of undefined (reading 'title')",
         "stack": "TypeError: x\n    at ListingCard (index-DkX1.js:1:48213)",
         "component_stack": "\n    at ListingCard\n    at ListingsView",
         "url": "/listings/abc", "build": "62ec7e8", "request_id": "a1b2c3d4"}


@pytest.fixture()
def api(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    return TestClient(main.app), dbmod


def test_a_crash_is_recorded_without_a_session(api):
    client, db = api
    res = client.post("/api/client-errors", json=CRASH)
    assert res.status_code == 202
    errorlog.flush()

    rows = db.error_events_list()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "frontend"
    assert "Cannot read properties of undefined" in row["message"]
    assert row["route"] == "/listings/abc"
    assert row["reference"] == "a1b2c3d4", "joins to the request that preceded it"
    assert row["data"]["build"] == "62ec7e8"
    assert "ListingCard" in row["data"]["component_stack"]


def test_the_same_crash_on_different_screens_is_one_row(api):
    """/listings/abc and /listings/def are the same bug."""
    client, db = api
    for listing in ("abc", "def", "ghi"):
        client.post("/api/client-errors",
                    json={**CRASH, "url": f"/listings/{listing}"})
    errorlog.flush()

    rows = db.error_events_list()
    assert len(rows) == 1
    assert rows[0]["count"] == 3


def test_a_flood_is_dropped_and_the_client_is_not_told(api):
    client, db = api
    limit = config.CLIENT_ERROR_MAX_PER_WINDOW

    codes = {client.post("/api/client-errors",
                         json={**CRASH, "message": f"crash {i}"}).status_code
             for i in range(limit + 25)}

    assert codes == {202}, "a rate-limited report must look like a taken one"
    errorlog.flush()
    assert len(db.error_events_list()) <= limit


def test_an_oversized_report_is_refused_before_it_is_parsed(api):
    client, db = api
    res = client.post("/api/client-errors",
                      json={**CRASH, "stack": "A" * (32 * 1024)})

    assert res.status_code == 202
    errorlog.flush()
    assert db.error_events_list() == []


def test_a_malformed_report_is_not_an_incident(api):
    client, db = api
    for body in (b"", b"not json", b"[]", b'{"message": null}', b"null"):
        res = client.post("/api/client-errors", content=body,
                          headers={"Content-Type": "application/json"})
        assert res.status_code == 202
    errorlog.flush()
    assert db.error_events_list() == []


def test_the_answer_reveals_nothing_at_all(api):
    """Same bytes every time, whatever happened. Anything that varies is a
    signal, and a signal is something to probe."""
    client, _db = api
    taken = client.post("/api/client-errors", json=CRASH)
    junk = client.post("/api/client-errors", content=b"{",
                       headers={"Content-Type": "application/json"})

    assert taken.json() == junk.json() == {"ok": True}
    assert taken.status_code == junk.status_code == 202


def test_a_report_cannot_smuggle_a_secret_into_the_record(api):
    """The body is attacker-controlled and an automated job reads it later."""
    client, db = api
    client.post("/api/client-errors", json={
        **CRASH,
        "message": "failed for seller@example.com with sk_live_51H4xAbCdEfGh",
        "stack": "at fetch(https://x/y?access_token=supersecretvalue)"})
    errorlog.flush()

    row = db.error_events_list()[0]
    blob = row["message"] + str(row["data"])
    assert "sk_live_51H4xAbCdEfGh" not in blob
    assert "seller@example.com" not in blob
    assert "supersecretvalue" not in blob
