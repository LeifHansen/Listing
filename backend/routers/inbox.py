"""What the header polls: the notifications bell and the buyer inbox.

/api/notifications and /api/messages are asked every minute from every
screen, and that shapes both: a list read answers 200 with an empty list and
says whether it could read, rather than a 503 or 502 that would make the
whole app noisy over one blip. What the seller asks for directly (a thread,
a reply) fails honestly.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import db, errors, ratelimit
from ..config import log
from ..services import messages as messages_service
from . import deps

router = APIRouter()


# --- notifications ----------------------------------------------------------

@router.get("/api/notifications")
def notifications_list(request: Request, limit: int = 50,
                       unread_only: bool = False) -> dict:
    """The signed-in user's notifications (newest first) + unread count.
    Empty for logged-out users — the bell just stays quiet.

    `checked` says whether the read actually happened. A 503 would be wrong
    here: the shell polls this every 60 seconds from every screen, so a blip
    would turn the whole app noisy. But an empty list is not a neutral answer
    either — the bell renders it as "Nothing yet", which is a claim about the
    seller's sales on the surface they check to find out whether they owe a
    buyer a parcel. So it answers 200 and says which of the two it is.
    """
    uid = deps.uid(request)
    if not uid:
        return {"notifications": [], "unread": 0, "checked": True}
    try:
        return {
            "notifications": db.list_notifications(
                uid, limit=max(1, min(limit, 200)), unread_only=unread_only),
            "unread": db.unread_notification_count(uid),
            "checked": True,
        }
    except errors.StorageUnavailable:
        return {"notifications": [], "unread": 0, "checked": False}


@router.post("/api/notifications/read")
def notifications_mark_read(request: Request, payload: dict) -> dict:
    """Mark notifications read: {"ids": [...]} for specific ones, or
    {"all": true} for everything unread."""
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    if payload.get("all"):
        return {"marked": db.mark_notifications_read(uid)}
    ids = [str(i) for i in (payload.get("ids") or []) if i]
    return {"marked": db.mark_notifications_read(uid, ids)}


# --- buyer messages (the unified P2P inbox) ---------------------------------
#
# One inbox across every marketplace that can carry a buyer conversation.
# Person-to-person only: each marketplace adapter excludes its own automated
# mail at the source (eBay asks for conversation_type=FROM_MEMBERS), because
# the whole point of this surface is that it is NOT the notifications bell.
#
# Conversation ids are namespaced "<marketplace>:<id>", which is how one merged
# list routes a click back to the provider that owns the thread.

@router.get("/api/messages")
def messages_list(request: Request, marketplace: str = "",
                  limit: int = 25) -> dict:
    """The merged inbox: {conversations, unread, sources, available, reason}.

    ALWAYS 200, never raises. A header icon polls this every minute, and the
    smoke test fails the build on any failed request — so an eBay outage has
    to read as an empty inbox that explains itself, not as a 502 storm.
    `sources` drives the marketplace toggle and is populated even when a
    source has nothing to give.
    """
    uid = deps.uid(request)
    if not uid:
        return {"conversations": [], "unread": 0, "sources": [],
                "available": False, "reason": "signed_out", "message": ""}
    try:
        out = messages_service.list_conversations(
            uid, marketplace=marketplace, limit=max(1, min(limit, 100)))
    except Exception as exc:  # noqa: BLE001 - a poll must never 500
        log.info("messages: inbox read failed: %s", exc)
        return {"conversations": [], "unread": 0, "sources": [],
                "available": False, "reason": "error", "message": str(exc)}
    live = [s for s in out["sources"] if s.get("available")]
    # The worst reason among supported sources is the one worth showing: with
    # nothing live, "reconnect eBay" is actionable where "no messages" isn't.
    reason = ""
    message = ""
    if not live:
        for s in out["sources"]:
            if s.get("supported") and s.get("reason") not in ("", "disabled"):
                reason, message = s["reason"], s.get("message", "")
                break
        else:
            reason = "disabled"
    out.update({"available": bool(live), "reason": reason, "message": message})
    return out


@router.get("/api/messages/{conversation_id}")
def messages_thread(conversation_id: str, request: Request,
                    limit: int = 50) -> dict:
    """One conversation: {conversation, messages} oldest-first.

    User-initiated, so this one fails honestly rather than soft-emptying.
    """
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    try:
        return messages_service.get_conversation(
            uid, conversation_id, limit=max(1, min(limit, 200)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - adapter errors carry the reason
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/messages/send")
def messages_send(request: Request, payload: dict) -> dict:
    """Reply into a conversation: {"conversation_id": ..., "text": ...}.

    Returns the refreshed thread, so the client renders what the marketplace
    actually stored rather than the optimistic bubble it drew.
    """
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    text = str(payload.get("text") or "").strip()
    cid = str(payload.get("conversation_id") or "").strip()
    if not text:
        raise HTTPException(400, "Write a message first.")
    if not cid:
        raise HTTPException(400, "No conversation was named.")
    # Chattier than an auth endpoint by design, but still bounded: a runaway
    # client must not burn the seller's marketplace API quota, because that
    # quota is shared with publishing.
    if not ratelimit.check(f"msg-send:{uid}", max_attempts=60):
        raise HTTPException(429, "Too many messages just now — give it a minute.")
    try:
        return messages_service.send(uid, cid, text)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/messages/read")
def messages_mark_read(request: Request, payload: dict) -> dict:
    """Mark one conversation read. Best-effort: the badge re-syncs on the next
    poll, so a marketplace that refuses this never becomes an error the seller
    has to look at."""
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    cid = str(payload.get("conversation_id") or "").strip()
    return {"ok": bool(cid) and messages_service.mark_read(uid, cid)}
