"""The monetization engine: AI-token metering, packs, and Stripe checkout.

The app itself is free. Every AI feature spends tokens; each account gets a
monthly free allowance (config.FREE_TOKENS_PER_MONTH, calendar-month UTC, no
rollover) and buys more when it runs out. Purchased tokens never expire and
are spent AFTER free ones (free tokens are the ones that evaporate at month
end, so they burn first).

Pricing rationale (defaults; costs are env-overridable):
  A full AI listing draft runs 4-6 vision calls (identify + item-specifics
  fill + tag zoom/transcribe + occasional maker check) over up to 8 photos.
  On the default Opus-tier vision model ($5/M input, $25/M output) that's
  roughly $0.25 for a typical 3-photo listing and up to ~$0.80 for a maxed-out
  8-photo one. At 5 tokens per draft and a $0.07-$0.12 retail token, a draft
  brings in $0.35-$0.60 — a ~40-60% gross margin in the typical case that
  still covers the worst case at the mid packs. Lighter features (refine,
  shelf scan, photo tools) cost cents and are priced at 1-2 tokens.
  The free allowance (50 = ~10 drafts) costs the operator at most ~$2.50 per
  active user per month — the acquisition spend that makes "free app" true.

Failure policy: tokens are charged up front and refunded when the AI call
fails ("only pay for AI that worked"). On a transient DB error we FAIL OPEN
(allow the call, log loudly): blocking every seller because the billing DB
hiccuped costs more trust than the pennies of un-metered usage — and auth
already fails closed before this layer when the DB is truly down.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import time
from typing import Optional
from urllib.parse import quote

import httpx

from .. import config, db
from ..config import log
from . import owed_refunds

# --- Feature costs (tokens) -------------------------------------------------
# Overridable per deployment: TOKENS_COST_IDENTIFY=4 etc. lets the operator
# re-tune margins after switching VISION_MODEL to a cheaper tier.
_DEFAULT_COSTS = {
    # Full AI listing draft: identify + category + item specifics + tag read
    # + maker check. Same price per item in a bulk batch (the batch's photo
    # grouping pass is bundled into it).
    "identify": 5,
    # Free-form "refine with AI" instruction on an existing draft.
    "refine": 1,
    # Standalone "Autofill item specifics" button (bundled free inside a draft).
    "specifics": 2,
    # Shop Mode shelf scan (one video's frames).
    "shelf_scan": 2,
    # AI photo tools, per photo: background removal / auto-clean / smart crop.
    "image_ai": 1,
}


def _load_costs() -> dict:
    out = {}
    for feature, default in _DEFAULT_COSTS.items():
        try:
            v = int(os.getenv(f"TOKENS_COST_{feature.upper()}", "") or default)
        except ValueError:
            v = default
        out[feature] = max(0, v)
    return out


COSTS = _load_costs()

# --- Token packs (one-time purchases; tokens never expire) ------------------
# Anchored so the effective per-token price falls with size ($0.12 -> $0.07)
# while every pack stays comfortably above the API cost of the usage it buys.
PACKS = [
    {"id": "starter", "tokens": 50, "usd_cents": 599, "label": "Starter"},
    {"id": "plus", "tokens": 120, "usd_cents": 1199, "label": "Plus"},
    {"id": "pro", "tokens": 300, "usd_cents": 2499, "label": "Pro"},
    {"id": "power", "tokens": 1000, "usd_cents": 6999, "label": "Power seller"},
]


def pack(pack_id: str) -> Optional[dict]:
    return next((p for p in PACKS if p["id"] == pack_id), None)


# --- Billing state ----------------------------------------------------------

def enabled() -> bool:
    return config.tokens_enabled()


def cost(feature: str, units: int = 1) -> int:
    return COSTS.get(feature, 0) * max(1, units)


def period_now(now: Optional[_dt.datetime] = None) -> str:
    """The current free-allowance period, e.g. '2026-08' (UTC calendar month)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def next_reset(now: Optional[_dt.datetime] = None) -> str:
    """ISO date the free allowance next resets (first of next month, UTC)."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return f"{year:04d}-{month:02d}-01"


# The arithmetic of one debit lives next to the ledger that applies it, as
# db.plan_spend -- token_spend is its only caller, and it applies it under the
# row lock. It used to be defined HERE as "the testable core" while
# db.token_spend carried its own inline copy of the same split, so the billing
# tests asserted against a function production never called and the two were
# free to drift apart without a single test failing.


def status(user_id: Optional[str]) -> dict:
    """Balance + catalog for the UI. Safe for anonymous callers (no balance)."""
    out = {
        "enabled": enabled(),
        "stripe": config.stripe_ready(),
        "free_quota": config.FREE_TOKENS_PER_MONTH,
        "resets_on": next_reset(),
        "costs": dict(COSTS),
        "packs": [{k: p[k] for k in ("id", "tokens", "usd_cents", "label")}
                  for p in PACKS],
    }
    if not enabled() or not user_id:
        return out
    snap = db.token_status(user_id, period_now(), config.FREE_TOKENS_PER_MONTH)
    if snap is None:  # DB hiccup — report the quota, not a scary zero
        snap = {"free_remaining": config.FREE_TOKENS_PER_MONTH, "purchased": 0}
    out.update({
        "free_remaining": snap["free_remaining"],
        "purchased": snap["purchased"],
        "total": snap["free_remaining"] + snap["purchased"],
    })
    return out


def spend(user_id: str, feature: str, units: int = 1) -> Optional[dict]:
    """Debit a feature's cost. Returns the db.token_spend result dict, or None
    when billing is off / the feature is free / the DB failed (fail open)."""
    if not enabled():
        return None
    amount = cost(feature, units)
    if amount <= 0:
        return None
    res = db.token_spend(user_id, amount, config.FREE_TOKENS_PER_MONTH,
                         period_now(), feature=feature)
    if res is None:
        log.warning("tokens: DB unavailable — allowing %s for %s un-metered",
                    feature, user_id)
        return None
    res["user_id"] = user_id  # refund() needs it
    return res


def refund(spend_result: Optional[dict], units: Optional[int] = None) -> bool:
    """Give back a (possibly partial) spend after an AI failure. Accepts the
    exact dict spend() returned; no-ops on None/declined/free spends.

    Returns whether the refund actually committed — and when it did not, the
    debt is written to the volume so a later pass can settle it.

    That answer used to be discarded. `db.token_refund` returns False when the
    write did not happen, so a database blip in the refund window left the
    seller charged for AI that failed, with nothing anywhere recording that
    they were owed anything. The crash recovery in main only covers jobs whose
    PROCESS died; a job that finished normally with a failed refund was never
    revisited. See services/owed_refunds for why the record goes on the volume
    rather than into the database that just refused the write.
    """
    if not spend_result or not spend_result.get("ok") or not spend_result.get("entry_id"):
        return False
    user_id = spend_result.get("user_id", "")
    paid = db.token_refund(user_id, spend_result["entry_id"], units=units)
    if not paid:
        owed_refunds.owe(spend_result, units=units)
    return bool(paid)


def receipts(*spend_results: Optional[dict]) -> list[dict]:
    """The part of each spend() result worth writing down, so a charge can
    still be given back after the process that made it is gone.

    Every in-process refund path holds the spend dict in a local variable,
    which is exactly what a machine that is killed rather than stopped takes
    with it — no finally block runs, and the seller has paid for a draft that
    was never saved. Persisting these lets a later boot settle up (see
    main._settle_interrupted_jobs).

    Only the three fields refund() actually reads, so the result stays small
    enough to mirror on every job and carries no account state beyond the ids
    already on the record. Declined and un-metered spends yield nothing:
    there is no charge to give back.

    ONLY pass spends that are refunded all-or-nothing. A charge that some code
    path may give back in PART (today: background removal, one photo's worth
    per failed cutout) must never be recorded here.

    That restriction is now belt-and-braces rather than load-bearing:
    db.token_refund sums what a spend has already had returned and caps every
    refund at the remainder, so a full refund following a partial one gives
    back only what is left. That running total lives in the ledger, which is
    exactly what survives the killed process this function exists to recover
    from — it used to live only in the process, which is why the rule was
    absolute. Keeping the rule anyway: under-refunding is recoverable, paying
    a seller twice out of the token balance is not, and the cap is one
    invariant rather than two."""
    return [{"ok": True, "entry_id": r["entry_id"], "user_id": r.get("user_id", "")}
            for r in spend_results
            if r and r.get("ok") and r.get("entry_id")]


def refund_all(receipt_list: Optional[list]) -> int:
    """Give back every receipt in a list, in full. Returns how many were
    attempted. Safe to call more than once with the same list: a FULL refund
    is keyed in the ledger by the spend's own entry id (db.token_refund's
    unique `ref`), so the second attempt is rejected by the database rather
    than paying the user twice. That guarantee is what lets the startup pass
    below re-run on every boot without keeping a "already refunded" flag of
    its own."""
    done = 0
    for item in receipt_list or []:
        if isinstance(item, dict):
            refund(item)
            done += 1
    return done


def insufficient_message(res: dict) -> str:
    """The 402 detail. The word 'token' is load-bearing: the frontend opens
    the buy-tokens dialog for 402s that mention tokens (and must NOT for the
    unrelated 402 the AI layer maps Anthropic credit exhaustion to)."""
    left = res.get("free_remaining", 0) + res.get("purchased", 0)
    return (f"Out of AI tokens ({left} left). Your free "
            f"{config.FREE_TOKENS_PER_MONTH} reset on {next_reset()} — or buy "
            "a token pack to keep going now.")


# --- Stripe (raw HTTPS via httpx; no SDK dependency) ------------------------

_STRIPE_API = "https://api.stripe.com/v1"


def _stripe_post(path: str, data: dict) -> dict:
    resp = httpx.post(f"{_STRIPE_API}{path}", data=data,
                      auth=(config.STRIPE_SECRET_KEY, ""), timeout=30)
    body = resp.json()
    if resp.status_code >= 400:
        msg = (body.get("error") or {}).get("message", f"HTTP {resp.status_code}")
        raise RuntimeError(f"Stripe error: {msg}")
    return body


def _stripe_get(path: str) -> dict:
    resp = httpx.get(f"{_STRIPE_API}{path}",
                     auth=(config.STRIPE_SECRET_KEY, ""), timeout=30)
    body = resp.json()
    if resp.status_code >= 400:
        msg = (body.get("error") or {}).get("message", f"HTTP {resp.status_code}")
        raise RuntimeError(f"Stripe error: {msg}")
    return body


def create_checkout(user_id: str, pack_id: str, base_url: str) -> str:
    """Create a Stripe Checkout session for a pack; returns the URL to send
    the buyer to. The user/pack ride in metadata so the webhook (or the
    client-side confirm) can credit the right account."""
    p = pack(pack_id)
    if p is None:
        raise ValueError("Unknown token pack")
    if not config.stripe_ready():
        raise RuntimeError("Payments aren't configured on this server (STRIPE_SECRET_KEY).")
    session = _stripe_post("/checkout/sessions", {
        "mode": "payment",
        "client_reference_id": user_id,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(p["usd_cents"]),
        "line_items[0][price_data][product_data][name]":
            f"Thryft Shop AI tokens — {p['label']} ({p['tokens']} tokens)",
        "line_items[0][quantity]": "1",
        "metadata[user_id]": user_id,
        "metadata[pack_id]": p["id"],
        "metadata[tokens]": str(p["tokens"]),
        # {CHECKOUT_SESSION_ID} is substituted by Stripe on redirect.
        "success_url": f"{base_url}/?tokens=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base_url}/?tokens=cancelled",
    })
    return session["url"]


def confirm_checkout(user_id: str, session_id: str) -> dict:
    """Client-side fallback after the Checkout redirect: verify with Stripe
    that the session is paid and belongs to this user, then credit
    (idempotently — the webhook may already have)."""
    if not session_id or "/" in session_id:
        raise ValueError("Invalid session id")
    session = _stripe_get(f"/checkout/sessions/{quote(session_id, safe='')}")
    meta = session.get("metadata") or {}
    if session.get("payment_status") != "paid":
        raise ValueError("This purchase hasn't completed yet.")
    if meta.get("user_id") != user_id:
        raise ValueError("This purchase belongs to a different account.")
    tokens = int(meta.get("tokens", "0") or 0)
    if tokens <= 0:
        raise ValueError("No token amount recorded on this purchase.")
    res = db.token_credit(user_id, tokens, ref=str(session.get("id")),
                          note=f"pack {meta.get('pack_id', '?')}")
    if res is None:
        raise RuntimeError("Payment received but the database is unavailable — "
                           "your tokens will be credited automatically; contact "
                           "support if they don't appear shortly.")
    return {"credited": not res.get("already"), "tokens": tokens}


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str,
                            tolerance: int = 300,
                            now: Optional[float] = None) -> bool:
    """Verify a Stripe-Signature header (t=...,v1=...) over the raw body.
    Pure — unit-testable without Stripe.

    EVERY v1 candidate is checked, not just one. The header carries a v1 per
    active endpoint secret, and Stripe's documented way to rotate a webhook
    secret is to serve both for a while — during which each delivery arrives
    signed twice. Parsing the header into a dict kept whichever came last, so
    for the whole rotation window half the deliveries verified and half were
    rejected as forged: token purchases silently not credited, refunds not
    clawed back, and Stripe retrying against the same coin flip.
    """
    if not sig_header or not secret:
        return False
    ts = ""
    sigs: list[str] = []
    for kv in sig_header.split(","):
        key, _, value = kv.partition("=")
        key, value = key.strip(), value.strip()
        if key == "t" and not ts:
            ts = value
        elif key == "v1" and value:
            sigs.append(value)
    if not ts or not sigs:
        return False
    try:
        ts_val = int(ts)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts_val) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                        hashlib.sha256).hexdigest()
    # compare_digest against each candidate rather than `expected in sigs`:
    # the whole point of this comparison is that it does not leak, through
    # timing, how much of a forged signature was right.
    return any(hmac.compare_digest(expected, sig) for sig in sigs)


# Events that mean the buyer's money went back to them. A refund is usually
# the operator's own doing; a dispute is the card network's. Either way the
# tokens have to follow the money, or a refund becomes a way to buy AI for
# free — and a chargeback becomes that plus a fee.
_REVERSAL_EVENTS = (
    "charge.refunded",
    "charge.dispute.created",
    "charge.dispute.closed",
)


def _session_id_for_payment_intent(payment_intent: str) -> str:
    """The Checkout session that produced a payment — the id purchases are
    credited under, and so the handle a reversal needs.

    Refund and dispute events carry the charge and its payment_intent, never
    the session, so this asks Stripe to map back. Returns "" when it can't.
    """
    if not payment_intent or not config.stripe_ready():
        return ""
    try:
        res = _stripe_get(
            f"/checkout/sessions?payment_intent={quote(payment_intent, safe='')}&limit=1")
        rows = res.get("data") or []
        return str(rows[0].get("id") or "") if rows else ""
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        log.warning("tokens: couldn't map payment_intent %s to a session: %s",
                    payment_intent, exc)
        return ""


def _handle_reversal(event_type: str, charge: dict) -> dict:
    """Debit the tokens from a purchase whose money was returned."""
    # A dispute that closed in OUR favour returns the money to us, so the
    # tokens stay where they are — only a lost dispute is a reversal.
    if event_type == "charge.dispute.closed":
        if (charge.get("status") or "").lower() != "lost":
            return {"handled": False, "reason": "dispute not lost"}
        payment_intent = str(charge.get("payment_intent") or "")
    else:
        payment_intent = str(charge.get("payment_intent") or "")
        # A partial refund is not a full reversal; treat only a full one as
        # cancelling the purchase rather than silently over-debiting.
        if event_type == "charge.refunded":
            amount = int(charge.get("amount") or 0)
            refunded = int(charge.get("amount_refunded") or 0)
            if amount and refunded and refunded < amount:
                log.info("tokens: partial refund (%d/%d) on %s — tokens left in "
                         "place, adjust by hand if needed",
                         refunded, amount, payment_intent)
                return {"handled": False, "reason": "partial refund"}

    session_id = _session_id_for_payment_intent(payment_intent)
    if not session_id:
        # Non-2xx so Stripe retries: a transient API failure here would
        # otherwise silently let the tokens stay bought.
        raise RuntimeError(f"Couldn't resolve session for {payment_intent}; retry")
    res = db.token_reverse_purchase(session_id, reason=event_type)
    if res is None:
        # Either the DB is down (retry is right) or this payment was never a
        # token purchase — which is normal if Stripe is used for anything else.
        log.info("tokens: no reversible purchase for session %s (%s)",
                 session_id, event_type)
        return {"handled": False, "reason": "no matching purchase"}
    if not res.get("already"):
        log.warning("tokens: reversed %d token(s) from %s after %s%s",
                    res.get("reversed", 0), res.get("user_id"), event_type,
                    f" ({res['shortfall']} already spent)" if res.get("shortfall") else "")
    return {"handled": True, "reversed": res.get("reversed", 0),
            "shortfall": res.get("shortfall", 0)}


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process a Stripe webhook delivery. Credits checkout.session.completed,
    reverses refunds and lost disputes; everything else is acknowledged and
    ignored."""
    if not verify_stripe_signature(payload, sig_header, config.STRIPE_WEBHOOK_SECRET):
        raise PermissionError("Invalid Stripe signature")
    event = json.loads(payload)
    event_type = event.get("type")
    if event_type in _REVERSAL_EVENTS:
        return _handle_reversal(event_type, (event.get("data") or {}).get("object") or {})
    if event_type != "checkout.session.completed":
        return {"handled": False}
    session = (event.get("data") or {}).get("object") or {}
    meta = session.get("metadata") or {}
    user_id = meta.get("user_id", "")
    tokens = int(meta.get("tokens", "0") or 0)
    if session.get("payment_status") != "paid" or not user_id or tokens <= 0:
        return {"handled": False}
    res = db.token_credit(user_id, tokens, ref=str(session.get("id")),
                          note=f"pack {meta.get('pack_id', '?')} (webhook)")
    if res is None:
        # Non-2xx makes Stripe retry the delivery — exactly what we want
        # while the DB is down.
        raise RuntimeError("Database unavailable; retry")
    log.info("tokens: credited %d to %s (stripe %s, already=%s)",
             tokens, user_id, session.get("id"), res.get("already"))
    return {"handled": True, "credited": not res.get("already")}
