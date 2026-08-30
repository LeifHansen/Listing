"""eBay buyer messages (Message API) — the P2P half of the unified inbox.

eBay's Message API (`/commerce/message/v1`) is conversation-shaped: a list of
conversations, each holding messages, with send and status-update calls. That
maps directly onto the inbox this app shows.

**The one thing this module exists to guarantee.** Every read passes
`conversation_type=FROM_MEMBERS`. eBay splits a seller's mail in two —
FROM_EBAY (order notices, policy mail, marketing) and FROM_MEMBERS (an actual
person typing to you) — so the exclusion happens at the source rather than by
guessing at senders. list_conversations() then filters the result AGAIN on the
same field, because that promise is the whole feature and it must not depend
on eBay honouring a query parameter forever.

LIMITED RELEASE, like the Logistics API: commerce.message must be approved for
the keyset before the scope can even be requested, so everything here sits
behind config.EBAY_MESSAGING_ENABLED (see the comment on it in config.py for
why requesting it unapproved is an outage, not an inconvenience).

Two shapes are deliberately defensive:

- The response payload is normalized through _ALT, a table of the key
  spellings eBay might be using. services/metrics.py learned this lesson the
  hard way — the documented spelling was the one the live API never sent, and
  every listing read as 0 views. Here the flatteners always return a complete
  dict, never raise, and log (key names only — message bodies are buyer PII)
  the first time a payload doesn't match, so the real shape shows up in
  production logs instead of in a stack trace.
- The API host is guessed. commerce.identity lives on apiz.ebay.com, not
  api.ebay.com (see ebay_auth.fetch_user_identity); commerce.message may too.
  A 404 on the collection endpoint retries the other host once and latches
  whichever answers, because otherwise a wrong host looks exactly like a
  missing scope and tells the seller to reconnect over and over.

Everything raises MessagesError with a user-facing sentence; no raw eBay JSON
escapes this module.
"""
from __future__ import annotations

import html
import re
import time
from typing import Optional

import httpx

from .. import config
from ..config import log

_TIMEOUT = 30
_MESSAGE = "/commerce/message/v1"
_P2P = "FROM_MEMBERS"        # the entire "no eBay system mail" filter
_SYSTEM = "FROM_EBAY"
_MAX_TEXT = 2000             # eBay's real cap is unverified; be conservative
_SNIPPET = 140

# Conversation lists are cached per user for a minute so that N open tabs on M
# devices cost one upstream call, not N*M. Bounded like metrics._CACHE.
_TTL = 60
_CACHE: dict[str, tuple[float, list[dict]]] = {}


class MessagesError(ValueError):
    """A message call failed — carries a user-facing reason.

    needs_reconnect marks the one cause the seller can actually fix (the
    scope was never granted, because they connected before it existed), so
    the route can offer that instead of a generic outage message.
    """

    def __init__(self, message: str, *, needs_reconnect: bool = False):
        super().__init__(message)
        self.needs_reconnect = needs_reconnect


# --- transport --------------------------------------------------------------

_HOST: Optional[str] = None   # which base answered; latched after first success


def _bases() -> list[str]:
    """Candidate API bases, best guess first.

    commerce.identity is served from apiz.*, not api.* — so commerce.message
    plausibly is too. Once one answers we remember it for the process.
    """
    primary = config.EBAY_API_BASE
    alt = (primary.replace("://api.", "://apiz.") if "://api." in primary
           else primary.replace("://apiz.", "://api."))
    if _HOST:
        return [_HOST]
    return [primary] if alt == primary else [primary, alt]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json",
            "Content-Type": "application/json"}


