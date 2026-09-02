"""Every console route answers 404 to everyone but a superadmin.

The gate is _require_superadmin, and three of its properties matter enough
to pin. It answers 404, not 401 — lib/api.js treats any 401 as "session
expired" and signs the caller out client-side, so a curious logged-in
seller probing /api/admin must not lose their session over it; and a 404
does not confirm an admin surface exists. It answers 503, not 404, when the
user row cannot be READ — "cannot check" is never "not an admin". And it
reads the role from the database on every request, so a revoked superadmin
is out on their next request, with no JWT claim to wait out.

Walked from app.routes rather than a hand-list, so the next admin route is
born tested: forgetting the gate on a new handler fails here, not in
production. /api/admin/diagnostics is excluded — it is the older shared
header token door (test_public_surface.py pins it) and deliberately keeps
working with the database down.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit

PASSWORD = "password123"


def _admin_routes():
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/admin/") or path == "/api/admin/diagnostics":
            continue
        for method in sorted((getattr(route, "methods", None) or set())
                             - {"HEAD", "OPTIONS"}):
            yield method, path


def _signed_up(dbmod, email: str) -> tuple[TestClient, str]:
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": email,
                             "password": PASSWORD}).status_code < 400
    return client, dbmod.get_user_by_email(email)["id"]


@pytest.fixture()
def console(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    anon = TestClient(main.app)
    user, uid = _signed_up(dbmod, "seller@example.com")
    admin, admin_uid = _signed_up(dbmod, "op@example.com")
    dbmod.set_user_role(admin_uid, "superadmin")
    assert dbmod.upsert_listing("sess-1", {"title": "A lamp"},
                                status="draft", user_id=uid)
    return {"anon": anon, "user": user, "admin": admin,
            "db": dbmod, "uid": uid, "admin_uid": admin_uid}


def _fill(path: str, console) -> str:
    """Real ids, so a superadmin's answer is about the gate, not about a
    target that doesn't exist."""
    return (path.replace("{user_id}", console["uid"])
                .replace("{listing_id}", "sess-1"))


def test_the_walk_actually_finds_the_console():
    """A sweep that quietly stops finding routes passes for ever."""
    assert len(list(_admin_routes())) >= 12


@pytest.mark.parametrize("method,path", sorted(_admin_routes()))
def test_an_anonymous_caller_gets_404(console, method, path):
    res = console["anon"].request(method, _fill(path, console), json={})
    assert res.status_code == 404, f"{method} {path} -> {res.status_code}"


@pytest.mark.parametrize("method,path", sorted(_admin_routes()))
def test_an_ordinary_user_gets_404_and_never_401(console, method, path):
    """404 exactly. A 401 would fire lib/api.js's auth:expired handler and
    sign an innocent seller out for having typed a URL."""
    res = console["user"].request(method, _fill(path, console), json={})
    assert res.status_code == 404, f"{method} {path} -> {res.status_code}"


@pytest.mark.parametrize("method,path", sorted(_admin_routes()))
def test_a_superadmin_gets_past_the_gate(console, method, path):
    """Not necessarily 200 — a POST with an empty body may be a 400 — but
    never the gate's 404: the ids in the path are real."""
    res = console["admin"].request(method, _fill(path, console), json={})
    assert res.status_code != 404, f"{method} {path} -> {res.status_code}"
    assert res.status_code < 500, f"{method} {path} -> {res.status_code}"


def test_an_unreadable_user_row_is_503_not_404(console, monkeypatch):
    """"We couldn't check who you are" must not read as "you are not an
    admin" — the same rule current_user enforces for every other route."""
    def _boom(_uid):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(console["db"], "get_user_by_id", _boom)
    res = console["admin"].get("/api/admin/overview")
    assert res.status_code == 503


def test_an_unreadable_listing_is_503_to_the_console_not_missing(console,
                                                                 monkeypatch):
    """The admin browse follows the same rule as every seller-facing route
    (test_a_listing_we_cannot_read_is_not_missing.py, which excludes
    /api/admin because its 404s are the gate speaking): to the caller who
    can actually reach it, "could not look" is never "does not exist"."""
    db = console["db"]
    monkeypatch.setattr(db, "get_listing_strict", lambda _id: db.UNAVAILABLE)
    res = console["admin"].get("/api/admin/listings/sess-1")
    assert res.status_code == 503


def test_a_revoked_superadmin_is_out_on_the_next_request(console):
    """The role lives on the row, not in the 30-day JWT: demotion takes
    effect immediately, with the same cookie still in hand."""
    assert console["admin"].get("/api/admin/overview").status_code == 200
    console["db"].set_user_role(console["admin_uid"], "user")
    assert console["admin"].get("/api/admin/overview").status_code == 404


def test_the_role_never_leaves_through_the_public_surface(console):
    """/api/auth/me carries the role (the UI gates the nav on it) but a
    user's own answer must never carry another account's, and the login
    response carries it too — pin the shape so a refactor notices."""
    me = console["admin"].get("/api/auth/me").json()["user"]
    assert me["role"] == "superadmin"
    me = console["user"].get("/api/auth/me").json()["user"]
    assert me["role"] == "user"
    assert "password_hash" not in me
