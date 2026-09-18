"""A photo added, removed or reordered here reaches the Etsy listing.

Photos used to ship with a NEW Etsy listing only; a revise sent none, so a
seller who added a photo to a listing already on Etsy saw "Your Etsy listing
has been updated" and no new photo on Etsy. Etsy takes photo bytes, so
"same photos?" cannot be answered by comparing URLs: the provider keeps a
digest of the set it last uploaded on the listing's Etsy entry, and a
revise re-sends the set only when the digest has moved.

The order of operations is the one thing to get right: a live listing must
never sit with no photo (Etsy will not keep it active), so when old and new
fit under Etsy's ceiling together the new ones go up first.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import etsy_provider, mapping_etsy
from backend.marketplaces.base import PublishContext
from backend.models import Listing

SETTINGS = {"shop_id": "1", "shipping_profile_id": "7",
            "return_policy_id": "8", "readiness_state_id": "9"}
CREDS = {"access_token": "tok", "shop_id": "1", "settings": SETTINGS}
PHOTOS = [("a.jpg", b"AAA"), ("b.jpg", b"BBB")]
SIG = etsy_provider.EtsyProvider.photo_signature(PHOTOS)


def _ctx(prev_sig, photos=PHOTOS, monkeypatch=None) -> PublishContext:
    listing = Listing(title="Vintage mug", description="Nice.", price=12.0,
                      quantity=1, images=[name for name, _ in photos],
                      etsy={"taxonomy_id": 1, "who_made": "someone_else",
                            "when_made": "1990s"})
    entry = {"listing_id": "77", "status": "published"}
    if prev_sig is not None:
        entry["photo_sig"] = prev_sig
    return PublishContext(session_id="s1", listing=listing, mode="live",
                          base_url="https://app.test", uid="u1",
                          prev_record={"listing": {"marketplaces": {"etsy": entry}}})


class _Shop:
    """What the fake Etsy recorded (calls) and what it holds (on_etsy)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.on_etsy = [{"listing_image_id": "501", "rank": 1}]


@pytest.fixture
def etsy(monkeypatch):
    shop = _Shop()
    calls, on_etsy = shop.calls, shop.on_etsy
    monkeypatch.setattr(etsy_provider.EtsyProvider, "_image_batches",
                        lambda self, ctx: [(n, d) for n, d in PHOTOS
                                           if n in (ctx.listing.images or [])])
    monkeypatch.setattr(etsy_provider.etsy, "get_listing_inventory",
                        lambda tok, lid: {"products": [{"sku": "", "offerings": [{}]}]})
    monkeypatch.setattr(etsy_provider.etsy, "update_listing_inventory",
                        lambda tok, lid, body: {})
    monkeypatch.setattr(etsy_provider.etsy, "update_listing",
                        lambda tok, shop, lid, patch: {"listing_id": lid, "url": ""})
    monkeypatch.setattr(etsy_provider.etsy, "list_listing_images",
                        lambda tok, lid: list(on_etsy))
    monkeypatch.setattr(etsy_provider.etsy, "delete_listing_image",
                        lambda tok, shop, lid, image_id: calls.append(("delete", image_id)))
    monkeypatch.setattr(etsy_provider.etsy, "upload_listing_image",
                        lambda tok, shop, lid, data, name, rank: calls.append(("upload", name, rank)))
    return shop


def test_an_unchanged_photo_set_is_left_alone(etsy):
    outcome = etsy_provider.EtsyProvider().publish(_ctx(SIG), CREDS)
    assert outcome.ok, outcome.message
    assert etsy.calls == []
    assert outcome.state == {"photo_sig": SIG}


def test_a_changed_set_goes_up_before_the_old_one_comes_down(etsy):
    outcome = etsy_provider.EtsyProvider().publish(_ctx("something-older"), CREDS)
    assert outcome.ok, outcome.message
    calls = etsy.calls
    uploads = [c for c in calls if c[0] == "upload"]
    deletes = [c for c in calls if c[0] == "delete"]
    assert sorted(uploads) == [("upload", "a.jpg", 1), ("upload", "b.jpg", 2)]
    assert deletes == [("delete", "501")]
    assert calls.index(deletes[0]) > max(calls.index(u) for u in uploads)
    assert outcome.state == {"photo_sig": SIG}


def test_a_record_that_never_wrote_a_signature_re_sends_once(etsy):
    """Nobody wrote down what Etsy received, so the honest answer is to send
    it — after which the signature is on the record."""
    outcome = etsy_provider.EtsyProvider().publish(_ctx(None), CREDS)
    assert [c for c in etsy.calls if c[0] == "upload"]
    assert outcome.state["photo_sig"] == SIG


def test_past_the_ceiling_the_old_photos_come_down_first(etsy):
    etsy.on_etsy[:] = [{"listing_image_id": str(500 + i), "rank": i}
                       for i in range(1, mapping_etsy.MAX_PHOTOS + 1)]
    etsy_provider.EtsyProvider().publish(_ctx("older"), CREDS)
    first_upload = min(i for i, c in enumerate(etsy.calls) if c[0] == "upload")
    last_delete = max(i for i, c in enumerate(etsy.calls) if c[0] == "delete")
    assert last_delete < first_upload


def test_a_refused_photo_sync_keeps_the_listing_id(etsy, monkeypatch):
    def _refuse(*a, **k):
        raise ValueError("Etsy rejected the photo")
    monkeypatch.setattr(etsy_provider.etsy, "upload_listing_image", _refuse)
    outcome = etsy_provider.EtsyProvider().publish(_ctx("older"), CREDS)
    assert outcome.ok is False
    assert outcome.listing_id == "77"
    assert outcome.state == {}
