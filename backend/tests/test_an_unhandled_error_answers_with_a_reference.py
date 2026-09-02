"""A crash answers with something the seller can quote, and leaves a record.

Before the catch-all handler existed, anything nobody had thought of fell
through to Starlette's default and became a bare "Internal Server Error". That
is the complaint the InvalidSessionId and out-of-space handlers were already
written for, one level up: the seller is shown a fault with no next step, and
there is nothing tying their report to the traceback uvicorn printed.

Two properties here are easy to lose and expensive to lose quietly.

The reference in the body, the X-Request-Id header and the recorded row must
all be the SAME value. That triple is the only join this app has ever had
between "the app said a1b2c3d4" and a cause.

The response must carry the security headers. `_security_headers` is an
@app.middleware("http"), and Starlette's ServerErrorMiddleware — which is what
calls the catch-all — sits OUTSIDE the whole middleware stack. So a genuine
500 is the one response that does not get them for free, and it is precisely
the response an attacker can most easily steer the app into.
test_security_headers.py's own error case passes on a 404, which is raised
INSIDE the stack and therefore proves nothing about this path.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import main
from backend.services import errorlog


def _crash_deep():
    raise RuntimeError("the thing that actually broke")


@pytest.fixture()
def crashing(dbmod, monkeypatch):
    """A route that raises, mounted ahead of the SPA catch-all.

    Inserted at the front because main mounts StaticFiles at "/", which
    answers anything registered after it.
    """
    monkeypatch.setattr(main, "db", dbmod)

    def boom(listing_id: str):
        _crash_deep()

    route = APIRoute("/api/_test_boom/{listing_id}", boom, methods=["GET"])
    main.app.router.routes.insert(0, route)
    try:
        yield TestClient(main.app, raise_server_exceptions=False), dbmod
    finally:
        main.app.router.routes.remove(route)


def test_a_crash_answers_500_with_a_quotable_reference(crashing):
    client, _db = crashing
    res = client.get("/api/_test_boom/abc")

    assert res.status_code == 500
    reference = res.headers["X-Request-Id"]
    assert reference, "a 500 with no reference cannot be reported by a seller"
    assert reference in res.json()["detail"]


def test_a_crash_response_is_protected_too(crashing):
    """The one response that does not inherit the middleware's headers."""
    client, _db = crashing
    res = client.get("/api/_test_boom/abc")

    assert res.status_code == 500
    assert res.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in res.headers


def test_the_reference_on_the_response_is_the_one_on_the_row(crashing):
    client, db = crashing
    res = client.get("/api/_test_boom/abc")
    errorlog.flush()

    rows = db.error_events_list()
    assert len(rows) == 1
    assert rows[0]["reference"] == res.headers["X-Request-Id"]
    assert rows[0]["status"] == 500
    assert rows[0]["route"] == "/api/_test_boom/abc"
    assert rows[0]["method"] == "GET"


def test_the_row_carries_the_traceback_and_the_real_crash_site(crashing):
    """Where it broke, not where it was caught.

    Recording the handler's own location would collapse every unhandled error
    in the app into one row called "_unhandled"; recording the request path
    would mint a new one per listing id. The innermost frame is neither.
    """
    client, db = crashing
    client.get("/api/_test_boom/abc")
    errorlog.flush()

    row = db.error_events_list()[0]
    assert row["func"] == "_crash_deep"
    assert "RuntimeError" == row["exc_type"]
    assert "the thing that actually broke" in row["traceback"]
    assert row["severity"] == "high"


def test_a_deep_traceback_keeps_the_end_where_the_error_is(crashing):
    """Truncation must cut the middle, never the tail.

    Python puts the outermost frames first and the exception's own type and
    message LAST, so plain truncation throws away precisely the part worth
    keeping. CI found this rather than the author: the runner's anyio produced
    a deeper ExceptionGroup than the development machine's, the traceback
    crossed the cap, and the recorded row stopped before it reached the error
    it was reporting — a wall of framework internals with the actual failure
    missing.
    """
    client, db = crashing

    def deep(n):
        if n:
            return deep(n - 1)
        raise RuntimeError("the innermost thing that broke")

    from backend.services import errorlog

    # The trimming itself, against a stack long enough to need it. (A
    # recursive one will not do: format_exception collapses repeats into
    # "[Previous line repeated N more times]", so it never grows.)
    frames = "".join(f'  File "f{i}.py", line {i}, in step{i}\n    call()\n'
                     for i in range(600))
    raw = ("Traceback (most recent call last):\n" + frames
           + "RuntimeError: the innermost thing that broke")
    trimmed = errorlog.trim_traceback(raw)

    assert len(raw) > 8000, "the fixture has to be long enough to matter"
    assert trimmed.endswith("RuntimeError: the innermost thing that broke")
    assert trimmed.startswith("Traceback (most recent call last):")
    assert "elided" in trimmed, "and it should say what it dropped"
    assert len(trimmed) < len(raw)

    # And end to end: what lands in the row still names the error.
    try:
        deep(3)
    except RuntimeError as exc:
        errorlog.record(kind="backend", level="ERROR", exc=exc)
    errorlog.flush()

    row = [r for r in db.error_events_list() if r["func"] == "deep"][0]
    assert len(row["traceback"]) <= 8000, "the column is String(8000)"
    assert "the innermost thing that broke" in row["traceback"]


def test_an_inbound_request_id_is_honoured_only_if_it_looks_like_one(crashing):
    """It is echoed and logged, so its shape is checked before it is trusted."""
    client, _db = crashing

    mine = "0123456789abcdef"
    res = client.get("/api/_test_boom/abc", headers={"X-Request-Id": mine})
    assert res.headers["X-Request-Id"] == mine

    junk = "<script>alert(1)</script>"
    res = client.get("/api/_test_boom/abc", headers={"X-Request-Id": junk})
    assert res.headers["X-Request-Id"] != junk
