"""Inactive has to be what eBay says finished — no more, and no less.

The seller's report was three words about the archive tab: it is not
reflective of eBay. Two mechanisms were behind it, and both are about the app
believing something other than eBay's own answer.

1. THE FINISHED LISTS WERE READ ONE PAGE DEEP. eBay's sold and unsold lists
   are paged, and their PaginationResult says how many pages there are. The
   read asked for 100 ids and stopped on that number without a word — so a
   seller with 250 finished listings in eBay's window had 150 of them
   invisible to every pass that matters:

     * a sale past the cap is never mirrored, so it is missing from Inactive;
     * an ending past it never makes its record a candidate for the cheap
       finished-list reconcile, so the listing sits under Active until the
       random per-item sweep happens to draw it — and its mirror is never
       removed;
     * and when a sale IS eventually drawn, the transaction is not in the
       capped map either, so the archive records today's date and the asking
       price for a sale eBay reported weeks ago at another figure.

   eBay's own paging is the only ceiling that belongs here, exactly as it is
   for ACTIVE_LIMIT — which was raised off 300 for the same reason after a
   616-listing store only ever showed half its inventory.

2. THE IMPORT FILED EACH RECORD BY WHICH LIST ITS ID CAME FROM. GetMyeBaySelling's
   lists are a cached view of the store, and the import walks them ONCE and
   then spends minutes fetching one GetItem per listing — so "it was in the
   active list" is not the same claim as "eBay says it is live". Filing the
   record on the former sent listings that had already finished back to
   Active, including over records this app had correctly archived: the seller
   ended a listing, watched its card move to Inactive, pressed Sync with eBay,
   and watched it climb back out.

   The fix costs no extra call, because the answer was already in hand: the
   GetItem response that carries a listing's content carries its state too
   (ebay_trading.ITEM_STATE_KEY). The lists now decide only where to LOOK.

   An item eBay reports as ended is then SETTLED rather than written back as
   live — the same decision refresh_statuses makes on the same evidence,
   through the same function, so the import and the sweep cannot disagree
   about what an ending costs: a mirror is removed with its photos, and one of
   the seller's own is filed as ended and swept after the grace period.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, listing_sync

ITEM = "555000111222"
MIRROR = listing_sync.record_id(ITEM)


# --------------------------------------------------------------- the doubles

class Store:
    """The seller's rows, with the writes and deletes made visible."""

    def __init__(self, records=()):
        self.records = {r["id"]: r for r in records}
        self.written: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def enabled(self):
        return True

    def get_listing(self, listing_id):
        return self.records.get(listing_id)

    def list_listings(self, limit=50, user_id=None, statuses=None, before=None):
        return [r for r in self.records.values()
                if (user_id is None or r.get("user_id") == user_id)
                and (statuses is None or r.get("status") in statuses)][:limit]

    def delete_listing(self, listing_id, user_id=None):
        if listing_id not in self.records:
            return False
        del self.records[listing_id]
        self.deleted.append(listing_id)
        return True

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        rec = self.records.get(listing_id) or {"id": listing_id,
                                               "user_id": user_id}
        rec.update({"listing": dict(listing), "status": status})
        self.records[listing_id] = rec
        self.written.append((listing_id, status))
        return True


def _rec(rid, status="published", images=(), item_id=ITEM):
    return {"id": rid, "user_id": "u1", "status": status,
            "listing": {"title": "A tie-dye shirt", "price": 89.99,
                        "source": "ebay", "ebay_listing_id": item_id,
                        "images": list(images)}}


def _detail(state="", **extra):
    """What GetItem gives the import for one listing. `state` is eBay's own
    answer about the listing; "" is a response this app has no word for, and
    what every caller that predates it hands back."""
    out = {"title": "A tie-dye shirt", "price": 34.0, "source": "ebay",
           "ebay_listing_id": ITEM, "image_urls": ["https://i.ebayimg/1.jpg"],
           "quantity": 1, "sold_quantity": 0, ebay_trading.ITEM_STATE_KEY: state}
    out.update(extra)
    return out


