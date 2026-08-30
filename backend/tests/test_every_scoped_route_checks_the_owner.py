"""A new route must not be able to reintroduce P0-01.

P0-01 was possession of a listing id granting access to the listing. It was
fixed route by route, and nothing stops the next route from forgetting: the
check is a line someone has to remember to write, in a file with 105 handlers,
and a session id is not a secret — it rides in the public /media URLs handed to
eBay, so it turns up in eBay's listing pages, in the seller's browser history
and in any log that records image fetches.

So this walks main.py's AST, finds every handler scoped to one listing or one
session, and requires each of them to be ownership-checked somewhere in its
call graph — or to appear below with a reason. It is a pure source scan: it
needs neither fastapi nor a booted app, and it fails on the route that was
added rather than on the seller who found it.

Nothing here is a claim that the check is CORRECT — `_assert_session_owner`'s
own behaviour (fail closed on a database outage, anonymous sessions still
usable) is tested in test_session_alias_authorization.py. This is the weaker,
broader claim the suite could not otherwise make: that no scoped route lacks
one entirely.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[1] / "main.py"
SRC = MAIN.read_text()
TREE = ast.parse(SRC)
FUNCS = {n.name: n for n in TREE.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

# The two shapes an ownership check takes in this file: the shared helper, and
# the inline comparison for handlers that already hold the record.
OWNERSHIP = re.compile(r"_assert_session_owner|\['user_id'\] != |\.get\('user_id'\) != ")

# Parameter names that mean "one listing" — the identifiers P0-01 was about.
SCOPED_ARGS = {"session_id", "listing_id", "record_id", "sid"}

# Routes that are scoped by one of those names and deliberately do NOT check.
# Each needs a reason, and the reason is asserted below — an exemption that
# stops being true has to fail here rather than sit in a comment.
EXEMPT = {
    "media":
        "The public photo URL. eBay's own ingestion fetches it with no "
        "cookie, so it cannot require a session; `name` is contained "
        "against traversal and nothing else is read.",
    "tokens_confirm":
        "`session_id` is a Stripe Checkout session, not a listing session — "
        "a different namespace. It requires a login and confirms against "
        "Stripe scoped to that user.",
    "delete_listing":
        "The check is inside `db.delete_listing(listing_id, uid)`, which "
        "filters by owner in the DELETE itself — one round trip instead of "
        "read-then-check.",
}


def _routes(node) -> list[tuple[str, str]]:
    out = []
    for d in node.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post", "put", "patch", "delete") and d.args):
            try:
                out.append((d.func.attr.upper(), ast.literal_eval(d.args[0])))
            except ValueError:            # a computed path — not expected here
                out.append((d.func.attr.upper(), "<computed>"))
    return out


def _guarded(node, depth: int = 0, seen: frozenset = frozenset()) -> bool:
    """Does an ownership check appear anywhere in this handler's call graph?"""
    if depth > 3 or node.name in seen:
        return False
    if OWNERSHIP.search(ast.unparse(node)):
        return True
    seen = seen | {node.name}
    called = {c.func.id for c in ast.walk(node)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    return any(name in FUNCS and _guarded(FUNCS[name], depth + 1, seen)
               for name in called)


def _scoped_handlers() -> dict:
    out = {}
    for name, node in FUNCS.items():
        routes = _routes(node)
        if not routes:
            continue
        args = {a.arg for a in node.args.args + node.args.kwonlyargs}
        if not (SCOPED_ARGS & args
                or any(f"{{{i}}}" in path for _m, path in routes for i in SCOPED_ARGS)):
            continue
        out[name] = (routes, node)
    return out


SCOPED = _scoped_handlers()


def test_the_scan_found_the_routes_it_is_meant_to_guard():
    """A scan that silently matches nothing passes forever. These are the
    handlers P0-01 was actually about; if one is renamed the list moves with
    it, but the scan going empty is the failure this catches."""
    assert len(SCOPED) >= 15, f"only found {len(SCOPED)}: {sorted(SCOPED)}"
    for expected in ("save_listing", "patch_listing", "get_listing",
                     "relist_listing", "upload_more"):
        assert expected in SCOPED, f"{expected} is no longer being scanned"


@pytest.mark.parametrize("name", sorted(SCOPED))
def test_a_listing_scoped_route_checks_who_is_asking(name):
    routes, node = SCOPED[name]
    if name in EXEMPT:
        assert EXEMPT[name].strip(), "an exemption needs a reason"
        return
    where = " ".join(f"{m} {p}" for m, p in routes)
    assert _guarded(node), (
        f"{where} ({name}, main.py:{node.lineno}) is scoped to one listing and "
        f"never checks who is asking. Call _assert_session_owner, or compare "
        f"the record's user_id — or add it to EXEMPT with a reason.")


@pytest.mark.parametrize("name", sorted(EXEMPT))
def test_every_exemption_is_still_a_real_route(name):
    """An exemption for a route that no longer exists is a hole waiting for
    the next handler that happens to reuse the name."""
    assert name in SCOPED, f"{name} is exempted but is not a scoped route"


def test_the_exempt_delete_really_is_scoped_in_the_query():
    """delete_listing's exemption rests entirely on db.delete_listing doing
    the check. Pin that, or the exemption is just a comment."""
    db_src = (MAIN.parent / "db.py").read_text()
    fn = next(n for n in ast.parse(db_src).body
              if isinstance(n, ast.FunctionDef) and n.name == "delete_listing")
    body = ast.unparse(fn)
    assert "user_id" in {a.arg for a in fn.args.args}, \
        "db.delete_listing no longer takes the caller's user id"
    assert "rec.user_id and rec.user_id != user_id" in body, \
        "db.delete_listing no longer refuses a listing owned by someone else"
    # And the route still hands it a uid rather than defaulting to None.
    route = ast.unparse(FUNCS["delete_listing"])
    assert "db.delete_listing(listing_id, _uid(request))" in route
