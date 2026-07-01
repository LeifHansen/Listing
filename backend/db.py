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
from typing import Optional

from sqlalchemy import DateTime, JSON, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config

_engine = None
_initialized = False


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


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
        # Lightweight migration for DBs created before user_id existed.
        try:
            with _engine.begin() as conn:
                conn.execute(text("ALTER TABLE listings ADD COLUMN user_id VARCHAR(64)"))
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
    listing_id: str, listing: dict, status: str = "draft", user_id: Optional[str] = None
) -> None:
    """Create or update a listing row. Never raises."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            now = _now()
            if rec is None:
                rec = ListingRecord(id=listing_id, created_at=now)
                s.add(rec)
            if user_id is not None:
                rec.user_id = user_id
            rec.status = status
            rec.title = (listing.get("title") or "")[:255]
            rec.data = listing
            rec.updated_at = now
            s.commit()
    except Exception as exc:  # noqa: BLE001 - DB must never break a request
        print(f"[db] upsert_listing failed: {exc}")


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
        print(f"[db] list_listings failed: {exc}")
        return []


# --- users -----------------------------------------------------------------

def _user_to_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "created_at": u.created_at.isoformat()}


def create_user(user_id: str, email: str, password_hash: str) -> Optional[dict]:
    """Create a user. Returns None if the email already exists or on error."""
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
                return None
            u = User(id=user_id, email=email, password_hash=password_hash, created_at=_now())
            s.add(u)
            s.commit()
            return _user_to_dict(u)
    except Exception as exc:  # noqa: BLE001
        print(f"[db] create_user failed: {exc}")
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
        print(f"[db] get_user_by_email failed: {exc}")
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
        print(f"[db] get_user_by_id failed: {exc}")
        return None


def get_listing(listing_id: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            return _record_to_dict(rec) if rec else None
    except Exception as exc:  # noqa: BLE001
        print(f"[db] get_listing failed: {exc}")
        return None


def db_status() -> dict:
    """Health probe: is a DB configured, and can we actually reach it?"""
    if not enabled():
        return {"configured": False, "connected": False}
    try:
        _get_engine()
        return {"configured": True, "connected": True}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "connected": False, "error": str(exc)}
