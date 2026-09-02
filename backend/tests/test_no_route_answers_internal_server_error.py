"""No route answers "Internal Server Error" to a badly-formed request.

A 500 is either a crash or a condition nobody handled. Both are bad for the
same two reasons this branch has already hit once: the seller is shown a fault
with no next step, and a real 500 — the kind worth paging about — ends up
buried under noise. P0-01's follow-up was exactly this shape, where making
`safe_session_name` reject rather than rewrite left the rejection with nowhere
to go and every route touching storage answered 500 to a malformed id.

These are not attack payloads. They are the shapes a confused client, an old
app version, a retried request or an ordinary scanner actually sends: missing
fields, nulls where objects go, a path segment that is `..`, a number where a
string belongs, and values far outside anything a person would type.

400, 401, 404, 422 and 503 are all fine answers — they say what happened and
what to do. What must not appear is 500.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

BIG = "A" * 20000

# Bodies, not attacks. Each one is something a real client has sent or could.
BODIES = [
    {},
    {"session_id": ""},
    {"session_id": "does-not-exist"},
    {"listing": None},
    {"listing": {}},
    {"ids": None},
    {"ids": ["x"] * 500},
    {"query": ""},
    {"target_id": "", "source_ids": []},
    {"marketplaces": []},
    {"listing": {"title": "x", "price": "not a number"}},
    {"listing": {"title": BIG, "price": 1e308, "quantity": 10 ** 12}},
    {"listing": {"item_specifics": [{"name": None, "value": None}]}},
    {"listing": {"images": ["../../x.jpg"], "image_urls": ["file:///etc/passwd"]}},
    {"limit": -1},
    {"prefs": {"auto_promote": "yes"}},
]

# Path-parameter values. No NUL byte: httpx refuses to build the URL, which
# tests the client rather than the app.
PATH_VALUES = ["..", "../../etc/passwd", "..%2f..%2fetc%2fpasswd", "-", "0",
               "A" * 2000, "sé$$ion"]

ALLOWED = {400, 401, 402, 403, 404, 405, 409, 412, 415, 422, 429, 503, 507}


@pytest.fixture(scope="module")
def api():
    from backend import main
    client = TestClient(main.app, raise_server_exceptions=False)
    client.post("/api/auth/signup",
                json={"email": "fuzz@example.com", "password": "hunter2hunter2"})
    return main, client


def _routes(main):
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        for method in sorted((getattr(route, "methods", None) or set())
                             - {"HEAD", "OPTIONS"}):
            yield method, path


def _fill(path: str, value: str) -> str:
    for part in path.split("{")[1:]:
        path = path.replace("{" + part.split("}")[0] + "}", value)
    return path


def test_the_sweep_actually_covers_the_api(api):
    """A sweep that quietly stops finding routes passes for ever."""
    main, _ = api
    assert len(list(_routes(main))) >= 60


def test_no_route_answers_500_to_a_malformed_request(api):
    main, client = api
    bad = []
    for method, path in _routes(main):
        values = PATH_VALUES if "{" in path else [None]
        bodies = [None] if method == "GET" else BODIES
        for value in values:
            url = _fill(path, value) if value is not None else path
            for body in bodies:
                res = client.request(method, url, json=body)
                if res.status_code >= 500 and res.status_code in ALLOWED:
                    continue
                if res.status_code >= 500:
                    bad.append(f"{res.status_code} {method} {path} "
                               f"id={value!r} body={json.dumps(body)[:60]} "
                               f"-> {res.text[:120]}")
    assert not bad, ("routes answered with a server fault:\n  "
                     + "\n  ".join(sorted(set(bad))[:20]))
