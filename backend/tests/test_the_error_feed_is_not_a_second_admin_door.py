"""The triage job's door opens onto the errors, and onto nothing else.

Two doors reach the same report, which is the shape /api/admin/system and
/api/admin/diagnostics already have: the session door authenticates a PERSON,
which is right for the console and wrong for a scheduled job that would
otherwise have to hold a human's 30-day session in a CI secret.

The part worth pinning is that the job's token is its OWN. Reusing ADMIN_TOKEN
would have been one line shorter and would have handed a credential in GitHub
Actions the ability to read /api/admin/diagnostics, whose payload carries raw
database and object-store exception text — the Neon host, the database role,
the R2 account id. A robot that reads which bugs are open has no business with
any of that, and a credential living in CI is the one most likely to leak.

Both doors fail CLOSED. An unset token denying rather than admitting is the
property that stops a deploy which forgot to set it from publishing the error
log, which names routes, modules and the shape of every failure in the system.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import config, main

FEED = "/api/ops/error-feed"
TOKEN = "feed-token-for-the-job"
ADMIN = "a-different-admin-token"


@pytest.fixture()
def api(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(config, "ERROR_FEED_TOKEN", TOKEN)
    monkeypatch.setattr(config, "ADMIN_TOKEN", ADMIN)
    return TestClient(main.app), dbmod


def test_the_feed_needs_its_token(api):
    client, _db = api
    assert client.get(FEED).status_code == 401
    assert client.get(FEED, headers={"x-error-feed-token": "wrong"}
                      ).status_code == 401
    assert client.get(FEED, headers={"x-error-feed-token": TOKEN}
                      ).status_code == 200


def test_an_unset_token_denies_rather_than_admits(api, monkeypatch):
    client, _db = api
    monkeypatch.setattr(config, "ERROR_FEED_TOKEN", "")

    assert client.get(FEED).status_code == 401
    assert client.get(FEED, headers={"x-error-feed-token": ""}
                      ).status_code == 401


def test_the_feed_token_does_not_open_diagnostics(api):
    """The whole reason it is a separate secret."""
    client, _db = api
    res = client.get("/api/admin/diagnostics",
                     headers={"x-admin-token": TOKEN})
    assert res.status_code == 401


def test_the_admin_token_does_not_open_the_feed(api):
    """And the separation holds in both directions."""
    client, _db = api
    assert client.get(FEED, headers={"x-error-feed-token": ADMIN}
                      ).status_code == 401


def test_the_console_door_is_a_404_not_a_401(api):
    """It is a console route, so it answers like every other one: lib/api.js
    signs a caller out on any 401, and a seller poking at /api/admin must not
    lose their session over it."""
    client, _db = api
    assert client.get("/api/admin/errors").status_code == 404


def test_both_doors_report_the_same_failures(api):
    client, db = api
    db.record_error_event(fingerprint="aa11", severity="high",
                          message="a real bug", exc_type="ValueError")

    feed = client.get(FEED, headers={"x-error-feed-token": TOKEN}).json()
    assert [e["fingerprint"] for e in feed["errors"]] == ["aa11"]
    assert feed["errors"][0]["message"] == "a real bug"


def test_the_report_says_whether_the_sink_is_dropping(api):
    """A queue that is losing rows must not look like a quiet day.

    This is check_health.py's lesson one layer down: a monitor that cannot
    tell "nothing happened" from "I could not see" is worse than none.
    """
    client, _db = api
    body = client.get(FEED, headers={"x-error-feed-token": TOKEN}).json()

    assert "sink" in body
    assert set(body["sink"]) == {"queued", "dropped", "running"}


def test_the_feed_hides_what_already_has_a_fix(api):
    """Otherwise the job proposes the same fix every morning."""
    client, db = api
    db.record_error_event(fingerprint="bb22", severity="high", message="fixed")
    db.record_error_event(fingerprint="cc33", severity="high", message="open")
    db.mark_error_fixed("bb22", "https://github.com/x/y/pull/1")

    body = client.get(FEED, headers={"x-error-feed-token": TOKEN}).json()
    assert [e["fingerprint"] for e in body["errors"]] == ["cc33"]


def test_the_job_can_write_back_what_it_opened(api):
    client, db = api
    db.record_error_event(fingerprint="dd44", severity="high", message="x")

    res = client.post("/api/ops/errors/dd44/fixed",
                      headers={"x-error-feed-token": TOKEN},
                      json={"pr": "https://github.com/x/y/pull/9"})

    assert res.status_code == 200 and res.json() == {"ok": True}
    row = db.error_events_list()[0]
    assert row["resolved_at"] and row["fix_pr"].endswith("/pull/9")


def test_writing_back_needs_the_token_too(api):
    client, db = api
    db.record_error_event(fingerprint="ee55", severity="high", message="x")

    assert client.post("/api/ops/errors/ee55/fixed", json={}).status_code == 401
    assert db.error_events_list()[0]["resolved_at"] is None
