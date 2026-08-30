"""The unified inbox across marketplaces.

The fan-out's job is to merge many marketplaces into one list, keep them
independently failable, and route each row back to the provider that owns it.
These tests use fake providers, so they hold for marketplace N+1 too.
"""
import pytest

from backend.marketplaces import messaging
from backend.services import messages as svc


class FakeProvider:
    """A marketplace that can do messaging."""
    def __init__(self, key, label, rows=None, available=True, reason="",
                 boom=None):
        self.key, self.label = key, label
        self._rows = rows or []
        self._available, self._reason, self._boom = available, reason, boom
        self.sent = []

    def messaging_status(self, uid):
        return {"available": self._available, "reason": self._reason,
                "message": ""}

    def list_conversations(self, uid, limit=25):
        if self._boom:
            raise self._boom
        return [dict(r) for r in self._rows]

    def get_conversation(self, uid, raw_id, limit=50):
        return {"conversation": {"raw_id": raw_id}, "messages": []}

    def send_message(self, uid, raw_id, text):
        self.sent.append((raw_id, text))
        return {"conversation": {"raw_id": raw_id}, "messages": []}

    def mark_read(self, uid, raw_id):
        return True


class SilentProvider:
    """A marketplace with no messaging support at all (Etsy/Depop today)."""
    key, label = "depop", "Depop"

    def oauth_ready(self):
        return True


def row(rid, at, unread=0):
    return {"raw_id": rid, "last_at": at, "unread": unread, "counterparty": "b"}


@pytest.fixture
def registry(monkeypatch):
    """Swap the provider registry for a set the test controls."""
    def install(*providers):
        monkeypatch.setattr(svc.marketplaces, "all_providers",
                            lambda: list(providers))
        monkeypatch.setattr(svc.marketplaces, "get",
                            lambda k: next((p for p in providers
                                            if p.key == k), None))
        return providers
    return install


def test_supports_messaging_is_all_or_nothing():
    """A half-implemented provider would show conversations the seller then
    couldn't open — worse than not listing it."""
    assert messaging.supports_messaging(FakeProvider("ebay", "eBay"))
    assert not messaging.supports_messaging(SilentProvider())

    class Partial(SilentProvider):
        def messaging_status(self, uid): return {}
        def list_conversations(self, uid, limit=25): return []
    assert not messaging.supports_messaging(Partial())


def test_conversations_merge_newest_first_across_marketplaces(registry):
    registry(
        FakeProvider("ebay", "eBay", [row("a", "2026-08-01T00:00:00Z"),
                                      row("c", "2026-08-30T00:00:00Z")]),
        FakeProvider("etsy", "Etsy", [row("b", "2026-08-15T00:00:00Z")]),
    )
    out = svc.list_conversations("u1")
    assert [c["id"] for c in out["conversations"]] == ["ebay:c", "etsy:b", "ebay:a"]
    assert [c["marketplace"] for c in out["conversations"]] == ["ebay", "etsy", "ebay"]


def test_ids_are_namespaced_so_a_click_routes_home(registry):
    ebay, etsy = registry(
        FakeProvider("ebay", "eBay", [row("42", "2026-08-01T00:00:00Z")]),
        FakeProvider("etsy", "Etsy", [row("42", "2026-08-02T00:00:00Z")]),
    )
    svc.send("u1", "etsy:42", "hello")
    assert etsy.sent == [("42", "hello")] and ebay.sent == []


def test_one_marketplace_failing_never_blanks_the_others(registry):
    """eBay being down must not empty an Etsy seller's inbox."""
    registry(
        FakeProvider("ebay", "eBay", boom=RuntimeError("eBay is down")),
        FakeProvider("etsy", "Etsy", [row("b", "2026-08-15T00:00:00Z")]),
    )
    out = svc.list_conversations("u1")
    assert [c["id"] for c in out["conversations"]] == ["etsy:b"]
    bad = next(s for s in out["sources"] if s["key"] == "ebay")
    assert bad["available"] is False and bad["reason"] == "error"
    assert "down" in bad["message"]


def test_a_scope_failure_is_reported_as_reconnectable(registry):
    err = RuntimeError("reconnect eBay")
    err.needs_reconnect = True
    registry(FakeProvider("ebay", "eBay", boom=err))
    src = svc.list_conversations("u1")["sources"][0]
    assert src["reason"] == "needs_reconnect"


def test_unsupported_marketplaces_are_listed_honestly(registry):
    """The toggle should say "Depop (soon)" rather than pretend it isn't a
    marketplace this app knows about."""
    registry(FakeProvider("ebay", "eBay"), SilentProvider())
    out = svc.list_conversations("u1")
    depop = next(s for s in out["sources"] if s["key"] == "depop")
    assert depop["supported"] is False and depop["reason"] == "unsupported"


def test_filtering_to_one_marketplace_keeps_the_global_unread(registry):
    """A filtered view must not make the other marketplace's unread vanish
    from the badge — that would read as 'it went away'."""
    registry(
        FakeProvider("ebay", "eBay", [row("a", "2026-08-01T00:00:00Z", unread=2)]),
        FakeProvider("etsy", "Etsy", [row("b", "2026-08-15T00:00:00Z", unread=3)]),
    )
    out = svc.list_conversations("u1", marketplace="etsy")
    assert [c["id"] for c in out["conversations"]] == ["etsy:b"]
    assert out["unread"] == 5


def test_disconnected_marketplaces_are_not_called(registry):
    registry(FakeProvider("ebay", "eBay", [row("a", "2026-08-01T00:00:00Z")],
                          available=False, reason="not_connected"))
    out = svc.list_conversations("u1")
    assert out["conversations"] == []
    assert out["sources"][0]["message"]      # explains itself to the seller


def test_rows_without_an_id_are_dropped(registry):
    registry(FakeProvider("ebay", "eBay", [{"last_at": "2026-08-01T00:00:00Z"}]))
    assert svc.list_conversations("u1")["conversations"] == []


def test_mark_read_never_raises(registry):
    registry(FakeProvider("ebay", "eBay"))
    assert svc.mark_read("u1", "nosuch:1") is False
    assert svc.mark_read("u1", "") is False


def test_unknown_marketplace_prefix_is_a_lookup_error(registry):
    registry(FakeProvider("ebay", "eBay"))
    with pytest.raises(LookupError):
        svc.get_conversation("u1", "shopify:1")


def test_bare_ids_still_route_to_the_first_marketplace(registry):
    """Ids minted before the namespace existed, or hand-typed, must resolve
    rather than 404 on a marketplace named after half an eBay id."""
    registry(FakeProvider("ebay", "eBay"))
    out = svc.get_conversation("u1", "1234")
    assert out["conversation"]["id"] == "ebay:1234"
