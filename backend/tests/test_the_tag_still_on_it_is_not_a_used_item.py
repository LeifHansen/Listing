"""A shirt with its price tag still on is new, and it is worth what that says.

A Scotch & Soda shirt was photographed with the brand's own swing ticket still
attached, $130 printed on it. The draft came back "Pre-owned - Good", $49.

Nothing in the app had misread the photo. Three rules that were each defensible
alone produced it between them, and this file holds each one to the fix:

  * The condition instruction was "grade the WEAR you can see", which against a
    garment that has never been worn finds none and returns the middle of the
    used ladder. Nothing said an attached tag ENDS that question.
  * eBay's enum for "new with tags" is the bare NEW (condition id 1000). A model
    looking at a tagged shirt writes the words everyone uses -- NEW_WITH_TAGS --
    which is not on eBay's list, and the server's fallback for an unrecognised
    grade was USED_EXCELLENT: id 3000, which eBay labels "Pre-owned - Good" in
    apparel. That is where the word "good" came from, and it was silent.
  * The $130 was defined as purchase_price ("what it costs to buy this item
    right now") and the prompt said a price tag fills that field "never the
    resale price". So the best price evidence on the item was thrown away, and
    the profit report was told the seller had spent $130 on it.

The asymmetry is the reason each of these is worth a test. A new item listed as
used sells at a used price inside the hour and cannot be got back; a used item
listed as new is a return. So the fixes go one way only -- they never invent
"new" out of an answer that does not claim it, and they never lower a price.
"""
from __future__ import annotations

import pytest

from backend.services import listing_prompt

pytestmark = pytest.mark.filterwarnings("ignore")


# --------------------------------------------- the words, before any SDK

def test_the_prompt_says_an_attached_tag_means_new():
    """The instruction that did not exist. Grading wear on an unworn garment
    is the whole bug, and only a rule about the TAG can stop it."""
    rule = listing_prompt.RETAIL_TAG_RULE
    assert "hang tag" in rule.lower()
    assert "swing ticket" in rule.lower()
    # ...and it names eBay's actual enum rather than the words a model reaches
    # for, which is the half the server used to throw away.
    assert '"NEW"' in rule
    assert "NEW WITH TAGS" in rule.upper()
    assert "NEW_WITH_TAGS" in rule, "the wording to avoid has to be named"


def test_the_prompt_separates_the_two_kinds_of_printed_price():
    """A brand's own tag and a thrift sticker are opposite facts. Conflating
    them is what sent $130 into "what the seller paid"."""
    rule = listing_prompt.RETAIL_TAG_RULE
    assert "retail_price" in rule and "purchase_price" in rule
    assert "MSRP" in rule
    for kind in ("thrift", "consignment", "price-gun"):
        assert kind in rule.lower(), kind


def test_the_sticker_rule_no_longer_bans_the_tag_from_the_price():
    """The one sentence that did the damage: every printed price went to
    purchase_price, "never the resale price"."""
    text = listing_prompt.STICKER_AND_BARCODE_RULE
    assert "never the resale price" not in text
    assert "anchors what the item LISTS for" in text


def test_the_schema_carries_the_retail_price_field():
    schema = listing_prompt.LISTING_SCHEMA
    assert '"retail_price"' in schema
    # And the whole rule reaches the model, not just the field name.
    assert "AN ATTACHED TAG OR AN INTACT SEAL SETTLES THE CONDITION" in schema


def test_the_schema_tells_the_model_what_new_means_on_ebay():
    """NEW/NEW_OTHER are eBay's "with tags"/"without tags". A list of bare
    enums does not say that, and the model has to pick one of them."""
    schema = listing_prompt.LISTING_SCHEMA
    assert "New with tags" in schema
    assert "New without tags" in schema


def test_the_prompt_will_not_price_a_new_item_at_a_fifth_of_its_tag():
    rule = listing_prompt.RETAIL_TAG_RULE
    assert "PRICING AN ITEM THAT IS STILL NEW" in rule
    assert "retail_price" in rule


# ------------------------------------------- the enum, where "good" came from

def _condition(value):
    pytest.importorskip("anthropic")
    from backend.services.claude_ai import _condition_enum
    return _condition_enum(value)


