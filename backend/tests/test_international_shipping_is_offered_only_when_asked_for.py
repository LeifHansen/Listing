"""The "Use eBay International Shipping" switch, and where it has to reach.

eBay International Shipping (eIS) is eBay's own export programme for US
sellers: the seller posts every sale to eBay's US hub with an ordinary
domestic label, and eBay carries it abroad, clears customs and handles any
return from overseas, charging the buyer for that leg. Turning it on changes
who a listing is sold to. That is a decision the seller makes once, in
Settings, and the app has to carry it to the two places eBay reads it:

  * the fulfillment POLICY the app creates (`globalShipping`) -- what eBay
    reads for a listing that uses business policies, which every listing
    this app publishes does; and
  * the LISTING itself (`ShippingDetails.GlobalShipping`), the per-listing
    opt-in, so a listing under a policy the seller made elsewhere says so
    too.

And the same rules as "Allow offers", because it is the same kind of switch:
absent is a no, unreadable is a no, a revise never carries it, and OFF sends
nothing rather than an opt-out -- a seller eBay enrolled on its own must not
lose their overseas buyers to a toggle they never touched.
"""
from __future__ import annotations

import httpx
import pytest

from backend import ebay_auth
from backend.models import Listing
from backend.services import ebay_trading, listing_sync, policy_terms

POLICIES = {"fulfillment_policy_id": "f1", "payment_policy_id": "p1",
            "return_policy_id": "r1"}
OPT_IN = ("<ShippingDetails><GlobalShipping>true</GlobalShipping>"
          "</ShippingDetails>")
GROUND = {"code": "USPSGroundAdvantage", "label": "USPS Ground Advantage",
          "carrier": "USPS"}


@pytest.fixture
def listing():
    return Listing(title="A brass desk lamp", price=48.0, category_id="20697",
                   description="Works.", quantity=1)


def _xml(listing, **kw):
    _call, body = ebay_trading.build_add_item(listing, ["https://x/1.jpg"],
                                              POLICIES, "97201", **kw)
    return body


def _ground_policy(**extra) -> dict:
    return {"fulfillmentPolicyId": "dom", "name": "Ground",
            "shippingOptions": [{"optionType": "DOMESTIC",
                                 "shippingServices": [
                                     {"shippingServiceCode": "USPSGroundAdvantage",
                                      "shippingCarrierCode": "USPS"}]}],
            **extra}


# ------------------------------------------------- the flag reaches the wire

def test_the_switch_opts_the_listing_in(listing):
    assert OPT_IN in _xml(listing, international_shipping=True)


def test_off_sends_nothing_rather_than_an_opt_out(listing):
    """An explicit false would opt the listing OUT. A seller eBay enrolled on
    its own had listings going abroad before this switch existed, and must
    not lose that to a toggle they never touched."""
    body = _xml(listing, international_shipping=False)
    assert "GlobalShipping" not in body
    assert "ShippingDetails" not in body


def test_off_is_the_default(listing):
    assert "GlobalShipping" not in _xml(listing)


def test_an_auction_is_opted_in_too(listing):
    """Unlike Best Offer, eIS is not a fixed-price feature."""
    for fmt in ("AUCTION", "AUCTION_BIN"):
        listing.listing_format = fmt
        listing.auction_start_price = 9.99
        assert OPT_IN in _xml(listing, international_shipping=True), fmt


def test_the_postage_services_still_come_from_the_policy_alone(listing):
    """Only the opt-in goes in the container. Naming the services here as
    well would be a second copy of the business policy for eBay to
    reconcile against the profile the request also references."""
    body = _xml(listing, international_shipping=True)
    assert "ShippingServiceOptions" not in body
    assert "InternationalShippingServiceOption" not in body
    assert "<ShippingProfileID>f1</ShippingProfileID>" in body


def test_the_dry_run_preview_shows_the_opt_in_the_publish_would_carry(
        monkeypatch, listing, tmp_path):
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext

    monkeypatch.setattr(ebay_provider.ebay, "image_urls_for", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.db, "get_ebay_account", lambda _uid: None)
    monkeypatch.setattr(ebay_provider.storage, "write_export",
                        lambda sid, name, payload: tmp_path / f"{sid}.json")
    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "sandbox")
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 1})

    out = ebay_provider.EbayProvider()._dry_run(PublishContext(
        session_id="s1", listing=listing, mode="live",
        base_url="https://example.test", uid="u1", prev_record={}))
    assert OPT_IN in out.raw["payload"]["xml"]


