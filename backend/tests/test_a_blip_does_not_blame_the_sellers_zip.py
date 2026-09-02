"""A network failure is not eBay refusing the seller's postal code.

Saving a ship-from ZIP calls eBay to create or repair the app's inventory
location. Everything that could go wrong there was caught as one thing:

    except Exception as exc:
        raise HTTPException(400, f"eBay rejected that ship-from location: {exc}")

A 400 is a claim about what the seller typed, and it is the claim the UI acts
on — the field goes red and the ZIP is what they will change. But eBay
refusing a postal code and the request never reaching eBay are different
answers, and only the first is about the ZIP. The second is a 503: nothing is
wrong with the input and retrying is the right next move.

The raw exception went into the sentence too, so an HTTPStatusError put the
API base, the path and the status line in front of the seller — the same P2-07
shape as the taxonomy lookups.

The same distinction, one route over: creating a business policy caught
`httpx.HTTPError` and pasted it in. eBay's own refusals there already answer
through AccountApiError with eBay's description, so the transport case is the
only one being reworded.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

URL_LEAK = ("Client error '401 Unauthorized' for url "
            "'https://api.ebay.com/sell/inventory/v1/location/thryft-loc-1'")


@pytest.fixture
def client(monkeypatch):
    from backend import main
    monkeypatch.setattr(main, "_uid", lambda *a, **k: "u1")
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda *a, **k: {"access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main.db, "save_ebay_account", lambda *a, **k: True)
    return main, TestClient(main.app, raise_server_exceptions=False)


def _location_raises(main, monkeypatch, exc):
    def _boom(*a, **k):
        raise exc
    monkeypatch.setattr(main.ebay_auth, "ensure_inventory_location", _boom)


def test_a_lost_connection_is_not_a_bad_postal_code(client, monkeypatch):
    main, api = client
    _location_raises(main, monkeypatch, httpx.ConnectError("connection refused"))

    res = api.post("/api/ebay/policies", json={"ship_from_postal": "97214"})
    assert res.status_code == 503, f"answered {res.status_code}: {res.text[:200]}"
    assert "reject" not in res.text.lower(), (
        "a network failure was reported as eBay refusing the ZIP")


def test_ebay_actually_refusing_the_zip_is_still_the_sellers_to_fix(
        client, monkeypatch):
    main, api = client
    _location_raises(main, monkeypatch, main.ebay_auth.AccountApiError(
        "Invalid postal code.", description="Invalid postal code."))

    res = api.post("/api/ebay/policies", json={"ship_from_postal": "00000"})
    assert res.status_code == 400
    assert "postal" in res.text.lower()


def test_the_message_never_carries_ebays_url(client, monkeypatch):
    main, api = client
    # The message ends with a support reference, and that reference is
    # `secrets.token_hex(4)` -- eight random hex characters. "401" is three of
    # them, so roughly one run in seven hundred drew a reference CONTAINING
    # the status code this test is looking for and failed on its own id:
    # "quote 58b04401 to support" is not eBay's 401 leaking, it is a coin
    # landing badly. Pinned to a reference with no hex run that could collide,
    # so the assertion below tests the one thing it is about -- that eBay's
    # status line stayed out of the sentence -- and tests it every time.
    monkeypatch.setattr(main, "_support_reference", lambda: "deadbeef")
    _location_raises(main, monkeypatch,
                     httpx.HTTPStatusError(URL_LEAK, request=None, response=None))

    res = api.post("/api/ebay/policies", json={"ship_from_postal": "97214"})
    assert "api.ebay.com" not in res.text, res.text[:200]
    assert "401" not in res.text, res.text[:200]


def test_a_working_save_is_untouched(client, monkeypatch):
    main, api = client
    monkeypatch.setattr(main.ebay_auth, "ensure_inventory_location",
                        lambda *a, **k: "thryft-loc-1")
    res = api.post("/api/ebay/policies", json={"ship_from_postal": "97214"})
    assert res.status_code == 200
    assert res.json()["selected"]["ship_from_postal"] == "97214"
