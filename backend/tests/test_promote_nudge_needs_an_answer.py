""""Not promoted yet" is a claim, and it costs money to act on.

The insights panel nudges a seller to buy a Promoted Listings ad — a
percentage of the sale price when the item sells through it — whenever the
listing does not look promoted. "Looks promoted" comes from two places: this
app's own flag, and eBay's live ad list, which is what catches an ad the
seller created in Seller Hub.

`promotions.active_ads` answered `{}` both when eBay said "no ads" and when
the lookup failed. So during an ads-API blip, a seller who promotes in Seller
Hub was told "Not promoted yet — promoted listings show up far more often"
about listings that were already running ads, and invited to pay for a second
one.

That is the P1-06 rule from the other side: a fee must not be recommended on
the strength of a question nobody managed to ask.

Note what this deliberately does NOT do: when the answer is unknown it drops
the promote nudge entirely rather than guessing either way. A seller who has
not granted the ads scope cannot be promoted by this app at all, so not
suggesting it is right rather than a loss.
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


class _Client:
    def __init__(self, answer):
        self._answer = answer

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get(self, url, **_k):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


@pytest.fixture()
def ads(monkeypatch):
    def _serve(answer):
        promotions._ADS_CACHE.clear()
        monkeypatch.setattr(promotions.httpx, "Client",
                            lambda *a, **k: _Client(answer))
        return promotions.active_ads_status({"access_token": "tok-abcdefghijklmnop"})
    return _serve


# ------------------------------------------------------------ the tri-state

def test_eBay_saying_no_campaigns_is_a_real_answer(ads):
    found, known = ads(_Resp(200, {"campaigns": []}))

    assert found == {}
    assert known is True


def test_a_failed_lookup_is_not_an_answer(ads):
    """The finding."""
    found, known = ads(RuntimeError("eBay is down"))

    assert found == {}
    assert known is False


def test_a_refused_lookup_is_not_an_answer_either(ads):
    """A 403 is eBay declining — most often the ads scope was never granted —
    and that is precisely when this app cannot promote anything anyway."""
    found, known = ads(_Resp(403))

    assert known is False


def test_no_credentials_is_not_an_answer(ads):
    assert promotions.active_ads_status(None) == ({}, False)


def test_the_lenient_wrapper_still_answers_a_plain_map(ads):
    """`active_ads` keeps its shape for anything that only wants the map."""
    promotions._ADS_CACHE.clear()
    assert promotions.active_ads(None) == {}


# ------------------------------------------------- and the nudge respects it

def _live(**over) -> dict:
    listing = {"title": "Blue lamp", "price": 25.0, "images": ["a", "b", "c"]}
    listing.update(over)
    return {"id": "l1", "status": "published", "created_at": "2020-01-01T00:00:00",
            "listing": listing}


def _kinds(**kw) -> list[str]:
    recs = recommender.recommendations([_live()], **kw)
    return [r["type"] for r in recs]


def test_a_listing_eBay_confirms_is_unpromoted_is_still_nudged():
    assert "promote" in _kinds(promoted_ids=set(), promotion_known=True)


def test_a_listing_eBay_confirms_IS_promoted_is_not_nudged():
    assert "promote" not in _kinds(promoted_ids={"l1"}, promotion_known=True)


def test_nothing_is_nudged_when_we_could_not_check():
    """The finding, at the surface: an ads outage used to read as "not
    promoted yet" and invite the seller to pay for a second ad."""
    assert "promote" not in _kinds(promoted_ids=set(), promotion_known=False)


def test_the_other_recommendations_survive_an_ads_outage():
    """Only the FEE-bearing one is suppressed. Dropping the rest would turn a
    third-party outage into an empty insights panel.

    Asserted as "something that is not promote", not as a named rec:
    `recommendations` keeps only the strongest action per listing, so pinning
    a particular one here would be testing the ranking, not this rule."""
    recs = recommender.recommendations(
        [_live(images=["a"])], promoted_ids=set(), promotion_known=False)

    kinds = [r["type"] for r in recs]
    assert kinds, "an ads outage emptied the whole insights panel"
    assert "promote" not in kinds


def test_the_metrics_driven_promote_nudge_is_gated_too():
    """"Only 2 views in 30 days — promote it" is the same purchase, arrived at
    from the traffic numbers instead of the age heuristic."""
    kinds = _kinds(metrics_by_id={"l1": {"views": 2, "watchers": 0}},
                   promoted_ids=set(), promotion_known=False)

    assert "promote" not in kinds


def test_an_unspecified_caller_gets_the_old_behaviour():
    """`promotion_known` defaults to True so nothing that does not pass it
    silently loses its promote recommendations."""
    assert "promote" in _kinds(promoted_ids=set())