def test_the_publish_reads_the_sellers_own_switch(monkeypatch, listing):
    sent = {}

    class _Trading:
        AlreadyListedError = ebay_trading.AlreadyListedError
        TradingError = ebay_trading.TradingError
        UnknownOutcome = ebay_trading.UnknownOutcome

        def create_listing(self, *_a, **kw):
            sent.update(kw)
            return {"published": True, "listing_id": "110040602158",
                    "view_url": "https://www.ebay.com/itm/110040602158"}

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 1})
    listing_sync.create_on_ebay(
        "tok", listing, ["https://x/1.jpg"],
        creds={"access_token": "tok", "ship_from_postal": "97201",
               "_uid": "u1"})
    assert sent["international_shipping"] is True


def test_the_probe_asks_about_the_publish_it_is_diagnosing(monkeypatch,
                                                           listing):
    """A verify that drops the opt-in answers a different question -- and
    would clear a listing eBay refused precisely because of it."""
    seen = []

    class _Trading:
        def verify_listing(self, _token, _candidate, _urls, **kw):
            seen.append(kw)

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 1})
    verify = listing_sync.verifier(
        "tok", ["https://x/1.jpg"],
        creds={"ship_from_postal": "97201", "_uid": "u1"})
    verify(listing)
    assert seen[0]["international_shipping"] is True


def test_a_revise_never_touches_a_live_listing(monkeypatch, listing):
    """The switch says NEW listings."""
    listing.source = "ebay"
    listing.ebay_listing_id = "110040602158"
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 1})
    sent = {}

    class _Trading:
        def revise_listing(self, _token, _item_id, _listing, **kw):
            sent.update(kw)
            return {"ok": True}

    monkeypatch.setattr(listing_sync, "ebay_trading", _Trading())
    monkeypatch.setattr(listing_sync.taxonomy, "sanitize_specifics",
                        lambda _l: None)
    listing_sync.push_edit("tok", listing)
    assert "international_shipping" not in sent


def test_a_revise_body_carries_no_opt_in_either(listing):
    listing.ebay_listing_id = "110040602158"
    listing.mark_dirty("title")
    _call, body = ebay_trading.build_revise_item(listing, "110040602158")
    assert "GlobalShipping" not in body


# ------------------------------------------------- and only when asked for

def test_a_seller_who_never_chose_is_not_opted_in(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs", lambda _uid: {})
    assert listing_sync.international_shipping_enabled("u1") is False


def test_an_unreadable_preference_never_turns_it_on(monkeypatch):
    def _boom(_uid):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(listing_sync.db, "get_prefs", _boom)
    assert listing_sync.international_shipping_enabled("u1") is False


def test_an_explicit_yes_and_no_are_honoured(monkeypatch):
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 1})
    assert listing_sync.international_shipping_enabled("u1") is True
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"ebay_international_shipping": 0})
    assert listing_sync.international_shipping_enabled("u1") is False


def test_an_anonymous_publish_is_never_opted_in(monkeypatch):
    assert listing_sync.international_shipping_enabled(None) is False
    assert listing_sync.international_shipping_enabled("") is False
    assert listing_sync.publish_international_shipping(None) is False
    assert listing_sync.publish_international_shipping({}) is False


def test_the_two_switches_do_not_read_each_other(monkeypatch):
    """Same helper underneath; a seller who allows offers has not thereby
    agreed to sell abroad, and vice versa."""
    monkeypatch.setattr(listing_sync.db, "get_prefs",
                        lambda _uid: {"allow_offers": 1})
    assert listing_sync.offers_enabled("u1") is True
    assert listing_sync.international_shipping_enabled("u1") is False


# ------------------------------------------------- the policy the app creates

def test_the_policy_carries_the_flag_only_when_asked():
    domestic = ebay_auth.fulfillment_body(GROUND)
    assert "globalShipping" not in domestic

    abroad = ebay_auth.fulfillment_body(GROUND, international_shipping=True)
    assert abroad["globalShipping"] is True
    # The seller still ships the domestic leg: eIS rides on top of an
    # ordinary calculated domestic option, not instead of it.
    assert abroad["shippingOptions"][0]["optionType"] == "DOMESTIC"
    assert abroad["shippingOptions"] == domestic["shippingOptions"]


