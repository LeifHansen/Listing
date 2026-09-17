"""The seller's whole store as one CSV — including a link to every photo.

Why this exists at all: everything the app knows about a listing lives in a
JSON column behind a login, and the seller's accountant, their spreadsheet,
their insurance schedule and whatever they move to next all speak CSV. An
export is the answer to "it's my inventory, let me have it" — so the rule
here is that the file says what the record says, and nothing else.

Three decisions are worth reading before changing anything:

**Photo links are the point, not a bonus.** A row naming an item with no way
to see it is a row about nothing, and the photos are the part of a listing
this app actually made. They come out as the SAME absolute URLs eBay is
given at publish (services/ebay._image_urls builds them the same way), so a
link in the spreadsheet and a link on the live listing are the same link —
the seller can check one against the other. Imported listings have no local
files and carry eBay's own URLs instead; both kinds land in one column.

**Nothing is summarised.** Prices stay in the currency they were recorded in
and the currency is a column, `price` stays the ASKING price with the amount
an item actually sold for beside it rather than folded into it, and a blank
cell means the record is blank — never "we didn't work it out". A cell is
never truncated either: an export that quietly shortens a description is
worse than a large file, and the caller streams this a page at a time so the
size costs nobody any memory.

**A spreadsheet runs what it opens.** A cell beginning `=`, `+`, `-`, `@` or
a control character is a FORMULA to Excel, Numbers and Sheets, and every
title, description and item specific in here was written by someone else —
an AI draft, an eBay import, a buyer-visible field anyone could have edited.
`=HYPERLINK(...)`, `=cmd|...` and friends are a real, documented attack on
exactly this kind of file. `_guard` below defuses them, and takes care not
to mangle a negative number doing it.
"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Iterable, Iterator, Sequence

from .. import config, objstore

# Columns, in the order they appear. Chosen to be the record as the SELLER
# thinks of it -- what the thing is, what it cost, what it made, where its
# photos are -- rather than the record as the database holds it: the sync
# ledger, the merge base and the per-marketplace error strings are internal
# bookkeeping and are left out on purpose (GET /api/listings drops two of
# them for the same reason).
#
# Append new columns at the END. A spreadsheet somebody built a formula
# against is a file format, and inserting a column in the middle silently
# moves every one after it.
COLUMNS = (
    "id",
    "status",
    "title",
    "subtitle",
    "sku",
    "brand",
    "condition",
    "condition_description",
    "category_id",
    "category_name",
    "store_category",
    "listing_format",
    "price",
    "auction_start_price",
    "sold_price",
    "currency",
    "quantity",
    "sold_quantity",
    "purchase_price",
    "retail_price",
    "watch_count",
    "source",
    "ebay_account",
    "ebay_listing_id",
    "listing_url",
    "marketplaces",
    "created_at",
    "updated_at",
    "sold_at",
    "ended_at",
    "item_specifics",
    "description",
    "image_count",
    "image_urls",
)

# What separates repeated values inside one cell -- the photo links, the item
# specifics, the marketplaces. A pipe because it is the one character none of
# them contains: a URL percent-encodes it, and an eBay aspect name or value
# may hold a comma, a semicolon or a newline but has never held this.
MULTI_SEP = " | "

# Leading characters a spreadsheet reads as the start of a formula. Tab and
# carriage return are in the list because Excel strips them and then reads
# whatever follows, so "\t=cmd" is the same attack wearing a hat.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
# A plain number, which is the one thing that may legitimately start with a
# "-" and must NOT be quoted into text: a cost basis of -4.50 is a number the
# seller wants to sum, not a string.
_NUMERIC_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _guard(value: str) -> str:
    """`value`, defused if a spreadsheet would treat it as a formula.

    The mitigation is the standard one -- prefix an apostrophe, which every
    spreadsheet reads as "the rest of this cell is text" -- applied only to
    cells that actually need it, so a CSV read by a script rather than a
    spreadsheet is unchanged apart from the handful of cells that were
    dangerous.
    """
    if not value or not value.startswith(_FORMULA_LEAD):
        return value
    if _NUMERIC_RE.match(value):
        return value
    return "'" + value


def _text(value) -> str:
    """One stored value as a cell. None and False-y blanks become "", which
    is the CSV for "the record does not say" -- distinct from a 0 the record
    genuinely holds."""
    if value is None or value is False:
        return ""
    if value is True:
        return "yes"
    return str(value)


def _number(value) -> str:
    """A money/count field. Blank when the record has no number, so an empty
    cell never averages in as a zero."""
    if value is None or value == "":
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return _text(value)
    # A stored NaN or infinity is not a number any column can hold -- and
    # int() RAISES on both, which inside a streaming response is a file that
    # stops mid-row rather than an error anyone sees. json.dumps writes both
    # by default, so the JSON column really can carry one. The raw value goes
    # through as text instead: nothing invented, nothing lost.
    if not math.isfinite(num):
        return _text(value)
    # Integers without a trailing ".0": quantity 1 should read as 1 and a
    # price of 48.0 as 48. Only while a float still IS its integer (2**53);
    # past that, str() keeps the exponent rather than spelling out three
    # hundred digits nobody asked for.
    return str(int(num)) if abs(num) < 2 ** 53 and num == int(num) else str(num)


def image_links(listing_id: str, listing: dict, base_url: str = "") -> list[str]:
    """Every photo on one listing, as an absolute URL.

    Two kinds of listing, one column:

      - imported from eBay: no local files at all, and `image_urls` already
        holds eBay's own absolute URLs. They are used as they stand.
      - created here: `images` holds filenames, and the public URL for one is
        either the R2 bucket (when a public base URL is configured) or this
        app's own /media route, which is public, stable across restarts, and
        redirects to the bucket when R2 is in presigned mode.

    That is the same rule, in the same order, that decides what URLs eBay is
    handed at publish -- see services/ebay._image_urls. Deliberately: a link
    in the export and a link on the live listing should be the same link.

    Unlike that function this never falls back to LISTING THE DISK when the
    record names no files. An export walks the seller's whole store, and a
    directory scan per listing turns one download into thousands of stat
    calls; a record with no photos has no photo links, which is the truth.
    """
    urls = [str(u).strip() for u in (listing.get("image_urls") or [])
            if str(u).strip()]
    if urls:
        return urls
    names = [str(n).strip() for n in (listing.get("images") or []) if str(n).strip()]
    if not names:
        return []
    if objstore.enabled() and config.r2_public_urls():
        return [objstore.public_url(objstore.key_for(listing_id, n)) for n in names]
    # An argument rather than module state, and deliberately: this runs on a
    # worker thread inside a streaming response, so a "current base URL"
    # stashed anywhere shared would be one export's origin appearing in
    # another's file the moment two sellers download at once.
    base = (base_url or "").rstrip("/")
    return [f"{base}/media/{listing_id}/optimized/{n}" for n in names]


def _listing_url(listing: dict) -> str:
    """Where a buyer would see this listing, or "" for one never published.

    eBay's own URL when the import carried one -- it names the right domain,
    which a guess cannot for a seller on ebay.co.uk or ebay.de. Then whatever
    the marketplace state recorded at publish, then the /itm/ form as a last
    resort, which is right for eBay.com and is all an id can tell us.
    """
    direct = str(listing.get("view_url") or "").strip()
    if direct:
        return direct
    for state in (listing.get("marketplaces") or {}).values():
        if isinstance(state, dict):
            url = str(state.get("url") or "").strip()
            if url:
                return url
    item_id = str(listing.get("ebay_listing_id") or "").strip()
    return f"https://www.ebay.com/itm/{item_id}" if item_id else ""


def _marketplaces(listing: dict) -> str:
    """Which marketplaces this listing reached, and in what state.

    Only the ones it actually got to: an entry exists the moment a publish is
    attempted, so a marketplace with no id and no status is one the seller
    looked at rather than one they are listed on.
    """
    out = []
    for name, state in sorted((listing.get("marketplaces") or {}).items()):
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "").strip()
        if not status and not str(state.get("listing_id") or "").strip():
            continue
        out.append(f"{name}={status}" if status else str(name))
    return MULTI_SEP.join(out)


def _item_specifics(listing: dict) -> str:
    """eBay's aspects as `Name=Value`, in the order the listing holds them."""
    out = []
    for spec in (listing.get("item_specifics") or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        value = str(spec.get("value") or "").strip()
        if name and value:
            out.append(f"{name}={value}")
    return MULTI_SEP.join(out)


def row_for(record: dict, base_url: str = "") -> list[str]:
    """One stored listing record as one CSV row, in COLUMNS order.

    `record` is what db.list_listings hands back: the row's own id, status and
    timestamps, with the listing itself under "listing".
    """
    listing = record.get("listing")
    if not isinstance(listing, dict):
        listing = {}
    listing_id = _text(record.get("id"))
    links = image_links(listing_id, listing, base_url)
    values = {
        "id": listing_id,
        "status": _text(record.get("status")),
        # The row's `title` column is a denormalised copy for searching; the
        # listing's own is the one the seller edits, so it wins where they
        # disagree.
        "title": _text(listing.get("title") or record.get("title")),
        "subtitle": _text(listing.get("subtitle")),
        "sku": _text(listing.get("sku")),
        "brand": _text(listing.get("brand")),
        "condition": _text(listing.get("condition")),
        "condition_description": _text(listing.get("condition_description")),
        "category_id": _text(listing.get("category_id")),
        "category_name": _text(listing.get("category_suggestion")),
        "store_category": _text(listing.get("store_category_name")),
        "listing_format": _text(listing.get("listing_format")),
        "price": _number(listing.get("price")),
        "auction_start_price": _number(listing.get("auction_start_price")),
        # Blank unless the marketplace told us what it went for. `price` above
        # is the ASK and stays that way after a sale -- an accepted offer or
        # an auction close settles below it -- so copying one into the other
        # here would overstate the take on every discounted sale in the file.
        "sold_price": _number(listing.get("sold_price")),
        "currency": _text(listing.get("currency")),
        "quantity": _number(listing.get("quantity")),
        "sold_quantity": _number(listing.get("sold_quantity")),
        "purchase_price": _number(listing.get("purchase_price")),
        "retail_price": _number(listing.get("retail_price")),
        "watch_count": _number(listing.get("watch_count")),
        "source": _text(listing.get("source")),
        "ebay_account": _text(listing.get("ebay_account")),
        "ebay_listing_id": _text(listing.get("ebay_listing_id")),
        "listing_url": _listing_url(listing),
        "marketplaces": _marketplaces(listing),
        "created_at": _text(record.get("created_at")),
        "updated_at": _text(record.get("updated_at")),
        "sold_at": _text(listing.get("sold_at")),
        "ended_at": _text(listing.get("ended_at")),
        "item_specifics": _item_specifics(listing),
        "description": _text(listing.get("description")),
        "image_count": str(len(links)),
        "image_urls": MULTI_SEP.join(links),
    }
    return [_guard(values[name]) for name in COLUMNS]


# Excel on Windows reads a CSV as the system codepage unless the file opens
# with a UTF-8 byte-order mark, so a title with a "£", an "é" or an em dash
# arrives as mojibake. Every other reader tolerates the mark; Excel is the
# one that needs it, and Excel is what most sellers will open this in.
BOM = "\ufeff"


def iter_csv(pages: Iterable[Sequence[dict]], base_url: str = "") -> Iterator[str]:
    """The whole file, a chunk at a time, from an iterable of record pages.

    Takes PAGES rather than records so the caller can read the store a page
    at a time and this never holds more than one of them: a seller with
    thousands of listings, each with a description, would otherwise be a
    request that builds their entire store in memory before sending a byte.

    Yields text, which Starlette encodes as UTF-8 on the way out.
    """
    buf = io.StringIO()
    # QUOTE_MINIMAL with the default dialect: RFC 4180, so a description with
    # commas, quotes or newlines in it round-trips through any reader.
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    yield BOM + buf.getvalue()
    for page in pages:
        buf.seek(0)
        buf.truncate(0)
        for record in page:
            writer.writerow(row_for(record, base_url))
        chunk = buf.getvalue()
        if chunk:
            yield chunk
