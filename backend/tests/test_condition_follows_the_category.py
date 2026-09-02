"""The category decides which conditions exist, so it decides the condition.

eBay does not offer one condition ladder. "Very Good", "Good" and
"Acceptable" (4000/5000/6000) exist only in media categories; the pre-owned
grades (2990/3000/3010) only across Pre-loved Apparel; most of the rest of the
site offers a bare "Used". A grade the category doesn't offer is not a warning
at publish time — it comes back as error 25021 and no listing at all.

That is what happened: the AI graded the wear it could see ("used, some
wear" -> USED_GOOD) with no idea where the item would be filed, nothing
between it and eBay ever asked, and three of four listings in a bulk batch —
a bone fish figurine, a ceramic bear, a graphic t-shirt — were refused for a
condition that was never on offer in Fish, Kitchen Tools or Clothing.

So a draft now leaves identify carrying a condition its own category accepts,
and the checklist says so before eBay has to.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import preflight, taxonomy

# What eBay answers for a Pre-loved Apparel category: three ways to say new,
# three pre-owned grades, and nothing else. Note what is NOT here — the very
# grade an AI reaches for first.
APPAREL = ["NEW", "NEW_OTHER", "NEW_WITH_DEFECTS",
           "PRE_OWNED_EXCELLENT", "USED_EXCELLENT", "PRE_OWNED_FAIR"]

# The far more common answer: new, refurbished, one plain "Used", parts.
# Fish figurines and kitchen gadgets both live here.
PLAIN = ["NEW", "NEW_OTHER", "SELLER_REFURBISHED", "USED_EXCELLENT",
         "FOR_PARTS_OR_NOT_WORKING"]


# ------------------------------------------------------- the substitution


@pytest.mark.parametrize("allowed", [APPAREL, PLAIN])
def test_used_good_becomes_the_nearest_used_grade_not_the_first_option(allowed):
    """The bug this file exists for, and the trap in fixing it.

    Snapping to the first allowed condition — which is what the editor did —
    turns a worn t-shirt into "New". The replacement has to be the closest
    grade in the same family, which in both of these categories is the one
    eBay shows as "Used"/"Pre-owned - Good" (3000).
    """
    assert taxonomy.nearest_allowed_condition("USED_GOOD", allowed) == "USED_EXCELLENT"


def test_the_apparel_grades_are_reachable():
    """Wear the AI grades as like-new or beaten up lands on eBay's own
    apparel wording rather than being flattened into the middle grade."""
    assert taxonomy.nearest_allowed_condition("LIKE_NEW", APPAREL) == "PRE_OWNED_EXCELLENT"
    assert taxonomy.nearest_allowed_condition("USED_ACCEPTABLE", APPAREL) == "PRE_OWNED_FAIR"
    assert taxonomy.nearest_allowed_condition("FOR_PARTS_OR_NOT_WORKING",
                                              APPAREL) == "PRE_OWNED_FAIR"


def test_a_used_item_is_never_relabelled_new():
    """The one substitution that must never happen. A category offering only
    New has no honest home for a worn item, so the answer is "we can't fit
    this" — the seller picks a different category, and the checklist says so.
    """
    assert taxonomy.nearest_allowed_condition("USED_GOOD", ["NEW"]) is None


def test_a_new_item_is_never_quietly_used():
    assert taxonomy.nearest_allowed_condition("NEW", ["USED_EXCELLENT"]) is None
    # ...but it does move between the ways of saying new.
    assert taxonomy.nearest_allowed_condition(
        "NEW_WITH_DEFECTS", ["NEW", "NEW_OTHER"]) == "NEW_OTHER"


def test_an_allowed_condition_is_left_exactly_as_it_is():
    for cond in APPAREL:
        assert taxonomy.nearest_allowed_condition(cond, APPAREL) == cond


def test_no_answer_from_ebay_changes_nothing():
    """An empty list is "we could not ask", never "anything goes"."""
    assert taxonomy.nearest_allowed_condition("USED_GOOD", []) == "USED_GOOD"


def test_ties_go_to_the_lower_grade():
    """Understating wear costs a few dollars; overstating it costs the sale
    and the feedback. PRE_OWNED_EXCELLENT sits exactly between LIKE_NEW and
    USED_EXCELLENT, so it must fall to the lower of the two."""
    assert taxonomy.nearest_allowed_condition(
        "PRE_OWNED_EXCELLENT", ["LIKE_NEW", "USED_EXCELLENT"]) == "USED_EXCELLENT"


# ------------------------------------------------- eBay's list, read whole


def test_the_apparel_condition_ids_are_not_dropped(monkeypatch):
    """`item_conditions` names each id eBay returns, and an id it cannot name
    disappears from the seller's choices. Without 2990/3010 a clothing
    category offered three flavours of New and one Used — while eBay was
    offering three pre-owned grades."""
    body = {"itemConditionPolicies": [{"itemConditions": [
        {"conditionId": "1000", "conditionDescription": "New with tags"},
        {"conditionId": "1500", "conditionDescription": "New without tags"},
        {"conditionId": "1750", "conditionDescription": "New with defects"},
        {"conditionId": "2990", "conditionDescription": "Pre-owned - Excellent"},
        {"conditionId": "3000", "conditionDescription": "Pre-owned - Good"},
        {"conditionId": "3010", "conditionDescription": "Pre-owned - Fair"},
    ]}]}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return body

    monkeypatch.setattr(taxonomy.httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(taxonomy, "_app_token", lambda: "t")
    taxonomy._CONDITIONS_CACHE.clear()
    got = taxonomy.allowed_condition_enums("15687")
    taxonomy._CONDITIONS_CACHE.clear()
    assert got == APPAREL
    # ...and the label the seller reads is eBay's own wording for the
    # category, not a de-shouted enum.
    taxonomy._CONDITIONS_CACHE.clear()
    labels = [c["label"] for c
              in taxonomy.item_conditions("15687")["conditions"]]
    taxonomy._CONDITIONS_CACHE.clear()
    assert "Pre-owned - Fair" in labels


# ------------------------------------------------------------- the checklist


ACCOUNT_READY = {"has_fulfillment": True, "has_payment": True,
                 "has_return": True, "has_location": True, "connected": True}


def _listing(**fields) -> Listing:
    base = {"title": "Hand Carved Bone Fish Figurine Pendant Ice Fishing",
            "condition": "USED_GOOD", "price": 24.99, "quantity": 1,
            "category_id": "1289", "description": "Lovely piece.",
            "package_weight_lb": 1.0, "images": ["img_000.jpg"]}
    base.update(fields)
    return Listing(**base)


def _conditions(enums):
    return [{"enum": e, "id": "", "label": e.replace("_", " ").title()}
            for e in enums]


def test_the_checklist_catches_a_condition_the_category_refuses():
    issues = preflight.validate(_listing(), "live", **ACCOUNT_READY,
                                allowed_conditions=_conditions(PLAIN))
    blocking = [i for i in issues if i["blocking"] and i["target"] == "condition"]
    assert blocking, "a condition eBay will refuse has to block the publish"
    # It names what the category DOES take — the fix is a dropdown away.
    assert "Used Excellent" in blocking[0]["fix"]


def test_an_allowed_condition_raises_nothing():
    issues = preflight.validate(_listing(condition="USED_EXCELLENT"), "live",
                                **ACCOUNT_READY,
                                allowed_conditions=_conditions(PLAIN))
    assert [i for i in issues if i["target"] == "condition"] == []


def test_not_being_able_to_ask_ebay_is_not_a_blocker():
    """The lookup fails soft everywhere else in the app; a checklist that
    invents blockers out of a network blip is worse than one that admits it
    could not see."""
    issues = preflight.validate(_listing(), "live", **ACCOUNT_READY,
                                allowed_conditions=None)
    assert [i for i in issues if i["target"] == "condition"] == []


# ------------------------------------------- the draft, before anyone sees it


def _identify(monkeypatch, allowed, **fields):
    """Resolve a category for a fresh AI draft, with eBay's answers stubbed."""
    # The identify route lives in main, which pulls the AI and image stacks;
    # the fast CI job runs without them and these four run in the smoke job.
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.taxonomy, "best_category_id",
                        lambda q, *a, **k: {"category_id": "155226",
                                            "path": "Clothing > Men > Shirts"})
    monkeypatch.setattr(main.taxonomy, "allowed_condition_enums",
                        lambda cid, *a, **k: allowed)
    listing = _listing(category_id="", **fields)
    main._resolve_category(listing)
    return listing


