"""Two ways a publish could go wrong that nothing was checking.

1. A seller whose eBay token refresh fails looks, to `creds_for`, exactly like
   a seller who never connected eBay: both get `None`. The second case is
   meant to fall through to the env-configured single-tenant credentials. The
   first must not — those credentials are the OPERATOR's, so a seller's item
   would go live on the operator's eBay account.

2. The pre-publish checklist gated only the Inventory publish route. Every
   listing this app publishes live is stamped `source="ebay"`, so from its
   second publish onward it takes the imported route, which returned before
   the gate. Revises and relists ran with no checks at all.
"""
from __future__ import annotations

import pytest

from backend import config
from backend.marketplaces import ebay_provider
from backend.services import preflight
from backend.marketplaces.base import PublishContext
from backend.models import Listing

CONNECTED = {"refresh_token": "r-1", "ebay_username": "seller"}


# --- 1. the operator's credentials are not a fallback for a broken token ----

@pytest.fixture
def broken_token(monkeypatch):
    """A user who IS connected, whose access-token refresh fails."""
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account",
                        lambda uid: dict(CONNECTED))

    def _boom(_refresh):
        raise RuntimeError("eBay token endpoint returned 500")
    monkeypatch.setattr(ebay_provider, "access_token_for", _boom)


def test_a_failed_token_refresh_is_not_the_same_as_never_connecting(broken_token):
    """Fails against the old code, which had no way to tell them apart."""
    assert ebay_provider.creds_for("u1") is None        # indistinguishable...
    assert ebay_provider.has_stored_connection("u1")    # ...until now


def test_a_seller_who_never_connected_has_no_stored_connection(monkeypatch):
    """The dry-run case must stay a dry run, not become an error."""
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda uid: None)
    assert not ebay_provider.has_stored_connection("u1")


def test_a_signed_out_request_has_no_stored_connection():
    assert not ebay_provider.has_stored_connection(None)


def test_the_check_never_re_attempts_the_token_exchange(monkeypatch):
    """A retry that happened to succeed would answer "not broken" and reopen
    the operator-credentials fallback. The check must be a plain lookup."""
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account",
                        lambda uid: dict(CONNECTED))

    def _must_not_run(_refresh):
        raise AssertionError("has_stored_connection refreshed the token")
    monkeypatch.setattr(ebay_provider, "access_token_for", _must_not_run)
    assert ebay_provider.has_stored_connection("u1")


# --- 2. the checklist runs on the revise/relist route too -------------------

def _live_listing(**over) -> Listing:
    """A listing that would pass the full live checklist."""
    fields = {
        "title": "Vintage Pyrex mixing bowl",
        "condition": "USED_EXCELLENT",
        "category_id": "12345",
        "price": 24.99,
        "quantity": 1,
        "description": "A bowl.",
        "image_urls": ["https://i.ebayimg.com/x.jpg"],
        "package_weight_lb": 2.0,
        "source": "ebay",
        "ebay_listing_id": "110000000001",
    }
    fields.update(over)
    return Listing(**fields)


ACCOUNT_OK = dict(has_fulfillment=True, has_payment=True, has_return=True,
                  has_location=True, connected=True)


def test_a_revise_checks_the_content_it_is_about_to_send():
    """Note: this one passes against the old code too — the pre-existing
    draft-mode block already covered the title. It pins that "revise" keeps
    the content checks; the route-level test below is the one that shows the
    defect."""
    issues = preflight.errors_only(preflight.validate(
        _live_listing(title=""), "revise", **ACCOUNT_OK))
    assert [i["target"] for i in issues] == ["title"]


def test_the_imported_route_consults_the_checklist_at_all(monkeypatch):
    """The actual defect: `_publish_locked` returned from the imported branch
    before reaching the preflight gate, so a revise or relist of any listing
    with source="ebay" — which is every listing this app has published live —
    was never checked.

    Fails against the old code: `asked` stays empty.
    """
    asked: list[str] = []
    monkeypatch.setattr(ebay_provider, "preflight_issues",
                        lambda uid, listing, mode: asked.append(mode) or [])
    monkeypatch.setattr(ebay_provider.listing_sync, "push_edit",
                        lambda *a, **k: {"listing_id": "110000000001"})
    monkeypatch.setattr(ebay_provider, "_record_published",
                        lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider.image_import, "images_changed",
                        lambda *a, **k: False)
    monkeypatch.setattr(ebay_provider.storage, "optimized_dir",
                        lambda sid: __import__("pathlib").Path("/nonexistent"))

    ctx = PublishContext(
        session_id="s1", listing=_live_listing(), mode="live",
        base_url="https://example.test", uid="u1",
        prev_record={"status": "published"})
    out = ebay_provider.EbayProvider()._publish_locked(
        ctx, {"access_token": "tok", "ebay_username": ""})

    assert asked == ["revise"], "the imported route must run the checklist"
    assert out.ok


def test_a_revise_does_not_demand_what_the_live_listing_already_satisfies():
    """An imported listing often carries no local package weight and not the
    category's full aspect list — eBay already accepted the listing without
    this app's copy of them. Blocking the edit would strand the seller on a
    listing they can see but cannot change."""
    issues = preflight.errors_only(preflight.validate(
        _live_listing(package_weight_lb=0.0), "revise",
        has_fulfillment=False, has_payment=False, has_return=False,
        has_location=False, connected=True,
        required_aspects=["Brand", "Type"]))
    assert issues == []


