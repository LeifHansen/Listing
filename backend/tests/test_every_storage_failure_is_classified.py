"""Every read and write in db.py has to say what it does when it fails.

This branch has now fixed the same bug ten times: a storage failure answered
with the shape of a real, empty answer, and a screen presented that as a fact
about the seller's account. The session lookup returning `None` logged
everyone out. `list_listings` returning `[]` made the eBay import duplicate an
entire store. `get_prefs` returning `{}` showed the app's fallbacks as saved
settings, one Save away from overwriting the real ones. `token_history`
returning `[]` said nothing had ever been charged. `pending_*` returning `[]`
reported an erasure backlog of zero to the operator watching for exactly that
number. `get_listing` returning `None` answered ten routes with "Listing not
found" — and told the publish path it was looking at a brand-new session, which
is how a revise becomes a second live listing. `delete_listing` returning
`False` for a write that never reached the database was read as "no such row".

They were found one at a time, by reading. This is the sweep that stops the
next one needing to be found: every public function in `db.py` with an
`except` arm is either one that RAISES, or one whose blank answer is recorded
here with the reason it is safe. A new swallow fails this test until somebody
writes that line — which is the point, because the reason is the part nobody
writes down.

Neither answer is "the right" one. A worker that cannot read its queue has
nothing to do this pass and should carry on; a screen that cannot read the
seller's store must not say it is empty. What must not happen is the choice
being made by whichever `except` was easiest to type.
"""
from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]

# The three modules that stand between the app and something that can be
# unreachable: the database, the bucket, and the volume. The eighth instance
# of this bug was in the second one, found by asking db.py's question there --
# so the sweep asks it of all three.
MODULES = ("db.py", "objstore.py", "storage.py")


# Raising one of these is the refusal, wherever it is written. A wrapper that
# checks a sentinel and raises is doing exactly what an `except: raise` arm
# does, and the sweep has to see both or the seam becomes a hiding place.
REFUSALS = ("StorageUnavailable", "ObjectStoreUnavailable")


