"""One item photographed again must not come out of bulk mode as several.

On 2026-11-01 a seller shot ONE cloisonne bell seven times -- upright, tilted,
looking into the mouth, on its side, then upright and on its side again --
with the same handwritten price tag and the same ribbon in every frame. The
guidance step came back with three items: "cloisonne bell green pink floral
#1", "cloisonne bell gold Victorian #2" and "cloisonne bell green Victorian
#3". Three listings of one bell.

The first pass started it: it was told the next overview starts the next
item, and that a different colour is a different item -- and enamel reads
gold in one shot and green in the next. So it numbered three bells.

The merge pass is the net for exactly that, and it was told the opposite of
its job: "never merge look-alikes on looks alone". It sees ONE photo per
group, so looks are all it ever has -- three near-identical bells, labelled
"#1", "#2" and "#3" as if someone had counted them, could not be merged.

Now the merge pass is told the same item shot again must be merged unless it
can point at what tells the groups apart, it is shown where each group sits
in the pile, and the first pass's numbering is taken off the labels.
"""
from __future__ import annotations

import json
import re
import types

import pytest

pytest.importorskip("anthropic")

from backend.services import claude_ai  # noqa: E402


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _reply(payload):
    return types.SimpleNamespace(
        stop_reason="end_turn",
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))])


class _Client:
    """A stand-in Anthropic client answering scripted replies in order."""

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


def _labels(request: dict) -> list[str]:
    return [b["text"] for b in request["messages"][0]["content"]
            if b["type"] == "text" and b["text"].startswith("Group ")]


# What the first pass made of the seller's seven photos of one bell.
_THE_BELL = [
    {"name": "cloisonne bell green pink floral #1", "indices": [0, 1, 2, 3]},
    {"name": "cloisonne bell gold Victorian #2", "indices": [4, 5]},
    {"name": "cloisonne bell green Victorian #3", "indices": [6]},
]


# ------------------------------------------------------------ the prompts

def test_the_merge_pass_is_no_longer_told_to_keep_look_alikes_apart():
    schema = _flat(claude_ai._GROUP_VERIFY_SCHEMA)
    assert "never merge look-alikes on looks alone" not in schema
    assert "THE SAME ITEM SHOT AGAIN" in schema
    assert "must be merged, unless you can POINT AT what tells them apart" in schema
    # Still: tags that read differently are two items.
    assert "are two items however alike they look" in schema


def test_the_merge_pass_is_told_a_number_in_a_name_is_not_a_count():
    schema = _flat(claude_ai._GROUP_VERIFY_SCHEMA)
    assert "The group names are the first pass's guesses and are not evidence" in schema


def test_the_merge_pass_is_told_shiny_things_change_colour_in_the_light():
    schema = _flat(claude_ai._GROUP_VERIFY_SCHEMA)
    assert "A shift in the light is not a different color" in schema


def test_the_first_pass_is_told_a_reshoot_is_the_same_item():
    schema = _flat(claude_ai._GROUP_SCHEMA)
    assert "THE SAME ITEM SHOT AGAIN IS STILL ONE ITEM" in schema
    assert "the same handwritten price tag" in schema
    assert "a shift in the light is never a second item" in schema
    # The line that started the bell's split now needs a tag to differ.
    assert ("the next overview starts the next item ONLY when its tag, patch "
            "or mark reads differently") in schema


def test_the_first_pass_is_told_not_to_number_its_groups():
    schema = _flat(claude_ai._GROUP_SCHEMA)
    assert "Never number groups" in schema


def test_the_split_check_does_not_split_on_the_light_or_a_shared_tag():
    schema = _flat(claude_ai._GROUP_SPLIT_SCHEMA)
    assert "a color that shifts with the light" in schema
    assert "The same handwritten price tag, sticker or ribbon in two photos means they are ONE item" in schema


# ------------------------------------------------- the first pass's numbers

@pytest.mark.parametrize("name, plain", [
    ("cloisonne bell green pink floral #1", "cloisonne bell green pink floral"),
    ("cloisonne bell gold Victorian #2", "cloisonne bell gold Victorian"),
    ("Levi's 501 (2)", "Levi's 501"),
    ("Levi's 501 (#2)", "Levi's 501"),
    ("brass bell #1 of 3", "brass bell"),
    ("brass bell 2 of 3", "brass bell"),
    ("brass bell - #3", "brass bell"),
])
def test_the_count_comes_off_the_end_of_a_name(name, plain):
    assert claude_ai._unnumbered(name) == plain


@pytest.mark.parametrize("name", [
    "Levi's 501", "Chanel No. 5 perfume", "Size 10 dress", "#1", "",
    "Set of 4 glasses",
])
def test_a_name_without_a_count_is_left_alone(name):
    assert claude_ai._unnumbered(name) == name


# ---------------------------------------------------- what the merge pass sees

def test_the_merge_pass_sees_the_bell_without_the_numbers_and_with_its_place():
    client = _Client([{"merge": []}])
    claude_ai._verify_groups(client, _photos(7), _THE_BELL)
    labels = _labels(client.requests[0])
    assert labels == [
        'Group 0 — "cloisonne bell green pink floral" (photos 0, 1, 2, 3 of the pile):',
        'Group 1 — "cloisonne bell gold Victorian" (photos 4, 5 of the pile):',
        'Group 2 — "cloisonne bell green Victorian" (photo 6 of the pile):',
    ]
    assert not any("#" in label for label in labels)


def test_merging_the_bell_gives_one_item_with_every_photo_and_no_number():
    client = _Client([{"merge": [[0, 1, 2]]}])
    out = claude_ai._verify_groups(client, _photos(7), _THE_BELL)
    assert out == [{"name": "cloisonne bell green pink floral",
                    "indices": [0, 1, 2, 3, 4, 5, 6]}]


def test_an_unmerged_group_keeps_its_name_as_given():
    """The numbers come off the LABEL the merge pass reads and off an item
    that was merged. A group nobody touched is handed back as it came."""
    groups = [{"name": "mug #1", "indices": [0]},
              {"name": "lamp", "indices": [1]}]
    assert claude_ai._apply_group_merges(groups, []) is groups


# ----------------------------------------------------- the whole grouping run

def test_seven_shots_of_one_bell_are_one_item(monkeypatch):
    client = _Client([
        {"groups": _THE_BELL},                       # the first pass's split
        {"merge": [[0, 1, 2]]},                      # the merge pass's answer
        {"items": [{"name": "cloisonne bell",        # 7 photos -> split check
                    "indices": [0, 1, 2, 3, 4, 5, 6], "evidence": ""}]},
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(_photos(7))["groups"]
    assert len(out) == 1
    assert out[0]["indices"] == [0, 1, 2, 3, 4, 5, 6]
    assert "#" not in out[0]["name"]