def test_an_international_policy_has_its_own_name_and_it_fits():
    """eBay refuses a second policy under a name in use, so the two policies
    a seller can end up with need two names -- and it caps names at 64, so
    the longest service label has to fit with the suffix."""
    for svc in ebay_auth.SHIPPING_SERVICES:
        home = ebay_auth.fulfillment_body(svc)["name"]
        away = ebay_auth.fulfillment_body(svc, international_shipping=True)["name"]
        assert home != away, svc["code"]
        assert len(away) <= ebay_auth.POLICY_NAME_MAX, away
        assert "Thryft Shop" in away, away


def test_a_domestic_policy_is_not_reused_for_an_international_request(
        monkeypatch):
    """Reusing it would hand back the policy the seller already had, and the
    terms they just agreed to -- worldwide through eIS -- would never come
    to exist on eBay."""
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "fulfillmentPolicies": [_ground_policy()]})

    found, known = ebay_auth.find_policy_for_service(
        "tok", "USPSGroundAdvantage", international_shipping=True)
    assert (found, known) == (None, True)

    found, _ = ebay_auth.find_policy_for_service("tok", "USPSGroundAdvantage")
    assert found["id"] == "dom"


def test_an_international_policy_is_found_for_an_international_request(
        monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "fulfillmentPolicies": [
            _ground_policy(),
            dict(_ground_policy(globalShipping=True),
                 fulfillmentPolicyId="abroad", name="Ground, worldwide")]})

    found, known = ebay_auth.find_policy_for_service(
        "tok", "USPSGroundAdvantage", international_shipping=True)
    assert known is True
    assert found == {"id": "abroad", "name": "Ground, worldwide"}


def test_the_switch_off_reuses_whatever_ships_the_service(monkeypatch):
    """Off means "leave that to eBay and my policies", not "domestic only":
    a seller whose one Ground Advantage policy already ships abroad keeps
    using it."""
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "fulfillmentPolicies": [_ground_policy(globalShipping=True)]})
    found, _ = ebay_auth.find_policy_for_service("tok", "USPSGroundAdvantage")
    assert found["id"] == "dom"


def test_ensure_creates_the_international_policy_beside_the_domestic_one(
        monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "fulfillmentPolicies": [_ground_policy()]})
    posted = {}

    def _post(url, **kw):
        posted.update(url=url, json=kw.get("json"))
        return httpx.Response(
            201, json={"fulfillmentPolicyId": "FP-abroad",
                       "name": kw["json"]["name"]},
            request=httpx.Request("POST", url))
    monkeypatch.setattr(ebay_auth.httpx, "post", _post)

    got = ebay_auth.ensure_service_policy("tok", GROUND,
                                          international_shipping=True)
    assert got == {"id": "FP-abroad", "created": True,
                   "name": posted["json"]["name"]}
    assert posted["json"]["globalShipping"] is True
    assert posted["json"]["name"] != ebay_auth.GROUND_POLICY_NAME


def test_ensure_without_the_switch_is_unchanged(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "fulfillmentPolicies": [_ground_policy()]})

    def _must_not_post(*a, **k):
        raise AssertionError("created a policy when one already existed")
    monkeypatch.setattr(ebay_auth.httpx, "post", _must_not_post)
    assert ebay_auth.ensure_service_policy("tok", GROUND) == {
        "id": "dom", "name": "Ground", "created": False}


def test_the_picker_says_which_shipping_policy_ships_abroad():
    """Two Ground Advantage policies look identical in a dropdown otherwise."""
    home = ebay_auth._policy_summary("fulfillment", _ground_policy())
    away = ebay_auth._policy_summary("fulfillment",
                                     _ground_policy(globalShipping=True))
    assert "eBay International Shipping" not in home
    assert "eBay International Shipping" in away
    assert "Ground Advantage" in away


# ------------------------------------------------- the terms say so first

def _where_to(described: dict) -> dict:
    terms = described["kinds"]["fulfillment"]["terms"]
    return next(t for t in terms if t["label"] == "Where you post to")


