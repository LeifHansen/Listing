"""A price drop sends the price, and nothing else the listing has pending.

Reported as: *"I just tried "lower all" with over 500 listed, but only one
was updated."* The button read "Lower 500+ prices", on the website, back when
the price cut was a dashboard suggestion group. It is the Manage tab's "Lower
prices…" on a tick now (test_lower_prices_lowers_the_ticked_ones), and it is
the same revise. The run DID reach every listing. eBay refused all but one of
them, and the reason had nothing to do with price.

A listing's pending edits are stored on it (`dirty_fields`), and a revise eBay
refuses keeps its marks so the retry carries them. "Enrich all" marks the
item specifics it fills and pushes them; where eBay turned those down, the
marks stayed. The price drop rebuilt the stored listing, marked the price on
top, and sent the lot — so eBay refused the specifics again and the price
went down with them. The one listing that changed was the one with nothing
else pending.

The revise already set two specifics refusals aside (a Best Offer freeze, a
newly required aspect — see ebay_trading.revise_listing). Any other refusal of
them ("has an invalid value") sank the whole edit.

These run the real eBay provider and the real Trading request builder; only
the HTTP call is stood in for.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402
from backend.services import bulk_actions, ebay_trading  # noqa: E402

SPECIFICS = [{"name": "Size Type", "value": "Reglar"}]
REFUSED_SPECIFICS = "The item specific Size Type has an invalid value."
CUT = bulk_actions.lower_price(40.0, 10)
_OK = ('<ReviseItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
       "<Ack>Success</Ack><ItemID>{}</ItemID></ReviseItemResponse>")


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    from backend.marketplaces import ebay_provider
    monkeypatch.setattr(ebay_provider, "db", dbmod)
    box: dict = {}
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: {
        "access_token": "t", "_uid": box.get("uid"), "ebay_username": "seller1"})
    # Nothing in here may reach a network: the taxonomy and token lookups the
    # revise path makes on the way are best-effort and fall back on refusal.
    def _offline(*_a, **_k):
        raise httpx.ConnectError("no network in tests")
    monkeypatch.setattr(httpx, "post", _offline)
    monkeypatch.setattr(httpx, "get", _offline)
    main._REPRICE_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "pricealone@example.com",
                             "password": "password123"}).status_code < 400
    box["uid"] = dbmod.get_user_by_email("pricealone@example.com")["id"]
    return client, dbmod, box["uid"]


def _stock(dbmod, uid, rid: str, n: int, *, pending=()) -> None:
    """One imported store listing, optionally carrying edits still pending."""
    data = {"title": f"Item {rid}", "price": 40.0, "quantity": 1,
            "images": [], "image_urls": ["https://i.ebayimg.com/x.jpg"],
            "ebay_listing_id": f"1100{n}", "source": "ebay",
            "ebay_account": "seller1", "category_id": "111",
            "item_specifics": SPECIFICS, "dirty_fields": list(pending)}
    assert dbmod.upsert_listing(rid, data, status="published", user_id=uid)


def _ebay(monkeypatch, refuse=None) -> list[str]:
    """eBay's Trading endpoint. `refuse(body)` returns an exception to raise
    for a request it turns down, or None to accept it."""
    sent: list[str] = []

    def _call(call, token, body):
        sent.append(body)
        exc = refuse(body) if refuse else None
        if exc:
            raise exc
        item = body.split("<ItemID>")[1].split("</ItemID>")[0]
        return ET.fromstring(_OK.format(item))
    monkeypatch.setattr(ebay_trading, "_call", _call)
    return sent


def _refuses_specifics(body):
    if "<ItemSpecifics>" in body:
        return ebay_trading.TradingError(REFUSED_SPECIFICS, code="21919301")
    return None


def _run(client, ids, percent=10) -> dict:
    body = client.post("/api/ebay/lower-prices",
                       json={"percent": percent, "listing_ids": list(ids)})
    assert body.status_code == 200, body.text
    job_id = body.json()["job_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        s = client.get(f"/api/bulk/status/{job_id}").json()
        if s.get("done"):
            assert not s.get("error"), s["error"]
            return s["result"]
        time.sleep(0.02)
    raise AssertionError("the price drop never finished")


# ------------------------------------------------------------ the report

def test_specifics_ebay_refused_once_do_not_sink_every_price(seller, monkeypatch):
    """The report in miniature: one clean listing, three still carrying a
    refused Enrich-all push. All four prices come down, not one."""
    client, dbmod, uid = seller
    ids = ["clean", "p1", "p2", "p3"]
    _stock(dbmod, uid, "clean", 0)
    for n, rid in enumerate(ids[1:], start=1):
        _stock(dbmod, uid, rid, n, pending=["item_specifics"])
    sent = _ebay(monkeypatch, refuse=_refuses_specifics)

    result = _run(client, ids)

    assert (result["changed"], result["failed"]) == (4, 0), result["results"]
    assert not any("<ItemSpecifics>" in body for body in sent)
    for rid in ids:
        assert dbmod.get_listing(rid)["listing"]["price"] == CUT


def test_the_request_carries_the_price_and_nothing_else(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "p1", 1, pending=["item_specifics", "title"])
    sent = _ebay(monkeypatch)

    _run(client, ["p1"])

    assert len(sent) == 1
    assert f"<StartPrice>{CUT:.2f}</StartPrice>" in sent[0]
    assert "<ItemSpecifics>" not in sent[0]
    assert "<Title>" not in sent[0]


def test_what_it_did_not_send_is_still_pending(seller, monkeypatch):
    """Not sending an edit is not the same as dropping it. The specifics stay
    on the listing, still marked, for the next save that is about them — and
    the price, which eBay took, is no longer marked."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "p1", 1, pending=["item_specifics"])
    _ebay(monkeypatch)

    _run(client, ["p1"])

    stored = dbmod.get_listing("p1")["listing"]
    assert stored["dirty_fields"] == ["item_specifics"]
    assert [(s["name"], s["value"]) for s in stored["item_specifics"]] == \
        [(s["name"], s["value"]) for s in SPECIFICS]


