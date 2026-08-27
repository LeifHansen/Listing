"""Three ways a client round-trip or a bad env value could hurt.

1. `_restore_server_state` protected `marketplaces` and `ebay_listing_id` from
   a stale client copy, but not `source`. `source="ebay"` is what routes a
   listing's next edit down the revise path, so a save that blanks it makes a
   live record look brand new — and the next publish creates a SECOND live
   listing instead of revising the one that exists.

2. `EBAY_POLICY_VERIFY_TTL` was parsed with a bare `float()` at import time.
   A value like "10m" raised ValueError while the module was loading, which
   stops the whole app from booting over one tuning knob.

3. The access-token cache enforced its cap with `.clear()`, throwing away
   every connected seller's token at once.
"""
from __future__ import annotations

import time

from backend import config
from backend.marketplaces import state as marketplace_state
from backend.models import Listing


# --- 1. source is server-owned ---------------------------------------------

def test_a_stale_save_cannot_blank_the_source_that_routes_a_revise():
    """The defect. Fails against the old code, where `source` was not in any
    protected list and the client's blank won."""
    incoming = Listing(title="Bowl", source="")
    changed = marketplace_state.restore_server_fields(
        incoming, {"source": "ebay", "view_url": "https://ebay.com/itm/1"})
    assert incoming.source == "ebay"
    assert set(changed) == {"source", "view_url"}


def test_a_first_publish_can_still_stamp_an_unstamped_record():
    """A blank stored value must leave the client's alone, or nothing could
    ever set these in the first place."""
    incoming = Listing(title="Bowl", source="", ebay_account="")
    assert marketplace_state.restore_server_fields(incoming, {}) == []
    assert incoming.source == ""


def test_the_stored_owner_survives_a_save_that_drops_it():
    """`ebay_account` is the ownership stamp the account-switch bookkeeping
    reads. A client that round-trips without it must not erase whose account
    the listing is on."""
    incoming = Listing(title="Bowl", ebay_account="")
    marketplace_state.restore_server_fields(incoming, {"ebay_account": "seller"})
    assert incoming.ebay_account == "seller"


def test_the_item_id_is_not_in_the_shared_list():
    """It has its own rule in both callers — the save path fills it only when
    the client didn't carry one. Pinning that it stays out of the list, so a
    later edit here can't silently change that."""
    assert "ebay_listing_id" not in marketplace_state.SERVER_OWNED_FIELDS


# --- 2. a bad tuning value costs its default, not the app ------------------

def test_an_unparseable_tuning_value_does_not_stop_the_app(monkeypatch):
    """Fails against the old code, which raised ValueError at import."""
    monkeypatch.setenv("EBAY_POLICY_VERIFY_TTL", "10m")
    assert config.env_float("EBAY_POLICY_VERIFY_TTL", 600.0) == 600.0


def test_a_real_tuning_value_is_still_honoured(monkeypatch):
    monkeypatch.setenv("EBAY_POLICY_VERIFY_TTL", "30")
    assert config.env_float("EBAY_POLICY_VERIFY_TTL", 600.0) == 30.0


def test_an_unset_value_takes_the_default(monkeypatch):
    monkeypatch.delenv("EBAY_POLICY_VERIFY_TTL", raising=False)
    assert config.env_float("EBAY_POLICY_VERIFY_TTL", 600.0) == 600.0


def test_a_negative_value_cannot_disable_re_verification(monkeypatch):
    """Clamped like _env_int does, so a negative TTL means "always re-verify",
    not "trust forever" via some later comparison."""
    monkeypatch.setenv("EBAY_POLICY_VERIFY_TTL", "-5")
    assert config.env_float("EBAY_POLICY_VERIFY_TTL", 600.0) == 0.0


# --- 3. the token cache evicts, it does not flush --------------------------

def test_filling_the_cache_does_not_evict_every_other_seller():
    """The defect: `.clear()` at the cap meant the 51st seller's refresh cost
    all fifty others a fresh eBay round-trip. Fails against the old code,
    which left the cache holding exactly one entry."""
    from backend.marketplaces import ebay_provider

    ebay_provider._TOKEN_CACHE.clear()
    live = time.time() + 3600
    for i in range(ebay_provider._TOKEN_CACHE_MAX):
        ebay_provider._TOKEN_CACHE[f"r-{i}"] = (live + i, f"tok-{i}")

    ebay_provider._make_room()

    # Room was made, and it cost one entry — not the whole cache.
    assert len(ebay_provider._TOKEN_CACHE) == ebay_provider._TOKEN_CACHE_MAX - 1
    assert "r-0" not in ebay_provider._TOKEN_CACHE   # soonest to expire went
    assert ebay_provider._TOKEN_CACHE["r-49"] == (live + 49, "tok-49")


def test_the_fifty_first_seller_does_not_evict_the_other_fifty(monkeypatch):
    """The defect, through the real entry point rather than the new helper.

    Old code: `if len(_TOKEN_CACHE) > 50: _TOKEN_CACHE.clear()` — so the
    refresh that tipped it over left the cache holding exactly ONE entry, and
    the other fifty sellers each paid a fresh eBay round-trip on their next
    request. Fails against the old code, which ends at 1.
    """
    from backend.marketplaces import ebay_provider

    ebay_provider._TOKEN_CACHE.clear()
    monkeypatch.setattr(
        ebay_provider.ebay_auth, "refresh_access_token",
        lambda refresh: {"access_token": f"tok-{refresh}",
                         "expires_at": time.time() + 7200})

    for i in range(ebay_provider._TOKEN_CACHE_MAX + 2):
        ebay_provider.access_token_for(f"r-{i}")

    assert len(ebay_provider._TOKEN_CACHE) == ebay_provider._TOKEN_CACHE_MAX
    # The most recent sellers are the ones still cached.
    assert f"r-{ebay_provider._TOKEN_CACHE_MAX + 1}" in ebay_provider._TOKEN_CACHE


def test_dead_entries_are_taken_before_any_live_one():
    """An expired token was going to be re-fetched anyway, so it is free to
    drop. Only when there are none does a live seller lose a cached token."""
    from backend.marketplaces import ebay_provider

    ebay_provider._TOKEN_CACHE.clear()
    live = time.time() + 3600
    ebay_provider._TOKEN_CACHE["dead"] = (time.time() - 1, "old")
    for i in range(ebay_provider._TOKEN_CACHE_MAX - 1):
        ebay_provider._TOKEN_CACHE[f"r-{i}"] = (live + i, f"tok-{i}")

    ebay_provider._make_room()

    assert "dead" not in ebay_provider._TOKEN_CACHE
    assert len(ebay_provider._TOKEN_CACHE) == ebay_provider._TOKEN_CACHE_MAX - 1
    assert "r-0" in ebay_provider._TOKEN_CACHE   # no live entry was touched
