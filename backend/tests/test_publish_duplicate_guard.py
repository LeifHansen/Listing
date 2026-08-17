"""Publishing one listing must never produce two eBay listings.

The failure this covers: a publish takes tens of seconds (eBay ingests every
photo), the seller reloads or taps the button again, and a second request
arrives while the first is still working. Both used to read the pre-publish
record — no item id, status "draft" — and both called AddFixedPriceItem, so the
account ended up with two live listings for one item. The store sync then
mirrored the one the app didn't know about, which is what surfaced as pairs of
identical cards (one "Thryft", one eBay import).

Three defences are tested here, because each covers a case the others can't:

  * the item id is read from the STORED record, so a stale browser payload
    can't make an already-published listing look new;
  * publishes of one listing are serialized, so two racing requests can't both
    decide to create;
  * the create carries an idempotency key, so eBay refuses the second attempt
    even when the two never meet in one process — and the listing that already
    exists is adopted instead of duplicated.
"""
from __future__ import annotations

import threading

import pytest

from backend.services import ebay_trading, publish_guard

ITEM = "123456789012"


# ---------------------------------------------------------------- the key

def test_the_key_is_stable_for_one_listing():
    """A retried publish has to compute the same key, or eBay can't match it."""
    assert publish_guard.idempotency_key("abc123") \
        == publish_guard.idempotency_key("abc123")


def test_different_listings_get_different_keys():
    assert publish_guard.idempotency_key("abc123") \
        != publish_guard.idempotency_key("def456")


def test_a_relist_is_not_blocked_by_the_original_publishs_key():
    """Relisting is a genuinely new listing — the seller ended the old one and
    asked for a fresh item id. It must not collide with the first publish."""
    first = publish_guard.idempotency_key("abc123")
    relist = publish_guard.idempotency_key("abc123", replacing_item_id=ITEM)
    assert relist != first
    # ...but a RETRIED relist of the same ended item still matches itself.
    assert relist == publish_guard.idempotency_key("abc123",
                                                  replacing_item_id=ITEM)


def test_the_key_fits_ebays_field():
    """InventoryTrackingNumber tops out at 50 characters."""
    key = publish_guard.idempotency_key("a" * 40, replacing_item_id="9" * 20)
    assert 0 < len(key) <= 50


def test_no_session_means_no_key():
    assert publish_guard.idempotency_key("") == ""
    assert publish_guard.idempotency_key("   ") == ""


# ------------------------------------------------------- the stored record

def test_the_stored_item_id_beats_an_empty_payload():
    """The browser echoes back whatever it last saw; a payload assembled
    before the first publish carries no item id at all."""
    record = {"listing": {"title": "x", "ebay_listing_id": ITEM}}
    assert publish_guard.stored_item_id(record) == ITEM
    assert publish_guard.stored_item_id({"listing": {}}) == ""
    assert publish_guard.stored_item_id({}) == ""
    assert publish_guard.stored_item_id(None) == ""


# --------------------------------------------------------------- the lock

def test_one_listings_publishes_are_serialized():
    """Two threads publishing the SAME listing must not overlap: overlapping is
    precisely how both read 'not listed yet' and both create."""
    lock = publish_guard.session_lock("sess-1")
    overlapped = []
    inside = threading.Event()

    def hold():
        with publish_guard.session_lock("sess-1"):
            inside.set()
            # Long enough that a second thread WOULD get in if the lock didn't
            # hold it out.
            threading.Event().wait(0.15)

    def contend():
        inside.wait(1)
        overlapped.append(lock.acquire(blocking=False))

    t1, t2 = threading.Thread(target=hold), threading.Thread(target=contend)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert overlapped == [False], "the second publish got in while the first ran"


def test_different_listings_publish_concurrently():
    """Bulk publish depends on this — one lock for everything would serialize
    a 40-draft run into a single queue."""
    assert publish_guard.session_lock("sess-a") \
        is not publish_guard.session_lock("sess-b")
    with publish_guard.session_lock("sess-a"):
        assert publish_guard.session_lock("sess-b").acquire(blocking=False)
        publish_guard.session_lock("sess-b").release()


def test_the_same_listing_gets_the_same_lock():
    assert publish_guard.session_lock("sess-a") \
        is publish_guard.session_lock("sess-a")


# ------------------------------------------------- eBay's duplicate refusal

@pytest.mark.parametrize("message, code", [
    ("UUID has already been used.", ""),
    ("The UUID has already been used for another item.", "21916884"),
    ("Inventory tracking number already exists.", ""),
    ("The InventoryTrackingNumber is not unique.", ""),
    ("Duplicate UUID supplied.", ""),
    ("anything at all", "21916884"),
])
def test_ebays_ways_of_saying_already_listed_are_recognized(message, code):
    assert ebay_trading._is_duplicate_rejection(
        ebay_trading.TradingError(message, code=code))


