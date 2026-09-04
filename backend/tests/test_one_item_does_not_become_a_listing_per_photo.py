"""One item's photos must not come out of bulk mode as a listing per photo.

On 2026-09-03 a seller's "Handmade Stained Glass Suncatcher Round Agate Slice
Center Orange Red Art Panel" was uploaded as one pile of photos and came back
as one DRAFT PER PHOTO -- every shot of the same suncatcher drafted, and
billed, as its own item. Backlit against a window and flat on a table, a
stained glass panel barely looks like the same object, which is what starts
it; four things in this pass then let it through.

The answer was read too narrowly. Only {"groups": [{"indices": ...}]} counted,
so the same model answering with "items" or "photos" -- the words the other
two passes in this file use -- placed no photo at all, and the repair meant
for a straggler the model forgot gave EVERY photo an item of its own. That is
the reported symptom exactly, and it arrived silently and charged per photo.
It is now read under either key, a wholly 1-based answer is shifted rather
than half-dropped, and an answer that places nothing is asked once more and
then raised: the pile is still staged, so running the batch again is honest
where drafting a listing per photo is not.

The merge instructions were applied non-transitively. [[0,1],[1,2]] is one
item in three groups, but group 1 was dropped from the second list for having
been absorbed by the first, leaving one group there and merging nothing --
and [[1,2],[0,1]] lost the 0-1 merge outright. A model untangling an
over-split pile names a group in several lists, so this failed exactly when
it mattered. Unions are now transitive and independent of the order the model
listed them in.

The merge pass had a flat 400 tokens to answer in, enough for a good
grouping's handful of merges and not for the sixty-group pile it exists to
rescue; a cut-off answer is unparseable JSON, which was swallowed whole.

And the seller's own note naming ONE item was read only by the pass that
SPLITS. The same gap pointing the other way -- one item named, six groups
found -- now reaches the merge pass, along with the shape of the answer when
every single group holds one photo.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")

from backend.services import claude_ai  # noqa: E402


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


def _prompt(request: dict) -> str:
    """The trailing instruction text of a scripted request."""
    return request["messages"][0]["content"][-1]["text"]


# ------------------------------------------------- reading the answer

def test_the_indices_are_read_under_the_words_the_model_actually_uses():
    """"photos" instead of "indices" placed nothing, and every photo then
    became an item of its own."""
    for key in ("indices", "indexes", "photos", "photo_indices"):
        data = {"groups": [{"name": "Suncatcher", key: [0, 1, 2]}]}
        assert claude_ai._parse_groups(data, 3) == [
            {"name": "Suncatcher", "indices": [0, 1, 2]}]


def test_the_groups_are_read_under_items_too():
    """The split check in this same file asks for "items"; the model does
    sometimes answer the grouping call with that word."""
    data = {"items": [{"name": "Suncatcher", "indices": [0, 1, 2]}]}
    assert claude_ai._parse_groups(data, 3) == [
        {"name": "Suncatcher", "indices": [0, 1, 2]}]


def test_a_bare_number_is_a_group_of_one():
    data = {"groups": [{"name": "Mug", "index": 2}]}
    assert claude_ai._parse_groups(data, 3) == [{"name": "Mug", "indices": [2]}]


def test_an_answer_numbered_from_one_is_shifted_not_half_dropped():
    """1..n cannot be 0-based: photo 0 claimed by nobody and photo n claimed
    by someone. It used to drop the last photo as out of range and hand the
    first one an item of its own."""
    data = {"groups": [{"name": "Suncatcher", "indices": [1, 2, 3, 4, 5, 6]}]}
    assert claude_ai._parse_groups(data, 6) == [
        {"name": "Suncatcher", "indices": [0, 1, 2, 3, 4, 5]}]


def test_a_plain_zero_based_answer_is_never_shifted():
    data = {"groups": [{"name": "a", "indices": [0, 1]},
                       {"name": "b", "indices": [2]}]}
    assert claude_ai._parse_groups(data, 3) == [
        {"name": "a", "indices": [0, 1]}, {"name": "b", "indices": [2]}]


def test_a_photo_claimed_twice_or_out_of_range_is_dropped_not_trusted():
    data = {"groups": [{"name": "a", "indices": [0, 1]},
                       {"name": "b", "indices": [1, 99]}]}
    assert claude_ai._parse_groups(data, 3) == [{"name": "a", "indices": [0, 1]}]


# ---------------------------------------- an answer that places nothing

def test_an_unreadable_answer_is_asked_again_before_anything_is_drafted(monkeypatch):
    client = _Client([
        {"result": "I could not tell these apart"},          # places nothing
        {"groups": [{"name": "Suncatcher", "indices": [0, 1, 2, 3]}]},
        {"merge": []},
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(_photos(4))
    assert out == {"groups": [{"name": "Suncatcher", "indices": [0, 1, 2, 3]}]}


def test_an_answer_that_stays_unreadable_is_raised_not_billed_per_photo(monkeypatch):
    """The repair for a forgotten photo must never become the answer for the
    whole pile: six drafts for one suncatcher, each charged as its own
    identify call. The photos are still staged, so re-running is the honest
    outcome."""
    client = _Client([{"result": "no"}, {"result": "still no"}])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    with pytest.raises(RuntimeError, match="could not be read"):
        claude_ai.group_photos(_photos(6))
    assert len(client.requests) == 2


def test_a_photo_the_model_forgot_still_becomes_its_own_item(monkeypatch):
    """The repair itself stays: one straggler is an item, not a lost photo."""
    client = _Client([
        {"groups": [{"name": "Suncatcher", "indices": [0, 1, 2]}]},
        {"merge": []},
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(_photos(4))["groups"]
    assert [g["indices"] for g in out] == [[0, 1, 2], [3]]


# ------------------------------------------ applying the merge answer

def test_merges_are_transitive():
    groups = [{"name": f"g{i}", "indices": [i]} for i in range(4)]
    assert claude_ai._apply_group_merges(groups, [[0, 1], [1, 2]]) == [
        {"name": "g0", "indices": [0, 1, 2]}, {"name": "g3", "indices": [3]}]


def test_the_order_the_model_lists_the_merges_in_changes_nothing():
    """[[1,2],[0,1]] used to lose the 0-1 merge entirely, because group 1 had
    already been absorbed and the second list was left holding one group."""
    groups = [{"name": f"g{i}", "indices": [i]} for i in range(4)]
    assert (claude_ai._apply_group_merges(groups, [[1, 2], [0, 1]])
            == claude_ai._apply_group_merges(groups, [[0, 1], [1, 2]]))


def test_a_merged_item_keeps_the_piles_photo_order_and_the_first_groups_name():
    """The photos come out in shooting order, so the overview the seller shot
    first is the cover photo -- not whichever group the model happened to
    name first in the merge list."""
    groups = [{"name": "close-up", "indices": [3, 4]},
              {"name": "the suncatcher", "indices": [0, 1, 2]}]
    assert claude_ai._apply_group_merges(groups, [[0, 1]]) == [
        {"name": "the suncatcher", "indices": [0, 1, 2, 3, 4]}]


def test_a_merge_list_naming_one_group_still_does_nothing():
    groups = [{"name": "a", "indices": [0]}, {"name": "b", "indices": [1]}]
    assert claude_ai._apply_group_merges(groups, [[0], [], "nope"]) is groups
    assert claude_ai._apply_group_merges(groups, []) is groups


# ------------------------------------------------ what the merge pass is told

def test_the_merge_pass_hears_that_every_group_holds_one_photo():
    client = _Client([{"merge": []}])
    groups = [{"name": f"S{i}", "indices": [i]} for i in range(6)]
    claude_ai._verify_groups(client, _photos(6), groups)
    assert "EVERY group below holds exactly ONE photo" in _prompt(client.requests[0])


def test_the_merge_pass_hears_the_sellers_count_when_it_found_more():
    assert "6 groups" in claude_ai._count_hint(1, 6)
    assert "1 item(s)" in claude_ai._count_hint(1, 6)
    # Fewer groups than the notes promised is the split check's business.
    assert claude_ai._count_hint(4, 2) == ""
    assert claude_ai._count_hint(0, 9) == ""


def test_the_merge_answer_has_room_for_a_big_pile():
    """A flat 400 tokens could not name the merges for the badly split pile
    this pass exists to rescue, and a cut-off answer was swallowed whole."""
    client = _Client([{"merge": []}, {"merge": []}])
    small = [{"name": f"g{i}", "indices": [i]} for i in range(3)]
    big = [{"name": f"g{i}", "indices": [i]} for i in range(60)]
    claude_ai._verify_groups(client, _photos(3), small)
    claude_ai._verify_groups(client, _photos(60), big)
    assert client.requests[1]["max_tokens"] > client.requests[0]["max_tokens"]


def test_a_cut_off_merge_answer_is_raised_rather_than_read(monkeypatch):
    """It reaches group_photos' best-effort catch, which now logs it. Silence
    there was a pile of duplicate drafts with nothing to read back."""
    client = _Client([{"merge": []}])
    client.messages.create = lambda **kw: types.SimpleNamespace(
        stop_reason="max_tokens",
        content=[types.SimpleNamespace(type="text", text='{"merge": [[0,')])
    groups = [{"name": "a", "indices": [0]}, {"name": "b", "indices": [1]}]
    with pytest.raises(RuntimeError, match="cut off"):
        claude_ai._verify_groups(client, _photos(2), groups)


# ------------------------------------------------------------ end to end

def test_one_suncatcher_shot_six_ways_comes_out_as_one_listing(monkeypatch):
    """The reported batch: the first pass reads the backlit and the flat-lit
    shots as six items, the merge pass -- told the seller named one item and
    that every group holds a single photo -- puts them back together."""
    monkeypatch.setattr(claude_ai, "GROUP_SPLIT_MIN_PHOTOS", 99)
    client = _Client([
        {"groups": [{"name": f"Suncatcher {i}", "indices": [i]}
                    for i in range(6)]},
        {"merge": [[0, 1, 2], [2, 3, 4, 5]]},   # overlapping, as models answer
    ])
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    out = claude_ai.group_photos(
        _photos(6), notes="handmade stained glass suncatcher, agate slice")
    assert out == {"groups": [
        {"name": "Suncatcher 0", "indices": [0, 1, 2, 3, 4, 5]}]}