@pytest.fixture()
def importing(monkeypatch):
    """Run import_active over one item, with eBay's lists and its answer for
    the item scripted independently — which is the whole point: they disagree.

    `fetched` is a list the detail calls are recorded in, for the tests about
    what reading eBay's whole sold window costs."""
    def _run(records=(), active=(ITEM,), sales=None, detail=None,
             fetched=None):
        store = Store(records)
        monkeypatch.setattr(listing_sync, "db", store)
        monkeypatch.setattr(listing_sync, "_purge_photos", lambda rid: None)
        monkeypatch.setattr(listing_sync.storage, "purge_session",
                            lambda rid: None)
        monkeypatch.setattr(listing_sync.notifications, "notify_sold",
                            lambda *a, **k: None)
        monkeypatch.setattr(listing_sync.inventory_mirror, "on_ebay_finished",
                            lambda *a, **k: None)
        monkeypatch.setattr(listing_sync.ebay_trading, "active_listing_ids",
                            lambda token, limit=None: list(active))
        monkeypatch.setattr(listing_sync, "recent_sales",
                            lambda token: dict(sales or {}))
        def _get(token, item_id):
            if fetched is not None:
                fetched.append(item_id)
            return dict(detail or _detail())

        monkeypatch.setattr(listing_sync.ebay_trading, "get_listing", _get)
        result = listing_sync.import_active(
            "tok", "u1", account={"ebay_user_id": "id1",
                                  "ebay_username": "seller"})
        return store, result
    return _run


# ------------------------------------- 1. eBay's finished lists, page by page

class _Resp:
    """As much of an httpx.Response as ebay_trading._call reads."""

    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


class _Pages:
    """GetMyeBaySelling, answering one scripted page per call and counting the
    calls — which is the measurement: a walk that stops early stops asking."""

    def __init__(self, pages: list[bytes]):
        self.pages = pages
        self.calls = 0

    def __call__(self, url, headers=None, content=None, timeout=None):
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return _Resp(page)


def _sold_page(item_ids, pages):
    txs = "".join(
        "<OrderTransaction><Transaction>"
        "<QuantityPurchased>1</QuantityPurchased>"
        '<TransactionPrice currencyID="USD">12.00</TransactionPrice>'
        "<CreatedDate>2026-08-12T18:04:11.000Z</CreatedDate>"
        f"<Item><ItemID>{i}</ItemID></Item>"
        "</Transaction></OrderTransaction>" for i in item_ids)
    return ('<?xml version="1.0"?>'
            '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<Ack>Success</Ack><SoldList>"
            f"<OrderTransactionArray>{txs}</OrderTransactionArray>"
            f"<PaginationResult><TotalNumberOfPages>{pages}</TotalNumberOfPages>"
            "</PaginationResult></SoldList></GetMyeBaySellingResponse>").encode()


def _unsold_page(item_ids, pages):
    items = "".join(f"<Item><ItemID>{i}</ItemID></Item>" for i in item_ids)
    return ('<?xml version="1.0"?>'
            '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<Ack>Success</Ack><UnsoldList>"
            f"<ItemArray>{items}</ItemArray>"
            f"<PaginationResult><TotalNumberOfPages>{pages}</TotalNumberOfPages>"
            "</PaginationResult></UnsoldList></GetMyeBaySellingResponse>").encode()


def _ids(prefix, first, last):
    return [f"{prefix}{n:011d}" for n in range(first, last)]


def test_every_sale_ebay_reports_is_read_not_just_the_first_page(monkeypatch):
    """250 sales across three pages, and eBay says there are three."""
    ids = _ids("1", 0, 250)
    pages = _Pages([_sold_page(ids[0:100], 3), _sold_page(ids[100:200], 3),
                    _sold_page(ids[200:250], 3)])
    monkeypatch.setattr(ebay_trading.httpx, "post", pages)

    sales = listing_sync.recent_sales("tok")

    assert len(sales) == 250, "a sale eBay reported is a sale to archive"
    assert ids[150] in sales
    assert pages.calls == 3


def test_every_ending_ebay_reports_is_read_not_just_the_first_page(monkeypatch):
    ids = _ids("2", 0, 250)
    pages = _Pages([_unsold_page(ids[0:100], 3), _unsold_page(ids[100:200], 3),
                    _unsold_page(ids[200:250], 3)])
    monkeypatch.setattr(ebay_trading.httpx, "post", pages)

    got = ebay_trading.unsold_listing_ids(
        "tok", limit=listing_sync._INACTIVE_LIMIT)

    assert len(got) == 250
    assert ids[150] in got
    assert pages.calls == 3


def test_a_small_store_still_costs_one_page_per_list(monkeypatch):
    """The cap is eBay's paging, not a number of calls to spend: a seller with
    forty sales pays for one page, exactly as they did at 100."""
    pages = _Pages([_sold_page(_ids("1", 0, 40), 1)])
    monkeypatch.setattr(ebay_trading.httpx, "post", pages)

    assert len(listing_sync.recent_sales("tok")) == 40
    assert pages.calls == 1


