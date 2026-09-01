"""A seller's 501st ad is still an ad.

`active_ads_status` answers "which of this seller's listings already have a
running Promoted Listings ad", and the insights panel turns a NO into an
invitation to buy one — a percentage of the sale price, charged when the item
sells through the ad. test_promote_nudge_needs_an_answer covers the case where
eBay never answered. This covers the case where eBay answered, and we only
read part of it.

The lookup asked for each collection once:

    GET /ad_campaign?limit=500
    GET /ad_campaign/{id}/ad?limit=500

500 is eBay's maximum page size and LIST_CAP is 3,000, so a store with more
than 500 ads had everything past the 500th come back as unpromoted — with the
map still flagged as a reliable answer, which is exactly what lets the nudge
through. The seller was told to promote listings that were already promoted,
and charged twice if they did it.

A non-200 on one campaign's ads was the same failure wearing different
clothes: `continue` dropped that campaign's listings and kept every other
campaign's, so one rate-limited call out of ten read as "those aren't
promoted" rather than "we couldn't check".

The rule both halves come down to: the map is COMPLETE or the flag is False.
A collection read only partway is a question only partly asked, and it has to
reach the caller as one.
"""
from __future__ import annotations

import pytest

from backend.services import promotions, recommender


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.text = ""
        self._payload = payload or {}

    def json(self):
        return self._payload


class _eBay:
    """The seller's Marketing API, served one page at a time.

    Pages honour the `offset`/`limit` the caller sends, which is the whole
    point: a client that never advances the offset sees page one forever, and
    every test here would notice.
    """

    def __init__(self, campaigns, ads, fail_ads_at=None, report_total=True):
        self.campaigns = campaigns          # [{campaignId, campaignStatus}]
        self.ads = ads                      # {campaign_id: [ad, ...]}
        self.fail_ads_at = fail_ads_at or {}  # {campaign_id: offset} -> 500s
        self.report_total = report_total
        self.calls: list[tuple] = []        # (kind, campaign_id, offset)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def _page(self, rows, key, offset, limit):
        body = {key: rows[offset:offset + limit]}
        if self.report_total:
            body["total"] = len(rows)
        return _Resp(200, body)

    def get(self, url, headers=None, params=None):
        offset = int((params or {}).get("offset", 0))
        limit = int((params or {}).get("limit", 10))
        if url.endswith("/ad"):
            cid = url.rsplit("/", 2)[-2]
            self.calls.append(("ads", cid, offset))
            if self.fail_ads_at.get(cid) == offset:
                return _Resp(500)
            return self._page(self.ads.get(cid, []), "ads", offset, limit)
        self.calls.append(("campaigns", None, offset))
        return self._page(self.campaigns, "campaigns", offset, limit)


@pytest.fixture()
def seller(monkeypatch):
    """Run active_ads_status against one fake store. Returns (ads, known, api)."""
    def _run(api):
        promotions._ADS_CACHE.clear()
        monkeypatch.setattr(promotions.httpx, "Client", lambda *a, **k: api)
        found, known = promotions.active_ads_status(
            {"access_token": "tok-abcdefghijklmnop"})
        return found, known, api
    return _run


def _ads(n, first=1):
    """n running ads, on listings numbered from `first`."""
    return [{"listingId": str(first + i), "adStatus": "ACTIVE",
             "bidPercentage": "8.5"} for i in range(n)]


def _campaign(cid, status="RUNNING"):
    return {"campaignId": cid, "campaignStatus": status}


# ------------------------------------------------------------- the truncation

def test_the_ad_past_the_first_page_is_found(seller):
    """The finding. 501 running ads, and the 501st is promoted like the rest."""
    over = promotions._PAGE + 1
    found, known, _ = seller(_eBay([_campaign("c1")], {"c1": _ads(over)}))

    assert known is True
    assert len(found) == over
    assert str(over) in found, "everything past the first page read as unpromoted"


def test_the_seller_is_not_asked_to_pay_twice_for_that_listing(seller):
    """What the truncation cost, at the surface the seller sees: the promote
    nudge, on a listing already running an ad."""
    over = promotions._PAGE + 1
    found, known, _ = seller(_eBay([_campaign("c1")], {"c1": _ads(over)}))

    # The listing on the second page, matched the way _promoted_record_ids
    # matches it: our record, keyed by the eBay listing id its ad carries.
    item = {"id": "rec-501", "status": "published",
            "created_at": "2020-01-01T00:00:00",
            "listing": {"title": "Blue lamp", "price": 25.0,
                        "images": ["a", "b", "c"],
                        "ebay_listing_id": str(over)}}
    promoted = {item["id"]} if str(over) in found else set()

    kinds = [r["type"] for r in recommender.recommendations(
        [item], promoted_ids=promoted, promotion_known=known)]
    assert "promote" not in kinds