@pytest.mark.parametrize("message", [
    "The title is too long.",
    "Item specifics are missing for this category.",
    "Your item's location was not filled in.",
    # Mentions a listing already existing, but nothing about our key — a real
    # failure the seller has to see, not something to swallow as 'already done'.
    "You already have a listing in this category.",
])
def test_ordinary_rejections_are_not_mistaken_for_duplicates(message):
    assert not ebay_trading._is_duplicate_rejection(
        ebay_trading.TradingError(message))


def test_the_item_id_is_read_out_of_ebays_message_when_it_names_one():
    assert ebay_trading._item_id_in_error(
        f"UUID already used for item {ITEM}.") == ITEM
    assert ebay_trading._item_id_in_error("UUID already used.") == ""


def test_the_key_is_converted_to_the_uuid_form_ebay_demands():
    """eBay's UUID element takes 32 hex characters and nothing else."""
    form = ebay_trading._uuid_form("qf-abc123")
    assert len(form) == 32 and all(c in "0123456789abcdef" for c in form)
    # Stable, or a retry wouldn't match.
    assert form == ebay_trading._uuid_form("qf-abc123")
    # An already-conforming key passes through (lowercased).
    raw = "A" * 32
    assert ebay_trading._uuid_form(raw) == "a" * 32


# ------------------------------------------- create_listing sends the key

class _FakeResponse:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


def _ok_response(item_id: str = ITEM) -> bytes:
    return (b'<?xml version="1.0"?>'
            b'<AddFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            b"<Ack>Success</Ack><ItemID>" + item_id.encode() + b"</ItemID>"
            b"</AddFixedPriceItemResponse>")


def _failure_response(message: str, code: str = "") -> bytes:
    return (b'<?xml version="1.0"?>'
            b'<AddFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            b"<Ack>Failure</Ack><Errors><SeverityCode>Error</SeverityCode>"
            b"<LongMessage>" + message.encode() + b"</LongMessage>"
            b"<ErrorCode>" + code.encode() + b"</ErrorCode>"
            b"</Errors></AddFixedPriceItemResponse>")


@pytest.fixture
def listing():
    from backend.models import Listing
    return Listing(title="Framed piano painting", price=45.0, quantity=1,
                   category_id="360", condition="USED_EXCELLENT",
                   package_weight_lb=2, package_weight_oz=0)


class _Sent(list):
    """The XML bodies create_listing posted, plus a queue of replies to hand
    back (defaulting to a successful AddFixedPriceItem)."""

    def __init__(self):
        super().__init__()
        self.queue: list[bytes] = []


@pytest.fixture
def sent(monkeypatch):
    bodies = _Sent()

    def fake_post(url, headers=None, content=None, timeout=None):
        bodies.append(content.decode("utf-8"))
        return _FakeResponse(bodies.queue.pop(0) if bodies.queue
                             else _ok_response())

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return bodies


def test_the_create_carries_both_forms_of_the_key(sent, listing):
    ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                postal_code="97201",
                                idempotency_key="qf-abc123")
    body = sent[0]
    assert "<InventoryTrackingNumber>qf-abc123</InventoryTrackingNumber>" in body
    assert f"<UUID>{ebay_trading._uuid_form('qf-abc123')}</UUID>" in body


def test_an_auction_carries_only_the_uuid(sent, listing):
    """InventoryTrackingNumber is a fixed-price-only field; sending it on an
    auction makes eBay reject the whole call."""
    listing.listing_format = "AUCTION"
    listing.auction_start_price = 9.99
    ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                postal_code="97201",
                                idempotency_key="qf-abc123")
    assert "<InventoryTrackingNumber>" not in sent[0]
    assert "<UUID>" in sent[0]


def test_publishing_without_a_key_is_unchanged(sent, listing):
    ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                postal_code="97201")
    assert "<UUID>" not in sent[0]
    assert "<InventoryTrackingNumber>" not in sent[0]


def test_a_duplicate_refusal_becomes_already_listed(sent, listing):
    sent.queue.append(_failure_response("UUID has already been used.",
                                        "21916884"))
    with pytest.raises(ebay_trading.AlreadyListedError):
        ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                    postal_code="97201",
                                    idempotency_key="qf-abc123")


def test_a_real_rejection_still_raises_a_plain_error(sent, listing):
    sent.queue.append(_failure_response("The title is too long.", "21919188x"))
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                    postal_code="97201",
                                    idempotency_key="qf-abc123")
    assert not isinstance(caught.value, ebay_trading.AlreadyListedError)
    assert "title is too long" in str(caught.value)


def test_a_duplicate_refusal_without_a_key_is_left_alone(sent, listing):
    """No key means eBay can't be talking about our idempotency key, so the
    message is the seller's to see."""
    sent.queue.append(_failure_response("UUID has already been used."))
    with pytest.raises(ebay_trading.TradingError) as caught:
        ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                    postal_code="97201")
    assert not isinstance(caught.value, ebay_trading.AlreadyListedError)


# ------------------------------------- adopting the listing that already exists

