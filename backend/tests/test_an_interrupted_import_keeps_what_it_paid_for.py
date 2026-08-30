"""What a store import has already fetched is written down as it goes.

The import ran in two passes: every `GetItem` first, then every save. A real
store is minutes of the first one, so a machine that went away at 95% -- an
OOM, a deploy, a Fly restart -- left the seller with **nothing**. Four hundred
eBay calls spent, four hundred listings' worth of answers in a dead process's
memory, and a job mirror saying the sync was interrupted.

The two passes were never independent. The save loop reads only state built
before the fetching started (`owned`, `by_key`, `known`, `sales`) plus
`claimed`, which it builds itself in eBay's order — so consuming the futures
IN ORDER and saving each as it lands is the same sequence of writes, arriving
earlier. What it buys is that an interruption keeps everything up to the point
it happened, and the seller's next run has that much less to re-fetch.

The progress phases collapse to one, honestly: each listing is now fetched and
saved as a single unit, so "fetching 400 of 400" followed by a separate save
pass no longer describes anything that happens. One `syncing` count does, and
it is the count that survives a restart in the job mirror.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, listing_sync

ITEMS = [f"1234567890{i:02d}" for i in range(4)]
DETAIL = {"title": "A thing", "price": 20.0, "quantity": 1, "source": "ebay",
          "view_url": "https://www.ebay.com/itm/x"}


class RecordingDb:
    """A store that writes down WHEN each save happened relative to a fetch."""

    def __init__(self, log):
        self.records: dict[str, dict] = {}
        self.log = log

    def list_listings(self, limit=50, user_id=None, statuses=None, before=None):
        return list(self.records.values())

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        self.log.append(("save", listing_id))
        self.records[listing_id] = {"id": listing_id, "user_id": user_id,
                                    "listing": listing, "status": status}
        return True

    def delete_listing(self, listing_id, user_id=None):
        return bool(self.records.pop(listing_id, None))


class RecordingTrading:
    RateLimited = ebay_trading.RateLimited

    def __init__(self, item_ids, log):
        self.item_ids = list(item_ids)
        self.log = log

    def active_listing_ids(self, token, limit=0):
        return list(self.item_ids)

    def sold_sales(self, token, limit=0):
        return {}

    def unsold_listing_ids(self, token, limit=0):
        return []

    def get_listing(self, token, item_id):
        self.log.append(("fetch", item_id))
        return dict(DETAIL, ebay_listing_id=item_id)


@pytest.fixture()
def run(monkeypatch):
    """import_active over four listings, fetched one at a time.

    One worker on purpose: with a pool the fetches interleave with each other
    and the order proves nothing. Serial, the two designs are told apart by
    inspection -- fetch,fetch,fetch,fetch,save,save,save,save is the old one.
    """
    def _go():
        log: list[tuple[str, str]] = []
        monkeypatch.setattr(listing_sync, "_FETCH_WORKERS", 1)
        monkeypatch.setattr(listing_sync, "db", RecordingDb(log))
        monkeypatch.setattr(listing_sync, "ebay_trading",
                            RecordingTrading(ITEMS, log))
        ticks: list[tuple[str, int, int]] = []
        result = listing_sync.import_active(
            "token", "u1", on_progress=lambda *a: ticks.append(a))
        return log, ticks, result
    return _go


def test_saving_starts_before_the_fetching_finishes(run):
    """Not a strict alternation: the pool runs ahead of the consumer by
    design, which is the point of keeping it. What must be true is that the
    writing has BEGUN before the last eBay call returns -- under the old
    two-pass import the first save came after every fetch, which is what made
    an interruption cost the whole run."""
    log, _ticks, result = run()
    assert result["imported"] == len(ITEMS)
    kinds = [k for k, _ in log]
    first_save = kinds.index("save")
    last_fetch = len(kinds) - 1 - kinds[::-1].index("fetch")
    assert first_save < last_fetch, (
        "the import fetched the whole store before writing any of it down, so "
        "an interruption loses every call it paid for")


def test_the_work_already_done_survives_the_next_call_failing(run, monkeypatch):
    """The point of the change, stated as the seller's outcome."""
    log: list[tuple[str, str]] = []
    db = RecordingDb(log)
    trading = RecordingTrading(ITEMS, log)
    monkeypatch.setattr(listing_sync, "_FETCH_WORKERS", 1)
    monkeypatch.setattr(listing_sync, "db", db)
    monkeypatch.setattr(listing_sync, "ebay_trading", trading)

    # The machine goes away part-way through. Not an exception the sync
    # handles -- one it does not, which is what a restart looks like.
    real = trading.get_listing
    def _die(token, item_id):
        if item_id == ITEMS[2]:
            raise KeyboardInterrupt("the machine went away")
        return real(token, item_id)
    trading.get_listing = _die

    with pytest.raises(BaseException):
        listing_sync.import_active("token", "u1")

    assert len(db.records) == 2, (
        "the listings already fetched and paid for were lost with the process")


def test_the_count_is_one_running_total_not_two_passes(run):
    _log, ticks, _result = run()
    phases = [t[0] for t in ticks]
    assert "fetching" not in phases and "saving" not in phases, (
        "a two-pass count describes a two-pass import, which this no longer is")
    counts = [t[1] for t in ticks if t[0] == "syncing"]
    assert counts == sorted(counts), "progress ran backwards"
    assert counts[-1] == len(ITEMS)
    assert {t[2] for t in ticks if t[0] == "syncing"} == {len(ITEMS)}


def test_the_listing_stage_still_reports_itself(run):
    """Listing the ids is its own stage and happens before any count exists."""
    _log, ticks, _result = run()
    assert ticks[0] == ("listing", 0, 0)
