"""Ended listings must always land in Inactive (or Sold) — never stay Active.

Two paths guard that:

- end(): EndItem refuses a listing that already finished on eBay. That's the
  exact state a seller is in when a listing ends (or sells) on eBay first and
  they click End here before a sync catches up — failing then would strand
  the record under Active with no way to move it. A definitive "it already
  finished" from eBay must come back as not_live instead of raising.

- reconcile_recent(): eBay's sold/unsold lists name every recently finished
  item, so a listing that ended ON eBay flips off Active on the next sync at
  any store size — without waiting its turn under the per-item probe caps.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import listing_sync
from backend.services.ebay_trading import TradingError

ITEM = "123456789012"


class FakeTradingEnd:
    """eBay's side of EndItem + the status probe, scriptable per test."""

    def __init__(self, end_error=None, probe=(None, 0, 0)):
        self.end_error = end_error
        self.probe = probe
        self.probed: list[str] = []

    def end_listing(self, token, item_id, reason="NotAvailable"):
        if not item_id:
            raise TradingError("This listing has no eBay item id to end.")
        if self.end_error:
            raise self.end_error
        return {"ended": True, "listing_id": item_id}

    def listing_status(self, token, item_id):
        self.probed.append(item_id)
        return self.probe


def _listing(item_id=ITEM):
    return Listing(ebay_listing_id=item_id, source="ebay")


def test_end_passes_through_when_ebay_accepts(monkeypatch):
    monkeypatch.setattr(listing_sync, "ebay_trading", FakeTradingEnd())
    assert listing_sync.end("token", _listing()) == {
        "ended": True, "listing_id": ITEM}


def test_end_of_an_already_ended_listing_reports_not_live(monkeypatch):
    fake = FakeTradingEnd(end_error=TradingError("The auction has already been closed."),
                          probe=("ended", 0, 3))
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    res = listing_sync.end("token", _listing())
    assert res["not_live"] is True and not res["ended"]
    assert res["status"] == "ended"
    assert fake.probed == [ITEM]


def test_end_of_a_listing_that_actually_sold_says_so(monkeypatch):
    fake = FakeTradingEnd(end_error=TradingError("The auction has already been closed."),
                          probe=("sold", 1, 0))
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    res = listing_sync.end("token", _listing())
    assert res["not_live"] is True
    assert res["status"] == "sold"


def test_end_still_fails_when_ebay_wont_say_what_happened(monkeypatch):
    """An indefinite probe (API blip) must NOT quietly mark a possibly-live
    listing ended — the original refusal is the honest answer."""
    fake = FakeTradingEnd(end_error=TradingError("Something else went wrong."),
                          probe=(None, 0, 0))
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    with pytest.raises(TradingError, match="Something else went wrong"):
        listing_sync.end("token", _listing())


def test_end_still_fails_when_a_still_live_listing_cannot_be_ended(monkeypatch):
    """eBay can refuse EndItem for a listing that IS live (e.g. an auction
    with bids) — that record must stay Active."""
    fake = FakeTradingEnd(end_error=TradingError("Auction has bids."),
                          probe=("published", 0, 5))
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    with pytest.raises(TradingError, match="Auction has bids"):
        listing_sync.end("token", _listing())


def test_end_without_an_item_id_fails_without_probing(monkeypatch):
    fake = FakeTradingEnd()
    monkeypatch.setattr(listing_sync, "ebay_trading", fake)
    with pytest.raises(TradingError):
        listing_sync.end("token", _listing(item_id=""))
    assert fake.probed == []


# ------------------------------------------------------- reconcile_recent

class FakeTradingLists:
    """GetMyeBaySelling's finished-item lists + the per-item probe.

    `sold` may be plain item ids or {item_id: sale} — the sold list is read
    as transactions now, because the amount an item actually went for (an
    accepted offer settles below the asking price) only exists there.
    """

    def __init__(self, sold=(), unsold=(), statuses=None):
        self.sold = ({k: dict(v) for k, v in sold.items()}
                     if isinstance(sold, dict)
                     else {i: {"price": None, "currency": "USD",
                               "quantity": 1, "sold_at": ""} for i in sold})
        self.unsold = list(unsold)
        self.statuses = statuses or {}
        self.probed: list[str] = []

    def sold_sales(self, token, limit=None):
        return {k: dict(v) for k, v in self.sold.items()}

    def unsold_listing_ids(self, token, limit=None):
        return self.unsold

    def listing_status(self, token, item_id):
        self.probed.append(item_id)
        return self.statuses.get(item_id, (None, 0, 0))


class FakeDb:
    def __init__(self):
        self.upserts: list[tuple[str, str]] = []  # (record id, status)
        self.saved: dict[str, dict] = {}          # record id -> listing written

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        self.upserts.append((listing_id, status))
        self.saved[listing_id] = dict(listing)


class FakeNotifications:
    def __init__(self):
        self.sold: list[str] = []

    def notify_sold(self, user_id, record_id, listing, sold_quantity=0):
        self.sold.append(record_id)


class FakeStorage:
    def __init__(self):
        self.purged: list[str] = []

    def purge_session(self, session_id):
        self.purged.append(session_id)


def _rec(rid, item_id, status="published"):
    return {"id": rid, "user_id": "u1", "status": status,
            "listing": {"ebay_listing_id": item_id, "title": rid}}


@pytest.fixture
def reconciled(monkeypatch):
    def run(records, trading):
        fake_db, notes, store = FakeDb(), FakeNotifications(), FakeStorage()
        monkeypatch.setattr(listing_sync, "db", fake_db)
        monkeypatch.setattr(listing_sync, "notifications", notes)
        monkeypatch.setattr(listing_sync, "storage", store)
        monkeypatch.setattr(listing_sync, "ebay_trading", trading)
        changed, handled = listing_sync.reconcile_recent("token", "u1", records)
        return changed, handled, fake_db, notes, store
    return run