def _scope_missing(resp: httpx.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    body = resp.text.lower()
    return "insufficient" in body and "scope" in body


_RECONNECT = ("eBay didn't allow reading your messages — reconnect eBay in "
              "Settings to grant the new permission, then try again.")


def _raise_for(resp: httpx.Response, verb: str) -> None:
    if _scope_missing(resp):
        raise MessagesError(_RECONNECT, needs_reconnect=True)
    raise MessagesError(f"eBay returned {resp.status_code} {verb} messages.")


def _request(method: str, token: str, path: str, *, params=None, json=None,
             verb: str = "reading", allow_host_retry: bool = False) -> dict:
    global _HOST
    last: Optional[httpx.Response] = None
    for base in _bases():
        try:
            resp = httpx.request(method, f"{base}{path}", headers=_headers(token),
                                 params=params, json=json, timeout=_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - network/timeout
            raise MessagesError(f"Couldn't reach eBay: {exc}") from exc
        if resp.status_code < 300:
            if _HOST != base:
                _HOST = base
                log.info("ebay_messages: using host %s", base)
            if resp.status_code == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise MessagesError(
                    "eBay sent an unreadable response for messages.") from exc
        last = resp
        # Only a 404 on the collection endpoint is worth trying the other
        # host for. A 404 on one conversation genuinely means "no such
        # conversation", and a 401/403 is a scope answer, not a host answer.
        if not (allow_host_retry and resp.status_code == 404):
            break
    _raise_for(last, verb)
    return {}   # unreachable; keeps type checkers and readers happy


def _get(token: str, path: str, params=None, *, allow_host_retry=False) -> dict:
    return _request("GET", token, path, params=params, verb="reading",
                    allow_host_retry=allow_host_retry)


def _post(token: str, path: str, body: dict, *, verb="sending") -> dict:
    return _request("POST", token, path, json=body, verb=verb)


# --- normalization ----------------------------------------------------------
#
# The exact payload shape is UNVERIFIED (eBay's docs were unreachable when this
# was written). Every key we read is listed here with its plausible spellings,
# so correcting this module against a real response is a one-line edit rather
# than a hunt through the code.

_ALT = {
    "text": ("messageText", "body", "text", "content", "message"),
    "sent_at": ("creationDate", "createdDate", "sentDate", "messageDate", "date"),
    "author": ("senderUsername", "sender", "author", "fromUsername", "from"),
    "message_id": ("messageId", "id"),
    "other_party": ("otherPartyUsername", "otherParty", "counterpartyUsername",
                    "memberUsername", "username", "recipientUsername"),
    "unread": ("unreadCount", "unreadMessageCount", "numUnread"),
    "read_flag": ("read", "isRead", "hasBeenRead"),
    "messages": ("messages", "messageDetails", "messageSummaries", "items"),
    "conversations": ("conversations", "conversationDetails",
                      "conversationSummaries", "items"),
    "latest": ("latestMessage", "lastMessage", "mostRecentMessage"),
}

_SEEN_SHAPES: set[str] = set()


def _log_shape_once(kind: str, payload: dict) -> None:
    """Record an unrecognized payload shape once per process.

    Key NAMES only, never values: message bodies are buyer PII and must not
    reach the logs. This line is how the real shape gets discovered without
    the docs — treat one appearing in production as a bug report from eBay.
    """
    if kind in _SEEN_SHAPES or not isinstance(payload, dict):
        return
    _SEEN_SHAPES.add(kind)
    log.info("ebay_messages: unfamiliar %s shape, keys=%s",
             kind, sorted(payload)[:20])


def _first(d: dict, group: str, default=""):
    """First present, non-empty value among a key group's spellings.

    Descends one level into a dict value, so `sender: "x"` and
    `sender: {"username": "x"}` both read as "x". Never raises.
    """
    if not isinstance(d, dict):
        return default
    for key in _ALT.get(group, ()):
        if key not in d:
            continue
        val = d[key]
        if isinstance(val, dict):
            for inner in ("username", "userId", "value", "text", "name"):
                if val.get(inner):
                    return val[inner]
            continue
        if val not in (None, "", []):
            return val
    return default


def _pick_dict(d: dict, group: str) -> dict:
    """First value in a key group that is itself a container.

    Separate from _first because that one DESCENDS into a dict looking for a
    username — the right move for a scalar field spelled as an object, and
    exactly wrong when the object IS the value (latestMessage).
    """
    if not isinstance(d, dict):
        return {}
    for key in _ALT.get(group, ()):
        val = d.get(key)
        if isinstance(val, dict) and val:
            return val
    return {}


def _pick_list(d: dict, group: str) -> list:
    if not isinstance(d, dict):
        return []
    for key in _ALT.get(group, ()):
        val = d.get(key)
        if isinstance(val, list):
            return val
    return []


# Elements whose CONTENT is code, not prose: strip them whole rather than
# unwrapping them, or "alert(1)" survives into the message body.
_DROP = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>|</p\s*>", re.I)
_WS = re.compile(r"[ \t\r\f\v]+")


def _plain_text(raw) -> str:
    """Strip a message body to plain text.

    Done on the SERVER so no component is ever one careless
    dangerouslySetInnerHTML away from stored XSS — these bodies are typed by
    strangers and eBay allows some markup in them.
    """
    s = str(raw or "")
    s = _DROP.sub("", s)
    s = _BR.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def _as_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _message_to_dict(m: dict, me: str = "") -> dict:
    """One message -> the shape a bubble renders. Never raises."""
    if not isinstance(m, dict):
        return {"id": "", "from_me": False, "author": "", "text": "", "sent_at": ""}
    if "messageText" not in m and "body" not in m and "text" not in m:
        _log_shape_once("message", m)
    author = str(_first(m, "author") or "")
    # Direction, safest resolution last. Falsely rendering the BUYER's words
    # as your own outgoing bubble is a trust bug, so an unknown author is
    # always treated as inbound.
    explicit = m.get("sentByMe", m.get("isSender", m.get("outbound")))
    if isinstance(explicit, bool):
        from_me = explicit
    else:
        from_me = bool(me) and author.lower() == me.lower()
    return {
        "id": str(_first(m, "message_id") or ""),
        "from_me": from_me,
        "author": author,
        "text": _plain_text(_first(m, "text")),
        "sent_at": str(_first(m, "sent_at") or ""),
    }


def _conversation_to_dict(c: dict, me: str = "") -> dict:
    """One ConversationDetail -> the shape the inbox renders. Never raises.

    Every key is always present, so no caller needs a .get() with a default
    and no component can crash on an absent field.
    """
    if not isinstance(c, dict):
        c = {}
    if "conversationId" not in c:
        _log_shape_once("conversation", c)
    raw_id = str(c.get("conversationId") or c.get("id") or "")
    latest = _pick_dict(c, "latest")
    last_msg = _message_to_dict(latest, me) if latest else None

    unread = _as_int(_first(c, "unread", 0), 0)
    if not unread and last_msg and not last_msg["from_me"]:
        # eBay may not expose a per-conversation count. Fall back to "the last
        # message is theirs and is flagged unread" — documented as a guess to
        # be replaced once a live payload confirms the real field.
        flag = _first(c, "read_flag", None)
        if flag is False or str(flag).lower() == "false":
            unread = 1

    ref_type = str(c.get("referenceType") or "").upper()
    return {
        "raw_id": raw_id,
        "marketplace": "ebay",
        "counterparty": str(_first(c, "other_party") or ""),
        "title": str(c.get("conversationTitle") or ""),
        "snippet": (last_msg["text"][:_SNIPPET] if last_msg else ""),
        "last_at": (last_msg["sent_at"] if last_msg
                    else str(c.get("createdDate") or "")),
        "unread": unread,
        "status": str(c.get("conversationStatus") or ""),
        "item_id": str(c.get("referenceId") or "") if ref_type == "LISTING" else "",
        "conversation_type": str(c.get("conversationType") or ""),
    }


def _is_p2p(conv: dict) -> bool:
    """The belt-and-braces half of the P2P promise.

    The query parameter already asks for FROM_MEMBERS. This drops anything
    that came back labelled otherwise, so a changed default or a renamed
    parameter can't leak eBay's system mail into an inbox that promises never
    to show it. An unlabelled conversation is kept — the request asked for
    members only, and dropping unlabelled rows would empty the inbox if eBay
    simply stops echoing the field.
    """
    return (conv.get("conversation_type") or _P2P).upper() != _SYSTEM


# --- public API -------------------------------------------------------------

def list_conversations(token: str, *, me: str = "", limit: int = 25,
                       cache_key: str = "") -> list[dict]:
    """The seller's buyer conversations, newest first. P2P only."""
    if cache_key:
        hit = _CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _TTL:
            return hit[1]
    data = _get(token, f"{_MESSAGE}/conversation",
                params={"conversation_type": _P2P,
                        "limit": str(max(1, min(limit, 100)))},
                allow_host_retry=True)
    rows = [_conversation_to_dict(c, me) for c in _pick_list(data, "conversations")]
    rows = [r for r in rows if _is_p2p(r) and r["raw_id"]]
    rows.sort(key=lambda r: r["last_at"] or "", reverse=True)
    rows = rows[:limit]
    if cache_key:
        if len(_CACHE) > 200:
            _CACHE.clear()
        _CACHE[cache_key] = (time.time(), rows)
    return rows


def invalidate(cache_key: str) -> None:
    """Drop a user's cached list after they send or read something, so their
    own action isn't invisible until the next poll."""
    _CACHE.pop(cache_key, None)


def get_conversation(token: str, raw_id: str, *, me: str = "",
                     limit: int = 50) -> dict:
    """One thread: {conversation, messages} with messages oldest-first."""
    rid = str(raw_id or "").strip()
    if not rid:
        raise MessagesError("No conversation was named.")
    data = _get(token, f"{_MESSAGE}/conversation/{rid}",
                params={"conversation_type": _P2P,
                        "limit": str(max(1, min(limit, 200)))})
    conv = _conversation_to_dict(data.get("conversation") or data, me)
    if not conv["raw_id"]:
        conv["raw_id"] = rid
    msgs = [_message_to_dict(m, me) for m in _pick_list(data, "messages")]
    # Oldest first (thread order). Undated messages keep the API's order
    # rather than being flung to one end.
    msgs.sort(key=lambda m: m["sent_at"] or "")
    if not _is_p2p(conv):
        raise MessagesError("That conversation isn't a buyer message.")
    return {"conversation": conv, "messages": msgs}


def send_message(token: str, *, text: str, raw_id: str = "",
                 other_party: str = "", me: str = "") -> dict:
    """Send into an existing thread (raw_id) or open a new one (other_party).

    Returns the REFRESHED thread rather than trusting the send response — the
    response shape is unverified, and re-reading makes the contract ours.
    """
    body_text = str(text or "").strip()
    if not body_text:
        raise MessagesError("Write a message first.")
    if bool(raw_id) == bool(other_party):
        raise MessagesError(
            "A message needs exactly one destination — a conversation or a member.")
    payload = {"messageText": body_text[:_MAX_TEXT]}
    if raw_id:
        payload["conversationId"] = raw_id
    else:
        payload["otherPartyUsername"] = other_party
    resp = _post(token, f"{_MESSAGE}/send_message", payload)

    target = raw_id or str(
        resp.get("conversationId") or resp.get("id")
        or (resp.get("conversation") or {}).get("conversationId") or "")
    if target:
        try:
            return get_conversation(token, target, me=me)
        except MessagesError:
            pass    # the send DID land; only the re-read failed
    return {"conversation": {"raw_id": target, "marketplace": "ebay"},
            "messages": []}


def mark_read(token: str, raw_id: str) -> bool:
    """Best-effort "I've read this". Never raises.

    The action name is unverified, so unknown-action rejections must degrade
    to "the badge clears on the next poll" rather than to an error the seller
    sees for an operation they didn't ask for.
    """
    rid = str(raw_id or "").strip()
    if not rid:
        return False
    for action in ("MARK_AS_READ", "READ", "MARKASREAD"):
        try:
            _post(token, f"{_MESSAGE}/conversation/{rid}/update_conversation",
                  {"action": action}, verb="updating")
            return True
        except MessagesError:
            continue
    log.info("ebay_messages: couldn't mark %s read (unknown action name)", rid)
    return False


def unread_total(conversations: list) -> int:
    return sum(_as_int(c.get("unread"), 0) for c in conversations or []
               if isinstance(c, dict))
