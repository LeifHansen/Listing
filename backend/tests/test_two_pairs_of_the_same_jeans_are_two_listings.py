"""Two of the same garment in one photo pile are two listings, not one.

The first grouping pass is told to keep look-alikes together when unsure --
a duplicate listing of one item is the worse mistake -- and the verify pass
behind it only ever merges. Nothing corrected the opposite error, and on
2026-09-02 it happened: two pairs of Levi's, each with its own leather patch
and size tag, drafted as one listing.

Two things change. The prompts now say that identity evidence outranks looks:
a tag, a patch, a lot number, a size that reads differently is a second item
however alike the garments are. And a group big enough to be two items (or,
when the seller's notes promised more items than the grouping found, the
biggest groups) gets a second look with all of its photos, and is split ONLY
on evidence the model can point at -- an angle is never a second item, and an
answer that drops or reuses a photo is discarded rather than trusted.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")

from backend.services import claude_ai  # noqa: E402


def _reply(payload):
    """What the API hands back: one text block with the JSON in it."""
    return types.SimpleNamespace(
        stop_reason="end_turn",
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))])


class _Client:
    """A stand-in Anthropic client answering a scripted list of replies in
    order, and keeping every request so a test can read what was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        if not self.replies:
            raise AssertionError("more calls than the test scripted")
        return _reply(self.replies.pop(0))


def _photos(n: int) -> list[bytes]:
    return [f"photo-{i}".encode() for i in range(n)]


# ------------------------------------------------------------ the prompts

def test_the_prompts_put_tags_and_patches_above_looks():
    assert "patch" in claude_ai._GROUP_SCHEMA.lower()
    assert "tag" in claude_ai._GROUP_VERIFY_SCHEMA.lower()
    assert "evidence" in claude_ai._GROUP_SPLIT_SCHEMA.lower()


# ------------------------------------------------------ the seller's count

def test_the_notes_count_items_not_lines():
    from backend.services.listing_prompt import expected_item_count
    assert expected_item_count("two pairs of levis, a mug") == 3
    assert expected_item_count("3 shirts, one hat, vintage lamp") == 5
    assert expected_item_count("") == 0


# ------------------------------------------------------ who gets looked at

def test_a_group_big_enough_to_be_two_items_is_looked_at(monkeypatch):
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    groups = [{"name": "jeans", "indices": list(range(7))},
              {"name": "shirt", "indices": [7, 8, 9]},
              {"name": "mug", "indices": [10]}]
    assert claude_ai._split_candidates(groups) == [0]


def test_notes_promising_more_items_send_the_biggest_groups_for_a_look(monkeypatch):
    """"two pairs of levis, a shirt, a mug" is four items; the grouping found
    three. The biggest groups that could hold two items are re-checked, the
    largest first, until the count could come out right."""
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    groups = [{"name": "jeans", "indices": [0, 1, 2, 3]},
              {"name": "shirt", "indices": [4, 5, 6]},
              {"name": "mug", "indices": [7, 8]}]
    assert claude_ai._split_candidates(groups, expected=4) == [0]
    assert claude_ai._split_candidates(groups, expected=5) == [0, 1]
    # A two-photo group cannot be two items with a tag each.
    assert claude_ai._split_candidates(groups, expected=9) == [0, 1]


def test_a_small_pile_with_no_notes_is_left_alone(monkeypatch):
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    groups = [{"name": "jeans", "indices": [0, 1, 2, 3]}]
    assert claude_ai._split_candidates(groups) == []


# ---------------------------------------------------- applying an answer

GROUP = {"name": "Levi's 501 jeans", "indices": [2, 3, 4, 5, 6, 7]}


def test_a_split_with_evidence_becomes_two_items():
    parts = claude_ai._apply_split(GROUP, [
        {"name": "Levi's 501 W32", "indices": [2, 3, 4],
         "evidence": "patch reads W32 L34, lot 501"},
        {"name": "Levi's 501 W34", "indices": [5, 6, 7],
         "evidence": "patch reads W34 L32, darker wash"},
    ])
    assert parts == [{"name": "Levi's 501 W32", "indices": [2, 3, 4]},
                     {"name": "Levi's 501 W34", "indices": [5, 6, 7]}]


def test_a_split_with_nothing_to_point_at_is_refused():
    parts = claude_ai._apply_split(GROUP, [
        {"indices": [2, 3, 4], "evidence": "front views"},
        {"indices": [5, 6, 7], "evidence": ""},
    ])
    assert parts == [GROUP]