def test_a_listing_that_ended_on_ebay_flips_to_inactive(reconciled):
    trading = FakeTradingLists(unsold=[ITEM],
                               statuses={ITEM: ("ended", 0, 2)})
    changed, handled, fake_db, notes, store = reconciled(
        [_rec("sess-a", ITEM), _rec("sess-b", "999888777666")], trading)
    assert changed == 1
    assert handled == {"sess-a"}
    assert ("sess-a", "ended") in fake_db.upserts
    # The record eBay never mentioned wasn't probed or touched.
    assert trading.probed == [ITEM]
    assert notes.sold == [] and store.purged == []


def test_a_listing_that_sold_on_ebay_is_archived_with_a_notification(reconciled):
    trading = FakeTradingLists(sold=[ITEM],
                               statuses={ITEM: ("sold", 1, 0)})
    changed, handled, fake_db, notes, store = reconciled(
        [_rec("sess-a", ITEM)], trading)
    assert changed == 1
    assert ("sess-a", "sold") in fake_db.upserts
    assert notes.sold == ["sess-a"]
    assert store.purged == ["sess-a"]


def test_the_recorded_sale_price_is_what_the_buyer_paid(reconciled):
    """An accepted offer settles below the asking price, and eBay leaves the
    listing's own price alone — so a sold record built from the listing alone
    reported the asking price. The transaction amount is what lands now."""
    trading = FakeTradingLists(
        sold={ITEM: {"price": 76.5, "currency": "USD", "quantity": 1,
                     "sold_at": "2026-08-12T18:04:11.000Z"}},
        statuses={ITEM: ("sold", 1, 0)})
    rec = _rec("sess-a", ITEM)
    rec["listing"]["price"] = 89.99
    _, _, fake_db, _, _ = reconciled([rec], trading)
    saved = fake_db.saved["sess-a"]
    assert saved["sold_price"] == 76.5
    assert saved["sold_at"] == "2026-08-12T18:04:11.000Z"
    assert saved["price"] == 89.99, "the asking price stays what was asked"


def test_a_sale_ebay_reports_no_amount_for_still_gets_a_date(reconciled):
    """No transaction amount (a sale outside eBay's ~90-day window, or a list
    that wouldn't load) leaves sold_price unset — the UI falls back to the
    asking price and says it's approximate — but the record still gets a sale
    date, so it counts toward the sold-in-the-last-N-days tile."""
    trading = FakeTradingLists(sold=[ITEM], statuses={ITEM: ("sold", 1, 0)})
    _, _, fake_db, _, _ = reconciled([_rec("sess-a", ITEM)], trading)
    saved = fake_db.saved["sess-a"]
    assert saved.get("sold_price") is None
    assert saved["sold_at"], "a record that just flipped to sold is dated now"


def test_a_second_sale_never_inherits_the_first_sales_numbers(reconciled):
    """A listing that sold, was relisted, and sold again. eBay hasn't reported
    the new transaction yet — and the old sale's price and date must not stand
    in for it, or the new sale is filed under the old date and falls outside
    the dashboard's window entirely."""
    trading = FakeTradingLists(sold=[ITEM], statuses={ITEM: ("sold", 1, 0)})
    rec = _rec("sess-a", ITEM)  # relisted: live again, carrying the old sale
    rec["listing"].update({"price": 89.99, "sold_price": 76.5,
                           "sold_at": "2026-05-01T10:00:00.000Z"})
    _, _, fake_db, _, _ = reconciled([rec], trading)
    saved = fake_db.saved["sess-a"]
    assert saved.get("sold_price") is None
    assert saved["sold_at"] != "2026-05-01T10:00:00.000Z"


def test_a_seller_entered_sale_price_survives_a_re_sync(reconciled):
    """eBay never reported this sale, so the seller typed the amount in. A
    later sweep of an already-sold record must not wipe it."""
    trading = FakeTradingLists(sold=[ITEM], statuses={ITEM: ("sold", 1, 0)})
    rec = _rec("sess-a", ITEM, status="sold")
    rec["listing"].update({"price": 89.99, "sold_price": 70.0,
                           "sold_at": "2026-08-01T10:00:00.000Z"})
    _, _, fake_db, _, _ = reconciled([rec], trading)
    saved = fake_db.saved.get("sess-a", rec["listing"])
    assert saved["sold_price"] == 70.0
    assert saved["sold_at"] == "2026-08-01T10:00:00.000Z"


def test_a_multi_quantity_listing_still_live_is_not_pulled_off_active(reconciled):
    """A partially-sold multi-quantity listing shows up in the sold list while
    it is STILL live — the per-item probe is what keeps it under Active."""
    trading = FakeTradingLists(sold=[ITEM],
                               statuses={ITEM: ("published", 2, 4)})
    changed, handled, fake_db, notes, store = reconciled(
        [_rec("sess-a", ITEM)], trading)
    assert handled == {"sess-a"}  # covered — the capped sweeps can skip it
    assert all(status != "ended" and status != "sold"
               for _, status in fake_db.upserts)


def test_unreachable_lists_change_nothing(reconciled):
    class Down(FakeTradingLists):
        def sold_sales(self, token, limit=None):
            raise RuntimeError("eBay is down")

        def unsold_listing_ids(self, token, limit=None):
            raise RuntimeError("eBay is down")

    changed, handled, fake_db, notes, store = reconciled(
        [_rec("sess-a", ITEM)], Down())
    assert (changed, handled) == (0, set())
    assert fake_db.upserts == []
