"""Per-marketplace state merge + top-level status derivation.

Pure-module tests: import only marketplaces.base/state (stdlib + pydantic),
so they run under CI's minimal install.
"""
from backend.marketplaces.base import PublishOutcome
from backend.marketplaces.state import (STICKY_STATUSES, derive_top_status,
                                        merge_state, owned_state_from)


def _ok(status="published", listing_id="123", url="https://x/123", message="ok"):
    return PublishOutcome(ok=True, listing_id=listing_id, url=url,
                          status=status, message=message)


def test_merge_success_writes_marketplace_entry():
    data = merge_state({}, "etsy", _ok(), now="2026-08-11T00:00:00+00:00")
    entry = data["marketplaces"]["etsy"]
    assert entry["listing_id"] == "123"
    assert entry["status"] == "published"
    assert entry["url"] == "https://x/123"
    assert entry["published_at"] == "2026-08-11T00:00:00+00:00"
    assert entry["error"] == ""


def test_merge_failure_records_error_but_keeps_state():
    data = {"marketplaces": {"etsy": {
        "listing_id": "123", "status": "published", "error": ""}}}
    merge_state(data, "etsy", PublishOutcome(ok=False, message="rate limited"))
    entry = data["marketplaces"]["etsy"]
    # A blocked revise must not un-publish a live listing.
    assert entry["status"] == "published"
    assert entry["listing_id"] == "123"
    assert entry["error"] == "rate limited"


def test_merge_success_clears_previous_error():
    data = {"marketplaces": {"etsy": {"status": "published", "error": "boom"}}}
    merge_state(data, "etsy", _ok())
    assert data["marketplaces"]["etsy"]["error"] == ""


def test_merge_dry_run_records_nothing():
    data = merge_state({}, "etsy", PublishOutcome(ok=True, dry_run=True))
    assert "marketplaces" not in data


def test_merge_keeps_published_at_on_republish():
    data = {"marketplaces": {"etsy": {
        "status": "published", "published_at": "2026-01-01T00:00:00+00:00"}}}
    merge_state(data, "etsy", _ok(), now="2026-08-11T00:00:00+00:00")
    assert data["marketplaces"]["etsy"]["published_at"] == "2026-01-01T00:00:00+00:00"


def test_merge_mirrors_ebay_id_to_legacy_field():
    data = merge_state({}, "ebay", _ok(listing_id="555"))
    assert data["ebay_listing_id"] == "555"


def test_merge_backfills_ebay_entry_from_legacy_field():
    data = {"ebay_listing_id": "777"}
    merge_state(data, "ebay", PublishOutcome(ok=False, message="nope"))
    assert data["marketplaces"]["ebay"]["listing_id"] == "777"


def test_top_status_any_live_success_publishes():
    outcomes = {"ebay": PublishOutcome(ok=False, message="x"),
                "etsy": _ok()}
    assert derive_top_status("draft", outcomes, "live") == "published"


def test_top_status_sticky_never_demoted():
    for sticky in STICKY_STATUSES:
        outcomes = {"ebay": PublishOutcome(ok=False, message="x")}
        assert derive_top_status(sticky, outcomes, "live") == sticky


def test_top_status_all_dry_run():
    outcomes = {"ebay": PublishOutcome(ok=True, dry_run=True),
                "etsy": PublishOutcome(ok=True, dry_run=True)}
    assert derive_top_status("", outcomes, "live") == "dry_run"


def test_top_status_draft_save_stays_draft():
    outcomes = {"ebay": PublishOutcome(ok=True, status="draft")}
    assert derive_top_status("draft", outcomes, "draft") == "draft"


# --- server-owned state (what a client save must not be able to erase) -------

def test_owned_state_replaces_client_map():
    """A stale client map (missing etsy) must not win over the stored one."""
    stored = {"marketplaces": {
        "ebay": {"listing_id": "111", "status": "published"},
        "etsy": {"listing_id": "222", "status": "published"}}}
    states, _ = owned_state_from(stored)
    assert set(states) == {"ebay", "etsy"}
    assert states["etsy"]["listing_id"] == "222"


def test_owned_state_is_a_copy_not_the_stored_dict():
    """Callers mutate the result; that must not write through to the record."""
    inner = {"listing_id": "222"}
    stored = {"marketplaces": {"etsy": inner}}
    states, _ = owned_state_from(stored)
    states["etsy"]["listing_id"] = "changed"
    assert inner["listing_id"] == "222"


def test_owned_state_fills_missing_ebay_id_from_record():
    stored = {"ebay_listing_id": "999"}
    _, ebay_id = owned_state_from(stored, "")
    assert ebay_id == "999"


def test_owned_state_keeps_the_client_ebay_id_when_present():
    """A relist mints a new id; the incoming one is authoritative."""
    stored = {"ebay_listing_id": "999"}
    _, ebay_id = owned_state_from(stored, "1000")
    assert ebay_id == "1000"


def test_owned_state_on_a_brand_new_listing():
    states, ebay_id = owned_state_from({}, "")
    assert states == {}
    assert ebay_id == ""
