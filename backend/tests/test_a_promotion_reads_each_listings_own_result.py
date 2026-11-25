"""A promoted listing's ad is judged by eBay's answer FOR THAT LISTING.

The bulk ad calls answer per listing inside the body,
{"responses": [{"listingId", "statusCode", "adId", "errors"}]}, and a 207
Multi-Status can carry a refusal. Success used to be "the body does not
contain the word error" -- so a success that carried an empty "errors" list
was taken for a refusal (and sent on to the bid-update call, for an ad that
did not exist yet), and the listing's own statusCode was never read.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import promotions


class _Client:
    def __init__(self, answers):
        self.answers = list(answers)
        self.posts: list[str] = []

    def post(self, url, json=None, **kw):
        self.posts.append(url.rsplit("/", 1)[-1])
        status, body = self.answers.pop(0)
        return httpx.Response(status, request=httpx.Request("POST", url), json=body)


def _run(client):
    return promotions._create_or_update_ad_by_listing(
        client, "https://api.ebay.test", "tok", "902", "110001", 5.0)


def test_a_success_with_an_empty_errors_list_is_a_success():
    client = _Client([(207, {"responses": [
        {"listingId": "110001", "statusCode": 201, "adId": "ad-1", "errors": []}]})])
    assert _run(client) == {"ok": True}
    assert client.posts == ["bulk_create_ads_by_listing_id"], "no bid update for a new ad"


def test_a_refusal_inside_a_207_moves_the_existing_ads_bid():
    client = _Client([
        (207, {"responses": [{"listingId": "110001", "statusCode": 409,
                              "errors": [{"errorId": 35036, "message": "already exists"}]}]}),
        (207, {"responses": [{"listingId": "110001", "statusCode": 200}]}),
    ])
    assert _run(client) == {"ok": True, "updated": True}
    assert client.posts == ["bulk_create_ads_by_listing_id",
                            "bulk_update_ads_bid_by_listing_id"]


def test_a_listing_refused_by_both_calls_is_reported():
    refused = (207, {"responses": [{"listingId": "110001", "statusCode": 400,
                                    "errors": [{"errorId": 35045}]}]})
    out = _run(_Client([refused, refused]))
    assert out["ok"] is False and "35045" in out["detail"]


@pytest.mark.parametrize("body, expected", [
    ({"responses": [{"statusCode": 201, "adId": "a"}]}, True),
    ({"responses": [{"adId": "a"}]}, True),                       # no statusCode: not a refusal
    ({"responses": [{"statusCode": 201, "warnings": [{}]}]}, True),
    ({"responses": [{"statusCode": "201"}]}, False),              # not the documented type
    ({"responses": [{"statusCode": 201, "errors": [{"errorId": 1}]}]}, False),
    ({"something": "else"}, True),                                # unfamiliar shape: the old test
    ({"something": "an error"}, False),
])
def test_what_counts_as_the_listing_having_its_ad(body, expected):
    resp = httpx.Response(207, request=httpx.Request("POST", "https://x"), json=body)
    assert promotions._bulk_ad_ok(resp, (200, 201, 207)) is expected


def test_a_status_outside_the_ones_that_mean_done_is_never_a_success():
    resp = httpx.Response(500, request=httpx.Request("POST", "https://x"),
                          json={"responses": [{"statusCode": 201}]})
    assert promotions._bulk_ad_ok(resp, (200, 201, 207)) is False