@pytest.mark.parametrize("said", [
    "NEW_WITH_TAGS", "new with tags", "NWT", "New With Tags",
    "BRAND_NEW", "new in box", "NIB", "sealed", "brand new, sealed",
    "NEW WITH ORIGINAL TAGS ATTACHED",
])
def test_every_way_of_saying_new_with_tags_lands_on_new(said):
    """The report's actual bug. Each of these used to become USED_EXCELLENT --
    "Pre-owned - Good" in apparel -- with nothing logged and nothing shown."""
    assert _condition(said) == "NEW", said


@pytest.mark.parametrize("said", [
    "NEW_WITHOUT_TAGS", "new without tags", "NWOT", "deadstock", "unworn",
    "open box", "new, no tags",
])
def test_unworn_but_tagless_lands_on_new_without_tags(said):
    assert _condition(said) == "NEW_OTHER", said


def test_new_with_a_flaw_keeps_its_own_grade():
    assert _condition("new with defects") == "NEW_WITH_DEFECTS"
    assert _condition("NEW WITH FLAWS") == "NEW_WITH_DEFECTS"


@pytest.mark.parametrize("said,want", [
    ("USED_GOOD", "USED_GOOD"),          # already an enum: untouched
    ("PRE_OWNED_FAIR", "PRE_OWNED_FAIR"),
    ("used", "USED_EXCELLENT"),
    ("pre-owned", "USED_EXCELLENT"),
    ("for parts", "FOR_PARTS_OR_NOT_WORKING"),
])
def test_the_used_ladder_is_unchanged(said, want):
    assert _condition(said) == want


@pytest.mark.parametrize("said", [
    "", None, "  ", "fine", "decent", "a bit tatty", "pretty good condition",
    # The traps: each SAYS new and is not.
    "like new", "near new", "almost new", "looks new but worn",
    "new-ish, worn twice",
])
def test_nothing_that_does_not_claim_new_is_made_new(said):
    """The guardrail on the guardrail. A used item relabelled new is a return
    and a defect, so the leniency runs one way: an answer that merely mentions
    "new" while qualifying it stays on the used ladder, and so does an answer
    nothing can read."""
    got = _condition(said)
    pytest.importorskip("anthropic")
    from backend.services import taxonomy
    assert taxonomy.CONDITION_FAMILY[got] == "used", f"{said!r} -> {got}"


def test_the_fallback_is_still_a_used_grade():
    """Most secondhand items are used, and an unreadable answer must not
    become a claim about tags nobody saw."""
    assert _condition("qwertyuiop") == "USED_EXCELLENT"


# ------------------------------------------------- the two prices, kept apart

def _listing(data):
    pytest.importorskip("anthropic")
    from backend.services.claude_ai import _to_listing
    return _to_listing(data, ["img_000.jpg"])


def test_the_tag_price_is_retail_and_the_sticker_price_is_what_was_paid():
    got = _listing({"title": "Scotch & Soda shirt", "condition": "NEW_WITH_TAGS",
                    "price": 49, "retail_price": 130, "purchase_price": 6.99})
    assert got.retail_price == 130.0
    assert got.purchase_price == 6.99
    # And the grade the whole report was about.
    assert got.condition == "NEW"


def test_a_missing_or_nonsense_price_is_none_not_zero():
    for bad in (None, 0, -5, "", "free", {}):
        got = _listing({"retail_price": bad, "purchase_price": bad})
        assert got.retail_price is None, bad
        assert got.purchase_price is None, bad


def test_a_refine_never_drops_what_the_tag_said():
    """A refine echoes the whole draft back through the model, which may not
    return a field it was not asked about. What the tag says is a fact about
    the item, not listing copy."""
    pytest.importorskip("anthropic")
    from backend.models import Listing
    from backend.services import claude_ai

    before = Listing(title="Scotch & Soda shirt", condition="NEW",
                     price=79.99, retail_price=130.0, purchase_price=6.99,
                     images=["img_000.jpg"])

    class _Block:
        type = "text"
        text = '{"title": "Scotch & Soda Amsterdam shirt", "condition": "NEW"}'

    class _Resp:
        content = [_Block()]
        stop_reason = "end_turn"
        usage = None

    claude_ai._client.cache_clear() if hasattr(claude_ai._client, "cache_clear") else None
    monkey = type("C", (), {"messages": type("M", (), {
        "create": staticmethod(lambda **k: _Resp())})()})()
    orig = claude_ai._client
    claude_ai._client = lambda: monkey
    try:
        after = claude_ai.refine(before, "mention Amsterdam")
    finally:
        claude_ai._client = orig
    assert after.retail_price == 130.0
    assert after.purchase_price == 6.99