def test_a_sale_deep_in_the_window_reaches_the_archive_with_its_own_figures(
        monkeypatch):
    """The end of the story above: the 150th sale is in the map, so the record
    reconciles on this sync — with what the buyer paid and the day they paid
    it, not the asking price and today."""
    item = _ids("1", 150, 151)[0]
    store = Store([_rec(listing_sync.record_id(item), item_id=item)])
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync.storage, "purge_session", lambda rid: None)
    monkeypatch.setattr(listing_sync.notifications, "notify_sold",
                        lambda *a, **k: None)
    monkeypatch.setattr(listing_sync.inventory_mirror, "on_ebay_finished",
                        lambda *a, **k: None)
    monkeypatch.setattr(listing_sync, "recent_sales", lambda token: {
        item: {"price": 76.5, "currency": "USD", "quantity": 1,
               "sold_at": "2026-07-02T10:00:00.000Z"}})
    monkeypatch.setattr(listing_sync.ebay_trading, "unsold_listing_ids",
                        lambda token, limit=None: [])
    monkeypatch.setattr(listing_sync.ebay_trading, "listing_status",
                        lambda token, item_id: ("sold", 1, 4))

    changed, handled = listing_sync.reconcile_recent(
        "tok", "u1", store.list_listings(user_id="u1"), account="seller")

    rec = store.records[listing_sync.record_id(item)]
    assert (changed, rec["status"]) == (1, "sold")
    assert rec["listing"]["sold_price"] == 76.5
    assert rec["listing"]["sold_at"] == "2026-07-02T10:00:00.000Z"
    assert handled == {listing_sync.record_id(item)}


# ------------------------------- 2. eBay's answer beats eBay's cached list

def test_ebays_answer_for_the_item_is_what_gets_parsed():
    """The state travels on the same response as the content, so the import
    needs no second call to know it."""
    import xml.etree.ElementTree as ET

    def state_of(xml: str) -> str:
        root = ET.fromstring(
            '<Item xmlns="urn:ebay:apis:eBLBaseComponents">' + xml + "</Item>")
        return ebay_trading._item_to_listing(root)[ebay_trading.ITEM_STATE_KEY]

    assert state_of("<SellingStatus><ListingStatus>Active</ListingStatus>"
                    "<QuantitySold>0</QuantitySold></SellingStatus>") == "published"
    assert state_of("<SellingStatus><ListingStatus>Completed</ListingStatus>"
                    "<QuantitySold>1</QuantitySold></SellingStatus>") == "sold"
    assert state_of("<SellingStatus><ListingStatus>Ended</ListingStatus>"
                    "<QuantitySold>0</QuantitySold></SellingStatus>") == "ended"
    # A state this app has no word for says nothing, and nothing is what it
    # may change: the caller's own idea of the status stands.
    assert state_of("<SellingStatus><ListingStatus>Custom</ListingStatus>"
                    "</SellingStatus>") == ""
    assert state_of("<Title>No selling status at all</Title>") == ""


def test_an_ended_listing_does_not_climb_back_out_of_inactive(importing):
    """The seller's sequence: end a listing, watch the card move to Inactive,
    press Sync with eBay while eBay's active list still names it."""
    store, res = importing(records=[_rec("sess-a", status="ended",
                                         images=["img_000.jpg"])],
                           detail=_detail("ended"))

    assert store.records["sess-a"]["status"] == "ended"
    assert store.deleted == []
    assert res["imported"] == 0


def test_a_listing_that_finished_mid_import_is_settled_not_relisted(importing):
    """A store of a few hundred is minutes of GetItem calls after the list
    walk, so the list is stale by the time the detail arrives. A mirror with
    nothing of the seller's in it goes, with its photos."""
    store, res = importing(records=[_rec(MIRROR)], detail=_detail("ended"))

    assert MIRROR not in store.records
    assert store.deleted == [MIRROR]
    # Counted with the sweep's own removals: to the seller it is the same
    # event, a card gone because the listing behind it is over.
    assert res["removed"] == 1
    assert res["imported"] == 0


def test_an_ending_keeps_the_sellers_own_work_for_its_grace_period(importing):
    """The other half of the same rule. A listing this app created holds the
    seller's photos and the AI's copy, so it is filed rather than destroyed —
    and `ended_at` is stamped, because that is the clock the sweep measures."""
    store, res = importing(records=[_rec("sess-a", images=["img_000.jpg"])],
                           detail=_detail("ended"))

    rec = store.records["sess-a"]
    assert (rec["status"], store.deleted) == ("ended", [])
    assert rec["listing"]["ended_at"], "the grace period has to start somewhere"
    assert res["updated"] == 1


def test_an_item_that_ended_before_we_ever_saw_it_is_not_imported(importing):
    """An ending is never mirrored in — importing one so a later sweep can
    delete it is work in a circle."""
    store, res = importing(records=[], detail=_detail("ended"))

    assert store.written == []
    assert (res["imported"], res["updated"]) == (0, 0)


