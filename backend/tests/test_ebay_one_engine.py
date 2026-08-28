"""One engine: everything a publish does now goes through the Trading API.

The Inventory publish engine (createOrReplaceInventoryItem / createOffer /
publishOffer, plus withdrawOffer and the SKU-keyed status lookup) is deleted.
It was never the path a real publish took — `create_on_ebay` stamps
source="ebay", so from its first publish onward every listing took the Trading
route — but it stayed reachable for drafts, dry runs, and any record that
predated the switch, and it was the only thing that could create an
inventory-based listing eBay then refuses to let the seller edit.

What has to remain true after the deletion:

  - a dry run describes the request a real publish would make;
  - a record that only the deleted engine could have created gets a sentence a
    seller can act on, not eBay's wording for a call we shouldn't send;
  - nothing else lost a route out.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import ebay_provider
from backend.marketplaces.base import PublishContext
from backend.models import Listing
from backend.services import ebay


def _listing(**over) -> Listing:
    fields = {
        "title": "Vintage Pyrex mixing bowl",
        "condition": "USED_EXCELLENT",
        "category_id": "12345",
        "price": 24.99,
        "quantity": 1,
        "description": "A bowl.",
    }
    fields.update(over)
    return Listing(**fields)


def _publish(listing, creds, prev_record=None, mode="live"):
    ctx = PublishContext(
        session_id="s1", listing=listing, mode=mode,
        base_url="https://example.test", uid="u1",
        prev_record=prev_record or {})
    return ebay_provider.EbayProvider()._publish_locked(ctx, creds)


@pytest.fixture(autouse=True)
def no_photos_on_disk(monkeypatch):
    """image_urls_for reads the session's optimized dir; these listings have
    no files, and the dry run only needs to render what it was given."""
    monkeypatch.setattr(ebay, "image_urls_for", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda uid: None)


# --- the dry run describes the call we would really make --------------------

def test_a_dry_run_renders_the_trading_request(tmp_path, monkeypatch):
    """It used to render an Inventory item + offer — a payload describing a
    call this app no longer makes, which a seller could read and still be
    surprised by the real publish."""
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}_{name}.json")
    out = _publish(_listing(), None)
    assert out.dry_run and out.ok
    payload = out.raw["payload"]
    assert payload["call"] == "AddFixedPriceItem"
    assert payload["xml"].startswith("<Item>")
    assert "<Title>Vintage Pyrex mixing bowl</Title>" in payload["xml"]
    # The shapes the old renderer produced are gone.
    assert "inventory_item" not in out.raw and "offer" not in out.raw


def test_an_auction_dry_run_names_the_call_an_auction_would_use(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}_{name}.json")
    out = _publish(_listing(listing_format="AUCTION"), None)
    assert out.raw["payload"]["call"] == "AddItem"


def test_the_dry_run_payload_is_what_the_builder_would_send(tmp_path, monkeypatch):
    """Pins the two together: the preview calls the same build_add_item
    create_listing does, so they cannot drift into describing different
    requests."""
    from backend.services import ebay_trading

    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}_{name}.json")
    listing = _listing()
    out = _publish(listing, None)
    _call, expected = ebay_trading.build_add_item(
        listing, [],
        policies={"fulfillment_policy_id": "", "payment_policy_id": "",
                  "return_policy_id": ""})
    assert out.raw["payload"]["xml"] == expected


# --- a record only the deleted engine could have made -----------------------

@pytest.fixture
def passes_preflight(monkeypatch):
    """The checklist runs before any of this and would stop these listings on
    their own merits. These tests are about the route taken after it passes."""
    monkeypatch.setattr(ebay_provider, "preflight_issues",
                        lambda uid, listing, mode: [])


def test_a_legacy_inventory_listing_gets_a_sentence_not_an_ebay_error(
        passes_preflight):
    """Not stamped source="ebay" but carrying an item id: only the Inventory
    engine produced those. eBay refuses to let Trading revise one and offers
    no conversion, so the app says what actually works instead of sending a
    call that comes back in eBay's words."""
    out = _publish(_listing(ebay_listing_id="110000000001"),
                   {"access_token": "tok", "ebay_username": "seller"},
                   prev_record={"status": "published"})
    assert not out.ok
    assert "Relist" in out.message
    assert out.issues and out.issues[0]["target"] == "account"