def _public_functions(module: str) -> list[ast.FunctionDef]:
    tree = ast.parse((BACKEND / module).read_text())
    return [fn for fn in tree.body
            if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_")]


def _refuses(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        name = getattr(exc, "attr", None) or getattr(exc, "id", None)
        if name in REFUSALS:
            return True
    return False


def _classify(module: str = "db.py") -> tuple[set[str], set[str]]:
    """(functions that refuse when storage fails, functions that swallow)."""
    raises: set[str] = set()
    swallows: set[str] = set()
    for fn in _public_functions(module):
        handlers = [h for node in ast.walk(fn) if isinstance(node, ast.Try)
                    for h in node.handlers]
        if any(isinstance(x, ast.Raise) for h in handlers for x in ast.walk(h)):
            raises.add(fn.name)
        elif _refuses(fn):
            raises.add(fn.name)
        elif handlers:
            swallows.add(fn.name)
    return raises, swallows


# Reads and writes where a blank answer would be a claim, so they raise and the
# route turns it into a 503. Listed rather than derived: the whole point is
# that removing one has to be a deliberate act somebody notices.
MUST_RAISE = {
    "get_user_by_id": "returning None logged every seller out during one DB blip",
    "list_listings": "returning [] made the sync import the seller's store a second time",
    "get_prefs": "returning {} showed the app's fallbacks as the seller's saved settings",
    "save_prefs": "returning {} reported a write that never landed as saved",
    "token_history": "returning [] told a seller they had never been charged",
    "list_notifications": "returning [] told a seller nothing had sold",
    "unread_notification_count": "the bell's badge is a count of real events",
    "get_ebay_account": "returning None turned a live publish into a dry run",
    "get_marketplace_account": "same, for every non-eBay marketplace",
    "save_ebay_account": "OAuth redirected to 'connected' on a token that was never stored",
    "count_pending_media_purges": "zero owed is a clean bill on somebody's erasure",
    "list_releasable_listings":
        "an empty answer reported an unlink that ran and found nothing, "
        "leaving the banner up with no explanation",
    "get_listings":
        "an empty answer told a seller every listing they had just ticked did "
        "not exist",
    "count_listings":
        "zero live listings is what suppresses the 'these stay up on eBay' "
        "warning on the delete-account dialog",
    "get_listing":
        "returning None answered ten routes with 'Listing not found', and told "
        "the publish path a live listing was a brand-new session",
    "delete_listing":
        "returning False for a write that never landed reads as 'no such row', "
        "so a failed delete was reported to the seller as a finished one",
    "count_pending_deletion_notices": "same, for the notices eBay is waiting on",
    "delete_prefix_strict":
        "counting zero for an unreachable bucket let a dropped erasure be recorded as done",
    "record_deletion_notice": "acknowledging to eBay before the row is durable loses the notice",
    "revoke_sessions": "'signed out everywhere' has to be true when it says so",
    "last_sweep": "a forgotten cooldown spends the whole application's daily eBay quota",
    "mark_sweep": "same, from the other end",
}

# Everything else that swallows, with why a blank answer cannot be mistaken
# for a fact about the seller. One line each, and the line is the deliverable.
WHY_A_BLANK_IS_SAFE = {
    # --- the best-effort halves of a strict pair -------------------------
    "list_listings_best_effort":
        "the tolerant twin of a strict read; the choice is made at the call site",
    "get_prefs_best_effort":
        "the tolerant twin: pre-filling a draft, never reporting what is saved",
    "get_ebay_account_best_effort":
        "the tolerant twin, for panels that hide themselves rather than claim",
    "get_marketplace_account_best_effort":
        "the tolerant twin, same reasoning as the eBay one",
    "save_ebay_account_best_effort":
        "returns False, which the caller checks; it never reports a save",

    # --- the error sink, whose whole job is not to make things worse ------
    # These are the ONE write family below db.py's admin_ line that swallows,
    # and it is deliberate. Recording a failure must never manufacture a
    # second one: the seller is already living through the first, and raising
    # here would turn a handled error into an unhandled one to complain about
    # the bookkeeping. Nothing reads their return as a fact about a seller —
    # the reader, error_events_list, still raises like every other report.
    "record_error_event":
        "returns False; a failure that cannot be recorded is still a failure, "
        "not a new one",
    "mark_error_fixed":
        "returns False; the row simply stays unresolved and surfaces again",
    "prune_error_events":
        "returns 0; housekeeping that skipped a pass runs again tomorrow",

    # --- writes that hand the caller a verdict to check -------------------
    "upsert_listing":
        "returns False for a write that did not land, and the publish path checks it",
    "save_marketplace_account":
        "returns False for a write that did not land, and the caller checks it",
    "token_refund":
        "returns False, and an unrefunded spend is recorded as owed rather than lost",

    # --- the billing family, whose None is a documented 'unknown' ---------
    "token_status":
        "None means unknown by design so the caller picks fail-open or fail-closed",
    "token_spend":
        "None means unknown; the spend path decides, and the module says so at the top",
    "token_credit":
        "None means unknown; the Stripe webhook retries, and the ref makes it idempotent",
    "token_reverse_purchase":
        "None means unknown; Stripe redelivers, and the derived ref stops a double debit",

    # --- sentinels that already distinguish 'could not ask' ---------------
    "find_users_by_ebay_user_id":
        "answers UNAVAILABLE, which is not an empty result and callers branch on it",
    "get_listing_strict":
        "answers UNAVAILABLE for a configured database that would not answer",
    "all_listing_ids":
        "answers None, not an empty set, so the orphan sweep skips rather than deletes",

    # --- work queues, where a worker's answer is 'nothing to do now' ------
    "pending_media_purges":
        "a worker that cannot look has nothing to do this pass; the count is separate",
    "pending_deletion_notices":
        "same, and the operator's number comes from the counter that raises",
    "finish_media_purge":
        "the photos are already gone; losing the bookkeeping re-runs a no-op",
    "note_media_purge_failure":
        "an uncounted attempt keeps the debt, which is the safe direction",
    "finish_deletion_notice":
        "the purge already ran; losing the bookkeeping must not undo it",

    # --- auth, where a blank fails closed ---------------------------------
    "create_user":
        "None makes signup answer 503 rather than pretending an account exists",
    "get_user_by_email":
        "None fails the login rather than letting one through",
    "get_password_hash":
        "None fails the password check; it can only refuse, never admit",
    "delete_user":
        "None means the erasure is not confirmed, and the route refuses to say it was",

    # --- state the UI hides rather than asserts ---------------------------
    "count_foreign_listings":
        "zero hides an explanatory banner; it never tells the seller they have none",
    "count_unowned_ebay_listings":
        "same banner, same direction: a missing warning, not a false all-clear",
    "add_notification":
        "None is also the normal deduplicated case, and no screen counts on it",
    "mark_notifications_read":
        "returns how many changed; unread is the safe direction to be wrong in",
    "touch_listing":
        "only bumps a cache-busting timestamp, so a stale thumbnail is the cost",
    "stamp_ebay_account":
        "returns how many rows it labelled; unlabelled records stay excluded",
    "disconnect_ebay_account":
        "the token is what matters and is cleared first; the rest is preference",
    "disconnect_marketplace_account":
        "same as the eBay disconnect, and the caller re-reads the connection state",
    "update_user":
        "None is surfaced by the caller as a failed save rather than a silent one",
    "mutate_listing_data":
        "None means the read-modify-write did not happen, which every caller checks",

    # --- the probe that must answer during the outage it describes --------
    "db_status":
        "the health probe: refusing to answer is the one thing it must never do",

    # --- objstore.py: best-effort by design, with one strict exception ----
    "url_for":
        "None is a signed URL we could not mint; /media answers 503, not 404",
    "upload":
        "None means not uploaded, and the offload verifies presence before unlinking",
    "restore":
        "False falls back to the local copy or a re-upload, never to 'no photo'",
    "exists":
        "False only ever causes another attempt; nothing reads it as 'deleted'",
    "delete":
        "one object; the prefix delete is the one erasure depends on, and it raises",
    "delete_prefix":
        "the tolerant twin, for the orphan sweep and post-sale cleanup",

    # --- storage.py: the volume, where a failure costs space not truth ----
    "purge_session":
        "reclaims space after a sold listing or an abandoned upload, promises nothing",
    "snapshot_image":
        "a missed undo step; the edit itself already landed",
    "image_index":
        "answers -1, which is not a position and every caller treats as absent",
    "disk_free_bytes":
        "0 means unknown, and the low-space guard is written to require knowing",
    "writable":
        "False refuses the write, which is the safe direction on a full volume",
    "prune_originals":
        "reclaims space; the files it leaves are found by the next pass",
    "prune_history":
        "same, and an unpruned history costs disk rather than correctness",
    "prune_exports":
        "same; an export left behind is regenerated on demand anyway",
    "load_listing":
        "None means 'no draft here', and the writer that reads it refuses rather "
        "than saving into a listing it could not read",
    "session_touched_at":
        "an unknown mtime makes the orphan sweep skip the dir rather than delete it",
    "sweep_orphan_sessions":
        "an unswept dir stays, which is the only safe direction for a delete pass",
}


def test_every_swallowed_failure_has_a_recorded_reason():
    swallows: set[str] = set()
    for module in MODULES:
        swallows |= _classify(module)[1]
    missing = sorted(swallows - set(WHY_A_BLANK_IS_SAFE))
    assert not missing, (
        "these answer a storage failure with a blank and nobody has said why "
        "that is safe:\n  " + "\n  ".join(missing)
        + "\n\nIf a screen could read the blank as a fact about the seller, "
          "raise StorageUnavailable instead. If not, add the reason to "
          "WHY_A_BLANK_IS_SAFE in this file.")


def test_the_reasons_have_not_gone_stale():
    """A function that now raises must not still be listed as safely blank."""
    raises: set[str] = set()
    for module in MODULES:
        raises |= _classify(module)[0]
    stale = sorted(raises & set(WHY_A_BLANK_IS_SAFE))
    assert not stale, (
        "these raise now, so their entry in WHY_A_BLANK_IS_SAFE is describing "
        "code that no longer exists: " + ", ".join(stale))


def test_the_reads_that_must_refuse_still_refuse():
    raises: set[str] = set()
    swallows: set[str] = set()
    for module in MODULES:
        r, w = _classify(module)
        raises |= r
        swallows |= w
    reverted = sorted(name for name in MUST_RAISE if name in swallows)
    assert not reverted, "\n".join(
        f"{name} stopped raising — {MUST_RAISE[name]}" for name in reverted)
    missing = sorted(name for name in MUST_RAISE if name not in raises)
    assert not missing, (
        "named in MUST_RAISE but not found raising in db.py (renamed, or "
        "gone): " + ", ".join(missing))


def test_a_reason_is_a_reason():
    """Guards against the entry that is really a shrug."""
    thin = sorted(k for k, v in WHY_A_BLANK_IS_SAFE.items()
                  if len(v.strip()) < 40)
    assert not thin, ("too short to be a reason anyone can check: "
                      + ", ".join(thin))


def test_every_best_effort_has_a_strict_twin_that_raises():
    """The pattern only works in pairs.

    A `*_best_effort` on its own is just a swallow with a longer name: the
    strict version is what makes choosing the tolerant one a decision.
    """
    raises = _classify("db.py")[0]
    orphans = []
    for fn in _public_functions("db.py"):
        if not fn.name.endswith("_best_effort"):
            continue
        strict = fn.name[: -len("_best_effort")]
        if strict not in raises:
            orphans.append(f"{fn.name} (no {strict} that raises)")
    assert not orphans, "\n".join(orphans)


def test_the_sweep_still_finds_every_module():
    """A sweep that quietly stops finding anything passes for ever."""
    for module in MODULES:
        raises, swallows = _classify(module)
        assert raises or swallows, f"{module} yielded nothing — renamed or moved?"
    raises, swallows = _classify("db.py")
    assert len(raises) >= 15, f"only found {len(raises)} raising functions in db.py"
    assert len(swallows) >= 25, f"only found {len(swallows)} swallowing ones in db.py"
