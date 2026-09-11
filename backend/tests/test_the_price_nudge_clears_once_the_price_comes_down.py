"""The dashboard's "Lower prices" group, and the fact that ends it.

Reported as: *the lower the price recommendation doesn't clear once done.*

The group is a to-do list, and this one could not be crossed off. Both rules
that put a listing in it are computed from signals a price cut does not move:

  * the age heuristic counts from `created_at`, which never changes; and
  * the traffic one reads eBay's VIEW count, which is cumulative for the life
    of the listing. The thirty views that earned "buyers are looking; the
    price may be high" are still thirty views the second after the price comes
    down, and stay so for good.

So the seller pressed "Lower all…", sat through a dozen serial eBay revises,
was told "Lowered 12 prices by 10%" — and the group came back in the same
slot, same twelve listings, same count, asking for the same cut. From outside
that is not distinguishable from the button having done nothing, which is
exactly how it was reported. The same shape, and the same report, as the
"Fill in details" loop that `enriched_at` ended — see
test_fill_in_details_stops_asking.

`price_lowered_at` is the difference between "this price has sat" and "this
price was just cut". It is derived on every write from the price already
stored, never taken from the payload, so no tab and no forged body can either
erase it or mint one to silence advice a listing has earned.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.marketplaces import state as marketplace_state
from backend.services import recommender

QUIET = recommender.PRICE_QUIET_DAYS


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days)).isoformat(timespec="seconds")


def _live(created_days_ago: float = 60, **listing) -> dict:
    """A live listing old enough for the age heuristic to fire on it."""
    return {"id": "L1", "status": "published",
            "created_at": _ago(created_days_ago),
            "listing": {"title": "Vintage bowling trophy", "price": 40.0,
                        "images": ["1.jpg", "2.jpg", "3.jpg"], **listing}}


def _types(item: dict, metrics: dict = None) -> list[str]:
    return [r["type"] for r in recommender.recommend_for(item, metrics=metrics)]


def _price_recs(item: dict, metrics: dict = None) -> list[dict]:
    return [r for r in recommender.recommend_for(item, metrics=metrics)
            if r["type"] == "lower_price"]


LOOKERS = {"views": 40, "watchers": 0}   # fires the priority-92 traffic rule


# ------------------------------------------------------ the loop, as reported

def test_the_group_does_not_ask_again_for_a_cut_that_just_happened():
    """The seller took the advice. Views are cumulative, so the signal that
    asked for it is untouched — and asking again is the button not working."""
    assert "lower_price" in _types(_live(), LOOKERS), "the nudge has to fire first"
    assert "lower_price" not in _types(_live(price_lowered_at=_ago(0)), LOOKERS)


def test_the_age_heuristic_stops_too():
    """The other half of the same group, and the one that needs no eBay
    metrics at all: a listing marked down this morning is not a listing whose
    price has sat for sixty days."""
    assert "lower_price" in _types(_live())
    assert "lower_price" not in _types(_live(price_lowered_at=_ago(0)))


def test_a_listing_nobody_has_marked_down_still_nudges():
    """The quiet period is about the days AFTER a cut, not about price advice
    in general. A listing that has sat at one price is what the group is for."""
    assert "lower_price" in _types(_live(price_lowered_at=""))
    assert "lower_price" in _types(_live())


# ------------------------------------------------------------ and it comes back

def test_a_cut_that_did_not_work_earns_the_nudge_again():
    """Nothing is lost by waiting. Three weeks on, a listing still sitting
    there at the new price is exactly what this advice is for."""
    assert "lower_price" in _types(_live(price_lowered_at=_ago(QUIET + 1)))


def test_it_comes_back_worded_from_the_drop_not_the_birthday():
    """"Live 90 days" is true and useless on a listing marked down twice
    since: what the rule is actually about is how long the CURRENT price has
    been sitting there."""
    recs = _price_recs(_live(created_days_ago=90,
                             price_lowered_at=_ago(QUIET + 1)))
    assert len(recs) == 1
    assert "after the last price drop" in recs[0]["reason"]
    assert "90 days" not in recs[0]["reason"]


def test_the_clock_runs_from_the_last_cut_not_the_first():
    """A listing dropped two months ago and again this morning is quiet. The
    stamp is overwritten by each cut, so only the latest one counts."""
    assert "lower_price" not in _types(_live(price_lowered_at=_ago(0.5)))


def test_a_fresh_listing_marked_down_is_not_suddenly_stale():
    """The cut must not act as a way INTO the group: a listing two days old is
    not stale however recently its price moved."""
    assert "lower_price" not in _types(_live(created_days_ago=2,
                                             price_lowered_at=_ago(1)))


# --------------------------------------------------- only the price advice stops

def test_a_price_cut_silences_nothing_else():
    """It is evidence about the price and about nothing else. A listing short
    of photos still wants photos."""
    item = _live(price_lowered_at=_ago(0), images=["only-one.jpg"])
    assert "photos" in _types(item, LOOKERS)


def test_an_unreadable_stamp_leaves_the_advice_standing():
    """Absence of a date this app can read is not evidence of a price cut —
    and a garbled one must not silence the group for ever."""
    assert "lower_price" in _types(_live(price_lowered_at="last tuesday"))


# ------------------------------------------------------------------- the stamp

def test_a_cut_is_stamped_and_a_rise_is_not():
    """Only the advice being TAKEN stops it being given. A seller who puts a
    price up has not done the thing the group asked for."""
    stored = {"price": 40.0, "price_lowered_at": ""}
    assert recommender.price_drop_stamp(stored, 29.99)
    assert recommender.price_drop_stamp(stored, 49.99) == ""
    assert recommender.price_drop_stamp(stored, 40.0) == ""


def test_a_save_that_leaves_the_price_alone_keeps_the_stamp():
    """Most saves are a title fix or a photo swap. None of them is a fresh cut
    and none of them may lose the one already recorded."""
    held = _ago(3)
    stored = {"price": 40.0, "price_lowered_at": held}
    assert recommender.price_drop_stamp(stored, 40.0) == held


def test_the_stamp_is_read_off_the_stored_record_not_the_payload():
    """It is not a client's to send. A second tab holding a copy from before
    this morning's markdown would otherwise blank it and put the listing
    straight back into the group — and a body could mint one to silence advice
    the listing has earned. Neither is even consulted: the only inputs are the
    stored record and the new price."""
    held = _ago(3)
    stored = {"price": 40.0, "price_lowered_at": held}
    assert recommender.price_drop_stamp(stored, 40.0) == held      # no erasing
    stamped = recommender.price_drop_stamp({"price": 40.0}, 29.99)  # no minting
    assert stamped and stamped != _ago(-500)


def test_a_record_with_no_usable_price_is_not_a_cut():
    """A draft that has never been priced, or a blank where a number should
    be. "Lower than nothing" is not a markdown."""
    assert recommender.price_drop_stamp({}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": None}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": "forty"}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": 40.0}, None) == ""


# ------------------------------------------------- what keeps the stamp alive

def test_the_stamp_is_not_restored_from_the_stored_copy_on_publish():
    """state.SERVER_OWNED_FIELDS means "the STORED value wins over whatever
    arrived" — which is right for an item id and fatal here. Every path that
    lowers a price does it by loading the stored record, changing the price
    and publishing; under that rule the publish would hand the listing back
    the OLD stamp it was just moved past, and the second markdown on any
    listing would go unrecorded.

    The field is protected a stronger way instead: it is derived from the
    stored price on every write (price_drop_stamp) and the payload's copy is
    never read, so there is nothing for this list to defend.
    """
    assert "price_lowered_at" not in marketplace_state.SERVER_OWNED_FIELDS


# ------------------------------------------------------ end to end, as pressed

@pytest.fixture()
def seller(dbmod, monkeypatch):
    """A signed-in seller with one live listing and eBay saying yes."""
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from fastapi.testclient import TestClient

    from backend import main, ratelimit

    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "prices@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("prices@example.com")["id"]
    assert dbmod.upsert_listing(
        "L1", {"title": "Vintage bowling trophy", "price": 40.0,
               "images": ["1.jpg", "2.jpg", "3.jpg"],
               "ebay_listing_id": "1234567890"},
        status="published", user_id=uid)

    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {"access_token": "t"})
    # Plenty of lookers, no watchers — the signal the group is built from, and
    # the one a price cut cannot move: these are the views this listing has
    # had since the day it went up.
    monkeypatch.setattr(main, "_metrics_by_record_id",
                        lambda creds, items, status=None, fresh=False:
                        {"L1": {"views": 40, "watchers": 0}})

    class _AcceptingEbay:
        """eBay accepting the revise, and storing the result the way the real
        provider does — which is the step that has to carry the stamp."""

        def publish(self, ctx, creds):
            from backend.marketplaces.base import PublishOutcome
            dbmod.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                                 status="published", user_id=uid)
            return PublishOutcome(ok=True, message="Revised.", status="published")

    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())
    return client, dbmod


def _price_group(client) -> list[dict]:
    r = client.get("/api/insights")
    assert r.status_code == 200, r.text
    return [rec for rec in r.json()["recommendations"]
            if rec["type"] == "lower_price"]


def test_pressing_lower_all_actually_clears_the_group(seller):
    """The whole report, start to finish: the group is there, the seller takes
    its advice with the button the group itself carries, and the group is
    gone."""
    client, dbmod = seller
    assert _price_group(client), "the suggestion has to be offered first"

    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": ["L1"]})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1, r.text

    stored = dbmod.get_listing("L1")["listing"]
    assert stored["price"] < 40.0, "the cut has to have landed"
    assert stored["price_lowered_at"], "the cut has to have been recorded"
    assert not _price_group(client), \
        "the group came back with the listing the seller just marked down"


def test_a_price_edit_from_a_card_counts_as_taking_the_advice(seller):
    """The bulk button is one way to do it. Typing a lower number on the card
    is another, and the group has to stop asking either way."""
    client, dbmod = seller
    assert _price_group(client)

    r = client.patch("/api/listings/L1", json={"price": 29.99})
    assert r.status_code == 200, r.text
    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"]
    assert not _price_group(client)


def test_a_price_edit_saved_from_the_editor_counts_too(seller):
    """And the third way: the full editor save."""
    client, dbmod = seller
    assert _price_group(client)

    listing = dict(dbmod.get_listing("L1")["listing"])
    listing["price"] = 24.99
    r = client.post("/api/save/L1", json=listing)
    assert r.status_code == 200, r.text
    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"]
    assert not _price_group(client)


def test_a_later_save_does_not_lose_the_stamp(seller):
    """The save path derives the stamp from the STORED record, so a save that
    does not touch the price carries the existing one forward — including one
    sent by a tab that has never heard of it."""
    client, dbmod = seller
    assert client.patch("/api/listings/L1", json={"price": 29.99}).status_code == 200
    stamped = dbmod.get_listing("L1")["listing"]["price_lowered_at"]
    assert stamped

    stale = dict(dbmod.get_listing("L1")["listing"])
    stale["title"] = "Vintage bowling trophy, 1962"
    stale["price_lowered_at"] = ""          # the tab that loaded this morning
    assert client.post("/api/save/L1", json=stale).status_code == 200

    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"] == stamped
    assert not _price_group(client)
