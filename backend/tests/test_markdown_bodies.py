"""What the app sends eBay for a sale, and how it reads eBay's back.

The pure half of services/ebay_markdowns and services/sale_events: the body
of a new sale, the body of an edit (which must carry back everything it does
not understand, because eBay's PUT replaces the whole promotion), which of
eBay's sales the app may edit, and what state a recorded sale is in by its
dates rather than by however stale its stored status is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services import ebay_markdowns as md
from backend.services import sale_events

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def test_a_new_sale_is_one_percentage_over_an_explicit_list():
    body = md.build_body(name="Black Friday", percent=20, start=NOW, end=NOW + timedelta(days=7),
                         item_ids=["111", "222"], image_url="https://i.ebayimg.com/x.jpg")
    assert body["promotionStatus"] == "SCHEDULED"
    assert body["autoSelectFutureInventory"] is False
    assert body["applyFreeShipping"] is False
    [tier] = body["selectedInventoryDiscounts"]
    assert tier["discountBenefit"] == {"percentageOffItem": "20"}
    assert tier["inventoryCriterion"] == {
        "inventoryCriterionType": "INVENTORY_BY_VALUE", "listingIds": ["111", "222"]}
    assert body["startDate"] == "2026-11-24T12:00:00.000Z"
    assert body["promotionImageUrl"].startswith("https://")


def test_an_edit_sends_back_what_it_does_not_understand():
    detail = {"promotionId": "p1", "promotionHref": "x", "name": "Old",
              "someNewEbayField": {"keep": True}, "priority": "PRIORITY_1",
              "selectedInventoryDiscounts": [{
                  "discountBenefit": {"percentageOffItem": "20"},
                  "inventoryCriterion": {"inventoryCriterionType": "INVENTORY_BY_VALUE",
                                         "listingIds": ["1"]}}]}
    body = md.patch_body(detail, name="New", item_ids=["1", "2"])
    assert body["someNewEbayField"] == {"keep": True}
    assert "promotionId" not in body and "promotionHref" not in body
    assert body["name"] == "New"
    assert md._ids_in(body) == ["1", "2"]
    assert detail["selectedInventoryDiscounts"][0]["inventoryCriterion"]["listingIds"] == ["1"], \
        "the copy eBay gave is not edited in place"


def test_an_edit_will_not_flatten_a_sale_with_tiers():
    detail = {"selectedInventoryDiscounts": [{}, {}]}
    with pytest.raises(ValueError):
        md.patch_body(detail, item_ids=["1"])
    # A rename is still fine.
    assert md.patch_body(detail, name="Renamed")["name"] == "Renamed"


@pytest.mark.parametrize("value, ok", [(5, True), (80, True), (4, False), (81, False),
                                       (12.5, False), ("lots", False), (None, False)])
def test_the_percentage_is_ebays_whole_numbers(value, ok):
    if ok:
        assert md.validate_percent(value) == float(value)
    else:
        with pytest.raises(ValueError):
            md.validate_percent(value)


def test_a_window_starts_now_when_it_does_not_say_and_runs_for_its_days():
    start, end = md.validate_window(days=7, now=NOW)
    assert start == NOW + md.START_BUFFER
    assert end == start + timedelta(days=7)


def test_a_start_in_the_past_means_now():
    start, _end = md.validate_window(start=NOW - timedelta(hours=1), days=3, now=NOW)
    assert start == NOW + md.START_BUFFER


@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": md.MAX_DAYS + 1}, {},
    {"start": NOW + timedelta(days=2), "end": NOW + timedelta(days=1)},
])
def test_a_window_ebay_would_refuse_is_refused_here(kwargs):
    with pytest.raises(ValueError):
        md.validate_window(now=NOW, **kwargs)


def _tier(benefit=None, crit=None):
    return {"discountBenefit": benefit or {"percentageOffItem": "15"},
            "inventoryCriterion": crit or {"inventoryCriterionType": "INVENTORY_BY_VALUE",
                                           "listingIds": ["1", "2"]}}


def test_a_simple_sale_is_editable_here():
    v = md.classify({"promotionStatus": "RUNNING", "selectedInventoryDiscounts": [_tier()]})
    assert v.editable and v.percent == 15.0 and v.item_ids == ["1", "2"]


@pytest.mark.parametrize("detail, reason", [
    ({"selectedInventoryDiscounts": [_tier(), _tier()]}, "Different discounts"),
    ({"selectedInventoryDiscounts": [_tier(benefit={"amountOffItem": {"value": "5"}})]},
     "An amount off"),
    ({"selectedInventoryDiscounts": [_tier(crit={"inventoryCriterionType": "INVENTORY_BY_RULE",
                                                 "ruleCriteria": {}})]}, "by rule"),
    ({"autoSelectFutureInventory": True, "selectedInventoryDiscounts": [_tier()]}, "by rule"),
    ({"selectedInventoryDiscounts": [_tier(crit={"inventoryCriterionType": "INVENTORY_BY_VALUE",
                                                 "inventoryItems": [{"inventoryReferenceId": "S"}]})]},
     "by SKU"),
    ({"promotionStatus": "ENDED", "selectedInventoryDiscounts": [_tier()]}, "ended"),
])
def test_a_sale_the_app_cannot_express_is_shown_not_edited(detail, reason):
    v = md.classify(detail)
    assert not v.editable
    assert reason.lower() in v.reason.lower()


def test_a_recorded_sale_says_its_state_by_its_dates():
    row = {"status": "SCHEDULED", "start_at": (NOW - timedelta(days=1)).isoformat(),
           "end_at": (NOW + timedelta(days=1)).isoformat()}
    assert sale_events.effective_state(row, NOW) == "running"
    assert sale_events.effective_state({**row, "end_at": NOW.isoformat()}, NOW) == "ended"
    assert sale_events.effective_state(
        {**row, "start_at": (NOW + timedelta(hours=1)).isoformat()}, NOW) == "scheduled"
    assert sale_events.effective_state({**row, "status": "ENDED"}, NOW) == "ended"


def test_a_quick_sale_is_named_for_what_it_is():
    assert sale_events.auto_name(20, 7, NOW) == "20% off · 7 days · Nov 24"
    assert sale_events.auto_name(15, 1, NOW) == "15% off · 1 day · Nov 24"


def test_membership_counts_only_sales_still_to_come_or_running():
    rows = [
        {"id": "a", "status": "RUNNING", "end_at": (NOW + timedelta(days=2)).isoformat(),
         "item_ids": ["1", "2"]},
        {"id": "b", "status": "ENDED", "end_at": (NOW - timedelta(days=2)).isoformat(),
         "item_ids": ["3"]},
    ]
    assert set(sale_events.membership(rows, NOW)) == {"1", "2"}


# ---- reading eBay's answers ------------------------------------------------

import httpx  # noqa: E402


def _resp(status, body=None, headers=None):
    return httpx.Response(status, json=body, headers=headers or {},
                          request=httpx.Request("POST", "https://api.ebay.com/x"))


def test_a_new_sales_id_is_read_off_the_location_header():
    r = _resp(201, None, {"Location": "https://api.ebay.com/sell/marketing/v1/"
                                      "item_price_markdown/1234567890"})
    assert md._id_from(r) == "1234567890"
    assert md._id_from(_resp(201, {"promotionId": "42"})) == "42"


def test_a_refusal_names_the_listings_ebay_named_and_only_those():
    r = _resp(400, {"errors": [{
        "errorId": 38210, "message": "Listing is already in a promotion.",
        "parameters": [{"name": "listingId", "value": "110000000002"},
                       {"name": "other", "value": "999999999999"}]}]})
    exc = md._refusal(r, ["110000000001", "110000000002"])
    assert exc.listing_ids == ["110000000002"]
    assert exc.code == 38210
    assert str(exc) == "Listing is already in a promotion."


def test_a_refusal_that_names_nobody_is_still_a_sentence():
    exc = md._refusal(_resp(500, None), ["1"])
    assert exc.listing_ids == [] and "eBay refused" in str(exc)


def test_a_missing_scope_is_reconnect_not_refused():
    with pytest.raises(md.ScopeError):
        md._check(_resp(403, {"errors": [{"message": "Insufficient permissions"}]}))
    with pytest.raises(md.NotFound):
        md._check(_resp(404, {}))
