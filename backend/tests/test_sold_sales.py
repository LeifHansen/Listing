"""What a sold listing ACTUALLY went for.

An accepted Best Offer settles below the asking price, and eBay never moves
the listing's own price to match — so a sold record built from GetItem alone
reported what the seller asked ($89.99), not what the buyer paid ($76.50).
The amount exists in exactly one place: the transaction under SoldList.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading

ITEM = "123456789012"
OTHER = "987654321098"


def _tx(item_id: str, price: str, when: str, qty: int = 1) -> str:
    return (
        "<OrderTransaction><Transaction>"
        f"<QuantityPurchased>{qty}</QuantityPurchased>"
        f'<TransactionPrice currencyID="USD">{price}</TransactionPrice>'
        f"<CreatedDate>{when}</CreatedDate>"
        f"<Item><ItemID>{item_id}</ItemID></Item>"
        "<OrderLineItemID>" + item_id + "-9988776655</OrderLineItemID>"
        "</Transaction></OrderTransaction>"
    )


def _page(transactions: str, pages: int = 1) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Success</Ack><SoldList>"
        f"<OrderTransactionArray>{transactions}</OrderTransactionArray>"
        f"<PaginationResult><TotalNumberOfPages>{pages}</TotalNumberOfPages>"
        "</PaginationResult></SoldList></GetMyeBaySellingResponse>"
    ).encode()


class _Resp:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


@pytest.fixture
def replies(monkeypatch):
    """Queue up one XML body per GetMyeBaySelling page."""
    queue: list[bytes] = []

    def fake_post(url, headers=None, content=None, timeout=None):
        return _Resp(queue.pop(0) if queue else _page(""))

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return queue


def test_the_transaction_price_is_what_gets_reported(replies):
    replies.append(_page(_tx(ITEM, "76.50", "2026-08-12T18:04:11.000Z")))
    sales = ebay_trading.sold_sales("tok")
    assert sales[ITEM]["price"] == 76.5
    assert sales[ITEM]["currency"] == "USD"
    assert sales[ITEM]["quantity"] == 1
    assert sales[ITEM]["sold_at"] == "2026-08-12T18:04:11.000Z"


def test_repeat_sales_of_one_listing_add_up_and_the_latest_sets_the_price(replies):
    """A multi-quantity listing sells across several orders. Quantities add;
    the most recent transaction owns the headline price and date."""
    replies.append(_page(
        _tx(ITEM, "40.00", "2026-08-01T10:00:00.000Z")
        + _tx(ITEM, "35.00", "2026-08-09T10:00:00.000Z", qty=2)))
    sales = ebay_trading.sold_sales("tok")
    assert sales[ITEM]["quantity"] == 3
    assert sales[ITEM]["price"] == 35.0
    assert sales[ITEM]["sold_at"] == "2026-08-09T10:00:00.000Z"


def test_out_of_order_transactions_do_not_rewrite_the_latest_price(replies):
    """eBay's paging order isn't guaranteed — an older sale arriving second
    must not overwrite the newer one's price."""
    replies.append(_page(
        _tx(ITEM, "35.00", "2026-08-09T10:00:00.000Z")
        + _tx(ITEM, "40.00", "2026-08-01T10:00:00.000Z")))
    sales = ebay_trading.sold_sales("tok")
    assert sales[ITEM]["price"] == 35.0


def test_every_page_is_read(replies):
    replies.append(_page(_tx(ITEM, "76.50", "2026-08-12T18:04:11.000Z"), pages=2))
    replies.append(_page(_tx(OTHER, "12.00", "2026-08-13T18:04:11.000Z"), pages=2))
    sales = ebay_trading.sold_sales("tok")
    assert set(sales) == {ITEM, OTHER}


def test_an_empty_sold_list_is_not_an_error(replies):
    replies.append(_page(""))
    assert ebay_trading.sold_sales("tok") == {}
