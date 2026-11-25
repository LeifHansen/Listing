"""Cross-marketplace buyer messaging: the contract, the id namespace, the roster.

One inbox, many marketplaces. Every marketplace that can carry a buyer↔seller
conversation exposes the same five methods on its provider; this module says
what those are, decides which providers actually have them, and owns the
namespaced conversation id that lets one merged list route each thread back to
the marketplace it came from.

Import-light on purpose — stdlib only, no httpx and no sqlalchemy — so the
registry and these pure helpers stay importable under CI's minimal install.
The transport lives in each provider's own service module.

**Only person-to-person messages belong here.** A marketplace's automated mail
(order confirmations, policy notices, marketing) is a different thing with a
different surface — the notifications bell — and an adapter that lets one
through has broken this module's only real promise.

Adding marketplace N+1 = implement the five methods on its provider and the
inbox picks it up; nothing here needs editing.
"""
from __future__ import annotations

import re

# Every method a provider needs before the inbox will talk to it -- the
# optional messaging half of a marketplace provider:
#
#   messaging_status(uid) -> {available, reason, message}. `reason` is "" when
#       the inbox can actually read this marketplace, else a stable token the
#       UI maps to copy: "disabled" (operator flag off), "not_connected",
#       "needs_reconnect" (scope never granted), "unsupported", "error".
#   list_conversations(uid, limit=25) -> list[dict]
#   get_conversation(uid, raw_id, limit=50) -> dict
#   send_message(uid, raw_id, text) -> dict
#   mark_read(uid, raw_id) -> bool
#
# The three readers return app-shaped dicts, never raw marketplace JSON, and
# ids are RAW (unqualified) — this module adds the marketplace prefix.
#
# Checked with getattr rather than isinstance so a provider that carries none
# of them (Etsy and Depop today) is simply absent from the inbox instead of
# erroring.
MESSAGING_METHODS = (
    "messaging_status",
    "list_conversations",
    "get_conversation",
    "send_message",
    "mark_read",
)


def supports_messaging(provider) -> bool:
    """Does this provider implement the whole messaging contract?

    All five or none: a half-implemented provider would show conversations the
    seller then couldn't open or answer, which is worse than not listing it.
    """
    return all(callable(getattr(provider, m, None)) for m in MESSAGING_METHODS)


# --- the id namespace -------------------------------------------------------
#
# Conversations from different marketplaces share one list, so ids carry their
# origin: "ebay:1234". That is what lets a click on a merged row route back to
# the right provider without a lookup table.

SEP = ":"

# What a marketplace key looks like (see registry keys: "ebay", "etsy").
_KEYISH = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")


def qualify(marketplace: str, raw_id) -> str:
    """"ebay" + "1234" -> "ebay:1234". Empty raw id yields "" (never a bare
    prefix, which would look like a real id and route nowhere)."""
    raw = str(raw_id or "").strip()
    key = str(marketplace or "").strip()
    if not raw or not key:
        return ""
    return f"{key}{SEP}{raw}"


def split(conversation_id: str, default: str = "") -> tuple[str, str]:
    """"ebay:1234" -> ("ebay", "1234"); "1234" -> (default, "1234").

    Splits on the FIRST separator only — a marketplace's own id is free to
    contain colons. An id is treated as namespaced when its prefix LOOKS like
    a marketplace key, whether or not that marketplace exists: the caller then
    fails to resolve it and says so. Falling back to the default here instead
    would route a reply for "shopify:1" to whichever marketplace happens to be
    first — sending a seller's words to the wrong platform, which is far worse
    than a 404.

    Only a genuinely bare id falls back, so ids minted before this namespace
    existed still resolve.
    """
    cid = str(conversation_id or "").strip()
    if not cid:
        return default, ""
    head, sep, tail = cid.partition(SEP)
    if sep and tail and _KEYISH.match(head):
        return head, tail
    return default, cid
