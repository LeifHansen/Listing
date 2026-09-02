"""Settings is the screen whose whole job is to say what you have saved.

Two routes on that screen still answered a broken database the way they
answer an ordinary one.

`GET /api/prefs` returned `{"prefs": {}}` with a **200** when the read threw,
because `db.get_prefs` swallowed the exception. The browser is fully prepared
for the other answer -- `SettingsView` keeps a `prefsError` and renders *"We
couldn't load your saved defaults (…), so nothing is shown here — this isn't
what you have saved"* with a retry -- and that guard could never fire, because
the server never let it. What the seller saw instead was the app's fallback
weight, dimensions, quantity, condition and pricing strategy, presented as
their own settings. The same panel's own comment says why that matters: it had
already been fixed for the two sections beside it.

Then it gets expensive. `POST /api/prefs` **merges**, so a seller who looks at
those fallbacks, changes one field and presses Save writes the whole fallback
set over their real defaults -- the package weight they measured, the ship-from
address, the pricing strategy -- because one read failed a minute earlier.

And the save had the same hole from the other end. `db.save_prefs` returned
`{}` on any exception, and the route only refused when `db.enabled()` was
False -- a database that is *configured but broken* took that branch, so a
write that never landed came back `{"ok": true}`. That is P0-06's shape,
still live on this route.

Both are storage failures answering with the shape of a real, empty answer.
The rule this branch keeps: a read that could not run is not "you have
nothing", and a write that did not land is not "saved".
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main
from backend import ratelimit

SAVED = {"package_weight_lb": 3, "pricing_strategy": "median"}


@pytest.fixture()
def seller(dbmod):
    """A signed-in seller with defaults already saved.

    `dbmod` binds the db module to a scratch SQLite file with the real schema,
    so the prefs really round-trip rather than passing through a double.
    """
    assert dbmod.enabled()
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "prefs@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    assert client.post("/api/prefs", json=SAVED).status_code == 200
    return client


def _break(monkeypatch, name: str) -> None:
    """Make one db call answer the way it should when storage is down.

    Raising the typed failure rather than a bare exception on purpose. These
    are route-level guards: they pin that the route does not add a swallow of
    its own -- several on this branch did -- and they pass today only because
    it has none. The bug itself is one layer down, in the db call that never
    raised at all, and is asserted against a broken Session at the bottom of
    this file.
    """
    def boom(*_a, **_k):
        raise errors.StorageUnavailable(
            "We couldn’t reach your settings just now. Try again in a moment.")
    monkeypatch.setattr(main.db, name, boom)


def test_defaults_we_could_not_read_are_not_reported_as_empty(seller, monkeypatch):
    _break(monkeypatch, "get_prefs")
    r = seller.get("/api/prefs")
    assert r.status_code == 503, (
        f"a failed prefs read answered {r.status_code} with {r.text[:120]} — "
        "the browser reads that as 'you have no saved defaults'")


def test_the_seller_is_told_to_try_again_rather_than_what_broke(seller, monkeypatch):
    _break(monkeypatch, "get_prefs")
    detail = seller.get("/api/prefs").json().get("detail", "")
    assert "connection reset" not in detail
    assert "try again" in detail.lower()


def test_a_save_that_did_not_land_is_not_reported_as_saved(seller, monkeypatch):
    _break(monkeypatch, "save_prefs")
    r = seller.post("/api/prefs", json={"package_weight_lb": 5})
    assert r.status_code >= 500, (
        f"a failed prefs write answered {r.status_code} {r.text[:120]} — "
        "the seller closes Settings believing it saved")


def test_what_was_already_saved_is_still_there_afterwards(seller, monkeypatch):
    """The point of refusing: nothing was overwritten on the way past."""
    real = main.db.save_prefs
    _break(monkeypatch, "save_prefs")
    seller.post("/api/prefs", json={"package_weight_lb": 5})
    # Restore just this one, not the whole fixture: `dbmod` monkeypatches the
    # database URL, and undoing everything would point the read below at a
    # different database than the write it is checking.
    monkeypatch.setattr(main.db, "save_prefs", real)

    prefs = seller.get("/api/prefs").json()["prefs"]
    assert prefs["package_weight_lb"] == 3
    assert prefs["pricing_strategy"] == "median"


def test_a_working_read_still_answers_normally(seller):
    r = seller.get("/api/prefs")
    assert r.status_code == 200
    assert r.json()["prefs"]["package_weight_lb"] == 3


def test_a_seller_with_no_defaults_yet_still_gets_an_empty_answer(dbmod):
    """The other half. An empty prefs dict is a real answer for a new account,
    and turning it into an error would break every first visit to Settings."""
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "fresh@example.com",
                             "password": "password123"}).status_code < 400
    r = client.get("/api/prefs")
    assert r.status_code == 200
    assert r.json()["prefs"] == {}


def test_publishing_still_works_when_the_prefs_read_is_down(seller, monkeypatch):
    """The reads that legitimately tolerate a blank answer must keep doing so.

    `_load_prefs` fills in draft defaults and picks a pricing strategy; a
    seller who cannot reach their saved weight should get a draft with no
    weight, not a failed identify. Making get_prefs raise would take those
    down too if the call sites had not been moved to the best-effort variant.
    """
    _break(monkeypatch, "get_prefs")
    assert main._load_prefs("someone") == {}
    assert main._pricing_strategy("someone") == ""


def test_the_best_effort_variant_says_what_it_is():
    assert hasattr(main.db, "get_prefs_best_effort")
    assert main.db.get_prefs_best_effort("nobody") == {}


def test_the_allow_offers_switch_round_trips(seller):
    """The Settings toggle is only a toggle if the answer comes back on.

    It also has to come back OFF once turned off: `save_prefs` merges, so a
    field that is dropped rather than stored as 0 would leave the seller
    unable to switch offers back off at all.
    """
    assert seller.post("/api/prefs", json={"allow_offers": 1}).status_code == 200
    assert seller.get("/api/prefs").json()["prefs"]["allow_offers"] == 1
    assert seller.post("/api/prefs", json={"allow_offers": 0}).status_code == 200
    assert seller.get("/api/prefs").json()["prefs"]["allow_offers"] == 0


def test_a_broken_prefs_read_does_not_allow_offers(monkeypatch):
    """An outage is not a decision to list the seller's items open to
    negotiation, any more than it is consent to an ad fee."""
    from backend.services import listing_sync

    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(listing_sync.db, "get_prefs", boom)
    assert listing_sync.offers_enabled("someone") is False


def test_a_broken_prefs_read_does_not_promote_anything(monkeypatch):
    """An outage is never consent, least of all to a per-sale ad fee."""
    from backend.marketplaces import ebay_provider

    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(ebay_provider.db, "get_prefs", boom)
    assert ebay_provider.auto_promote_enabled("someone") is False


def test_the_read_itself_stops_swallowing_the_failure(dbmod, monkeypatch):
    """Where the bug actually lived.

    `get_prefs` caught everything and returned `{}` -- indistinguishable from
    a new account -- so the route above had nothing to propagate and the
    browser’s prepared error state could never fire. Broken here at the
    Session, which is where a real outage surfaces.
    """
    def boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")
    monkeypatch.setattr(dbmod, "Session", boom)

    with pytest.raises(errors.StorageUnavailable):
        dbmod.get_prefs("someone")
    with pytest.raises(errors.StorageUnavailable):
        dbmod.save_prefs("someone", {"package_weight_lb": 2})