def test_a_sale_ebays_active_list_still_names_is_archived(importing):
    """The same staleness the other way up: eBay's list says live, eBay's
    answer for the item says it sold."""
    store, res = importing(records=[_rec(MIRROR)],
                           detail=_detail("sold", sold_quantity=1, quantity=0))

    assert store.records[MIRROR]["status"] == "sold"
    assert res["updated"] == 1


def test_a_sold_record_is_not_demoted_back_to_active(importing):
    """The archive is the app's own history and a sync may correct it, never
    unwind it: a record archived as sold whose id is still in eBay's active
    list used to be written back as published, offering to revise and
    repromote a listing that is gone."""
    store, res = importing(records=[_rec(MIRROR, status="sold")],
                           detail=_detail("sold", sold_quantity=1, quantity=0))

    assert store.records[MIRROR]["status"] == "sold"
    assert ("ebay-" + ITEM, "published") not in store.written


def test_a_multi_quantity_listing_that_sold_some_units_stays_live(importing):
    """It is in eBay's SOLD list AND still running, so the list alone cannot
    tell them apart — the item's own answer can."""
    store, _res = importing(
        records=[], active=(),
        sales={ITEM: {"price": 12.0, "currency": "USD", "quantity": 2,
                      "sold_at": "2026-09-01T00:00:00.000Z"}},
        detail=_detail("published", sold_quantity=2, quantity=3))

    assert store.written == [(MIRROR, "published")]


def test_a_list_this_app_has_no_answer_for_still_decides(importing):
    """eBay said nothing usable about the item (an older stand-in hands back
    no state at all), so the list its id came from is the best thing there is
    and the import goes on working exactly as it did."""
    store, _res = importing(records=[], detail=_detail(""))

    assert store.written == [(MIRROR, "published")]


# ------------------------------------ what reading the whole window costs

def test_a_sale_already_archived_is_not_fetched_all_over_again(importing):
    """The other side of reading eBay's whole sold window: the Trading API's
    daily allowance belongs to the app, not to one seller, so a backfill must
    be paid for once and not on every sync. A finished listing's content cannot
    change again, and nothing in GetItem's answer for it would be new."""
    fetched: list[str] = []
    store, res = importing(
        records=[_rec(MIRROR, status="sold")], active=(),
        sales={ITEM: {"price": 76.5, "currency": "USD", "quantity": 1,
                      "sold_at": "2026-07-02T10:00:00.000Z"}},
        fetched=fetched)

    assert fetched == [], "eBay was asked nothing about a sale we hold"
    # Covered, not skipped: the seller is told about every listing eBay named.
    assert res["found"] == 1
    assert store.records[MIRROR]["status"] == "sold"


def test_the_figures_of_an_archived_sale_still_catch_up(importing):
    """What CAN still change on a sold listing is what it made — another unit
    of a multi-quantity listing goes — and that arrives in the sold list this
    run has already read, so it costs no call to apply."""
    store, _res = importing(
        records=[_rec(MIRROR, status="sold")], active=(),
        sales={ITEM: {"price": 12.0, "currency": "USD", "quantity": 3,
                      "sold_at": "2026-09-02T00:00:00.000Z"}})

    sold = store.records[MIRROR]["listing"]
    assert (sold["sold_price"], sold["sold_quantity"]) == (12.0, 3)
    # Dated from eBay's transaction, never from the sync's own clock: this
    # record was already sold, so today is not when it happened.
    assert sold["sold_at"] == "2026-09-02T00:00:00.000Z"


def test_a_sale_we_have_never_seen_is_still_mirrored_in(importing):
    """The skip is about what we already hold. A sale the app has no record of
    is the archive card the seller is looking for, and it needs the fetch."""
    fetched: list[str] = []
    store, res = importing(
        records=[], active=(),
        sales={ITEM: {"price": 76.5, "currency": "USD", "quantity": 1,
                      "sold_at": "2026-07-02T10:00:00.000Z"}},
        detail=_detail("sold", sold_quantity=1, quantity=0), fetched=fetched)

    assert fetched == [ITEM]
    assert store.written == [(MIRROR, "sold")]
    assert res["imported"] == 1


def test_nothing_ebay_said_about_the_state_is_ever_stored(importing):
    """The state is a fact about the listing's life, not content — and the
    Listing model would drop it at validation anyway, which is the quiet way
    for a stored field to go missing. Popped where it is read instead."""
    store, _res = importing(records=[], detail=_detail("published"))

    assert ebay_trading.ITEM_STATE_KEY not in store.records[MIRROR]["listing"]
    shadow = store.records[MIRROR]["listing"].get("remote_shadow") or {}
    assert ebay_trading.ITEM_STATE_KEY not in shadow
