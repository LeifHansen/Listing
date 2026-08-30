"""Every read and write in db.py has to say what it does when it fails.

This branch has now fixed the same bug seven times: a storage failure answered
with the shape of a real, empty answer, and a screen presented that as a fact
about the seller's account. The session lookup returning `None` logged
everyone out. `list_listings` returning `[]` made the eBay import duplicate an
entire store. `get_prefs` returning `{}` showed the app's fallbacks as saved
settings, one Save away from overwriting the real ones. `token_history`
returning `[]` said nothing had ever been charged. `pending_*` returning `[]`
reported an erasure backlog of zero to the operator watching for exactly that
number.

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

DB_PY = pathlib.Path(__file__).resolve().parents[1] / "db.py"


def _classify() -> tuple[set[str], set[str]]:
    """(functions that raise from an except arm, functions that swallow)."""
    tree = ast.parse(DB_PY.read_text())
    raises: set[str] = set()
    swallows: set[str] = set()
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_"):
            continue
        handlers = [h for node in ast.walk(fn) if isinstance(node, ast.Try)
                    for h in node.handlers]
        if not handlers:
            continue
        if any(isinstance(x, ast.Raise) for h in handlers for x in ast.walk(h)):
            raises.add(fn.name)
        else:
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
    "count_pending_deletion_notices": "same, for the notices eBay is waiting on",
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

    # --- writes that hand the caller a verdict to check -------------------
    "upsert_listing":
        "returns False for a write that did not land, and the publish path checks it",
    "save_marketplace_account":
        "returns False for a write that did not land, and the caller checks it",
    "delete_listing":
        "returns False when no row was removed, which the merge path checks",
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
}


def test_every_swallowed_failure_has_a_recorded_reason():
    _, swallows = _classify()
    missing = sorted(swallows - set(WHY_A_BLANK_IS_SAFE))
    assert not missing, (
        "these answer a storage failure with a blank and nobody has said why "
        "that is safe:\n  " + "\n  ".join(missing)
        + "\n\nIf a screen could read the blank as a fact about the seller, "
          "raise StorageUnavailable instead. If not, add the reason to "
          "WHY_A_BLANK_IS_SAFE in this file.")


def test_the_reasons_have_not_gone_stale():
    """A function that now raises must not still be listed as safely blank."""
    raises, _ = _classify()
    stale = sorted(raises & set(WHY_A_BLANK_IS_SAFE))
    assert not stale, (
        "these raise now, so their entry in WHY_A_BLANK_IS_SAFE is describing "
        "code that no longer exists: " + ", ".join(stale))


def test_the_reads_that_must_refuse_still_refuse():
    raises, swallows = _classify()
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
    raises, swallows = _classify()
    everything = raises | swallows
    orphans = []
    for name in sorted(everything):
        if not name.endswith("_best_effort"):
            continue
        strict = name[: -len("_best_effort")]
        if strict not in raises:
            orphans.append(f"{name} (no {strict} that raises)")
    assert not orphans, "\n".join(orphans)


def test_the_sweep_still_finds_the_module():
    """A sweep that quietly stops finding anything passes for ever."""
    raises, swallows = _classify()
    assert len(raises) >= 15, f"only found {len(raises)} raising functions"
    assert len(swallows) >= 25, f"only found {len(swallows)} swallowing ones"
