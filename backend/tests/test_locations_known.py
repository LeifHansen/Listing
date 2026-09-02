""""No inventory locations found" is a claim about the seller's eBay account.

`account_overview` fetches the ship-from locations best-effort and swallows
every failure — a timeout, a 401, an eBay outage — into `[]`. The Settings
panel then renders that as "No inventory locations found."

It is the same shape as the `programs_known` tri-state sitting two fields
away, which exists precisely because "eBay said none" and "eBay didn't answer"
lead to different actions. Here the stakes are concrete: publishing needs a
ship-from location, so a seller told they have none goes to eBay and makes a
second one — for an account that already had it.

An empty list still means empty when eBay actually answered. What changes is
that the panel can now tell the two apart.
"""
from __future__ import annotations

import pytest

from backend import ebay_auth


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


@pytest.fixture()
def ebay(monkeypatch):
    """Answer only the locations call; everything else in the overview is
    stubbed out so this is about one field."""
    monkeypatch.setattr(ebay_auth, "list_business_policies",
                        lambda _t: {"fulfillment": [], "payment": [], "return": []})
    monkeypatch.setattr(ebay_auth, "opted_in_programs", lambda _t: set())
    monkeypatch.setattr(ebay_auth, "fetch_privileges", lambda _t: None)
    monkeypatch.setattr(ebay_auth, "fetch_payments_program", lambda _t: {})

    def _serve(answer):
        def _get(*_a, **_k):
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(ebay_auth.httpx, "get", _get)
        return ebay_auth.account_overview("tok")
    return _serve


def test_an_account_with_no_locations_says_so(ebay):
    out = ebay(_Resp(200, {"locations": []}))

    assert out["locations"] == []
    assert out["locations_known"] is True


def test_locations_eBay_returned_come_through(ebay):
    out = ebay(_Resp(200, {"locations": [{"merchantLocationKey": "HOME"}]}))

    assert out["locations_known"] is True
    assert out["locations"][0]["merchantLocationKey"] == "HOME"


def test_a_failed_lookup_is_not_an_account_with_no_locations(ebay):
    """The finding. Publishing needs a ship-from location, so a seller told
    they have none makes a second one on an account that already had it."""
    import httpx

    out = ebay(httpx.ConnectError("no route"))

    assert out["locations"] == []
    assert out["locations_known"] is False, \
        "a failed lookup was reported as an account with no locations"


def test_a_refused_lookup_is_unknown_too(ebay):
    """A 401 is eBay declining to answer, not answering 'none'."""
    out = ebay(_Resp(401))

    assert out["locations_known"] is False


def test_one_failing_section_still_leaves_the_others(ebay):
    """The whole point of the best-effort shape: a seller with no business
    policies still gets their locations, and vice versa."""
    out = ebay(_Resp(200, {"locations": [{"merchantLocationKey": "HOME"}]}))

    assert "policies" in out and "programs" in out