# ------------------------------------------------------------ a real refusal

def test_a_refused_price_leaves_the_listing_as_it_was(seller, monkeypatch):
    """When eBay turns the PRICE down (here: the listing is in a sale that
    blocks price revisions), the listing must not claim a price eBay does not
    show — nor record a markdown that never happened."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "sale", 1)
    _ebay(monkeypatch, refuse=lambda body: ebay_trading.TradingError(
        "The price of this item can't be revised while it's in a sale.",
        code="21919189"))
    before = dbmod.get_listing("sale")["listing"]

    result = _run(client, ["sale"])

    assert result["failed"] == 1
    after = dbmod.get_listing("sale")["listing"]
    assert after["price"] == 40.0
    assert not after.get("price_lowered_at")
    assert after["dirty_fields"] == before["dirty_fields"] == []


def test_an_unanswered_revise_keeps_the_attempt(seller, monkeypatch):
    """No answer is not a refusal: eBay may have taken the price. The record
    keeps the new price marked pending, so the next revise settles it rather
    than this one guessing that it did not land."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "lost", 1)
    _ebay(monkeypatch, refuse=lambda body: ebay_trading.UnknownOutcome(
        "eBay didn't answer.", call="ReviseItem"))

    _run(client, ["lost"])

    after = dbmod.get_listing("lost")["listing"]
    assert after["price"] == CUT
    assert "price" in after["dirty_fields"]


def test_a_run_with_refusals_reaches_the_error_feed(seller, monkeypatch, caplog):
    """At INFO, "changed=1 failed=499" was visible to nobody who was not
    watching the toast. Refusals are logged where the daily triage reads."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "sale", 1)
    _ebay(monkeypatch, refuse=lambda body: ebay_trading.TradingError(
        "The price of this item can't be revised while it's in a sale."))

    with caplog.at_level("INFO", logger="thryft"):
        _run(client, ["sale"])

    rows = [r for r in caplog.records
            if r.getMessage().startswith("lower-prices ") and "failed=1" in r.getMessage()]
    assert rows and rows[0].levelname == "WARNING"
    assert "in a sale" in rows[0].getMessage()
