"""Postgres (Neon) persistence via SQLAlchemy - optional and resilient.

If DATABASE_URL is unset, every function is a safe no-op and the app runs
purely off the filesystem store. When a DB is configured, listing drafts are
persisted durably so they survive restarts and power a "My Listings" history
(the foundation for the web + mobile product).

Design rule: a database problem must NEVER break a request. Writes swallow
errors (and log); reads return empty/None on failure. Use db_status() to see
whether the DB is actually reachable.
"""
from __future__ import annotations

import datetime as _dt
import time as _time
import uuid as _uuid
from typing import Optional

from sqlalchemy import (DateTime, JSON, String, create_engine, delete, select,
                        text)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config
from .config import log

_engine = None
_initialized = False


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80), default="")
    # New-listing defaults (package weight/dims, quantity, condition, …) that
    # pre-fill every AI draft so repeat sellers stop re-typing them.
    prefs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class EbayAccount(Base):
    __tablename__ = "ebay_accounts"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    refresh_token: Mapped[str] = mapped_column(String(2048), default="")
    ebay_username: Mapped[str] = mapped_column(String(128), default="")
    ebay_email: Mapped[str] = mapped_column(String(255), default="")
    fulfillment_policy_id: Mapped[str] = mapped_column(String(64), default="")
    payment_policy_id: Mapped[str] = mapped_column(String(64), default="")
    return_policy_id: Mapped[str] = mapped_column(String(64), default="")
    merchant_location_key: Mapped[str] = mapped_column(String(64), default="")
    ship_from_postal: Mapped[str] = mapped_column(String(16), default="")
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class MarketplaceAccount(Base):
    """One row per (user, marketplace) connection for every marketplace other
    than eBay — eBay predates this table and stays on ebay_accounts; the
    provider layer hides the split. Marketplace-specific settings (Etsy's
    shop id / shipping profile / return policy defaults, etc.) ride the JSON
    column so future fields need no migration."""

    __tablename__ = "marketplace_accounts"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(32), primary_key=True)
    refresh_token: Mapped[str] = mapped_column(String(4096), default="")
    external_username: Mapped[str] = mapped_column(String(128), default="")
    external_id: Mapped[str] = mapped_column(String(64), default="")
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class ListingRecord(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    title: Mapped[str] = mapped_column(String(255), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class TokenAccount(Base):
    """Per-user AI-token balance (the monetization engine's one mutable row).

    `purchased` never expires; the monthly free allowance is tracked as
    `free_used` within `free_period` ("YYYY-MM", UTC) and lazily resets the
    first time the account is touched in a new month — no cron needed, and a
    user who never comes back costs nothing to reset."""

    __tablename__ = "token_accounts"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purchased: Mapped[int] = mapped_column(default=0)
    free_used: Mapped[int] = mapped_column(default=0)
    free_period: Mapped[str] = mapped_column(String(7), default="")
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class TokenLedger(Base):
    """Append-only audit trail of every token movement. `ref` is unique so a
    purchase (Stripe session id) or a refund (spend entry id) can never be
    applied twice — the webhook and the client-side confirm race for the same
    credit, and the DB, not the request order, settles it."""

    __tablename__ = "token_ledger"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # spend | refund | purchase | grant
    feature: Mapped[str] = mapped_column(String(48), default="")
    tokens: Mapped[int] = mapped_column(default=0)  # signed: spends are negative
    free_part: Mapped[int] = mapped_column(default=0)
    paid_part: Mapped[int] = mapped_column(default=0)
    # The free_period the spend happened in — a refund only restores the free
    # part if the month hasn't rolled over (free tokens expire monthly).
    period: Mapped[str] = mapped_column(String(7), default="")
    ref: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


def enabled() -> bool:
    return bool(config.DATABASE_URL)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _normalize_url(url: str) -> str:
    """Accept Neon's standard URL and point it at the psycopg (v3) driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _get_engine():
    global _engine, _initialized
    if not enabled():
        return None
    if _engine is None:
        # Pool sized against the concurrency that can actually reach it, and
        # set to fail fast rather than hang. Almost every route here is a sync
        # `def`, so FastAPI runs it in anyio's 40-thread pool, and background
        # daemons (bulk, identify, the R2 pushes, the store sync) add more on
        # top. Against SQLAlchemy's default 5+10 that meant callers past the
        # fifteenth queued for the default 30s checkout and then hit a timeout
        # that db.py swallows into []/None — a half-minute hang followed by a
        # confidently empty answer. pool_recycle matters for Neon specifically:
        # it drops idle connections, and a stale one surfaces as a failed query
        # on somebody's next request.
        _engine = create_engine(
            _normalize_url(config.DATABASE_URL),
            pool_pre_ping=True, pool_size=10, max_overflow=20,
            pool_timeout=5, pool_recycle=300,
        )
    if not _initialized:
        Base.metadata.create_all(_engine)  # may raise if DB unreachable
        # Lightweight migrations for DBs created before a column existed. Each
        # ALTER is separately guarded so an already-applied one doesn't skip
        # the rest.
        for stmt in (
            "ALTER TABLE listings ADD COLUMN user_id VARCHAR(64)",
            "ALTER TABLE ebay_accounts ADD COLUMN ebay_username VARCHAR(128) DEFAULT ''",
            "ALTER TABLE ebay_accounts ADD COLUMN ebay_email VARCHAR(255) DEFAULT ''",
            "ALTER TABLE ebay_accounts ADD COLUMN ship_from_postal VARCHAR(16) DEFAULT ''",
            "ALTER TABLE users ADD COLUMN display_name VARCHAR(80) DEFAULT ''",
            "ALTER TABLE users ADD COLUMN prefs JSON",
        ):
            try:
                with _engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception:  # noqa: BLE001 - column already exists
                pass
        _initialized = True
    return _engine


def _record_to_dict(rec: ListingRecord) -> dict:
    return {
        "id": rec.id,
        "user_id": rec.user_id,
        "status": rec.status,
        "title": rec.title,
        "listing": rec.data,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


def upsert_listing(
    listing_id: str, listing: dict, status: str = "draft", user_id: Optional[str] = None,
    when: Optional[_dt.datetime] = None,
) -> None:
    """Create or update a listing row. Never raises.

    `when` overrides the row's updated_at. The store sync passes each eBay
    listing's own start time, because stamping every row with "now" on every
    sync made 600 listings share one timestamp and destroyed the ordering the
    dashboard reads — recency has to come from the listing, not the sync.
    A sync that changes nothing doesn't touch the row at all."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            now = _now()
            if rec is None:
                rec = ListingRecord(id=listing_id, created_at=when or now)
                s.add(rec)
            elif rec.status == status and rec.data == listing:
                return  # nothing changed — leave updated_at where it was
            # Claim ownership only if unowned; never reassign a listing to
            # whoever happens to save it (session ids can leak in URLs).
            if user_id is not None and rec.user_id in (None, user_id):
                rec.user_id = user_id
            rec.status = status
            rec.title = (listing.get("title") or "")[:255]
            rec.data = listing
            rec.updated_at = when or now
            s.commit()
    except Exception as exc:  # noqa: BLE001 - DB must never break a request
        log.warning(f"db: upsert_listing failed: {exc}")


def list_listings(limit: int = 50, user_id: Optional[str] = None) -> list[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            q = select(ListingRecord)
            if user_id is not None:
                q = q.where(ListingRecord.user_id == user_id)
            q = q.order_by(ListingRecord.updated_at.desc()).limit(limit)
            rows = s.execute(q).scalars().all()
            return [_record_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: list_listings failed: {exc}")
        return []


def all_listing_ids() -> Optional[set[str]]:
    """Every known listing id — lets the app tell real listing dirs from
    orphaned bulk staging / abandoned uploads on disk. Returns None (not an
    empty set) when there's no DB or the read fails, so callers can safely skip
    cleanup rather than mistake every dir for an orphan."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            return set(s.execute(select(ListingRecord.id)).scalars().all())
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: all_listing_ids failed: {exc}")
        return None


# --- users -----------------------------------------------------------------

def _user_to_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email,
            "display_name": getattr(u, "display_name", "") or "",
            "created_at": u.created_at.isoformat()}


def update_user(user_id: str, display_name: Optional[str] = None) -> Optional[dict]:
    """Update profile fields; returns the updated user dict (or None)."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            u = s.get(User, user_id)
            if not u:
                return None
            if display_name is not None:
                u.display_name = display_name[:80]
            s.commit()
            return _user_to_dict(u)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: update_user failed: {exc}")
        return None


# Sentinel so callers can tell "email taken" (user error, 409) apart from
# "database broken" (server error, 503).
EMAIL_TAKEN = object()


def get_prefs(user_id: str) -> dict:
    """The user's new-listing defaults ({} when unset). Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return {}
        with Session(eng) as s:
            u = s.get(User, user_id)
            return dict(u.prefs) if u and isinstance(u.prefs, dict) else {}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_prefs failed: {exc}")
        return {}


def save_prefs(user_id: str, prefs: dict) -> dict:
    """Merge new values into the user's defaults; returns the merged dict.
    Returns {} if there's no DB / no such user (the caller surfaces that)."""
    try:
        eng = _get_engine()
        if eng is None:
            return {}
        with Session(eng) as s:
            u = s.get(User, user_id)
            if u is None:
                return {}
            merged = dict(u.prefs) if isinstance(u.prefs, dict) else {}
            merged.update(prefs)
            u.prefs = merged
            s.commit()
            return merged
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: save_prefs failed: {exc}")
        return {}


def create_user(user_id: str, email: str, password_hash: str):
    """Create a user. Returns the user dict, EMAIL_TAKEN, or None on DB error."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
                return EMAIL_TAKEN
            u = User(id=user_id, email=email, password_hash=password_hash, created_at=_now())
            s.add(u)
            s.commit()
            return _user_to_dict(u)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: create_user failed: {exc}")
        return None


def get_user_by_email(email: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            u = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not u:
                return None
            d = _user_to_dict(u)
            d["password_hash"] = u.password_hash
            return d
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_user_by_email failed: {exc}")
        return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            u = s.get(User, user_id)
            return _user_to_dict(u) if u else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_user_by_id failed: {exc}")
        return None


def get_password_hash(user_id: str) -> Optional[str]:
    """The stored bcrypt hash for a user (None if absent). Kept separate from
    get_user_by_id so the hash never rides along in a dict that gets returned
    to a client by accident."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            u = s.get(User, user_id)
            return u.password_hash if u else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_password_hash failed: {exc}")
        return None


def delete_user(user_id: str) -> Optional[list[str]]:
    """Erase a user and everything keyed to them, in one transaction.

    Returns the ids of the deleted listings so the caller can purge their
    photos from disk and R2; returns None if nothing was deleted (no such
    user, no DB, or the delete failed).

    This is the one function in this module that must NOT fail quietly. The
    module-wide rule is "a database problem never breaks a request", but a
    delete that silently does nothing while telling the user their account is
    gone is exactly the trap this feature exists to avoid — so failure is
    reported and the caller surfaces it.

    No table here has a foreign key to users, so nothing cascades on its own;
    every table keyed to a user is cleared explicitly below. When a table is
    added to this module it MUST be added here too — Apple's account-deletion
    requirement (App Store guideline 5.1.1(v)) and every privacy policy we
    publish promise that "delete my account" leaves nothing behind. Leaving a
    marketplace refresh token behind is worse than a stale row: it is a live
    credential for someone else's eBay/Etsy/Depop account.
    """
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            u = s.get(User, user_id)
            if u is None:
                return None
            # Select only the ids, then delete set-wise. Loading whole rows to
            # collect ids would pull every listing's JSON blob across the wire
            # (a synced store is thousands of them) just to throw it away, and
            # ORM-deleting them one at a time would issue a statement each.
            listing_ids = list(s.execute(
                select(ListingRecord.id).where(ListingRecord.user_id == user_id)
            ).scalars().all())
            s.execute(
                delete(ListingRecord).where(ListingRecord.user_id == user_id))
            s.execute(delete(EbayAccount).where(EbayAccount.user_id == user_id))
            # Etsy/Depop/every future marketplace: these rows hold live OAuth
            # refresh tokens, so they are the most important thing to erase.
            s.execute(delete(MarketplaceAccount)
                      .where(MarketplaceAccount.user_id == user_id))
            # Billing: the balance row and its ledger. The ledger is an audit
            # trail, but it is keyed to a person who asked to be forgotten and
            # holds no money of ours — Stripe keeps the payment record it is
            # legally required to keep, independently of this table.
            s.execute(delete(TokenLedger).where(TokenLedger.user_id == user_id))
            s.execute(delete(TokenAccount).where(TokenAccount.user_id == user_id))
            s.execute(delete(User).where(User.id == user_id))  # prefs ride along
            s.commit()
            return listing_ids
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        log.warning(f"db: delete_user failed: {exc}")
        return None


# --- eBay accounts ---------------------------------------------------------

_EBAY_FIELDS = (
    "refresh_token", "ebay_username", "ebay_email",
    "fulfillment_policy_id", "payment_policy_id",
    "return_policy_id", "merchant_location_key", "ship_from_postal",
)


def save_ebay_account(user_id: str, **fields) -> None:
    """Create/update a user's eBay connection. Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            acct = s.get(EbayAccount, user_id)
            if acct is None:
                acct = EbayAccount(user_id=user_id)
                s.add(acct)
            for key in _EBAY_FIELDS:
                if key in fields and fields[key] is not None:
                    setattr(acct, key, fields[key])
            acct.updated_at = _now()
            s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: save_ebay_account failed: {exc}")


def get_ebay_account(user_id: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            a = s.get(EbayAccount, user_id)
            if not a:
                return None
            return {f: getattr(a, f) for f in _EBAY_FIELDS}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_ebay_account failed: {exc}")
        return None



def disconnect_ebay_account(user_id: str) -> None:
    """Disconnect the live link (clear the refresh token) but KEEP the saved
    policy/location preferences and which account they belonged to, so
    reconnecting the SAME account restores them instead of reverting to
    auto-picked defaults (e.g. eBay Standard Envelope). Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            acct = s.get(EbayAccount, user_id)
            if acct is not None:
                acct.refresh_token = ""  # 'connected' checks this; prefs stay
                acct.updated_at = _now()
                s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: disconnect_ebay_account failed: {exc}")


# --- marketplace accounts (everything except eBay) -------------------------

_MARKETPLACE_FIELDS = ("refresh_token", "external_username", "external_id",
                       "settings")


def save_marketplace_account(user_id: str, marketplace: str, **fields) -> None:
    """Create/update a user's connection to one marketplace. Never raises.
    `settings` keys MERGE into the stored JSON (a reconnect refreshing the
    shop id must not wipe the user's saved shipping/return defaults)."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            acct = s.get(MarketplaceAccount, (user_id, marketplace))
            if acct is None:
                acct = MarketplaceAccount(user_id=user_id, marketplace=marketplace)
                s.add(acct)
            for key in _MARKETPLACE_FIELDS:
                if key in fields and fields[key] is not None:
                    if key == "settings":
                        acct.settings = {**(acct.settings or {}), **fields[key]}
                    else:
                        setattr(acct, key, fields[key])
            acct.updated_at = _now()
            s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: save_marketplace_account failed: {exc}")


def get_marketplace_account(user_id: str, marketplace: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            a = s.get(MarketplaceAccount, (user_id, marketplace))
            if not a:
                return None
            out = {f: getattr(a, f) for f in _MARKETPLACE_FIELDS}
            out["settings"] = out.get("settings") or {}
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_marketplace_account failed: {exc}")
        return None


def disconnect_marketplace_account(user_id: str, marketplace: str) -> None:
    """Clear the live link but keep settings, mirroring the eBay behavior:
    reconnecting the same account restores its saved defaults. Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            acct = s.get(MarketplaceAccount, (user_id, marketplace))
            if acct is not None:
                acct.refresh_token = ""  # 'connected' checks this; settings stay
                acct.updated_at = _now()
                s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: disconnect_marketplace_account failed: {exc}")


def get_listing(listing_id: str) -> Optional[dict]:
    res = get_listing_strict(listing_id)
    return None if res is UNAVAILABLE else res


# Sentinel telling "the read could not be performed" apart from "no such
# listing" — the same trick as EMAIL_TAKEN above. get_listing collapses the
# two into None, which is right for the callers that just want the record but
# wrong for a security check: an ownership guard that reads None as "unowned"
# stops guarding the moment the database hiccups.
UNAVAILABLE = object()


def get_listing_strict(listing_id: str):
    """The listing record, None when there is genuinely no such row, or
    UNAVAILABLE when a database is configured but the read failed."""
    try:
        eng = _get_engine()
        if eng is None:
            return None  # no DB configured at all — nothing is owned
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            return _record_to_dict(rec) if rec else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_listing failed: {exc}")
        return UNAVAILABLE


def touch_listing(listing_id: str) -> None:
    """Bump updated_at (no other changes) so list thumbnails — versioned by
    updated_at — refetch after an image-only edit like rotate. Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            if rec is not None:
                rec.updated_at = _now()
                s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: touch_listing failed: {exc}")


def delete_listing(listing_id: str, user_id: Optional[str] = None) -> bool:
    """Delete a listing row; returns True if a row was removed. Ownership-
    checked: a listing owned by an account can only be deleted by that owner.
    Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return False
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            if rec is None:
                return False
            # An owned record is NEVER deletable anonymously: session ids leak
            # via public /media image URLs on live eBay listings, so requiring
            # a user match only when the caller is logged in would let anyone
            # delete any listing (architect finding #2).
            if rec.user_id and rec.user_id != user_id:
                return False
            s.delete(rec)
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: delete_listing failed: {exc}")
        return False


# --- AI tokens (monetization) ----------------------------------------------
# The billing invariant lives here: every balance change happens inside one
# transaction holding a row lock on the account, paired with a ledger entry.
# Unlike the rest of this module these functions return None on DB error
# (rather than pretending success) so the caller can decide fail-open vs
# fail-closed — see services/tokens.py.

def _token_account(s: Session, user_id: str,
                   period: Optional[str] = None) -> TokenAccount:
    """Fetch-or-create the account row, locked. Only spends pass `period`,
    lazily rolling the monthly free allowance forward; credits and refunds
    pass None and must NOT touch the period — crediting a pack (or refunding
    an old spend) is not permission to reset this month's free usage."""
    acct = s.get(TokenAccount, user_id, with_for_update=True)
    if acct is None:
        # FOR UPDATE cannot lock a row that does not exist, so two concurrent
        # first-ever spends by the same account both see None and both INSERT.
        # The loser's flush raises IntegrityError, which token_spend's blanket
        # handler turns into "DB unavailable" — and that path deliberately
        # FAILS OPEN, handing out un-metered AI. A double-tap on a new
        # account's first draft should not be a free-AI coupon: catch the race
        # here and re-read, where the lock is now real.
        acct = TokenAccount(user_id=user_id, purchased=0, free_used=0,
                            free_period=period or "", updated_at=_now())
        s.add(acct)
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            acct = s.get(TokenAccount, user_id, with_for_update=True)
            if acct is None:  # not the race after all — let the caller see it
                raise
    if period is not None and acct.free_period != period:
        acct.free_period = period
        acct.free_used = 0
    return acct


def token_status(user_id: str, period: str, free_quota: int) -> Optional[dict]:
    """Read-only balance snapshot (does not write the period rollover)."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            acct = s.get(TokenAccount, user_id)
            if acct is None:
                return {"free_used": 0, "free_remaining": free_quota, "purchased": 0}
            free_used = acct.free_used if acct.free_period == period else 0
            return {"free_used": free_used,
                    "free_remaining": max(0, free_quota - free_used),
                    "purchased": acct.purchased}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: token_status failed: {exc}")
        return None


def token_spend(user_id: str, cost: int, free_quota: int, period: str,
                feature: str = "") -> Optional[dict]:
    """Atomically debit `cost` tokens (free allowance first, then purchased).

    Returns {"ok": True, "entry_id", "free_part", "paid_part", ...} on
    success, {"ok": False, "reason": "insufficient", ...} when the balance
    can't cover it, or None on DB error (caller chooses the failure policy).
    """
    if cost <= 0:
        return {"ok": True, "entry_id": None, "free_part": 0, "paid_part": 0}
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            acct = _token_account(s, user_id, period)
            free_remaining = max(0, free_quota - acct.free_used)
            if free_remaining + acct.purchased < cost:
                s.commit()  # keep the period rollover even when declining
                return {"ok": False, "reason": "insufficient",
                        "free_remaining": free_remaining, "purchased": acct.purchased}
            free_part = min(cost, free_remaining)
            paid_part = cost - free_part
            acct.free_used += free_part
            acct.purchased -= paid_part
            acct.updated_at = _now()
            entry = TokenLedger(id=_uuid.uuid4().hex, user_id=user_id, kind="spend",
                                feature=feature, tokens=-cost, free_part=free_part,
                                paid_part=paid_part, period=period, created_at=_now())
            s.add(entry)
            s.commit()
            return {"ok": True, "entry_id": entry.id, "free_part": free_part,
                    "paid_part": paid_part,
                    "free_remaining": free_remaining - free_part,
                    "purchased": acct.purchased}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: token_spend failed: {exc}")
        return None


def token_refund(user_id: str, entry_id: str, units: Optional[int] = None) -> bool:
    """Reverse a spend (fully, or `units` tokens of it) — "only pay for AI
    that worked". Paid tokens come back first (they never expire, so they're
    worth more to the user); the free part is only restored while the spend's
    month is still current. Idempotent per (entry, units) via the unique ref.
    """
    try:
        eng = _get_engine()
        if eng is None:
            return False
        with Session(eng) as s:
            entry = s.get(TokenLedger, entry_id)
            if entry is None or entry.kind != "spend" or entry.user_id != user_id:
                return False
            total = entry.free_part + entry.paid_part
            amount = total if units is None else max(0, min(int(units), total))
            if amount == 0:
                return False
            paid_back = min(amount, entry.paid_part)
            free_back = amount - paid_back
            acct = _token_account(s, user_id)
            acct.purchased += paid_back
            if free_back and acct.free_period == entry.period:
                acct.free_used = max(0, acct.free_used - free_back)
            acct.updated_at = _now()
            ref = entry_id if units is None else f"{entry_id}:{amount}"
            s.add(TokenLedger(id=_uuid.uuid4().hex, user_id=user_id, kind="refund",
                              feature=entry.feature, tokens=amount,
                              free_part=free_back, paid_part=paid_back,
                              period=entry.period, ref=ref, created_at=_now()))
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001 - duplicate ref lands here too
        log.info(f"db: token_refund skipped ({entry_id}): {exc}")
        return False


def token_credit(user_id: str, tokens: int, ref: Optional[str], kind: str = "purchase",
                 note: str = "") -> Optional[dict]:
    """Add purchased/granted tokens. Idempotent by `ref` (unique column):
    crediting the same Stripe session twice returns already=True instead of
    double-paying. Returns None on DB error."""
    if tokens <= 0:
        return None
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            if ref:
                existing = s.execute(
                    select(TokenLedger).where(TokenLedger.ref == ref)
                ).scalar_one_or_none()
                if existing is not None:
                    return {"ok": True, "already": True}
            acct = _token_account(s, user_id)
            acct.purchased += tokens
            acct.updated_at = _now()
            s.add(TokenLedger(id=_uuid.uuid4().hex, user_id=user_id, kind=kind,
                              tokens=tokens, paid_part=tokens, ref=ref, note=note[:255],
                              created_at=_now()))
            try:
                s.commit()
            except Exception:  # noqa: BLE001 - lost the idempotency race
                s.rollback()
                return {"ok": True, "already": True}
            return {"ok": True, "already": False, "purchased": acct.purchased}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: token_credit failed: {exc}")
        return None


def token_history(user_id: str, limit: int = 50) -> list[dict]:
    """Recent ledger entries, newest first. Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            rows = s.execute(
                select(TokenLedger).where(TokenLedger.user_id == user_id)
                .order_by(TokenLedger.created_at.desc()).limit(limit)
            ).scalars().all()
            return [{"kind": r.kind, "feature": r.feature, "tokens": r.tokens,
                     "note": r.note,
                     "created_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: token_history failed: {exc}")
        return []


# db_status() round-trips to the DB, and several hot paths call it per
# request (login errors, /api/listings payloads) — a short TTL keeps the
# probe honest about outages without paying a SELECT on every call.
_STATUS_TTL = 30  # seconds
_status_cache: tuple[float, dict] | None = None


def db_status() -> dict:
    """Health probe: is a DB configured, and can we actually reach it?
    Cached briefly (see _STATUS_TTL)."""
    global _status_cache
    if not enabled():
        return {"configured": False, "connected": False}
    if _status_cache and _time.time() - _status_cache[0] < _STATUS_TTL:
        return _status_cache[1]
    try:
        eng = _get_engine()
        # Actually round-trip to the DB. _get_engine() only reaches the server
        # on first call (create_all); afterwards it returns a cached engine, so
        # without this the probe would report "connected" even if the DB later
        # went down.
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        result = {"configured": True, "connected": True}
    except Exception as exc:  # noqa: BLE001
        result = {"configured": True, "connected": False, "error": str(exc)}
    _status_cache = (_time.time(), result)
    return result
