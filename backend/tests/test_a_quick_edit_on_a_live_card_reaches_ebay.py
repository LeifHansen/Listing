"""A change made from a listing's card lands where its buyers see it.

The grid's cards could only be opened, not edited, once a listing was live:
the draft controls stayed off live cards because a number changed there would
leave this app and eBay disagreeing with nothing saying so. The card's Quick
edit panel closes that gap through one route, POST /api/listings/{id}/quick-edit:

  - a draft (or a Shop Mode find) is SAVED, and that is all;
  - a live listing is saved AND revised, through the same eBay provider the
    editor's Update uses, carrying only the fields that actually moved;
  - what the seller paid is saved and never offered to a marketplace;
  - nothing is written when the revise could not possibly go (eBay not
    connected, a listing with variations), and a change no marketplace took
    is PUT BACK — the card shows what the record holds, and a refused price
    left in the record is a card disagreeing with the listing it shows.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

LIVE = {
    "id": "lst1", "user_id": "u1", "status": "published",
    "listing": {"title": "Johnny O Featherweight Golf Polo", "price": 22.99,
                "quantity": 1, "condition": "USED_EXCELLENT", "brand": "Johnny O",
                "ebay_listing_id": "110011", "source": "ebay",
                "listing_format": "FIXED_PRICE"},
}


def _record(status="published", **listing):
    return {**LIVE, "status": status, "listing": {**LIVE["listing"], **listing}}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    """The route against an in-memory row and a stand-in eBay provider.

    `state["row"]` is the stored record; every upsert replaces its listing, so
    the re-read after a revise sees what was written. `state["published"]`
    collects each PublishContext the provider was handed, and `state["sent"]`
    the fields it was told to send — read at the call, since an accepted
    revise clears them."""
    from backend import config, db, main, marketplaces
    from backend.marketplaces.base import PublishOutcome

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    state = {"row": _record(), "saves": [], "published": [], "sent": [],
             "outcome": PublishOutcome(ok=True, status="published",
                                       message="Your eBay listing has been updated."),
             "creds": {"access_token": "tok"}}

    def get_listing(lid):
        row = state["row"]
        if lid != row["id"]:
            return None
        return {**row, "listing": dict(row["listing"])}

    def upsert(lid, data, status=None, **_k):
        state["saves"].append(dict(data))
        state["row"] = {**state["row"], "listing": dict(data),
                        "status": status or state["row"]["status"]}
        return True

    monkeypatch.setattr(db, "get_listing", get_listing)
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "upsert_listing", upsert)
    def mutate(lid, fn, status=None, **_k):
        if state.get("mutate_fails"):
            return None
        data = fn(dict(state["row"]["listing"]))
        state["row"] = {**state["row"], "listing": data,
                        "status": status or state["row"]["status"]}
        return data

    monkeypatch.setattr(db, "mutate_listing_data", mutate)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)

    ebay = marketplaces.get("ebay")

    def publish(ctx, creds):
        state["published"].append(ctx)
        state["sent"].append(list(ctx.listing.dirty_fields))
        if isinstance(state["outcome"], Exception):
            raise state["outcome"]
        if state["outcome"].ok:
            # What the real provider does on acceptance: the record and eBay
            # agree again, so nothing is left pending.
            ctx.listing.clear_dirty()
            upsert(ctx.session_id, ctx.listing.model_dump(), status="published")
        return state["outcome"]

    monkeypatch.setattr(ebay, "publish", publish)
    monkeypatch.setattr(ebay, "creds_for", lambda uid: state["creds"])
    return TestClient(main.app), state


def _edit(client, body):
    return client.post("/api/listings/lst1/quick-edit", json=body)


# --- a draft is saved, and that is all -------------------------------------

def test_a_draft_is_saved_and_never_sent(api):
    client, state = api
    state["row"] = _record(status="draft", ebay_listing_id="", source="")

    resp = _edit(client, {"title": "Johnny O Golf Polo Shirt Mens L"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["pushed"] == []
    assert state["published"] == [], "a draft was sent to eBay from its card"
    assert state["row"]["listing"]["title"] == "Johnny O Golf Polo Shirt Mens L"
    assert state["row"]["status"] == "draft"


def test_a_draft_keeps_every_field_it_did_not_name(api):
    """The card holds whatever /api/listings last loaded; writing that back is
    how an edit made in another tab is lost. Only named fields move."""
    client, state = api
    state["row"] = _record(status="draft", ebay_listing_id="", source="",
                           description="Fixed in the editor a minute ago")

    _edit(client, {"quantity": 3})

    saved = state["row"]["listing"]
    assert saved["quantity"] == 3
    assert saved["description"] == "Fixed in the editor a minute ago"
    assert saved["title"] == LIVE["listing"]["title"]


# --- a live listing is saved AND revised -----------------------------------

def test_a_live_price_change_goes_to_ebay_with_only_what_moved(api):
    client, state = api

    resp = _edit(client, {"price": 19.99, "title": LIVE["listing"]["title"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["pushed"] == ["ebay"]
    assert body["message"] == "Your eBay listing has been updated."
    [ctx] = state["published"]
    assert ctx.mode == "live"
    assert ctx.listing.price == 19.99
    # The title was named but did not move: sending it would push this app's
    # snapshot over whatever eBay has now.
    assert state["sent"] == [["price"]]


def test_the_answer_carries_the_record_as_the_revise_left_it(api):
    client, _ = api
    body = _edit(client, {"quantity": 4}).json()

    assert body["listing"]["quantity"] == 4
    assert body["listing"]["dirty_fields"] == [], \
        "an accepted revise still showed its fields as pending"
    assert body["status"] == "published"


def test_what_you_paid_is_saved_and_never_offered_to_ebay(api):
    client, state = api

    body = _edit(client, {"purchase_price": 4.5}).json()

    assert body["ok"] is True
    assert state["published"] == [], "the cost basis triggered a revise"
    assert state["row"]["listing"]["purchase_price"] == 4.5
    assert "purchase_price" not in state["row"]["listing"]["dirty_fields"]
    assert "never goes to eBay" in body["message"]


def test_a_stale_card_resending_ebays_own_value_sends_nothing(api):
    client, state = api

    body = _edit(client, {"price": 22.99}).json()

    assert body["ok"] is True
    assert body["pushed"] == []
    assert state["published"] == []
    assert body["message"] == "Nothing changed."


def test_a_refused_revise_is_reported_and_put_back(api):
    from backend.marketplaces.base import PublishOutcome

    client, state = api
    state["outcome"] = PublishOutcome(
        ok=False, message="The price is below the category minimum.",
        issues=[{"target": "price", "level": "error",
                 "title": "eBay won't take that price"}])

    resp = _edit(client, {"price": 0.5, "title": "Golf Polo, renamed"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False, "a refused revise was reported as a success"
    assert body["saved"] is False
    assert body["results"]["ebay"]["issues"][0]["title"] == "eBay won't take that price"
    assert body["message"] == "The price is below the category minimum."
    # The card shows what the record holds — and eBay is still at 22.99.
    row = state["row"]["listing"]
    assert row["price"] == 22.99
    assert row["title"] == LIVE["listing"]["title"]
    assert row["dirty_fields"] == []
    assert body["listing"]["price"] == 22.99


def test_a_refusal_that_could_not_be_put_back_says_the_change_is_saved(api):
    """Undoing is a write too. When it does not land, the refused change is
    what the record holds — still marked for the next revise — and the
    answer must not claim nothing changed."""
    from backend.marketplaces.base import PublishOutcome

    client, state = api
    state["outcome"] = PublishOutcome(ok=False, message="No.")
    state["mutate_fails"] = True

    body = _edit(client, {"price": 0.5}).json()

    assert body["ok"] is False
    assert body["saved"] is True
    assert state["row"]["listing"]["price"] == 0.5
    assert "price" in state["row"]["listing"]["dirty_fields"]


def test_try_again_resends_a_field_an_earlier_attempt_left_behind(api):
    """After a refusal the card already holds the new value, so the retry
    names a field that has not moved — and it must still go."""
    client, state = api
    state["row"] = _record(price=19.99, dirty_fields=["price"])

    body = _edit(client, {"price": 19.99}).json()

    assert body["pushed"] == ["ebay"]
    assert state["sent"] == [["price"]]


def test_a_revise_that_crashed_is_not_called_refused_or_done(api):
    client, state = api
    state["outcome"] = RuntimeError("socket closed")

    body = _edit(client, {"price": 18}).json()

    assert body["ok"] is False
    assert body["saved"] is True
    assert body["results"]["ebay"]["outcome_unknown"] is True
    assert "check the listing on eBay" in body["message"]
    # Not put back: it may well be on eBay already, and undoing it here would
    # leave the card disagreeing the other way.
    assert state["row"]["listing"]["price"] == 18


# --- refused before anything is written ------------------------------------

def test_nothing_is_saved_when_ebay_is_not_connected(api):
    client, state = api
    state["creds"] = None

    resp = _edit(client, {"price": 18})

    assert resp.status_code == 400, resp.text
    assert "Connect eBay first" in resp.json()["detail"]
    assert state["saves"] == []


def test_a_listing_with_variations_is_refused_whole(api):
    client, state = api
    state["row"] = _record(has_variations=True)

    resp = _edit(client, {"quantity": 5})

    assert resp.status_code == 409, resp.text
    assert "variations" in resp.json()["detail"]
    assert state["saves"] == []


@pytest.mark.parametrize("status", ["sold", "ended"])
def test_a_finished_listing_is_not_changed_from_its_card(api, status):
    client, state = api
    state["row"] = _record(status=status)

    resp = _edit(client, {"title": "Something else"})

    assert resp.status_code == 409, resp.text
    assert state["saves"] == []


@pytest.mark.parametrize("body, words", [
    ({"title": "   "}, "needs a title"),
    ({"title": "x" * 81}, "80 characters"),
    ({"price": -1}, "can't be negative"),
    ({"price": "abc"}, "has to be a number"),
    ({"price": None}, "needs a price"),
    ({"price": 0}, "above zero"),
    ({"quantity": 1.5}, "whole number"),
    ({"quantity": -2}, "whole number"),
    ({"condition": "like new!"}, "isn't one eBay knows"),
    ({"description": "not a quick-edit field"}, "Nothing to change"),
])
def test_a_value_the_seller_can_fix_is_refused_in_words(api, body, words):
    client, state = api

    resp = _edit(client, body)

    assert resp.status_code == 400, resp.text
    assert words in resp.json()["detail"]
    assert state["saves"] == []


def test_a_live_auction_gets_no_buy_it_now_from_its_card(api):
    """The revise writes `price` as <BuyItNowPrice> on an auction, so a number
    typed here would bolt a Buy It Now onto an auction that never had one."""
    client, state = api
    state["row"] = _record(listing_format="AUCTION", price=None,
                           auction_start_price=0.99)

    resp = _edit(client, {"price": 40})

    assert resp.status_code == 400, resp.text
    assert "highest bid" in resp.json()["detail"]
    assert state["saves"] == []


def test_a_stranger_cannot_quick_edit_someone_elses_listing(api, monkeypatch):
    from backend import main

    client, state = api
    monkeypatch.setattr(main, "_uid", lambda _r: "someone-else")

    resp = _edit(client, {"price": 1})

    assert resp.status_code == 404, resp.text
    assert state["saves"] == []


# --- every copy that exists, and never a new one ---------------------------

@pytest.fixture()
def etsy_on(monkeypatch):
    """Etsy offered by this deployment (it is launch-gated, and a marketplace
    the deployment withholds is never a target)."""
    from backend import config

    monkeypatch.setattr(config, "marketplace_enabled", lambda key: True)


def test_a_crossposted_listing_is_updated_everywhere_it_is_live(api, monkeypatch, etsy_on):
    from backend import main
    from backend.marketplaces.base import PublishOutcome

    client, state = api
    state["row"] = _record(marketplaces={
        "etsy": {"listing_id": "77", "status": "published"}})
    seen = {}

    def fan_out(session_id, listing, mode, targets, uid, base_url, prev):
        seen.update(targets=targets, mode=mode)
        return {"ebay": PublishOutcome(ok=True, status="published"),
                "etsy": PublishOutcome(ok=True, status="published")}

    monkeypatch.setattr(main, "_publish_targets", fan_out)

    body = _edit(client, {"price": 21}).json()

    assert seen == {"targets": ["ebay", "etsy"], "mode": "live"}
    assert body["ok"] is True
    assert body["message"] == "Updated on eBay and Etsy."


def test_a_change_one_marketplace_took_is_not_put_back(api, monkeypatch, etsy_on):
    from backend import main
    from backend.marketplaces.base import PublishOutcome

    client, state = api
    state["row"] = _record(marketplaces={
        "etsy": {"listing_id": "77", "status": "published"}})
    monkeypatch.setattr(main, "_publish_targets", lambda *a, **k: {
        "ebay": PublishOutcome(ok=True, status="published"),
        "etsy": PublishOutcome(ok=False, message="Etsy is down")})

    body = _edit(client, {"price": 21}).json()

    assert body["ok"] is False
    assert body["saved"] is True
    assert body["message"] == "Updated on eBay. Etsy didn't take it: Etsy is down"
    assert state["row"]["listing"]["price"] == 21


def test_a_copy_on_a_marketplace_this_deployment_withholds_is_left_alone(api):
    client, state = api
    state["row"] = _record(marketplaces={
        "etsy": {"listing_id": "77", "status": "published"}})

    body = _edit(client, {"price": 21}).json()

    assert body["pushed"] == ["ebay"]


def test_a_listing_live_only_on_etsy_is_never_created_on_ebay(api, monkeypatch, etsy_on):
    """"published" is the record's status whichever marketplace made it so;
    without an eBay item id, a live publish to eBay would be a CREATE."""
    from backend import main
    from backend.marketplaces.base import PublishOutcome

    client, state = api
    state["row"] = _record(ebay_listing_id="", source="", marketplaces={
        "etsy": {"listing_id": "77", "status": "published"}})
    seen = {}

    def fan_out(session_id, listing, mode, targets, uid, base_url, prev):
        seen["targets"] = targets
        return {"etsy": PublishOutcome(ok=False, message="Etsy is down")}

    monkeypatch.setattr(main, "_publish_targets", fan_out)

    body = _edit(client, {"title": "Mug, but renamed"}).json()

    assert seen["targets"] == ["etsy"]
    assert state["published"] == [], "eBay was asked to publish a listing it never had"
    assert body["ok"] is False
    assert body["message"] == "Etsy didn't take it: Etsy is down"
