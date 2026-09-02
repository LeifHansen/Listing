"""Every outbound POST/PUT/PATCH/DELETE has to have answered one question.

The question: if this request goes out and the answer never comes back, may
the other side have acted on it?

It has one right answer per call site and the wrong one is expensive. The same
bug was found in four separate clients on this branch -- the eBay Trading
client, eBay's orders/logistics client, Etsy and Depop -- and in every case a
lost answer was reported to the seller as a REJECTION: "eBay rejected the
listing", "Couldn't reach eBay", "Etsy rejected the listing", "Depop rejected
the listing". Someone who reads that fixes a field and tries again, which is
how one item becomes two live listings, one label becomes two charges, and one
order gets two fulfillments.

Four times is a pattern, not four accidents, so this is the guard: every place
the app writes to somebody else's system is listed below with what a lost
answer means there. A new write, or a new client, fails this test until
somebody writes that line -- which is the only moment the question is cheap to
answer.

This checks COVERAGE, not behaviour. What each classification actually
produces is pinned by the per-client suites (test_unknown_outcome_is_its_own_
answer, test_label_purchase_outcome, test_etsy_unknown_outcome,
test_depop_unknown_outcome).
"""
from __future__ import annotations

import ast
import pathlib

# Call sites that CHANGE something on someone else's system and cannot be
# repeated for free. Each raises a client-specific UnknownOutcome when the
# answer is lost, and says so to the seller.
CHANGES_SOMETHING = {
    "backend.services.ebay_trading._call":
        "AddItem/ReviseItem/EndItem — a duplicate live listing. Classified "
        "per call name inside _call; reads and the Verify dry runs are exempt.",
    "backend.services.ebay_orders.purchase_label":
        "buys postage — a second charge.",
    "backend.services.ebay_orders.mark_shipped":
        "files a fulfillment and emails the buyer tracking — a second one is "
        "its own mess.",
    "backend.services.etsy.create_draft_listing":
        "mints a draft on the seller's Etsy shop — a second one on a retry.",
    "backend.services.etsy.update_listing":
        "edits, or activates, a listing on the seller's shop.",
    "backend.services.etsy.upload_listing_image":
        "adds a photo at a rank — a repeat is a duplicate photo. All three go "
        "through etsy._send, whose `changes` argument carries the answer.",
    "backend.services.depop._request":
        "the single choke point for Depop; classified by HTTP method, so a "
        "new call cannot slip past unclassified.",
    "backend.services.ebay_messages._request":
        "send_message puts words in a buyer's inbox — a repeat is the same "
        "message twice. The one choke point for eBay messaging, classified "
        "by HTTP method like depop._request; reads stay repeatable, and the "
        "best-effort mark_read swallows every MessagesError by design.",
}

# Call sites where a lost answer costs nothing that matters: the request can
# simply be made again. Each line has to say WHY -- "it's fine" is how the
# four bugs above got written.
SAFE_TO_REPEAT = {
    "backend.ebay_auth._token_request":
        "OAuth token exchange. A lost answer means no token, and asking again "
        "mints another; nothing of the seller's changes.",
    "backend.etsy_auth._token_request": "same as ebay_auth._token_request.",
    "backend.depop_auth._token_request": "same as ebay_auth._token_request.",
    "backend.services.ebay_notify._app_token":
        "application token fetch — no seller state.",
    "backend.services.taxonomy._app_token": "same as ebay_notify._app_token.",
    "backend.services.adobe._access_token": "same as ebay_notify._app_token.",
    "backend.ebay_auth._create_policy":
        "creates a business policy, but every caller looks the account up "
        "FIRST and adopts an existing one (ensure_payment_policy and friends), "
        "so a lost answer is adopted on the next attempt rather than "
        "duplicated. That lookup is P1-07's fix and is what makes this safe.",
    "backend.ebay_auth.ensure_service_policy": "same as _create_policy.",
    "backend.ebay_auth.ensure_inventory_location":
        "keyed on a merchant location key the app chooses, so eBay rejects a "
        "repeat rather than making a second location.",
    "backend.ebay_auth.opt_in_to_program":
        "opting into a program is idempotent — already opted in is not an "
        "error, and there is nothing to undo.",
    "backend.services.ebay_orders.create_shipping_quote":
        "a quote costs nothing and reserves nothing.",
    "backend.services.promotions.suggested_ad_rates":
        "a POST that READS: findListingRecommendations. Commits to no fee.",
    "backend.services.tokens._stripe_post":
        "creates a Checkout Session, which moves no money — the webhook "
        "credits the purchase, idempotently, and is the thing that charges.",
    "backend.services.images._pixian_cutout":
        "a paid cutout API, so a lost answer may still be billed for one "
        "image. Repeating costs one more cutout and nothing of the seller's "
        "changes — no listing, no order, no marketplace state. Worth knowing, "
        "not worth a dialog.",
    "backend.services.images._photoroom_cutout": "same as _pixian_cutout.",
    "backend.services.adobe._submit":
        "submits an async image job and returns a poll URL. The job operates "
        "on an image WE uploaded, not on anything of the seller's on another "
        "service; a lost answer loses the poll URL, so the work is redone and "
        "nothing is left behind that anyone can see.",
}

