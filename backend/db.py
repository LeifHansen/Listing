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
from typing import Optional

from sqlalchemy import DateTime, JSON, String, create_engine, select, text
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


class ListingRecord(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    title: Mapped[str] = mapped_column(String(255), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


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
        _engine = create_engine(_normalize_url(config.DATABASE_URL), pool_pre_ping=True)
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


def get_listing(listing_id: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            return _record_to_dict(rec) if rec else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_listing failed: {exc}")
        return None


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