def test_a_relist_answers_to_the_full_create_checklist():
    """A relist calls the same create_on_ebay a first publish does, so it is
    a new listing and takes the full contract — including the package weight
    and the account's business policies."""
    issues = preflight.errors_only(preflight.validate(
        _live_listing(package_weight_lb=0.0), "live",
        has_fulfillment=False, has_payment=True, has_return=True,
        has_location=True, connected=True))
    targets = {i["target"] for i in issues}
    assert "weight" in targets and "shipping" in targets


def test_a_draft_is_still_only_checked_for_what_a_draft_needs():
    """Unchanged behaviour — guards the new mode from leaking into drafts."""
    issues = preflight.errors_only(preflight.validate(
        _live_listing(price=None, category_id=""), "draft", **ACCOUNT_OK))
    assert issues == []


def test_blocking_a_revise_is_off_until_the_logs_say_it_is_safe():
    """Shipped observing-first: the flag decides, and it defaults off. A
    checklist that has never run against live listings will find things eBay
    accepted years ago, and locking sellers out of editing is worse than the
    rejection the check is meant to prevent."""
    assert config.EBAY_PREFLIGHT_BLOCKS_REVISE is False


# --- what the route DOES with problems, not just that it asks ---------------
#
# test_the_imported_route_consults_the_checklist_at_all above stubs the
# checklist to return [], so it only ever proves the route CALLED it — an
# assertion on a mock's arguments. The gate itself ran in no test. Two
# mutations were shown to leave the whole suite green: hard-blocking a revise
# despite the flag being off, and dropping the problems entirely so a blocked
# publish goes to eBay. These cover both.

def _imported_ctx(prev_status):
    return PublishContext(
        session_id="s1", listing=_live_listing(), mode="live",
        base_url="https://example.test", uid="u1",
        prev_record={"status": prev_status})


@pytest.fixture
def route(monkeypatch):
    """The imported route with one blocking problem and eBay stubbed out."""
    sent = []
    monkeypatch.setattr(
        ebay_provider, "preflight_issues",
        lambda uid, listing, mode: [{"target": "weight", "level": "error",
                                     "title": "Package weight is missing",
                                     "fix": "Enter it."}])
    monkeypatch.setattr(ebay_provider.listing_sync, "push_edit",
                        lambda *a, **k: sent.append("revise") or {"listing_id": "1"})
    monkeypatch.setattr(ebay_provider.listing_sync, "create_on_ebay",
                        lambda *a, **k: sent.append("relist") or {"listing_id": "2"})
    monkeypatch.setattr(ebay_provider, "_record_published", lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider.db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider.image_import, "images_changed",
                        lambda *a, **k: False)
    monkeypatch.setattr(ebay_provider.storage, "optimized_dir",
                        lambda sid: __import__("pathlib").Path("/nonexistent"))
    return sent


CREDS = {"access_token": "tok", "ebay_username": "", "_uid": "u1"}


def test_with_the_flag_off_a_failing_revise_still_reaches_ebay(route, monkeypatch):
    """Observe-first is the shipped behaviour. Hard-blocking here would lock
    sellers out of editing listings eBay accepted years ago."""
    monkeypatch.setattr(ebay_provider.config, "EBAY_PREFLIGHT_BLOCKS_REVISE", False)
    out = ebay_provider.EbayProvider()._publish_locked(
        _imported_ctx("published"), dict(CREDS))
    assert out.ok and route == ["revise"]


def test_with_the_flag_off_a_failing_relist_also_reaches_ebay(route, monkeypatch):
    """A relist is the same population of records as a revise — an imported
    listing that ended — so it gets the same observe period. It used to block
    with no flag at all, which stranded sellers on the Inactive tab's own
    promise that they can relist any time."""
    monkeypatch.setattr(ebay_provider.config, "EBAY_PREFLIGHT_BLOCKS_REVISE", False)
    out = ebay_provider.EbayProvider()._publish_locked(
        _imported_ctx("ended"), dict(CREDS))
    assert out.ok and route == ["relist"]


def test_with_the_flag_on_a_failing_revise_is_blocked(route, monkeypatch):
    """The other half: when the flag is turned on, the gate must actually
    stop the publish rather than merely log."""
    monkeypatch.setattr(ebay_provider.config, "EBAY_PREFLIGHT_BLOCKS_REVISE", True)
    out = ebay_provider.EbayProvider()._publish_locked(
        _imported_ctx("published"), dict(CREDS))
    assert not out.ok and route == []
    assert [i["target"] for i in out.issues] == ["weight"]


def test_with_the_flag_on_a_failing_relist_is_blocked(route, monkeypatch):
    monkeypatch.setattr(ebay_provider.config, "EBAY_PREFLIGHT_BLOCKS_REVISE", True)
    out = ebay_provider.EbayProvider()._publish_locked(
        _imported_ctx("ended"), dict(CREDS))
    assert not out.ok and route == []


def test_a_blocked_revise_does_not_demote_the_live_record(route, monkeypatch):
    """It is still live on eBay whatever the checklist thinks."""
    written = []
    monkeypatch.setattr(ebay_provider.config, "EBAY_PREFLIGHT_BLOCKS_REVISE", True)
    monkeypatch.setattr(
        ebay_provider.db, "upsert_listing",
        lambda sid, dump, status="", user_id=None, **k: written.append(status) or True)
    ebay_provider.EbayProvider()._publish_locked(
        _imported_ctx("published"), dict(CREDS))
    assert written == ["published"]