class _RefusingTrading:
    """eBay having already processed this publish. `by_tracking` is what a
    GetItem-by-InventoryTrackingNumber lookup finds."""

    AlreadyListedError = ebay_trading.AlreadyListedError
    TradingError = ebay_trading.TradingError

    def __init__(self, by_tracking: str = "", named_item: str = ""):
        self.by_tracking = by_tracking
        self.named_item = named_item
        self.creates = 0

    def create_listing(self, *a, **kw):
        self.creates += 1
        raise ebay_trading.AlreadyListedError(
            "This listing was already published to eBay.",
            item_id=self.named_item)

    def item_id_for_tracking_number(self, token, key):
        return self.by_tracking


@pytest.fixture
def creds():
    return {"access_token": "tok", "ship_from_postal": "97201"}


def test_a_second_publish_adopts_the_listing_ebay_already_made(
        monkeypatch, listing, creds):
    """The whole point: the seller ends up with ONE live listing, and the app
    records its real item id."""
    from backend.services import listing_sync

    fake = _RefusingTrading(by_tracking=ITEM)
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    res = listing_sync.create_on_ebay("tok", listing, ["https://x/1.jpg"],
                                      creds=creds, idempotency_key="qf-abc123")
    assert res["listing_id"] == ITEM
    assert res["already_listed"] is True
    assert listing.ebay_listing_id == ITEM, "the record must carry the real id"
    assert listing.source == "ebay", "later edits have to route through Trading"
    assert fake.creates == 1, "it must not retry the create"


def test_the_item_id_ebay_names_is_used_without_a_lookup(
        monkeypatch, listing, creds):
    from backend.services import listing_sync

    fake = _RefusingTrading(named_item=ITEM)  # no tracking-number lookup result
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    res = listing_sync.create_on_ebay("tok", listing, ["https://x/1.jpg"],
                                      creds=creds, idempotency_key="qf-abc123")
    assert res["listing_id"] == ITEM


def test_an_unresolvable_duplicate_fails_loudly_instead_of_listing_again(
        monkeypatch, listing, creds):
    """eBay says 'already sent' but won't say what it became. Creating anyway
    is the one thing that must not happen — a duplicate is worse than an error
    the seller can act on."""
    from backend.services import listing_sync

    fake = _RefusingTrading()  # nothing found either way
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    with pytest.raises(ebay_trading.TradingError) as caught:
        listing_sync.create_on_ebay("tok", listing, ["https://x/1.jpg"],
                                    creds=creds, idempotency_key="qf-abc123")
    assert "duplicate" in str(caught.value).lower()
    assert fake.creates == 1


# ------------------------------------ the payload never decides "not listed yet"

def _ctx(session_id, listing, mode="live"):
    from backend.marketplaces.base import PublishContext
    return PublishContext(session_id=session_id, listing=listing, mode=mode,
                          base_url="https://app.test", uid="u1", prev_record={})


def test_a_stale_payload_cannot_make_a_live_listing_look_new(monkeypatch, listing):
    """The browser's copy predates the first publish, so it carries no item id
    and no source. The stored record's must win — believing the payload is what
    sent a second AddFixedPriceItem."""
    from backend.marketplaces import ebay_provider

    stored = {"id": "sess-abc", "status": "published", "user_id": "u1",
              "listing": {"title": "Framed piano painting", "source": "ebay",
                          "ebay_listing_id": ITEM,
                          "view_url": f"https://www.ebay.com/itm/{ITEM}"}}
    monkeypatch.setattr(ebay_provider.db, "get_listing", lambda _id: stored)

    assert listing.ebay_listing_id in ("", None)
    ctx = ebay_provider._with_stored_identity(_ctx("sess-abc", listing))
    assert ctx.listing.ebay_listing_id == ITEM
    assert ctx.listing.source == "ebay"
    assert ctx.prev_record["status"] == "published", "status is re-read too"


def test_the_payload_still_drives_a_genuinely_new_listing(monkeypatch, listing):
    from backend.marketplaces import ebay_provider

    monkeypatch.setattr(ebay_provider.db, "get_listing", lambda _id: None)
    ctx = ebay_provider._with_stored_identity(_ctx("sess-new", listing))
    assert not ctx.listing.ebay_listing_id
    assert ctx.prev_record == {}


def test_the_sellers_own_edits_survive_the_identity_takeover(monkeypatch, listing):
    """Only the eBay identity fields come from the record — the point of the
    request is to publish the seller's NEW title and price."""
    from backend.marketplaces import ebay_provider

    stored = {"id": "sess-abc", "status": "published", "user_id": "u1",
              "listing": {"title": "The old title", "price": 10.0,
                          "source": "ebay", "ebay_listing_id": ITEM}}
    monkeypatch.setattr(ebay_provider.db, "get_listing", lambda _id: stored)

    listing.title = "A better title the seller just wrote"
    listing.price = 45.0
    ctx = ebay_provider._with_stored_identity(_ctx("sess-abc", listing))
    assert ctx.listing.title == "A better title the seller just wrote"
    assert ctx.listing.price == 45.0
    assert ctx.listing.ebay_listing_id == ITEM
