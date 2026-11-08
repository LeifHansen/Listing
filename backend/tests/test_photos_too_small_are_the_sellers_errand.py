"""A listing eBay won't revise over its photo size is the seller's errand, not
a failed run.

Reported from the dashboard: a red toast reading "2 failed", each line eBay's
own sentence, "The resolution for provided picture(s) does not meet eBay's
Picture Policy requirements. Please only use pictures that are at least 500
pixels on the longest side." The run itself had done everything right.

A price drop sends eBay the price and nothing else (see
test_a_price_drop_sends_only_the_price). eBay refused it anyway, because it
checks the photos the listing ALREADY has on every revise. Both listings had
been imported from eBay, where they were first put up with photos under 500
pixels, so eBay will not accept any change to them until those photos are
replaced. The app can't resend its way past that. Only the seller can clear
it, and only by swapping in bigger photos.

So it is reported as what it is: a skip the seller has to act on, in words that
say what to do, and not a red "failed" for a button that worked.
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

from backend import ebay_errors, main, ratelimit  # noqa: E402
from backend.models import ItemSpecific  # noqa: E402
from backend.services import bulk_actions, ebay_trading  # noqa: E402

EBAY_SAYS = ("The resolution for provided picture(s) does not meet eBay's "
             "Picture Policy requirements. Please only use pictures that are "
             "at least 500 pixels on the longest side.")
LOOKERS = {"views": 40, "watchers": 0}
CUT = bulk_actions.lower_price(40.0, 10)
_OK = ('<ReviseItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
       "<Ack>Success</Ack><ItemID>{}</ItemID></ReviseItemResponse>")


# ------------------------------------------------------------ the wording

def test_ebays_sentence_is_recognised():
    assert ebay_errors.photos_too_small(EBAY_SAYS)
    # Line breaks and case are eBay's to vary, not part of the meaning.
    assert ebay_errors.photos_too_small(EBAY_SAYS.upper().replace(" ", "\n  "))


@pytest.mark.parametrize("message", [
    "The price of this item can't be revised while it's in a sale.",
    "The item specific Size Type has an invalid value.",
    "Your return policy is missing.",
    "Picture URL is not valid.",
])
def test_other_refusals_are_not_read_as_small_photos(message):
    assert not ebay_errors.photos_too_small(message)


def test_the_editor_names_the_size_rather_than_the_quality():
    """The single-listing save gets the same reading. "Re-upload clear photos"
    is advice about blur, and these photos may be perfectly sharp."""
    issue = ebay_errors.explain({"errorId": "21919137", "message": EBAY_SAYS})
    assert issue["target"] == "photos"
    assert "too small" in issue["title"]
    assert "500 pixels" in issue["fix"]


# ------------------------------------------------------------ Lower all

@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    from backend.marketplaces import ebay_provider
    monkeypatch.setattr(ebay_provider, "db", dbmod)
    box: dict = {}
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: {
        "access_token": "t", "_uid": box.get("uid"), "ebay_username": "seller1"})
    monkeypatch.setattr(main.ebay_offers, "eligible_cached", lambda creds: None)

    def _offline(*_a, **_k):
        raise httpx.ConnectError("no network in tests")
    monkeypatch.setattr(httpx, "post", _offline)
    monkeypatch.setattr(httpx, "get", _offline)
    main._REPRICE_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "smallphotos@example.com",
                             "password": "password123"}).status_code < 400
    box["uid"] = dbmod.get_user_by_email("smallphotos@example.com")["id"]
    return client, dbmod, box["uid"]


def _stock(dbmod, uid, rid: str, n: int) -> None:
    data = {"title": f"Item {rid}", "price": 40.0, "quantity": 1,
            "images": [], "image_urls": ["https://i.ebayimg.com/x.jpg"],
            "ebay_listing_id": f"2200{n}", "source": "ebay",
            "ebay_account": "seller1", "category_id": "111"}
    assert dbmod.upsert_listing(rid, data, status="published", user_id=uid)


def _ebay(monkeypatch, small: set[str]) -> list[str]:
    """eBay's Trading endpoint, refusing every revise of an item in `small`
    over its photos, whatever the request carries."""
    sent: list[str] = []

    def _call(call, token, body):
        sent.append(body)
        item = body.split("<ItemID>")[1].split("</ItemID>")[0]
        if item in small:
            raise ebay_trading.TradingError(EBAY_SAYS, code="21919137")
        return ET.fromstring(_OK.format(item))
    monkeypatch.setattr(ebay_trading, "_call", _call)
    return sent


def _metrics(monkeypatch, ids) -> None:
    monkeypatch.setattr(main, "_metrics_by_record_id",
                        lambda creds, items, status=None, fresh=False:
                        {rid: LOOKERS for rid in ids})


def _run(client, path: str, payload: dict) -> dict:
    body = client.post(path, json=payload)
    assert body.status_code == 200, body.text
    job_id = body.json()["job_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        s = client.get(f"/api/bulk/status/{job_id}").json()
        if s.get("done"):
            assert not s.get("error"), s["error"]
            return s["result"]
        time.sleep(0.02)
    raise AssertionError(f"{path} never finished")


def test_the_reported_toast_is_a_skip_not_a_failure(seller, monkeypatch):
    """The screenshot: two imported listings with small photos on eBay. The
    run reports them as skipped and tells the seller to change the photos.
    No "failed", which is what made the client paint the toast red."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "penguin", 1)
    _stock(dbmod, uid, "johnny", 2)
    _metrics(monkeypatch, ["penguin", "johnny"])
    sent = _ebay(monkeypatch, small={"22001", "22002"})

    result = _run(client, "/api/ebay/lower-all", {"percent": 10})

    assert (result["changed"], result["skipped"], result["failed"]) == (0, 2, 0)
    # The drop never sent a photo. eBay was judging the ones already up.
    assert sent and not any("<PictureDetails>" in b for b in sent)
    for row in result["results"]["skipped"]:
        assert "500 pixels" in row["message"]
        assert "bigger" in row["message"]
        assert row["needs_you"] is True


