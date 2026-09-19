"""A price or stock change on a listing Etsy already holds reaches Etsy.

updateListing — the PATCH the revise sends — has no price or quantity
fields. The revise used to send them anyway; Etsy ignored them and answered
200, so a seller who dropped a price here was told "Your Etsy listing has
been updated" while etsy.com went on asking the old one. Price and stock
live on the inventory record (PUT /listings/{id}/inventory), which is read
whole and written whole, with everything that is not ours to change carried
over exactly as read.
"""
from __future__ import annotations

import httpx
import pytest

from backend.marketplaces import etsy_provider, mapping_etsy
from backend.marketplaces.base import PublishContext
from backend.models import Listing
from backend.services import etsy as etsy_service

SETTINGS = {"shop_id": "1", "shipping_profile_id": "7",
            "return_policy_id": "8", "readiness_state_id": "9"}
CREDS = {"access_token": "tok", "shop_id": "1", "settings": SETTINGS}
CURRENT = {
    "products": [{
        "product_id": 555, "sku": "THRYFT-abc",
        "property_values": [{"property_id": 1, "value_ids": [2], "values": ["x"]}],
        "offerings": [{"offering_id": 9, "quantity": 5, "is_enabled": True,
                       "price": {"amount": 4500, "divisor": 100, "currency_code": "USD"},
                       "readiness_state_id": 3}],
    }],
    "price_on_property": [], "quantity_on_property": [], "sku_on_property": [],
}


def _listing(**kw) -> Listing:
    fields = dict(title="Vintage mug", description="Nice.", price=19.5, quantity=2,
                  images=["a.jpg"],
                  etsy={"taxonomy_id": 1, "who_made": "someone_else",
                        "when_made": "1990s"})
    fields.update(kw)
    return Listing(**fields)


def _ctx(mode="live", status="published") -> PublishContext:
    prev = {"listing": {"marketplaces": {"etsy": {"listing_id": "77", "status": status}}}}
    return PublishContext(session_id="s1", listing=_listing(), mode=mode,
                          base_url="https://app.test", uid="u1", prev_record=prev)


@pytest.fixture
def etsy_calls(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(etsy_provider.etsy, "get_listing_inventory",
                        lambda tok, lid: (calls.append(("read", lid)), CURRENT)[1])
    monkeypatch.setattr(etsy_provider.etsy, "update_listing_inventory",
                        lambda tok, lid, body: (calls.append(("inventory", lid, body)), {})[1])
    monkeypatch.setattr(etsy_provider.etsy, "update_listing",
                        lambda tok, shop, lid, patch: (calls.append(("patch", lid, patch)),
                                                       {"listing_id": lid, "url": ""})[1])
    return calls


def test_the_patch_no_longer_carries_price_or_quantity(etsy_calls):
    outcome = etsy_provider.EtsyProvider().publish(_ctx(), CREDS)
    assert outcome.ok, outcome.message
    patch = next(c[2] for c in etsy_calls if c[0] == "patch")
    assert "price" not in patch and "quantity" not in patch
    assert patch["title"] == "Vintage mug"


def test_the_inventory_carries_them_and_keeps_what_is_not_ours(etsy_calls):
    etsy_provider.EtsyProvider().publish(_ctx(), CREDS)
    body = next(c[2] for c in etsy_calls if c[0] == "inventory")
    product = body["products"][0]
    offering = product["offerings"][0]
    assert offering == {"price": 19.5, "quantity": 2, "is_enabled": True,
                        "readiness_state_id": 9}
    assert product["sku"] == "THRYFT-abc"
    assert product["property_values"] == CURRENT["products"][0]["property_values"]
    for key in ("price_on_property", "quantity_on_property", "sku_on_property"):
        assert body[key] == []


def test_stock_moves_before_the_listing_goes_live(etsy_calls):
    """A refused inventory write must leave the listing as it was, never
    live at the wrong price — so the PUT precedes the activating PATCH."""
    etsy_provider.EtsyProvider().publish(_ctx(status="draft"), CREDS)
    order = [c[0] for c in etsy_calls]
    assert order == ["read", "inventory", "patch"]
    assert next(c[2] for c in etsy_calls if c[0] == "patch")["state"] == "active"


def test_a_listing_etsy_holds_with_variations_is_refused_not_flattened(etsy_calls, monkeypatch):
    two = {**CURRENT, "products": [CURRENT["products"][0], CURRENT["products"][0]]}
    monkeypatch.setattr(etsy_provider.etsy, "get_listing_inventory", lambda tok, lid: two)
    outcome = etsy_provider.EtsyProvider().publish(_ctx(), CREDS)
    assert outcome.ok is False and outcome.outcome_unknown is False
    assert outcome.listing_id == "77"
    assert outcome.issues[0]["target"] == "variations"
    assert "variations" in outcome.message
    assert not [c for c in etsy_calls if c[0] in ("inventory", "patch")]


def test_the_readiness_state_etsy_already_had_is_kept_when_we_have_none():
    body = mapping_etsy.build_inventory_body(_listing(), CURRENT, {})
    assert body["products"][0]["offerings"][0]["readiness_state_id"] == 3


def test_the_inventory_write_is_json_not_a_form(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    def _put(url, **kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr(httpx, "put", _put)
    etsy_service.update_listing_inventory("tok", "77", {"products": []})
    assert "json" in seen and "data" not in seen
