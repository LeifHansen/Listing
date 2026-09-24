"""A new route must not be able to reintroduce P0-01.

P0-01 was possession of a listing id granting access to the listing. It was
fixed route by route, and nothing stops the next route from forgetting: the
check is a line someone has to remember to write, in a file with 105 handlers,
and a session id is not a secret — it rides in the public /media URLs handed to
eBay, so it turns up in eBay's listing pages, in the seller's browser history
and in any log that records image fetches.

So this walks the AST of every module that registers routes — main.py and
the modules split out of it under routers/ — finds every handler scoped to
one listing or one session, and requires each of them to be ownership-checked
somewhere in its call graph — or to appear below with a reason. It is a pure
source scan: it needs neither fastapi nor a booted app, and it fails on the
route that was added rather than on the seller who found it.

Nothing here is a claim that the check is CORRECT —
`deps.assert_session_owner`'s own behaviour (fail closed on a database
outage, anonymous sessions still usable) is tested in
test_session_alias_authorization.py. This is the weaker, broader claim the
suite could not otherwise make: that no scoped route lacks one entirely.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[1] / "main.py"
# main.py held every handler until the split into routers/ began. A handler
# moved out of the scan's sight would be exactly how the next cross-user read
# ships unreviewed, so the scan reads every module there too.
SOURCES = [MAIN, *sorted((MAIN.parent / "routers").glob("*.py"))]
DEFINED = [(path.relative_to(MAIN.parent), n)
           for path in SOURCES for n in ast.parse(path.read_text()).body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
FUNCS = {n.name: n for _where, n in DEFINED}
WHERE = {n.name: where for where, n in DEFINED}

# Two of the three shapes an ownership check takes in this file: the shared
# helper, and the inline comparison for a handler that already holds the
# record. The third — threading the caller's identity into the lookup so the
# QUERY is scoped — cannot be a substring match, because the same expression
# appears in log lines that check nothing; _scopes_a_call below finds it.
OWNERSHIP = re.compile(r"deps\.assert_session_owner"
                       r"|\['user_id'\] != |\.get\('user_id'\) != ")

# How this file spells "who is asking".
IDENTITY = re.compile(r"deps\.uid\(request\)|run_in_threadpool\(deps\.uid, request\)"
                      r"|user\['id'\]|creds\['_uid'\]")

# Calls that only RECORD who is asking. note_user tags the error log with the
# caller; it sits inside the helper every handler calls to learn who that is,
# so counting it as a scoped lookup made every such handler look checked.
RECORDS_ONLY = ("debug", "info", "warning", "error", "exception", "critical",
                "note_user")

# Every identifier namespace a route can be scoped by — not just the listing
# ids P0-01 was about. A bulk job holds a seller's drafts and photos, and a
# shipment id reaches a label carrying the BUYER's name and address, so those
# ids need an owner too.
SCOPED_ARGS = {"session_id", "listing_id", "record_id", "sid",
               "job_id", "shipment_id"}

# Routes that are scoped by one of those names and deliberately do NOT check.
# Each needs a reason, and the reason is asserted below — an exemption that
# stops being true has to fail here rather than sit in a comment.
EXEMPT = {
    "media":
        "The public photo URL. eBay's own ingestion fetches it with no "
        "cookie, so it cannot require a session; `name` is contained "
        "against traversal and nothing else is read.",
    "media_video":
        "The listing's video, played by a <video> tag. Same constraint as "
        "`media` above and a DIFFERENT reason, so it is written out rather "
        "than borrowed: eBay never fetches this URL (a video is PUSHED "
        "through the Media API and referenced by the id eBay mints, not "
        "pulled from a link), but the native shell authenticates with a "
        "bearer token and a <video src> carries no header — so requiring a "
        "session would leave every video unplayable in the app. It exposes "
        "nothing a session id did not already expose: the same id serves "
        "that listing's photos from `media`, and `name` is checked against "
        "storage.safe_video_name before any path is built from it.",
    "tokens_confirm":
        "`session_id` is a Stripe Checkout session, not a listing session — "
        "a different namespace. It requires a login and confirms against "
        "Stripe scoped to that user.",
    "admin_get_listing":
        "A superadmin console route: the cross-user read is the point. "
        "Access is gated by require_superadmin — fail-closed, the role "
        "re-read from the user row on every request, 404 to everyone else "
        "— pinned by test_admin_requires_a_superadmin.py.",
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


def _scopes_a_call(node) -> bool:
    """Is the caller's identity passed INTO a call — i.e. does the lookup
    itself filter by owner (`db.delete_listing(id, deps.uid(request))`)?
    Directly, or through the local it was put in first
    (`uid = deps.uid(request)` ... `jobstore.internal(job_id, uid)`).

    An argument, deliberately, not a substring of the function: a handler that
    only logs `deps.uid(request)` has checked nothing, and that is exactly what
    `delete_listing` also does one line below its real check.
    """
    held = {t.id for a in ast.walk(node) if isinstance(a, ast.Assign)
            and IDENTITY.search(ast.unparse(a.value))
            for t in a.targets if isinstance(t, ast.Name)}
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        target = ast.unparse(call.func)
        if target.split(".")[-1] in RECORDS_ONLY:
            continue
        args = call.args + [k.value for k in call.keywords]
        if any(IDENTITY.search(ast.unparse(a))
               or any(isinstance(n, ast.Name) and n.id in held for n in ast.walk(a))
               for a in args):
            return True
    return False


def _guarded(node, depth: int = 0, seen: frozenset = frozenset()) -> bool:
    """Does an ownership check appear anywhere in this handler's call graph?"""
    if depth > 3 or node.name in seen:
        return False
    if OWNERSHIP.search(ast.unparse(node)) or _scopes_a_call(node):
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
    assert len(SCOPED) >= 20, f"only found {len(SCOPED)}: {sorted(SCOPED)}"
    for expected in ("save_listing", "patch_listing", "get_listing",
                     "relist_listing", "upload_more", "bulk_status",
                     "import_status", "easypost_refund"):
        assert expected in SCOPED, f"{expected} is no longer being scanned"


