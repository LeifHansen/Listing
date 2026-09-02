"""The eBay message adapter: the P2P promise, and surviving an unverified payload.

Two things are being defended here. First, that eBay's own system mail can
never reach an inbox that promises person-to-person messages only. Second,
that the normalizer keeps working when the payload isn't shaped the way we
guessed — the docs were unreachable when this was written, so every field is
read through a table of plausible spellings and nothing may raise.
"""
import httpx
import pytest

from backend.services import ebay_messages as em


# A conversation shaped the way we EXPECT eBay to send one.
CANON = {
    "conversationId": "c-1",
    "conversationType": "FROM_MEMBERS",
    "conversationStatus": "ACTIVE",
    "conversationTitle": "Nikon 50mm",
    "otherPartyUsername": "sarah_m",
    "createdDate": "2026-08-01T10:00:00Z",
    "referenceType": "LISTING",
    "referenceId": "1122334455",
    "latestMessage": {
        "messageId": "m-9",
        "messageText": "Is the lens still available?",
        "senderUsername": "sarah_m",
        "creationDate": "2026-08-30T09:00:00Z",
    },
}


def test_canonical_conversation_flattens():
    c = em._conversation_to_dict(CANON, me="my_shop")
    assert c["raw_id"] == "c-1"
    assert c["marketplace"] == "ebay"
    assert c["counterparty"] == "sarah_m"
    assert c["snippet"] == "Is the lens still available?"
    assert c["last_at"] == "2026-08-30T09:00:00Z"
    assert c["item_id"] == "1122334455"
    assert c["status"] == "ACTIVE"


def test_item_id_only_for_listing_references():
    """referenceId means different things per referenceType; only a LISTING
    reference is an item we can link to."""
    other = dict(CANON, referenceType="ORDER", referenceId="99")
    assert em._conversation_to_dict(other)["item_id"] == ""


# --- tolerance: the same conversation in every spelling we might meet -------

ALIASES = [
    pytest.param({**CANON, "latestMessage": {"body": "Is the lens still available?",
                                             "sender": "sarah_m",
                                             "sentDate": "2026-08-30T09:00:00Z",
                                             "id": "m-9"}}, id="body/sender/sentDate"),
    pytest.param({**CANON, "latestMessage": {"text": "Is the lens still available?",
                                             "fromUsername": "sarah_m",
                                             "createdDate": "2026-08-30T09:00:00Z",
                                             "id": "m-9"}}, id="text/fromUsername"),
    pytest.param({**{k: v for k, v in CANON.items() if k != "otherPartyUsername"},
                  "otherParty": {"username": "sarah_m"}}, id="otherParty-as-object"),
    pytest.param({**{k: v for k, v in CANON.items() if k != "otherPartyUsername"},
                  "memberUsername": "sarah_m"}, id="memberUsername"),
    pytest.param({**{k: v for k, v in CANON.items() if k != "latestMessage"},
                  "lastMessage": CANON["latestMessage"]}, id="lastMessage"),
]


@pytest.mark.parametrize("payload", ALIASES)
def test_alternate_spellings_produce_the_same_row(payload):
    """This is the test that makes an unverified payload survivable: whichever
    spelling eBay actually uses, the inbox row is identical."""
    got = em._conversation_to_dict(payload, me="my_shop")
    want = em._conversation_to_dict(CANON, me="my_shop")
    assert got["counterparty"] == want["counterparty"]
    assert got["snippet"] == want["snippet"]
    assert got["last_at"] == want["last_at"]
    assert got["raw_id"] == want["raw_id"]


def test_empty_and_junk_payloads_never_raise():
    for payload in ({}, {"nothing": "familiar"}, None, [], "a string"):
        c = em._conversation_to_dict(payload)
        m = em._message_to_dict(payload)
        assert set(c) >= {"raw_id", "counterparty", "snippet", "last_at", "unread"}
        assert set(m) == {"id", "from_me", "author", "text", "sent_at"}


# --- direction --------------------------------------------------------------

def test_from_me_matches_username_case_insensitively():
    m = em._message_to_dict({"messageText": "hi", "senderUsername": "My_Shop"},
                            me="my_shop")
    assert m["from_me"] is True


def test_unknown_author_is_never_treated_as_mine():
    """Rendering the buyer's words as your own outgoing bubble is a trust bug;
    the reverse is merely odd. So an unresolvable author is always inbound."""
    assert em._message_to_dict({"messageText": "hi"}, me="my_shop")["from_me"] is False
    assert em._message_to_dict({"messageText": "hi", "senderUsername": "someone"},
                               me="")["from_me"] is False


def test_explicit_direction_flag_wins():
    m = em._message_to_dict({"messageText": "hi", "sentByMe": True}, me="my_shop")
    assert m["from_me"] is True


# --- untrusted content ------------------------------------------------------

def test_message_bodies_are_stripped_to_plain_text():
    """Bodies are typed by strangers. Stripping server-side means no component
    can be one careless dangerouslySetInnerHTML away from stored XSS."""
    dirty = '<script>alert(1)</script>Hi<br>there &amp; welcome<b>!</b>'
    clean = em._plain_text(dirty)
    assert "<" not in clean and ">" not in clean
    assert "script" not in clean.lower() or "alert" not in clean
    assert "Hi\nthere & welcome!" == clean


def test_snippet_is_truncated():
    long = dict(CANON, latestMessage={"messageText": "x" * 500,
                                      "senderUsername": "sarah_m"})
    assert len(em._conversation_to_dict(long)["snippet"]) <= em._SNIPPET


# --- the P2P guarantee ------------------------------------------------------

