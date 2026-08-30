"""eBay can move a listing's category on a revise, and nothing noticed.

`create_listing` already reads the remapped `CategoryID` out of eBay's
response and stores what eBay actually filed. `revise_listing` returned
`{"ok": True, "listing_id"}` and threw the rest away.

That matters more than it looks. eBay's documentation is explicit that
`ReviseItemResponse` and `ReviseFixedPriceItemResponse` return `CategoryID`
when the primary category was changed by the revision OR when eBay remapped
the one that was sent — and that remapping happens when `CategoryMappingAllowed`
is true **or is omitted**, which this app's revise does omit. So the revise
path is exactly where a silent remap can happen, and it was the one path not
looking.

The id in the record is what every later aspect lookup, condition list and
revise is built from. Holding a category the listing is no longer in sends
all of them somewhere else, and the seller sees required aspects that do not
apply and a publish that fails for reasons that make no sense on screen.

Sources, checked rather than assumed:
  https://developer.ebay.com/devzone/xml/Docs/Reference/eBay/types/ReviseItemResponseType.html
  https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/map-auto-remap.html
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading

_NS = "urn:ebay:apis:eBLBaseComponents"


def _response(category_id: str = "") -> bytes:
    cat = f"<CategoryID>{category_id}</CategoryID>" if category_id else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<ReviseFixedPriceItemResponse xmlns="{_NS}"><Ack>Success</Ack>'
        f"<ItemID>110001</ItemID>{cat}"
        "</ReviseFixedPriceItemResponse>").encode()


class _Resp:
    def __init__(self, content):
        self.status_code = 200
        self.content = content
        self.headers = {}


@pytest.fixture()
def ebay(monkeypatch):
    def _serve(category_id=""):
        monkeypatch.setattr(ebay_trading.httpx, "post",
                            lambda *a, **k: _Resp(_response(category_id)))
    return _serve


def _listing(**over) -> Listing:
    base = {"title": "Blue lamp", "price": 25.0, "quantity": 1,
            "category_id": "111", "ebay_listing_id": "110001"}
    base.update(over)
    return Listing(**base).mark_dirty("price")


def test_a_remapped_category_comes_back(ebay):
    """The finding: this response was read for the item id and nothing else."""
    ebay("20081")
    got = ebay_trading.revise_listing("tok", "110001", _listing())

    assert got["category_id"] == "20081"


def test_the_same_category_is_not_reported_as_a_change(ebay):
    """eBay echoes the category on some revises. Reporting an unchanged id as
    a remap would write the record on every edit and log a move that never
    happened."""
    ebay("111")
    assert "category_id" not in ebay_trading.revise_listing(
        "tok", "110001", _listing())


def test_no_category_in_the_response_changes_nothing(ebay):
    ebay("")
    assert "category_id" not in ebay_trading.revise_listing(
        "tok", "110001", _listing())


def test_the_revise_still_reports_what_it_always_did(ebay):
    ebay("20081")
    got = ebay_trading.revise_listing("tok", "110001", _listing())

    assert got["ok"] is True
    assert got["listing_id"] == "110001"


def test_the_seller_is_told_the_listing_moved():
    """A silent remap is how a seller ends up looking at required aspects for
    a category their listing is not in."""
    from backend.marketplaces import ebay_provider

    message = ebay_provider.revise_message({}, relist=False, remapped="20081")

    assert "categor" in message.lower()
    assert "Your eBay listing has been updated" in message


def test_no_remap_leaves_the_message_alone():
    from backend.marketplaces import ebay_provider

    assert ebay_provider.revise_message({}, relist=False, remapped="") == \
        "Your eBay listing has been updated."


def test_the_remapped_category_is_actually_written_to_the_record(monkeypatch):
    """The order matters and is easy to get wrong: set AFTER the record is
    written, the new id lives only in memory and the next load is back to the
    retired one — which is exactly the state this exists to prevent."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext
    from backend.services import ebay as ebay_service
    from backend.services import image_import, listing_sync

    listing = _listing(source="ebay")
    monkeypatch.setattr(listing_sync, "push_edit",
                        lambda *a, **k: {"ok": True, "listing_id": "110001",
                                         "category_id": "20081"})
    monkeypatch.setattr(listing_sync, "named_account_of", lambda _l: "")
    monkeypatch.setattr(ebay_service, "image_urls_for",
                        lambda *a, **k: ["https://x/1.jpg"])
    monkeypatch.setattr(image_import, "images_changed", lambda *a, **k: False)
    monkeypatch.setattr(ebay_provider, "preflight_issues", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.db, "upsert_listing",
                        lambda *a, **k: True)

    written: dict = {}
    monkeypatch.setattr(ebay_provider, "_record_published",
                        lambda sid, data, status, uid: written.update(data) or True)

    out = ebay_provider.EbayProvider()._publish_locked(
        PublishContext(session_id="s1", listing=listing, mode="live",
                       base_url="https://app.example", uid="u1",
                       prev_record={"status": "published"}),
        {"access_token": "tok", "_uid": "u1", "ebay_username": "seller"})

    assert out.ok is True, out.message
    assert written.get("category_id") == "20081", \
        "the remapped category never reached the stored record"
    assert "categor" in out.message.lower()