def test_asking_who_is_asking_is_not_a_check():
    """Resolving the caller's id checks it against nothing. The resolver hands
    the id to the error log, and the scan used to follow a handler into it
    and read that as a lookup scoped by owner — so any handler that so much
    as asked who was calling passed, whatever it then did with the id."""
    assert "uid" in FUNCS, "the resolver is no longer in the scan's call graph"
    handler = ast.parse(
        "def peek(listing_id, request):\n"
        "    who = uid(request)\n"
        "    log.info('peek by %s', who)\n"
        "    return db.get_listing(listing_id)\n").body[0]
    assert not _guarded(handler)


def test_no_function_name_is_defined_in_two_scanned_modules():
    """The call graph is followed by bare name. Two modules defining the same
    one would let the scan read one module's function as the other's, and a
    guarded helper could vouch for an unguarded copy of itself."""
    seen: dict[str, list[str]] = {}
    for where, node in DEFINED:
        seen.setdefault(node.name, []).append(f"{where}:{node.lineno}")
    twice = {name: at for name, at in seen.items() if len(at) > 1}
    assert not twice, twice


@pytest.mark.parametrize("name", sorted(SCOPED))
def test_a_listing_scoped_route_checks_who_is_asking(name):
    routes, node = SCOPED[name]
    if name in EXEMPT:
        assert EXEMPT[name].strip(), "an exemption needs a reason"
        return
    where = " ".join(f"{m} {p}" for m, p in routes)
    assert _guarded(node), (
        f"{where} ({name}, {WHERE[name]}:{node.lineno}) is scoped to one "
        f"listing and never checks who is asking. Call "
        f"deps.assert_session_owner, or compare the record's user_id — or "
        f"add it to EXEMPT with a reason.")


@pytest.mark.parametrize("name", sorted(EXEMPT))
def test_every_exemption_is_still_a_real_route(name):
    """An exemption for a route that no longer exists is a hole waiting for
    the next handler that happens to reuse the name."""
    assert name in SCOPED, f"{name} is exempted but is not a scoped route"


def test_the_delete_is_scoped_in_the_query_itself():
    """delete_listing passes the caller's uid rather than reading first, so
    the scan sees it as guarded. That only holds while db.delete_listing
    actually filters on it — pin the far end too."""
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
    assert "db.delete_listing(listing_id, deps.uid(request))" in route


def test_the_job_readers_actually_use_the_uid_they_are_given():
    """The three bulk/import routes are seen as guarded because they thread
    the caller's uid into jobstore's lookup. That is only a check if jobstore
    honours it — the far end of the same trust the delete rests on.

    A job with no owner stays readable by id: the app supports logged-out
    bulk uploads, and that matches `deps.assert_session_owner`'s rule for an
    anonymous session. What must never happen is an OWNED job answering
    someone else.
    """
    js = ast.parse((MAIN.parent / "services" / "jobstore.py").read_text())
    readers = {n.name: n for n in js.body
               if isinstance(n, ast.FunctionDef)
               and n.name in ("snapshot", "snapshot_json", "brief_json")}
    assert set(readers) == {"snapshot", "snapshot_json", "brief_json"}, \
        f"a job reader was renamed or removed: {sorted(readers)}"
    for name, fn in readers.items():
        assert "uid" in {a.arg for a in fn.args.args}, \
            f"jobstore.{name} no longer takes the caller's uid"
        assert "if owner and owner != uid:" in ast.unparse(fn), \
            f"jobstore.{name} no longer refuses a job owned by someone else"
