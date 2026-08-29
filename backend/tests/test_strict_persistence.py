"""A write that did not commit must never be reported as success.

The repository layer was uniformly best-effort: every command caught its own
failure, logged a warning, and returned None. Callers could not tell a commit
from a database outage, so they told the seller the same thing either way —
"eBay connected", "Saved", "Disconnected" — while the durable state had not
moved.

That is worse than an error message. The seller believes the work is done and
stops checking. The next publish then fails for a reason that makes no sense
("connect eBay first" on a screen that says connected), and reconnecting looks
like the fix for a problem that was never about the connection.

The split here is deliberate and fail-safe: a command RAISES by default, and
the handful of genuinely optional writes (caching a ZIP eBay can tell us again,
remembering a location key) opt in to best-effort explicitly. A call site
nobody thought about therefore gets the safe behaviour, not the silent one.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from backend import main

    return TestClient(main.app)


def test_a_failed_save_raises_instead_of_returning_none(monkeypatch):
    """The root of it. save_ebay_account swallowed everything and returned
    None, which is indistinguishable from a clean commit."""
    from backend import db

    def _boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(db, "_get_engine", _boom)
    with pytest.raises(db.StorageUnavailable):
        db.save_ebay_account("user-1", ebay_username="seller")


def test_the_best_effort_variant_still_swallows(monkeypatch):
    """Caching a ship-from ZIP is genuinely optional — eBay can be asked
    again. Those callers keep the lenient behaviour, under a name that says
    so."""
    from backend import db

    def _boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(db, "_get_engine", _boom)
    assert db.save_ebay_account_best_effort("user-1", ship_from_postal="97201") is False


def test_the_best_effort_variant_reports_whether_it_committed(monkeypatch):
    """Best-effort is allowed to fail; it is not allowed to LIE. A caller that
    wants to know still can."""
    from backend import db

    monkeypatch.setattr(db, "_get_engine", lambda *_a, **_k: None)
    # No database configured at all is not a failure — it is this app's
    # supported single-box mode.
    assert db.save_ebay_account_best_effort("user-1", ship_from_postal="97201") is True


def test_settings_save_reports_503_not_ok_true(client, monkeypatch):
    """The consumer-visible half. Settings returned {"ok": true} on a write
    that never landed."""
    from backend import auth, db, main

    monkeypatch.setattr(main, "_uid", lambda _r: "user-1")
    monkeypatch.setattr(auth, "current_user", lambda _r: {"id": "user-1",
                                                          "email": "a@b.c"})

    def _boom(*_a, **_k):
        raise db.StorageUnavailable("database is down")

    monkeypatch.setattr(db, "save_ebay_account", _boom)
    resp = client.post("/api/ebay/policies",
                       json={"fulfillment_policy_id": "12345"})
    assert resp.status_code == 503
    assert "ok" not in resp.json() or resp.json().get("ok") is not True


def test_the_error_type_survives_reloading_db():
    """FastAPI registers exception handlers against the class OBJECT, so if
    reloading backend.db minted a fresh StorageUnavailable the handler would
    silently stop matching and the 503 would become a 500 — the same class of
    "the mapping quietly stopped working" failure this whole change is about.
    Defining it in backend.errors, which nothing reloads, is what pins it."""
    import importlib

    from backend import db, errors

    before = db.StorageUnavailable
    importlib.reload(db)
    assert db.StorageUnavailable is before is errors.StorageUnavailable


def test_a_storage_outage_is_503_and_not_404(client, monkeypatch):
    """503 says "try again"; 404 says "it isn't there". Telling a seller their
    connection does not exist because the database blinked sends them to
    reconnect, which cannot help and re-arms the same failure."""
    from backend import db

    assert issubclass(db.StorageUnavailable, Exception)
    # Deliberately NOT a subclass of ValueError/LookupError: those are already
    # mapped to 4xx elsewhere, and inheriting one would quietly reclassify an
    # outage as the seller's mistake.
    assert not issubclass(db.StorageUnavailable, (ValueError, LookupError))
