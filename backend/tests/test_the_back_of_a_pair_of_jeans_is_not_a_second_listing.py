"""A pair of jeans is shot front and back, and both shots are one listing.

Denim comes into bulk mode in a fixed shape: laid flat, one pair at a time,
the front and then the same pair turned over. A seller moving six pairs
uploads twelve photos -- and got twelve drafts back, each pair listed twice,
once for its front and once for its back, with the halves scattered so a
front sat under one draft and its own back under another.

Every rule the grouping pass had pushed it there. "Identity evidence outranks
looks" is right, and on denim every identity mark -- red tab, leather patch,
lot, W/L size -- is on the BACK, so a front and its own back read as one
photo with marks and one without. "Count the tags and patches you can see and
expect at least that many items" counted the tab, the patch and the care tag
of ONE pair as three. Nothing said what the front of a pair is supposed to
look like, so the pass inferred that it was a different garment.

So DENIM_FRONT_AND_BACK_RULE tells the grouping passes the shape of the
upload: the two views of one pair look nothing alike BY DESIGN, pairs are
counted by BACKS rather than by markers in frame, a second pair is a second
back whose patch READS differently (quoted), and the back that follows a
front is the back OF that front -- so a pair keeps its own order and no photo
moves between pairs. A swapped back puts the wrong lot and the wrong size on
two listings at once, and nothing downstream can see that it happened.

The shuffle has a second half that no prompt can fix: the answer is free to
list a group's photos in any order, and did. `_lead_then_upload_order` keeps
the lead photo the model chose -- the draft's cover image, the one thing it
is actually asked to pick -- and puts everything behind it back into the
order the seller uploaded it in.
"""
from __future__ import annotations

import pytest

from backend.services.listing_prompt import DENIM_FRONT_AND_BACK_RULE


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- what the rule says -----------------------------------------------------

def test_the_rule_says_a_front_and_a_back_are_one_listing():
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "JEANS ARE SHOT FRONT AND BACK, AND BOTH SHOTS ARE ONE LISTING" in rule
    # The arithmetic, stated, because it is the mistake being made.
    assert "six pairs is six listings, not twelve" in rule
    assert "A back view is never an item of its own." in rule


def test_the_rule_says_the_two_views_look_different_by_design():
    """The reason the pass split them: a front and a back of one pair share
    almost nothing on screen."""
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "THE TWO VIEWS OF ONE PAIR LOOK NOTHING ALIKE" in rule
    # What is on each side, so "no patch here" is read as "this is the front".
    assert "the fly, the top button, the coin pocket" in rule
    assert "RED TAB and the leather PATCH" in rule
    assert "It is NOT evidence of two items." in rule


def test_the_rule_counts_pairs_by_backs_not_by_markers():
    """One pair carries a tab AND a patch AND a care tag. Counting marks in
    frame turned that into three items."""
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "COUNT PAIRS BY BACKS, NOT BY MARKERS" in rule
    assert "number of distinct patches" in rule
    assert "is still one pair" in rule
    # And the other direction: a photo with no patch is a front, not an
    # item whose tag went missing.
    assert "not an item whose tag is missing" in rule


def test_a_second_pair_is_a_second_back_that_reads_differently():
    """Splitting stays possible -- two pairs really are two listings -- but
    only on a reading, quoted, never on how different the photos look."""
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "A SECOND PAIR IS A SECOND BACK THAT READS DIFFERENTLY" in rule
    assert '"501 W32 L34" against "505 W34 L32"' in rule
    assert "Quote both readings as the evidence." in rule
    # Ties go to one listing: an extra photo is a drag away, a duplicate
    # live listing is not.
    assert "stay ONE listing" in rule
    assert "nobody can undo two live eBay listings for one pair" in rule


def test_the_merge_pass_is_told_which_direction_its_evidence_runs():
    """The merge pass sees ONE photo per group, so it can never read a patch.
    Left at "count pairs by backs" it would have merged every front in the
    pile -- six real pairs into one listing."""
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "WHEN YOU ARE SHOWN ONE PHOTO PER GROUP" in rule
    assert "The reverse is NOT true" in rule
    assert "several groups each showing a FRONT are several pairs" in rule
    assert "Never merge two fronts because neither one shows a patch." in rule