def test_the_condition_is_picked_after_the_category(monkeypatch):
    """The order is the whole fix: eBay's list for the category it just
    resolved decides the grade the draft carries."""
    listing = _identify(monkeypatch, APPAREL, condition="USED_GOOD")
    assert listing.category_id == "155226"
    assert listing.condition == "USED_EXCELLENT"


def test_a_grade_the_category_offers_is_left_alone(monkeypatch):
    listing = _identify(monkeypatch, PLAIN, condition="NEW")
    assert listing.condition == "NEW"


def test_a_condition_with_no_honest_substitute_is_flagged_not_invented(monkeypatch):
    listing = _identify(monkeypatch, ["NEW"], condition="USED_GOOD")
    assert listing.condition == "USED_GOOD"     # never quietly "New"
    assert any("item condition" in m.lower() for m in listing.missing_info)


def test_a_failed_lookup_leaves_the_draft_alone(monkeypatch):
    """Identify must never fail on a taxonomy problem — it draws a draft from
    photos, and eBay being unreachable is not a reason to lose it."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main

    def _boom(*a, **k):
        raise RuntimeError("eBay said no")

    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.taxonomy, "allowed_condition_enums", _boom)
    listing = _listing(condition="USED_GOOD")
    main._fit_condition_to_category(listing)
    assert listing.condition == "USED_GOOD"


# ------------------------------------------------- and again at publish time


def test_a_draft_made_before_any_of_this_still_publishes(monkeypatch):
    """The drafts already sitting in the queue carry the grade that eBay
    refused. The publish path fits them the same way rather than sending
    another 25021 — and reports which grade it replaced."""
    from backend.marketplaces import ebay_provider

    monkeypatch.setattr(ebay_provider.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(ebay_provider.taxonomy, "item_conditions",
                        lambda cid, **k: {"conditions": _conditions(PLAIN)})
    listing = _listing(condition="USED_GOOD")
    assert ebay_provider.fit_condition_to_category(listing) == "USED_GOOD"
    assert listing.condition == "USED_EXCELLENT"


def test_publish_leaves_a_grade_the_category_offers(monkeypatch):
    from backend.marketplaces import ebay_provider

    monkeypatch.setattr(ebay_provider.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(ebay_provider.taxonomy, "item_conditions",
                        lambda cid, **k: {"conditions": _conditions(PLAIN)})
    listing = _listing(condition="NEW_OTHER")
    assert ebay_provider.fit_condition_to_category(listing) == ""
    assert listing.condition == "NEW_OTHER"
