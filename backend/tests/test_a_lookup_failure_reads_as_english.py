"""eBay's HTTP error is not a sentence the seller can act on.

P2-07 fixed this for the payments check and for Stripe. Four lookups the
editor makes on nearly every listing still handed the raw exception straight
into a toast:

    eBay Taxonomy API error: Client error '401 Unauthorized' for url
    'https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/get_category_
    suggestions?q=vintage+levis' For more information check:
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401

That is the deployment's API base, the exact call, the seller's own query and
an MDN link, in place of anything they could do about it. It is also the same
sentence for a rate limit, an expired application token and a network blip —
three different waits.

The detail is not discarded. It goes to the log under a short reference that
comes back in the message, which is the pattern the payments check and the
token checkout already use, so support can join the two.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

# What raise_for_status() actually produces, including the bits that must not
# reach a seller: the API host, the path, and the query they typed.
RAW = httpx.HTTPStatusError(
    "Client error '401 Unauthorized' for url "
    "'https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/"
    "get_category_suggestions?q=vintage+levis'\nFor more information check: "
    "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401",
    request=None, response=None)

LEAKS = ("api.ebay.com", "developer.mozilla.org", "401 Unauthorized",
         "category_tree", "get_category_suggestions")


@pytest.fixture
def client(monkeypatch):
    from backend import main
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_taxonomy_guard", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda *a, **k: "u1")
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda *a, **k: {"access_token": "tok", "_uid": "u1"})
    return main, TestClient(main.app, raise_server_exceptions=False)


def _boom(*a, **k):
    raise RAW


CASES = [
    ("/api/category-suggestions", {"query": "vintage levis"},
     ("taxonomy", "suggest")),
    ("/api/price-suggestions", {"query": "vintage levis"},
     ("pricing", "suggest")),
    ("/api/item-aspects", {"category_id": "11450"},
     ("taxonomy", "item_aspects")),
    ("/api/item-conditions", {"category_id": "11450"},
     ("taxonomy", "item_conditions")),
]


@pytest.mark.parametrize("path,body,target", CASES)
def test_ebays_http_error_does_not_reach_the_seller(client, monkeypatch,
                                                    path, body, target):
    main, api = client
    module, name = target
    monkeypatch.setattr(getattr(main, module), name, _boom)

    res = api.post(path, json=body)
    shown = res.text
    for leak in LEAKS:
        assert leak not in shown, f"{path} leaked {leak!r}: {shown[:200]}"


@pytest.mark.parametrize("path,body,target", CASES[:3])
def test_it_still_says_something_and_can_be_traced(client, monkeypatch,
                                                   path, body, target):
    """A message with nothing in it is not an improvement. Each answer names
    what could not be done and carries a reference the log also records."""
    main, api = client
    module, name = target
    monkeypatch.setattr(getattr(main, module), name, _boom)

    res = api.post(path, json=body)
    assert res.status_code == 502, res.status_code
    shown = res.json()["detail"]
    assert "couldn" in shown.lower() and "try again" in shown.lower()
    assert re.search(r"[0-9a-f]{8}", shown), (
        f"{path} carries no support reference: {shown[:200]}")


def test_the_conditions_lookup_says_it_could_not_check(client, monkeypatch):
    """This one fails SOFT on purpose — a category's conditions are an
    enhancement, not a blocker. But an empty list reads as "eBay puts no
    condition requirement here", and the editor would then offer conditions
    eBay rejects at publish time (error 25021, the reason this lookup
    exists). Same `checked` flag as the price lookup and the bell."""
    main, api = client
    monkeypatch.setattr(main.taxonomy, "item_conditions", _boom)

    res = api.post("/api/item-conditions", json={"category_id": "11450"})
    assert res.status_code == 200
    assert res.json() == {"conditions": [], "checked": False}


def test_a_working_conditions_lookup_says_it_checked(client, monkeypatch):
    main, api = client
    monkeypatch.setattr(main.taxonomy, "item_conditions",
                        lambda *a, **k: {"conditions": [{"id": "1000"}]})
    res = api.post("/api/item-conditions", json={"category_id": "11450"})
    assert res.status_code == 200
    assert res.json()["checked"] is True
    assert res.json()["conditions"] == [{"id": "1000"}]


def test_a_working_lookup_is_untouched(client, monkeypatch):
    main, api = client
    monkeypatch.setattr(main.taxonomy, "suggest", lambda *a, **k: {
        "query": "x", "tree_id": "0",
        "suggestions": [{"category_id": "1", "category_name": "A", "path": "A"}]})
    res = api.post("/api/category-suggestions", json={"query": "x"})
    assert res.status_code == 200
    assert res.json()["suggestions"][0]["category_id"] == "1"