@pytest.mark.parametrize("items", [
    [{"indices": [2, 3], "evidence": "a"}, {"indices": [5, 6, 7], "evidence": "b"}],   # 4 dropped
    [{"indices": [2, 3, 4], "evidence": "a"}, {"indices": [4, 5, 6, 7], "evidence": "b"}],  # 4 twice
    [{"indices": [2, 3, 4], "evidence": "a"}, {"indices": [5, 6, 7, 9], "evidence": "b"}],  # 9 is not ours
    [{"indices": [2, 3, 4, 5, 6, 7], "evidence": ""}],                                    # one item
    "not a list",
])
def test_an_answer_that_is_not_a_partition_keeps_the_group(items):
    assert claude_ai._apply_split(GROUP, items) == [GROUP]


def test_unnamed_parts_are_numbered_after_the_group():
    parts = claude_ai._apply_split(GROUP, [
        {"indices": [2, 3, 4], "evidence": "W32"},
        {"indices": [5, 6, 7], "evidence": "W34"},
    ])
    assert [p["name"] for p in parts] == ["Levi's 501 jeans", "Levi's 501 jeans (2)"]


# ------------------------------------------------------------ the check

def test_the_check_shows_every_photo_of_the_group_by_its_own_number(monkeypatch):
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    client = _Client([{"items": [
        {"name": "W32", "indices": [2, 3, 4], "evidence": "patch W32"},
        {"name": "W34", "indices": [5, 6, 7], "evidence": "patch W34"},
    ]}])
    groups = [{"name": "shirt", "indices": [0, 1]}, GROUP]
    out = claude_ai._check_splits(client, _photos(8), groups)
    assert out == [{"name": "shirt", "indices": [0, 1]},
                   {"name": "W32", "indices": [2, 3, 4]},
                   {"name": "W34", "indices": [5, 6, 7]}]
    labels = [b["text"] for b in client.requests[0]["messages"][0]["content"]
              if b["type"] == "text"]
    assert labels[:6] == [f"Photo {i}:" for i in range(2, 8)]
    sent = [b["source"]["data"] for b in client.requests[0]["messages"][0]["content"]
            if b["type"] == "image"]
    assert len(sent) == 6


def test_a_failed_check_keeps_the_group_and_the_batch(monkeypatch):
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    client = _Client([])   # every call raises
    groups = [GROUP]
    assert claude_ai._check_splits(client, _photos(8), groups) == [GROUP]


# ------------------------------------------- end to end, in group_photos

def test_two_pairs_of_levis_come_out_as_two_listings(monkeypatch):
    """The first pass puts both pairs in one group; the merge pass has
    nothing to merge; the split check reads the patches and separates them.
    The split runs AFTER the merge pass, so what it separates is not put
    back together."""
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    client = _Client([
        {"groups": [{"name": "Levi's jeans", "indices": [0, 1, 2, 3, 4, 5]},
                    {"name": "mug", "indices": [6]}]},
        {"merge": []},
        {"items": [{"name": "Levi's 501 W32", "indices": [0, 1, 2],
                    "evidence": "patch reads W32 L34"},
                   {"name": "Levi's 505 W34", "indices": [3, 4, 5],
                    "evidence": "patch reads W34 L32, lot 505"}]},
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(_photos(7))
    assert out == {"groups": [
        {"name": "Levi's 501 W32", "indices": [0, 1, 2]},
        {"name": "Levi's 505 W34", "indices": [3, 4, 5]},
        {"name": "mug", "indices": [6]},
    ]}
    assert len(client.requests) == 3


def test_the_sellers_count_sends_a_small_group_for_the_look(monkeypatch):
    """No group is big, but the notes say two pairs and the grouping found
    one: the biggest group gets the second look."""
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 6)
    client = _Client([
        {"groups": [{"name": "Levi's jeans", "indices": [0, 1, 2, 3]},
                    {"name": "mug", "indices": [4]}]},
        {"merge": []},
        {"items": [{"indices": [0, 1], "evidence": "W30 tag"},
                   {"indices": [2, 3], "evidence": "W32 tag"}]},
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(_photos(5), notes="two pairs of levis, a mug")
    assert [g["indices"] for g in out["groups"]] == [[0, 1], [2, 3], [4]]
