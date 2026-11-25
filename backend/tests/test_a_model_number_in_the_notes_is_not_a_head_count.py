""""501 levis" is one pair of jeans, not five hundred and one.

The seller's notes are read for how many items the pile holds: "two pairs of
levis, a mug" is three. A line that starts with a number takes that number as
its count -- which was right for "3 shirts" and wrong for the line this app
sees most, because Levi's name their jeans by number. "501 levis, 505 levis"
counted as a hundred items (fifty each, after the cap), and the grouping pass,
told the seller expected a hundred, sent every group of three photos or more
for a second, separately billed split check.

A count is a number of one or two digits. Three or more is a lot number, a
model, a year -- a name for the item, which is one item.
"""
from __future__ import annotations

from backend.services.listing_prompt import expected_item_count


def test_a_lot_number_is_the_name_of_one_item():
    assert expected_item_count("501 levis, 505 levis") == 2
    assert expected_item_count("1960 pyrex bowl") == 1
    assert expected_item_count("2 levis 501") == 2


def test_a_real_count_still_counts():
    assert expected_item_count("two pairs of levis, a mug") == 3
    assert expected_item_count("3 shirts, one hat, vintage lamp") == 5
    assert expected_item_count("12 records") == 12
    assert expected_item_count("") == 0


def test_the_split_check_is_not_sent_every_group_for_a_lot_number():
    import pytest
    pytest.importorskip("anthropic")
    from backend.services.claude_ai import _split_candidates
    groups = [{"name": n, "indices": list(range(i * 3, i * 3 + 3))}
              for i, n in enumerate(("501", "505"))]
    assert _split_candidates(groups, expected_item_count("501 levis, 505 levis")) == []