def test_the_terms_say_the_listing_will_sell_abroad():
    """A business policy is a promise the seller reads before it is made.
    This is the promise that changes who they sell to."""
    where = _where_to(policy_terms.describe(international_shipping=True))
    assert "eBay International Shipping" in where["value"]
    assert "hub" in where["detail"].lower()

    where = _where_to(policy_terms.describe())
    assert "eBay International Shipping" not in where["value"]
    assert "United States only" in where["value"]


def test_the_preview_is_the_body_that_would_be_sent():
    """The derivation stays live for this flag too: the previewed policy is
    fulfillment_body with the same argument, not a second copy."""
    described = policy_terms.describe(service_code="USPSPriority",
                                      international_shipping=True)
    svc = ebay_auth.service_by_code("USPSPriority")
    assert described["kinds"]["fulfillment"]["body"] == \
        ebay_auth.fulfillment_body(svc, international_shipping=True)
    assert described["options"]["international_shipping"] is True
    assert policy_terms.describe()["options"]["international_shipping"] is False


# ------------------------------------------------- through the routes

@pytest.fixture()
def connected(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from fastapi.testclient import TestClient

    from backend import main

    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: None)
    monkeypatch.setattr(main.ebay_account, "note_verified", lambda uid: None)
    return TestClient(main.app)


@pytest.fixture()
def would_create(monkeypatch):
    """Record how each policy create was asked for, without eBay."""
    from backend import main

    asked: dict = {}

    def _fulfillment(_token, svc, **kw):
        asked["fulfillment"] = {"svc": svc["code"], **kw}
        return {"id": "f-1", "name": "f", "created": True}
    monkeypatch.setattr(main.ebay_auth, "ensure_service_policy", _fulfillment)
    for name, kind in (("ensure_payment_policy", "payment"),
                       ("ensure_return_policy", "return")):
        monkeypatch.setattr(
            main.ebay_auth, name,
            lambda *a, _k=kind, **kw: {"id": f"{_k}-1", "name": _k,
                                       "created": True})
    return asked


def test_the_preview_route_reflects_the_switch(connected):
    on = connected.get("/api/ebay/policy-preview",
                       params={"international_shipping": "true"})
    assert on.status_code == 200, on.text
    assert "eBay International Shipping" in _where_to(on.json())["value"]
    assert on.json()["options"]["international_shipping"] is True

    off = connected.get("/api/ebay/policy-preview").json()
    assert "eBay International Shipping" not in _where_to(off)["value"]
    assert off["options"]["international_shipping"] is False


def test_the_create_makes_the_policy_the_preview_described(connected,
                                                           would_create):
    """The dialog echoes the previewed options back. The flag has to
    survive the round trip, or the seller agrees to one policy and gets
    another."""
    options = connected.get("/api/ebay/policy-preview",
                            params={"international_shipping": "1"}
                            ).json()["options"]
    resp = connected.post("/api/ebay/ensure-all-policies",
                          json={**options, "accept_terms": True})
    assert resp.status_code == 200, resp.text
    assert would_create["fulfillment"]["international_shipping"] is True


def test_a_create_that_did_not_ask_stays_domestic(connected, would_create):
    connected.post("/api/ebay/ensure-all-policies",
                   json={"accept_terms": True})
    assert would_create["fulfillment"]["international_shipping"] is False


@pytest.mark.parametrize("value", ["false", "0", "", None, "no", 0])
def test_nothing_short_of_yes_ships_abroad(connected, would_create, value):
    connected.post("/api/ebay/ensure-all-policies",
                   json={"accept_terms": True, "international_shipping": value})
    assert would_create["fulfillment"]["international_shipping"] is False


def test_the_single_service_route_carries_it_too(connected, would_create):
    resp = connected.post(
        "/api/ebay/ensure-policy",
        json={"service_code": "USPSPriority", "accept_terms": True,
              "international_shipping": True})
    assert resp.status_code == 200, resp.text
    assert would_create["fulfillment"] == {
        "svc": "USPSPriority", "international_shipping": True}


def test_the_switch_is_a_saved_preference():
    """It lives with the other new-listing defaults, as an on/off int, so
    the Settings screen's one Save carries it and the read is fail-closed
    like the rest."""
    from backend import main

    assert main._PREF_FIELDS["ebay_international_shipping"] == (int, 0, 1)