def test_the_offset_advances_a_page_at_a_time(seller):
    over = promotions._PAGE + 1
    _found, _known, api = seller(_eBay([_campaign("c1")], {"c1": _ads(over)}))

    assert [c for c in api.calls if c[0] == "ads"] == [
        ("ads", "c1", 0), ("ads", "c1", promotions._PAGE)]


def test_the_campaign_list_is_paged_too(seller):
    """Campaigns come back through the same collection endpoint, with the same
    cap. A seller past it would lose whole campaigns rather than whole pages."""
    many = [_campaign(f"c{i}") for i in range(promotions._PAGE + 1)]
    ads = {"c500": _ads(1, first=77)}  # an ad in the campaign past the cap
    found, known, _ = seller(_eBay(many, ads))

    assert known is True
    assert "77" in found


def test_the_reported_total_ends_the_paging(seller):
    """A full last page is not a reason to ask again — eBay says how many
    there are, and asking past the end is a wasted round trip on every
    dashboard load."""
    _found, _known, api = seller(
        _eBay([_campaign("c1")], {"c1": _ads(promotions._PAGE)}))

    assert [c for c in api.calls if c[0] == "ads"] == [("ads", "c1", 0)]


def test_a_short_page_ends_it_when_eBay_reports_no_total(seller):
    found, _known, api = seller(
        _eBay([_campaign("c1")], {"c1": _ads(3)}, report_total=False))

    assert len(found) == 3
    assert [c for c in api.calls if c[0] == "ads"] == [("ads", "c1", 0)]


# --------------------------------------------- a page that did not come back

def test_a_lost_second_page_is_not_a_complete_answer(seller):
    """Half an answer is not an answer. The first page's ads are real, but
    handing them over as the whole map means every listing on the lost page
    gets the nudge — the same fee, recommended on the same missing evidence."""
    over = promotions._PAGE + 1
    found, known, _ = seller(_eBay([_campaign("c1")], {"c1": _ads(over)},
                                   fail_ads_at={"c1": promotions._PAGE}))

    assert known is False
    assert found == {}


def test_a_campaign_we_cannot_read_does_not_leave_the_rest_looking_complete(seller):
    """The `continue` that used to sit here kept the other campaigns' ads and
    still reported the answer as reliable."""
    found, known, _ = seller(_eBay(
        [_campaign("c1"), _campaign("c2")],
        {"c1": _ads(2), "c2": _ads(2, first=50)},
        fail_ads_at={"c2": 0}))

    assert known is False
    assert found == {}


def test_a_partial_answer_is_not_cached(seller):
    """An outage must not be remembered as "this seller has no ads" for the
    whole TTL — the same rule active_ads_status already keeps for a lookup
    that failed outright."""
    seller(_eBay([_campaign("c1")], {"c1": _ads(promotions._PAGE + 1)},
                 fail_ads_at={"c1": promotions._PAGE}))

    assert promotions._ADS_CACHE == {}


def test_a_collection_with_no_end_stops_rather_than_running_forever(seller):
    """A page size that never shortens and a total that never arrives would
    page until the request outlives the gateway. It stops — and says it could
    not read them, rather than returning the part it got."""
    endless = _ads(promotions._PAGE * (promotions._MAX_PAGES + 2))
    found, known, api = seller(
        _eBay([_campaign("c1")], {"c1": endless}, report_total=False))

    assert known is False
    assert found == {}
    assert len([c for c in api.calls if c[0] == "ads"]) == promotions._MAX_PAGES


# ------------------------------------------------- what was already answered

def test_a_store_inside_one_page_still_answers_the_same(seller):
    """The paging is invisible to everyone it did not affect."""
    found, known, api = seller(_eBay([_campaign("c1")], {"c1": _ads(2)}))

    assert known is True
    assert set(found) == {"1", "2"}
    assert len(api.calls) == 2  # one campaign page, one ad page


def test_an_ended_campaigns_ads_are_still_not_read(seller):
    found, known, api = seller(_eBay([_campaign("c9", "ENDED")], {"c9": _ads(2)}))

    assert (found, known) == ({}, True)
    assert not [c for c in api.calls if c[0] == "ads"]