def test_the_rest_of_the_run_still_reports_as_lowered(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "clean", 0)
    _stock(dbmod, uid, "small", 1)
    _metrics(monkeypatch, ["clean", "small"])
    _ebay(monkeypatch, small={"22001"})

    result = _run(client, "/api/ebay/lower-all", {"percent": 10})

    assert (result["changed"], result["skipped"], result["failed"]) == (1, 1, 0)
    assert dbmod.get_listing("clean")["listing"]["price"] == CUT


def test_the_listing_eBay_refused_is_left_as_it_was(seller, monkeypatch):
    """Same rule as any refused price: the record does not claim a price eBay
    is not showing, and it is not stamped as lowered, so it is still in the
    group once its photos are replaced."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "small", 1)
    _metrics(monkeypatch, ["small"])
    _ebay(monkeypatch, small={"22001"})

    _run(client, "/api/ebay/lower-all", {"percent": 10})

    after = dbmod.get_listing("small")["listing"]
    assert after["price"] == 40.0
    assert not after.get("price_lowered_at")
    assert "price" not in (after.get("dirty_fields") or [])


def test_any_other_refusal_is_still_a_failure(seller, monkeypatch):
    """Only the photo refusal changes. A price eBay turns down is still a
    failure, reported in eBay's words."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "sale", 1)
    _metrics(monkeypatch, ["sale"])

    def _call(call, token, body):
        raise ebay_trading.TradingError(
            "The price of this item can't be revised while it's in a sale.",
            code="21916626")
    monkeypatch.setattr(ebay_trading, "_call", _call)

    result = _run(client, "/api/ebay/lower-all", {"percent": 10})

    assert (result["skipped"], result["failed"]) == (0, 1)


# ------------------------------------------------------------ Finish all

class _SmallPhotosEbay:
    """eBay refusing the revise over the listing's photo size, after the
    provider has saved the attempt the way the real one does on a refusal."""

    def __init__(self, dbmod, uid):
        self.db, self.uid = dbmod, uid

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.db.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                               status="published", user_id=self.uid)
        return PublishOutcome(ok=False, message=EBAY_SAYS, issues=[
            ebay_errors.explain({"errorId": "21919137", "message": EBAY_SAYS})])


def test_a_fill_eBay_refused_over_photos_still_needs_the_seller(
        dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t"})
    monkeypatch.setattr(main, "_metrics_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    monkeypatch.setattr(main, "_adopt_imported_images", lambda rid, rec: [])
    monkeypatch.setattr(
        main, "_enrich_listing_v2",
        lambda listing, paths, tags, progress=None: (
            listing.item_specifics.append(
                ItemSpecific(name="Size", value="M", confidence="high")) or 1))
    main._ENRICH_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "smallfill@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("smallfill@example.com")["id"]
    assert dbmod.upsert_listing("small", {
        "title": "Item small", "category_id": "11450", "source": "ebay",
        "ebay_listing_id": "33001", "images": ["a.jpg"]},
        status="published", user_id=uid)
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(
        main.storage.optimized_dir("small") / "a.jpg", "JPEG")
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _SmallPhotosEbay(dbmod, uid))

    started = client.post("/api/listings/finish-all").json()
    deadline = time.time() + 30
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{started['job_id']}").json()
        if body.get("done"):
            break
        time.sleep(0.02)
    result = body["result"]

    assert result["failed"] == 0
    [row] = result["results"]["skipped"]
    assert "500 pixels" in row["message"]
    assert row["needs_you"] is True
    # What the AI filled in is kept, and still marked to go with the next save.
    stored = dbmod.get_listing("small")["listing"]
    assert ("Size", "M") in [(s["name"], s["value"])
                             for s in stored["item_specifics"]]
    assert "item_specifics" in stored["dirty_fields"]