def test_the_rule_keeps_a_pairs_own_order_and_moves_no_photo_between_pairs():
    """The shuffle the seller saw, and why it is worse than untidy."""
    rule = _flat(DENIM_FRONT_AND_BACK_RULE)
    assert "KEEP EACH PAIR'S OWN ORDER AND NEVER MOVE A PHOTO BETWEEN PAIRS" in rule
    assert "The back that follows a front is the back OF that front." in rule
    assert "never collect the fronts into one item and the backs into another" in rule
    assert "the wrong lot, the wrong era and the wrong W/L size" in rule


# --- where it rides ---------------------------------------------------------

def test_every_grouping_pass_reads_the_same_front_and_back_text():
    """One text, one home. The pass that GROUPS, the pass that MERGES and the
    pass that SPLITS can each make this mistake, so each one carries the
    constant rather than a paraphrase that drifts. Skipped where the SDK is
    not installed (CI's light job)."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    assert DENIM_FRONT_AND_BACK_RULE in claude_ai._GROUP_SCHEMA
    assert DENIM_FRONT_AND_BACK_RULE in claude_ai._GROUP_VERIFY_SCHEMA
    assert DENIM_FRONT_AND_BACK_RULE in claude_ai._GROUP_SPLIT_SCHEMA


def test_the_grouping_pass_no_longer_counts_every_marker_as_an_item():
    """The line that read one pair's tab, patch and care tag as three items.
    It still counts -- two patches that READ differently are two pairs -- it
    just counts readings rather than hardware."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    schema = _flat(claude_ai._GROUP_SCHEMA)
    assert "Count the tags and patches that READ DIFFERENTLY" in schema
    assert "Count the tags and patches you can see" not in schema
    assert "are one item, not three" in schema


def test_the_grouping_pass_is_told_not_to_reorder_within_a_group():
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    schema = _flat(claude_ai._GROUP_SCHEMA)
    assert "best overview shot first and leave the rest in the order they " \
           "were uploaded" in schema


# --- and the half no prompt can carry: the order, fixed in code -------------

def test_the_lead_photo_is_kept_and_the_rest_go_back_into_upload_order():
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    # The model's pick of a cover photo survives; the shuffle behind it does
    # not.
    assert claude_ai._lead_then_upload_order([5, 3, 0, 4]) == [5, 0, 3, 4]
    # Already in order: unchanged.
    assert claude_ai._lead_then_upload_order([0, 1, 2, 3]) == [0, 1, 2, 3]
    # Degenerate shapes are not special cases.
    assert claude_ai._lead_then_upload_order([7]) == [7]
    assert claude_ai._lead_then_upload_order([]) == []


def test_a_shuffled_grouping_answer_is_read_back_in_the_order_it_was_shot():
    """Four photos, two pairs, front-then-back. The answer collected the
    fronts and put the backs after them, so pair one's draft led with its
    front and then showed the OTHER pair's back."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    data = {"groups": [{"name": "Levi's 501", "indices": [0, 3, 1]},
                       {"name": "Levi's 505", "indices": [2]}]}
    assert claude_ai._parse_groups(data, 4) == [
        {"name": "Levi's 501", "indices": [0, 1, 3]},
        {"name": "Levi's 505", "indices": [2]}]


def test_a_split_hands_each_pair_its_photos_in_order_too():
    """The split check answers with its own index lists, and the same shuffle
    reaches the seller through them."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    # Two pairs, each shot front, back and a tag close-up; the answer put
    # each pair's tag shot in the middle of it.
    group = {"name": "Levi's jeans", "indices": [0, 1, 2, 3, 4, 5]}
    parts = claude_ai._apply_split(group, [
        {"name": "W32", "indices": [0, 2, 1], "evidence": "patch reads W32"},
        {"name": "W34", "indices": [3, 5, 4], "evidence": "patch reads W34"},
    ])
    assert parts == [{"name": "W32", "indices": [0, 1, 2]},
                     {"name": "W34", "indices": [3, 4, 5]}]
