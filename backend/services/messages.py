"""The unified inbox: one conversation list across every marketplace.

This is the fan-out. Each marketplace that implements the messaging contract
(marketplaces.messaging.MESSAGING_METHODS) contributes its buyer conversations;
this module merges them into one list ordered by recency, tags each row with
the marketplace it came from, and routes a click on any row back to the
provider that owns it via the namespaced id ("ebay:1234").

Three rules shape everything here:

1. **P2P only.** Each adapter is responsible for excluding its marketplace's
   automated mail at the source (eBay does it with conversation_type=
   FROM_MEMBERS). This layer never relaxes that.

2. **One marketplace failing must never blank the others.** eBay being down
   cannot empty an Etsy seller's inbox, so every provider is called inside its
   own try and its outcome recorded in `sources`. A partial inbox that says
   which half is missing beats an empty one that says nothing.

3. **Sources are reported even when they have nothing to give.** The UI's
   marketplace toggle is built from `sources`, so a marketplace that is
   connected-but-empty, not connected, or not yet supported all have to be
   distinguishable — that's the difference between "no messages" and "you
   haven't connected this yet".

Adding marketplace N+1 = implement the five methods on its provider. Nothing
in this module changes.
"""
from __future__ import annotations

from typing import Optional

from .. import marketplaces
from ..config import log
from ..marketplaces import messaging

# What a source can be doing, worst-first for the UI's benefit.
_REASON_COPY = {
    "disabled": "",           # operator hasn't enabled it; say nothing
    "signed_out": "",
    "unsupported": "",
    "not_connected": "Connect this marketplace to see its messages.",
    "needs_reconnect": "Reconnect in Settings to grant message access.",
    "error": "Couldn't reach this marketplace just now.",
}


def _providers() -> list:
    """Every registered provider implementing the messaging contract."""
    return [p for p in marketplaces.all_providers()
            if messaging.supports_messaging(p)]


def known_keys() -> list[str]:
    return [p.key for p in _providers()]


def _provider_for(key: str):
    p = marketplaces.get(key)
    return p if p is not None and messaging.supports_messaging(p) else None


def sources(uid: Optional[str]) -> list[dict]:
    """The marketplace roster behind the inbox's source toggle.

    Includes marketplaces that CAN'T do messaging yet, marked unsupported, so
    the UI can show an honest "eBay · Etsy (soon)" rather than silently
    pretending the others don't exist.
    """
    out = []
    for p in marketplaces.all_providers():
        key, label = p.key, getattr(p, "label", p.key)
        if not messaging.supports_messaging(p):
            out.append({"key": key, "label": label, "available": False,
                        "reason": "unsupported", "message": "",
                        "unread": 0, "supported": False})
            continue
        try:
            st = p.messaging_status(uid) or {}
        except Exception as exc:  # noqa: BLE001 - a roster must never fail
            log.info("messages: %s status failed: %s", key, exc)
            st = {"available": False, "reason": "error", "message": ""}
        reason = str(st.get("reason") or "")
        out.append({
            "key": key, "label": label,
            "available": bool(st.get("available")),
            "reason": reason,
            "message": str(st.get("message") or _REASON_COPY.get(reason, "")),
            "unread": 0, "supported": True,
        })
    return out


def list_conversations(uid: Optional[str], *, marketplace: str = "",
                       limit: int = 25) -> dict:
    """The merged inbox.

    `marketplace` filters to one source ("" or "all" for everything). Returns
    {conversations, unread, sources} — always, even when every source failed.
    """
    roster = sources(uid)
    by_key = {s["key"]: s for s in roster}
    wanted = str(marketplace or "").strip().lower()
    rows: list[dict] = []

    for p in _providers():
        src = by_key.get(p.key) or {}
        if not src.get("available"):
            continue
        # NOTE: every available source is read even when the view is filtered.
        # The badge is global, so skipping a source here would make the other
        # marketplace's unread count vanish while a filter is on — which reads
        # as "those messages went away". Rows are filtered after the merge.
        try:
            found = p.list_conversations(uid, limit=limit) or []
        except Exception as exc:  # noqa: BLE001 - one source can't sink the rest
            needs = bool(getattr(exc, "needs_reconnect", False))
            src["available"] = False
            src["reason"] = "needs_reconnect" if needs else "error"
            src["message"] = str(exc)
            log.info("messages: %s list failed: %s", p.key, exc)
            continue
        unread = 0
        for c in found:
            if not isinstance(c, dict):
                continue
            raw = str(c.get("raw_id") or "")
            if not raw:
                continue
            c["marketplace"] = p.key
            c["marketplace_label"] = getattr(p, "label", p.key)
            c["id"] = messaging.qualify(p.key, raw)
            unread += int(c.get("unread") or 0)
            rows.append(c)
        src["unread"] = unread

    # Newest first across every marketplace. Dates are ISO-8601 from each
    # adapter, so a lexical sort is a chronological one; undated rows sink
    # rather than jumping the queue.
    if wanted and wanted != "all":
        rows = [c for c in rows if c.get("marketplace") == wanted]
    rows.sort(key=lambda c: str(c.get("last_at") or ""), reverse=True)
    return {
        "conversations": rows[:limit],
        # The badge counts EVERY source, not just the one being viewed — a
        # filtered view must not make the other marketplace's unread vanish.
        "unread": sum(int(s.get("unread") or 0) for s in roster),
        "sources": roster,
    }


def _route(uid: Optional[str], conversation_id: str):
    """(provider, raw_id) for a namespaced id, or (None, raw)."""
    key, raw = messaging.split(conversation_id,
                               default=(known_keys() or [""])[0])
    if not raw:
        return None, ""
    return _provider_for(key), raw


def get_conversation(uid: str, conversation_id: str, limit: int = 50) -> dict:
    p, raw = _route(uid, conversation_id)
    if p is None:
        raise LookupError("That conversation's marketplace isn't available.")
    out = p.get_conversation(uid, raw, limit=limit) or {}
    conv = out.get("conversation") or {}
    conv["marketplace"] = p.key
    conv["marketplace_label"] = getattr(p, "label", p.key)
    conv["id"] = messaging.qualify(p.key, conv.get("raw_id") or raw)
    out["conversation"] = conv
    return out


def send(uid: str, conversation_id: str, text: str) -> dict:
    p, raw = _route(uid, conversation_id)
    if p is None:
        raise LookupError("That conversation's marketplace isn't available.")
    out = p.send_message(uid, raw, text) or {}
    conv = out.get("conversation") or {}
    conv["marketplace"] = p.key
    conv["marketplace_label"] = getattr(p, "label", p.key)
    conv["id"] = messaging.qualify(p.key, conv.get("raw_id") or raw)
    out["conversation"] = conv
    return out


def mark_read(uid: str, conversation_id: str) -> bool:
    """Best-effort; never raises. The badge re-syncs on the next poll."""
    try:
        p, raw = _route(uid, conversation_id)
        return bool(p is not None and p.mark_read(uid, raw))
    except Exception as exc:  # noqa: BLE001
        log.info("messages: mark_read failed for %s: %s", conversation_id, exc)
        return False
