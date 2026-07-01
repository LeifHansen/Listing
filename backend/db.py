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

from sqlalchemy import DateTime, JSON, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config

_engine = None
_initialized = False


class Base(DeclarativeBase):
    pass


class ListingRecord(Base):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
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
        _initialized = True
    return _engine


def _record_to_dict(rec: ListingRecord) -> dict:
    return {
        "id": rec.id,
        "status": rec.status,
        "title": rec.title,
        "listing": rec.data,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


def upsert_listing(listing_id: str, listing: dict, status: str = "draft") -> None:
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
            rec.status = status
            rec.title = (listing.get("title") or "")[:255]
            rec.data = listing
            rec.updated_at = now
            s.commit()
    except Exception as exc:  # noqa: BLE001 - DB must never break a request
        print(f"[db] upsert_listing failed: {exc}")


def list_listings(limit: int = 50) -> list[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            rows = (
                s.execute(
                    select(ListingRecord)
                    .order_by(ListingRecord.updated_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [_record_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        print(f"[db] list_listings failed: {exc}")
        return []


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