def test_system_conversations_are_dropped():
    """The query already asks for FROM_MEMBERS. This is the second lock: if
    eBay ever ignores or renames that parameter, its system mail still cannot
    reach an inbox that promises buyer messages only."""
    system = em._conversation_to_dict(dict(CANON, conversationType="FROM_EBAY"))
    assert em._is_p2p(system) is False
    assert em._is_p2p(em._conversation_to_dict(CANON)) is True


def test_unlabelled_conversations_are_kept():
    """We asked for members-only; dropping unlabelled rows would empty the
    inbox the day eBay stops echoing the field back."""
    bare = {k: v for k, v in CANON.items() if k != "conversationType"}
    assert em._is_p2p(em._conversation_to_dict(bare)) is True


def test_list_conversations_filters_and_sorts(monkeypatch):
    payload = {"conversations": [
        dict(CANON, conversationId="old",
             latestMessage={"messageText": "older", "senderUsername": "a",
                            "creationDate": "2026-08-01T00:00:00Z"}),
        dict(CANON, conversationId="sys", conversationType="FROM_EBAY"),
        dict(CANON, conversationId="new",
             latestMessage={"messageText": "newer", "senderUsername": "a",
                            "creationDate": "2026-08-30T00:00:00Z"}),
    ]}
    monkeypatch.setattr(em, "_get", lambda *a, **k: payload)
    rows = em.list_conversations("tok")
    assert [r["raw_id"] for r in rows] == ["new", "old"]


def test_list_conversations_asks_for_members_only(monkeypatch):
    seen = {}

    def fake_get(token, path, params=None, **kw):
        seen.update(params or {})
        return {"conversations": []}

    monkeypatch.setattr(em, "_get", fake_get)
    em.list_conversations("tok")
    assert seen["conversation_type"] == "FROM_MEMBERS"


# --- sending ----------------------------------------------------------------

def test_send_requires_text():
    with pytest.raises(em.MessagesError):
        em.send_message("tok", text="   ", raw_id="c-1")


def test_send_requires_exactly_one_destination():
    with pytest.raises(em.MessagesError):
        em.send_message("tok", text="hi")
    with pytest.raises(em.MessagesError):
        em.send_message("tok", text="hi", raw_id="c-1", other_party="sarah_m")


def test_send_truncates_and_rereads_the_thread(monkeypatch):
    sent = {}
    monkeypatch.setattr(em, "_post", lambda t, p, body, **k: sent.update(body) or {})
    monkeypatch.setattr(em, "get_conversation",
                        lambda *a, **k: {"conversation": {"raw_id": "c-1"},
                                         "messages": [{"text": "hi"}]})
    out = em.send_message("tok", text="y" * 5000, raw_id="c-1")
    assert len(sent["messageText"]) == em._MAX_TEXT
    assert sent["conversationId"] == "c-1"
    assert out["messages"] == [{"text": "hi"}]


def test_mark_read_never_raises_on_unknown_action(monkeypatch):
    """The update action name is unverified. An unknown one must degrade to
    'the badge clears on the next poll', not to an error the seller sees."""
    def boom(*a, **k):
        raise em.MessagesError("nope")
    monkeypatch.setattr(em, "_post", boom)
    assert em.mark_read("tok", "c-1") is False


def test_unread_total_sums_and_ignores_junk():
    assert em.unread_total([{"unread": 2}, {"unread": "3"}, {}, None, "x"]) == 5


# --- a lost answer on a send is not "it didn't go" --------------------------

def _wire(monkeypatch, outcome):
    """Stub the HTTP layer: `outcome` is an exception to raise or a status."""
    calls = []

    def request(method, url, **kw):
        calls.append(method)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, text="{}",
                              request=httpx.Request(method, url))
    monkeypatch.setattr(em.httpx, "request", request)
    monkeypatch.setattr(em, "_bases", lambda: ["https://api.example.test",
                                               "https://apiz.example.test"])
    return calls


@pytest.mark.parametrize("lost", [httpx.ReadTimeout("slow"),
                                  httpx.RemoteProtocolError("cut off"),
                                  RuntimeError("something nobody expected")])
def test_a_send_whose_answer_was_lost_says_so(monkeypatch, lost):
    calls = _wire(monkeypatch, lost)
    with pytest.raises(em.UnknownOutcome) as caught:
        em._post("tok", "/commerce/message/v1/send_message", {"messageText": "hi"})
    assert caught.value.outcome_unknown is True
    assert "landed" in str(caught.value)
    assert calls == ["POST"]        # never retried on the other host


def test_a_send_that_never_left_is_a_plain_failure(monkeypatch):
    _wire(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(em.MessagesError) as caught:
        em._post("tok", "/commerce/message/v1/send_message", {"messageText": "hi"})
    assert caught.value.outcome_unknown is False


def test_ebay_falling_over_after_a_send_is_unknown_not_rejected(monkeypatch):
    calls = _wire(monkeypatch, 502)
    with pytest.raises(em.UnknownOutcome):
        em._post("tok", "/commerce/message/v1/send_message", {"messageText": "hi"})
    assert calls == ["POST"]


def test_a_lost_read_is_just_a_read_to_repeat(monkeypatch):
    _wire(monkeypatch, httpx.ReadTimeout("slow"))
    with pytest.raises(em.MessagesError) as caught:
        em._get("tok", "/commerce/message/v1/conversation")
    assert caught.value.outcome_unknown is False


def test_mark_read_swallows_an_unknown_outcome_too(monkeypatch):
    """Best effort means best effort: an unconfirmed "I read this" is not an
    error the seller sees for something they never asked for."""
    _wire(monkeypatch, httpx.ReadTimeout("slow"))
    assert em.mark_read("tok", "c-1") is False