def test_a_blocked_legacy_publish_does_not_demote_the_record(passes_preflight):
    """It is still live on eBay whatever we can or can't do with it here."""
    writes: list[str] = []
    listing = _listing(ebay_listing_id="110000000001")
    import backend.marketplaces.ebay_provider as mod
    original = mod.db.upsert_listing
    try:
        mod.db.upsert_listing = (
            lambda sid, dump, status="", user_id=None, **k:
                writes.append(status) or True)
        _publish(listing, {"access_token": "tok", "ebay_username": "seller"},
                 prev_record={"status": "published"})
    finally:
        mod.db.upsert_listing = original
    assert writes == ["published"]


# --- the engine is actually gone -------------------------------------------

@pytest.mark.parametrize("name", [
    "publish", "withdraw", "live_status", "build_inventory_item",
    "build_offer", "_push_live",
])
def test_the_inventory_engine_is_gone(name):
    assert not hasattr(ebay, name), f"services.ebay still exposes {name}"


def test_what_survived_is_still_there():
    """image_urls_for feeds Depop too, and rest_headers feeds Promoted
    Listings — neither belonged to the Inventory engine."""
    for name in ("image_urls_for", "sku_for", "rest_headers"):
        assert hasattr(ebay, name), f"services.ebay lost {name}"


# --- server-side credentials no longer publish ------------------------------
#
# The Inventory engine was the only thing that could publish with the
# env-configured credentials (config.ebay_ready()). Deleting it removed that
# capability, but three places still treated env config as "connected" — so an
# env-only deployment showed "Publish Live", ran the blocking checklist, and
# then silently produced a dry run. Either behaviour is defensible; claiming
# one and doing the other is not. These pin the decision: env credentials are
# for the OAuth app and the dry run, never for creating a listing.

@pytest.fixture
def env_configured(monkeypatch):
    """A deployment with server-side eBay credentials and nobody connected."""
    monkeypatch.setattr(ebay_provider.config, "ebay_ready", lambda: True)
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda uid: None)


def test_env_credentials_produce_a_dry_run_not_a_claim_of_publishing(
        env_configured, tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / "x.json")
    out = _publish(_listing(), None)
    assert out.dry_run is True and out.ok is True
    # And it does not tell the operator that adding credentials would help —
    # they already have them.
    assert "add credentials" not in out.message


def test_the_checklist_does_not_block_the_dry_run(env_configured, tmp_path, monkeypatch):
    """The gate used to fire on config.ebay_ready(), so an env-only deployment
    was blocked by a full live checklist before reaching the dry run — the one
    thing it can actually do. A listing missing a price must still render its
    payload."""
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / "x.json")
    out = _publish(_listing(price=None, category_id=""), None)
    assert out.dry_run is True and not out.issues


def test_the_checklist_still_judges_a_connected_seller(monkeypatch):
    """The other half: removing config.ebay_ready() from the gate must not
    stop it running for someone who really is connected."""
    asked = []
    monkeypatch.setattr(ebay_provider, "preflight_issues",
                        lambda uid, listing, mode: asked.append(mode) or [])
    monkeypatch.setattr(ebay_provider.listing_sync, "create_on_ebay",
                        lambda *a, **k: {"listing_id": "110000000001"})
    monkeypatch.setattr(ebay_provider, "_record_published", lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider.storage, "save_listing", lambda *a, **k: None)
    _publish(_listing(), {"access_token": "tok", "ebay_username": "seller",
                          "_uid": "u1"})
    assert asked == ["live"]


def test_an_env_only_deployment_is_told_it_will_dry_run(env_configured):
    """preflight computed `connected` from env config, which suppressed the
    one warning that is now simply true: "Publishing will run as a dry-run
    payload until you connect eBay." Server credentials satisfied the check
    while being unable to publish, so the seller was told the opposite of
    what would happen."""
    issues = ebay_provider.preflight_issues("u1", _listing(), "live")
    warning = next((i for i in issues if i.get("target") == "generic"), None)
    assert warning is not None, "an unconnected deployment must be told it will dry-run"
    assert warning.get("level") == "warn", "it is a warning, not a blocker"
    assert "dry-run" in warning.get("fix", "")