_VERBS = {"post", "put", "patch", "delete", "request"}


def _write_sites() -> set[str]:
    """Every `module.function` that makes an outbound HTTP write."""
    found: set[str] = set()
    root = pathlib.Path(__file__).resolve().parents[2]
    for path in sorted((root / "backend").rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            # Any MENTION of httpx.post and friends, not only a direct call:
            # services/etsy passes the verb as an argument to its own _send
            # helper, and a call-only scan walked straight past three writes
            # to a seller's shop.
            if not isinstance(node, ast.Attribute):
                continue
            name = ast.unparse(node)
            if not name.startswith("httpx."):
                continue
            if name.rsplit(".", 1)[-1] not in _VERBS:
                continue
            holder = parents.get(node)
            while holder is not None and not isinstance(
                    holder, (ast.FunctionDef, ast.AsyncFunctionDef)):
                holder = parents.get(holder)
            module = str(path.relative_to(root)).replace("/", ".")[:-3]
            found.add(f"{module}."
                      f"{holder.name if holder else '<module>'}")
    return found


def test_every_outbound_write_says_what_a_lost_answer_means():
    classified = set(CHANGES_SOMETHING) | set(SAFE_TO_REPEAT)
    unclassified = _write_sites() - classified
    assert not unclassified, (
        "These write to someone else's system and nothing here says what a "
        "lost answer means. Decide, then add the line:\n  "
        + "\n  ".join(sorted(unclassified)))


def test_the_lists_do_not_describe_calls_that_are_gone():
    """A stale entry is worse than none: it reads as a decision somebody made
    about code that no longer exists, and hides the one they didn't make."""
    classified = set(CHANGES_SOMETHING) | set(SAFE_TO_REPEAT)
    stale = classified - _write_sites()
    assert not stale, ("no longer makes an outbound write:\n  "
                       + "\n  ".join(sorted(stale)))


def test_nothing_is_in_both_lists():
    assert not (set(CHANGES_SOMETHING) & set(SAFE_TO_REPEAT))


def test_the_change_making_clients_all_raise_an_unknown_outcome():
    """Coverage of the CHANGES_SOMETHING side: each client that owns one of
    those call sites must have a condition for it, and it must be
    recognisable without importing that client (see `outcome_unknown`)."""
    from backend.services import (depop, ebay_messages, ebay_orders,
                                  ebay_trading, etsy)

    for module in (ebay_trading, ebay_orders, etsy, depop, ebay_messages):
        unknown = getattr(module, "UnknownOutcome", None)
        assert unknown is not None, f"{module.__name__} has no UnknownOutcome"
        assert unknown.outcome_unknown is True
        base = unknown.__mro__[1]
        assert base.outcome_unknown is False, (
            f"{base.__name__} must answer the question too, so a caller can "
            "ask any failure from this client")
