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
import threading
import time as _time
import uuid as _uuid
from typing import Optional

from sqlalchemy import (DateTime, JSON, String, create_engine, delete, func,
                        or_, select, text)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config, crypto
from .config import log
# Re-exported so callers can keep saying db.StorageUnavailable. It is DEFINED
# in errors.py because reloading this module would otherwise mint a new class
# and silently unbind main.py's exception handler — see errors.py.
from .errors import StorageUnavailable  # noqa: F401

_engine = None
_initialized = False
# _get_engine() assigns _engine BEFORE create_all() has finished, so an
# unguarded second caller saw a non-None engine, found _initialized still
# False, and issued create_all() concurrently -- both emitting plain CREATE
# TABLE, so the loser raises. Startup now has three daemons that reach the DB
# within microseconds of each other (db-status, reclaim's orphan sweep) plus
# the first request, which is exactly the race. Serialize the init; the fast
# path below skips the lock once it is warm.
_engine_lock = threading.Lock()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80), default="")
    # Sessions issued before this instant are refused. It is how "log out
    # everywhere" is possible at all: the session JWT is self-contained and
    # lives 30 days, so without somewhere to record a cancellation, a token
    # that leaks stays good for the rest of the month and clearing the cookie
    # only affects the browser doing the clearing.
    #
    # A stamp on this row rather than a token blocklist, because this row is
    # ALREADY read on every authenticated request (auth.current_user resolves
    # the subject to a user dict). A revocation check that costs an extra
    # round trip is one that gets skipped under load, and a blocklist has to
    # be kept, expired, and consulted -- and fails open when its store is
    # unreachable. NULL means nothing has ever been revoked.
    sessions_valid_from: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # New-listing defaults (package weight/dims, quantity, condition, …) that
    # pre-fill every AI draft so repeat sellers stop re-typing them.
    prefs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class EbayAccount(Base):
    __tablename__ = "ebay_accounts"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Holds ciphertext (backend/crypto.py), ~1.5x the plaintext token.
    refresh_token: Mapped[str] = mapped_column(String(4096), default="")
    # eBay's IMMUTABLE account id, from the Identity API. The username above
    # is a display name the seller can change, so it cannot be the tenancy
    # key: a rename orphans the row, and a renamed-then-reused handle can
    # match the WRONG user. Account-deletion notices identify the account by
    # this id and nothing else, so without it a notice cannot be resolved to
    # the data it is asking us to erase.
    ebay_user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
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
    refresh_token: Mapped[str] = mapped_column(String(8192), default="")
    external_username: Mapped[str] = mapped_column(String(128), default="")
    external_id: Mapped[str] = mapped_column(String(64), default="")
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


class DeletionNotice(Base):
    """One row per eBay account-deletion notification we have accepted.

    eBay requires that an application storing eBay data process account
    deletion/closure notices. Acknowledging one is a promise that the erasure
    will happen, so the promise has to outlive the request: the row is written
    inside the request, and the purge runs against it afterwards. A crash
    between the two leaves a row in 'pending', which is recoverable — the
    alternative, a 200 with nothing recorded, is not, because eBay stops
    resending once acknowledged.

    `notification_id` is the primary key, which is what makes redelivery
    idempotent: eBay resends until it gets a 2xx, so the same notice arrives
    more than once as a matter of routine, not as an error.
    """

    __tablename__ = "ebay_deletion_notices"

    notification_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # eBay's immutable account id from notification.data.userId. This is the
    # only identifier the notice carries that we can resolve; username is
    # mutable and eiasToken is legacy.
    ebay_user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    # pending | done | no_match | failed
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    # Deliberately NOT the payload: it is personal data about someone who has
    # just asked to be erased. Only a digest, so a redelivery can be compared
    # without retaining what it said.
    payload_digest: Mapped[str] = mapped_column(String(64), default="")
    last_error: Mapped[str] = mapped_column(String(255), default="")
    received_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)


class MediaPurge(Base):
    """Photos a deleted account still owns in storage, until they don't.

    Deleting an account drops the rows and then erases the photos — the local
    directory and the R2 prefix — in a background pass. Nothing recorded that
    the pass was owed, so a deploy, a restart or a crash part-way through left
    the rest of the seller's photos in the bucket indefinitely, with the rows
    that named them already gone. Nothing would ever look for them again, and
    the app had already said they were deleted.

    The row is written inside delete_user's own transaction. That is the whole
    point: either the rows go and the debt is recorded, or neither happens.
    Recording afterwards leaves a window where the listings are gone and the
    obligation is not written down, which is precisely the state that cannot
    be detected later.

    Rows are deleted on success, so the table is a work queue and its size is
    the backlog. A row that keeps failing keeps its place — there is no
    give-up count, because nothing else remembers these objects exist.
    """

    __tablename__ = "media_purges"

    # The listing id, which is also the session id the photos live under.
    listing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Kept only for operator triage ("whose deletion is stuck?"). The user row
    # is already gone; this is an opaque id, not personal data.
    user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(String(255), default="")
    requested_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True))


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


class Notification(Base):
    """An in-app alert for one user — today that means "your item sold".

    `dedupe_key` is unique across the whole table so the same event can be
    reported by several code paths (the store import, the status sweep, a
    manual sync) without ever producing two rows: the DB settles it, not the
    order the syncs happen to run in."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="")  # sold | ...
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(String(512), default="")
    # The listing this is about, so the UI can open it in one tap ("" if none).
    listing_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(160), unique=True,
                                                      nullable=True)
    read_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
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
    if _engine is not None and _initialized:
        return _engine          # warm: no lock on the common path
    with _engine_lock:
        if _engine is None:
            # Pool sized against the concurrency that can actually reach it,
            # and set to fail fast rather than hang. Almost every route here is
            # a sync `def`, so FastAPI runs it in anyio's 40-thread pool, and
            # background daemons (bulk, identify, the R2 pushes, the store
            # sync) add more on top. Against SQLAlchemy's default 5+10 that
            # meant callers past the fifteenth queued for the default 30s
            # checkout and then hit a timeout that db.py swallows into []/None
            # — a half-minute hang followed by a confidently empty answer.
            # pool_recycle matters for Neon specifically: it drops idle
            # connections, and a stale one surfaces as a failed query on
            # somebody's next request.
            url = _normalize_url(config.DATABASE_URL)
            # pool_timeout bounds the wait for a pool SLOT, not the TCP
            # connect. Without a connect timeout a new connection inherits the
            # OS default and can hang for minutes on an unreachable host - and
            # /api/health round-trips the DB inside Fly's 5s liveness timeout.
            # A Neon stall therefore became a failed health check, and on a
            # single-machine app Fly answers that by replacing the machine,
            # killing whatever batch was running. Bound it well under 5s.
            #
            # This bounds the CONNECT only; a server that accepts and then
            # stalls is handled on the other side, by keeping /api/health on
            # the warm cache (see db_status and main._db_status_loop).
            #
            # libpq-only: SQLite's connect() rejects the keyword outright, and
            # the test suite runs the billing invariants on SQLite.
            connect_args = ({"connect_timeout": 3}
                            if url.startswith("postgresql") else {})
            _engine = create_engine(
                url,
                pool_pre_ping=True, pool_size=10, max_overflow=20,
                pool_timeout=5, pool_recycle=300,
                connect_args=connect_args,
            )
        if not _initialized:
            Base.metadata.create_all(_engine)  # may raise if DB unreachable
            # Lightweight migrations for DBs created before a column existed.
            # Each ALTER is separately guarded so an already-applied one
            # doesn't skip the rest.
            for stmt in (
                "ALTER TABLE listings ADD COLUMN user_id VARCHAR(64)",
                "ALTER TABLE ebay_accounts ADD COLUMN ebay_username VARCHAR(128) DEFAULT ''",
                "ALTER TABLE ebay_accounts ADD COLUMN ebay_email VARCHAR(255) DEFAULT ''",
                "ALTER TABLE ebay_accounts ADD COLUMN ship_from_postal VARCHAR(16) DEFAULT ''",
                "ALTER TABLE users ADD COLUMN display_name VARCHAR(80) DEFAULT ''",
                "ALTER TABLE users ADD COLUMN prefs JSON",
                # Encrypted tokens are roughly 1.5x their plaintext, and a
                # silently truncated one disconnects a seller with no error.
                # SQLite ignores VARCHAR lengths entirely, so these are a
                # no-op there and the guard below swallows the syntax error.
                "ALTER TABLE ebay_accounts ALTER COLUMN refresh_token TYPE VARCHAR(4096)",
                "ALTER TABLE marketplace_accounts ALTER COLUMN refresh_token TYPE VARCHAR(8192)",
                # The unread badge polls every minute and filters on both of
                # these; user_id alone left the read_at test to a scan of every
                # notification the seller has ever received. IF NOT EXISTS is
                # understood by Postgres and SQLite alike, so this is a no-op
                # after the first boot rather than something the guard below
                # has to swallow on every start.
                "CREATE INDEX IF NOT EXISTS ix_notifications_user_unread "
                "ON notifications (user_id, read_at)",
                # eBay's immutable account id. Deletion notices identify the
                # account by this and nothing else, and it is what every
                # ownership check should key on instead of the mutable
                # username.
                "ALTER TABLE ebay_accounts ADD COLUMN ebay_user_id VARCHAR(64) DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS ix_ebay_accounts_ebay_user_id "
                "ON ebay_accounts (ebay_user_id)",
                # Session revocation. Nullable with no default: NULL means
                # this account has never revoked, which is every account on
                # the day this ships, and must keep every live session working.
                #
                # WITH TIME ZONE, matching the model's DateTime(timezone=True)
                # — which is what create_all emits on a fresh database, so a
                # bare TIMESTAMP here would give existing and new deployments
                # different column types. On Postgres that difference is not
                # cosmetic: an aware datetime written into a naive column is
                # converted to the SESSION's timezone, so on any deployment
                # not running in UTC the stored cutoff would be off by the
                # offset — and being off in the lenient direction means a
                # revocation quietly does not take effect for hours.
                "ALTER TABLE users ADD COLUMN sessions_valid_from "
                "TIMESTAMP WITH TIME ZONE",
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
) -> bool:
    """Create or update a listing row. Never raises.

    Returns True when the row now reflects `status`/`listing` (including the
    no-op case where it already did), False when the write did not happen —
    no DB configured, or the write failed. Callers that only save may ignore
    it; the publish path must not. A publish records "this listing is live"
    by writing that status here, and a swallowed failure there is invisible
    and expensive: the listing IS live on eBay, the seller is told so, and
    their copy of it sits under Drafts forever, one click away from being
    published a second time.

    `when` overrides the row's updated_at. The store sync passes each eBay
    listing's own start time, because stamping every row with "now" on every
    sync made 600 listings share one timestamp and destroyed the ordering the
    dashboard reads — recency has to come from the listing, not the sync.
    A sync that changes nothing doesn't touch the row at all."""
    try:
        eng = _get_engine()
        if eng is None:
            return False
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id)
            now = _now()
            if rec is None:
                rec = ListingRecord(id=listing_id, created_at=when or now)
                s.add(rec)
            elif rec.status == status and rec.data == listing:
                return True  # nothing changed — leave updated_at where it was
            # Claim ownership only if unowned; never reassign a listing to
            # whoever happens to save it (session ids can leak in URLs).
            if user_id is not None and rec.user_id in (None, user_id):
                rec.user_id = user_id
            rec.status = status
            rec.title = (listing.get("title") or "")[:255]
            rec.data = listing
            rec.updated_at = when or now
            s.commit()
            return True
    except Exception as exc:  # noqa: BLE001 - DB must never break a request
        log.warning(f"db: upsert_listing failed: {exc}")
        return False


def mutate_listing_data(
    listing_id: str, mutate, status: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """Read-modify-write the listing's JSON blob with the row LOCKED.

    `mutate(data)` receives the stored dict and returns the dict to persist
    (mutating in place is fine). The lock is the point: the plain
    get_listing -> edit -> upsert_listing sequence is a lost-update race
    whenever two writers touch one listing, and this app has a guaranteed
    concurrent writer — publishing kicks off a background thread that
    rewrites the same blob (the eBay EPS URL refresh) while the request
    keeps working through the other marketplaces. Whichever plain write
    landed second silently erased the other's fields: freshly-recorded Etsy
    or Depop listing ids, or the refreshed eBay photo URLs.

    Returns the persisted dict, or None when there is no DB / the row is
    missing / the write failed (callers keep their existing fallbacks).
    """
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            rec = s.get(ListingRecord, listing_id, with_for_update=True)
            if rec is None:
                return None
            data = mutate(dict(rec.data or {}))
            if data is None:
                return None
            rec.data = data
            rec.title = (data.get("title") or "")[:255]
            if status is not None:
                rec.status = status
            # Claim ownership only if unowned — same rule as upsert_listing.
            if user_id is not None and rec.user_id in (None, user_id):
                rec.user_id = user_id
            rec.updated_at = _now()
            s.commit()
            return data
    except Exception as exc:  # noqa: BLE001 - DB must never break a request
        log.warning(f"db: mutate_listing_data failed: {exc}")
        return None


def list_listings(limit: int = 50, user_id: Optional[str] = None) -> list[dict]:
    """The user's listings, newest first. RAISES on a read failure.

    Not `[]`, which is what this used to answer and what every other read in
    this module still answers. "The seller's store" is the input to decisions
    that write, and an invented empty answer is indistinguishable from a
    seller who genuinely has nothing:

      - the eBay import matches incoming items against what it finds here and
        imports whatever it does not find. One failed read during a sync
        therefore imported a SECOND copy of the seller's entire eBay store,
        reported as a successful sync, leaving real duplicate listings to be
        merged by hand;
      - a release pass reports "released 0" as success;
      - a status sweep reports checking a store it never read;
      - the session-id migration reports nothing to migrate.

    Callers that genuinely tolerate an empty answer call
    list_listings_best_effort, so the decision sits at the call site where
    someone can see what it costs.
    """
    eng = _get_engine()
    # No database configured is a configuration, not a failure: nothing is
    # persisted, so the store really is empty and /api/health says why.
    if eng is None:
        return []
    try:
        with Session(eng) as s:
            q = select(ListingRecord)
            if user_id is not None:
                q = q.where(ListingRecord.user_id == user_id)
            q = q.order_by(ListingRecord.updated_at.desc()).limit(limit)
            rows = s.execute(q).scalars().all()
            return [_record_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: list_listings failed: {exc}")
        raise StorageUnavailable(
            "We couldn't load your listings just now. Try again in a moment."
        ) from exc


def list_listings_best_effort(limit: int = 50,
                              user_id: Optional[str] = None) -> list[dict]:
    """list_listings, but an unreadable store answers `[]`.

    Only for callers where an empty result degrades the answer rather than
    changing what gets written — a metrics panel with no numbers in it, a
    weight lookup that falls back to asking. Never for anything that decides
    what to create, release, or end.
    """
    try:
        return list_listings(limit=limit, user_id=user_id)
    except StorageUnavailable:
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
            # auth.current_user compares this against the token's `iat`. It
            # rides along here rather than being fetched separately because
            # this dict is already the per-request read.
            "sessions_valid_from": getattr(u, "sessions_valid_from", None),
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


def revoke_sessions(user_id: str,
                    at: Optional[_dt.datetime] = None) -> _dt.datetime:
    """End every session issued before now. Returns the instant recorded.

    RAISES rather than reporting a failure as success. Telling a seller their
    other sessions are gone when the write never landed is the worst outcome
    available here: they stop looking, and whoever holds the token keeps it.
    An unknown user raises for the same reason -- there is no version of
    "nothing to revoke" that is safe to report as "revoked".
    """
    stamp = (at or _dt.datetime.now(_dt.timezone.utc)).replace(microsecond=0)
    try:
        eng = _get_engine()
        if eng is None:
            raise StorageUnavailable(
                "Couldn't sign out your other sessions just now. Try again "
                "in a moment.")
        with Session(eng) as s:
            u = s.get(User, user_id)
            if u is None:
                raise StorageUnavailable(
                    "Couldn't sign out your other sessions just now. Try "
                    "again in a moment.")
            u.sessions_valid_from = stamp
            s.commit()
            return stamp
    except StorageUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        log.warning(f"db: revoke_sessions failed: {exc}")
        raise StorageUnavailable(
            "Couldn't sign out your other sessions just now. Try again in a "
            "moment.") from exc


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
            s.execute(delete(Notification).where(Notification.user_id == user_id))
            s.execute(delete(User).where(User.id == user_id))  # prefs ride along
            # In THIS transaction, before the commit: the rows that name these
            # photos are about to stop existing, so the obligation to erase
            # them has to become durable at the same instant. Queued after the
            # commit there is a window — small, but the state it produces
            # (rows gone, debt unrecorded) is undetectable afterwards.
            _queue_media_purges(s, user_id, listing_ids)
            s.commit()
            return listing_ids
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        log.warning(f"db: delete_user failed: {exc}")
        return None


# How many listing ids go into one queue statement. Chosen for SQLite's
# default 999-host-parameter ceiling with room to spare; Postgres allows far
# more, and the round-trip saving is already flat by this point.
_PURGE_BATCH = 400


def _queue_media_purges(session, user_id: str, listing_ids: list[str]) -> None:
    """Record, in the caller's open transaction, that these listings' photos
    are still in storage.

    Set-wise, in batches, NOT row by row. `session.merge()` per listing issues
    a SELECT and an INSERT each: a 2,000-listing account is four thousand
    serial round trips inside the open delete transaction, which against a
    cross-region Postgres is long enough to hit a statement timeout — and then
    the whole deletion rolls back and the seller is told it failed. The
    listings themselves are already deleted set-wise a few lines up, for the
    same reason.

    The delete before the insert is not an optimisation: a listing whose purge
    keeps failing still has its row, and re-queueing it must not collide with
    the primary key. Deleting first re-arms it from zero attempts, which is
    right — this is a fresh request to erase the same objects.

    Raising here fails the whole delete, which is the intended behaviour:
    better to tell the seller the deletion did not happen than to delete the
    rows and lose track of the photos.
    """
    now = _now()
    for start in range(0, len(listing_ids), _PURGE_BATCH):
        batch = listing_ids[start:start + _PURGE_BATCH]
        session.execute(
            delete(MediaPurge).where(MediaPurge.listing_id.in_(batch)))
        session.execute(
            MediaPurge.__table__.insert(),
            [{"listing_id": lid, "user_id": user_id or "", "attempts": 0,
              "last_error": "", "requested_at": now} for lid in batch])


def pending_media_purges(limit: int = 500) -> list[dict]:
    """Photos still owed an erasure, oldest first. `[]` on a read failure --
    the pass simply runs again later, and inventing work is worse than
    skipping a round."""
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            rows = s.execute(
                select(MediaPurge).order_by(MediaPurge.requested_at.asc())
                .limit(limit)).scalars().all()
            return [{"listing_id": r.listing_id, "user_id": r.user_id or "",
                     "attempts": r.attempts or 0,
                     "last_error": r.last_error or ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: pending_media_purges failed: {exc}")
        return []


def finish_media_purge(listing_id: str) -> None:
    """The photos are gone; drop the debt. Only ever called after a purge
    that raised nothing."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            s.execute(delete(MediaPurge)
                      .where(MediaPurge.listing_id == listing_id))
            s.commit()
    except Exception as exc:  # noqa: BLE001
        # The row survives, so the next pass tries again. Purging twice is
        # harmless -- both deletes are by prefix and idempotent -- while
        # dropping the row on a failed write would lose the obligation.
        log.warning(f"db: finish_media_purge failed: {exc}")


def note_media_purge_failure(listing_id: str, error: str) -> None:
    """Count an attempt and keep the row. There is deliberately no give-up
    threshold: nothing else remembers these objects exist, so a purge that
    stops being retried is a purge that never happens."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            row = s.get(MediaPurge, listing_id)
            if row is None:
                return
            row.attempts = (row.attempts or 0) + 1
            row.last_error = (error or "")[:255]
            s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: note_media_purge_failure failed: {exc}")


# --- eBay accounts ---------------------------------------------------------

_EBAY_FIELDS = (
    "refresh_token", "ebay_user_id", "ebay_username", "ebay_email",
    "fulfillment_policy_id", "payment_policy_id",
    "return_policy_id", "merchant_location_key", "ship_from_postal",
)


def save_ebay_account(user_id: str, **fields) -> None:
    """Create/update a user's eBay connection.

    Raises StorageUnavailable when the write did not commit. That is the
    point: this used to swallow every failure and return None, which a caller
    cannot tell from a clean commit — so the OAuth callback redirected to
    "eBay connected" and Settings answered {"ok": true} while nothing had
    been stored. The seller then believes the work is done and stops
    checking, and the next publish fails for a reason that makes no sense on
    a screen that says connected.

    Strict is the DEFAULT so that a call site nobody thought about gets the
    safe behaviour. Writes that are genuinely optional — caching something
    eBay can be asked for again — call save_ebay_account_best_effort, which
    says so in its name.
    """
    try:
        eng = _get_engine()
        if eng is None:
            # No database configured at all is this app's supported
            # single-box mode, not a failure to report.
            return
        with Session(eng) as s:
            acct = s.get(EbayAccount, user_id)
            if acct is None:
                acct = EbayAccount(user_id=user_id)
                s.add(acct)
            for key in _EBAY_FIELDS:
                if key in fields and fields[key] is not None:
                    value = fields[key]
                    if key == "refresh_token":
                        value = crypto.encrypt(value)
                    setattr(acct, key, value)
            acct.updated_at = _now()
            s.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed failure
        log.warning(f"db: save_ebay_account failed: {exc}")
        raise StorageUnavailable(
            "Couldn't save your eBay connection just now.") from exc


def save_ebay_account_best_effort(user_id: str, **fields) -> bool:
    """save_ebay_account for writes that may be lost without harming anyone.

    Returns True when the write committed (or there is no database to write
    to) and False when it failed. Use ONLY where the value can be recomputed
    or re-fetched — a cached ship-from ZIP, a remembered inventory-location
    key. Anything the seller is told about must use the strict command.
    """
    try:
        save_ebay_account(user_id, **fields)
        return True
    except StorageUnavailable:
        return False


def get_ebay_account(user_id: str) -> Optional[dict]:
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            a = s.get(EbayAccount, user_id)
            if not a:
                return None
            out = {f: getattr(a, f) for f in _EBAY_FIELDS}
            # Rows written before backend/crypto.py existed hold plaintext and
            # come back unchanged; they re-encrypt the next time anything
            # saves them. Callers only ever see the plaintext, so the token
            # cache in ebay_provider keys on the same value as before.
            out["refresh_token"] = crypto.decrypt(out.get("refresh_token") or "")
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: get_ebay_account failed: {exc}")
        return None


def record_deletion_notice(notification_id: str, ebay_user_id: str,
                           payload_digest: str) -> str:
    """Durably accept one deletion notice. Returns "new" or "duplicate".

    Raises StorageUnavailable if it could not be written. The endpoint MUST
    let that reach eBay as a non-2xx: acknowledging a notice we did not
    record means eBay stops resending and the erasure silently never happens.
    """
    try:
        eng = _get_engine()
        if eng is None:
            raise StorageUnavailable("no database configured for deletion notices")
        with Session(eng) as s:
            if s.get(DeletionNotice, notification_id) is not None:
                return "duplicate"
            s.add(DeletionNotice(
                notification_id=notification_id, ebay_user_id=ebay_user_id,
                payload_digest=payload_digest, state="pending",
                received_at=_now()))
            try:
                s.commit()
            except IntegrityError:
                # Two deliveries raced. The row exists either way, which is
                # exactly what idempotent means here.
                s.rollback()
                return "duplicate"
            return "new"
    except StorageUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed failure
        log.warning(f"db: record_deletion_notice failed: {exc}")
        raise StorageUnavailable("could not record the deletion notice") from exc


def finish_deletion_notice(notification_id: str, state: str,
                           last_error: str = "") -> None:
    """Mark a notice done / no_match / failed. Best-effort by design: the
    purge has already run, and losing the bookkeeping must not undo it."""
    try:
        eng = _get_engine()
        if eng is None:
            return
        with Session(eng) as s:
            row = s.get(DeletionNotice, notification_id)
            if row is None:
                return
            row.state = state
            row.attempts = (row.attempts or 0) + 1
            row.last_error = (last_error or "")[:255]
            row.completed_at = _now() if state in ("done", "no_match") else None
            s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: finish_deletion_notice failed: {exc}")


def pending_deletion_notices(limit: int = 100) -> list[dict]:
    """Notices accepted but not yet completed — what a restart has to pick
    back up, and what an operator alert should be counting."""
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            rows = s.execute(
                select(DeletionNotice)
                .where(DeletionNotice.state.in_(("pending", "failed")))
                .order_by(DeletionNotice.received_at)
                .limit(limit)).scalars().all()
            return [{"notification_id": r.notification_id,
                     "ebay_user_id": r.ebay_user_id,
                     "state": r.state, "attempts": r.attempts,
                     "last_error": r.last_error} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: pending_deletion_notices failed: {exc}")
        return []


def find_users_by_ebay_user_id(ebay_user_id: str):
    """Our user ids connected to this eBay account, or UNAVAILABLE.

    A LIST, not one id: the same eBay account can legitimately be connected
    by more than one of our users, and a deletion notice has to reach all of
    them. An empty list means "connected by nobody"; UNAVAILABLE means the
    question could not be answered.

    That distinction is the whole point. An account-deletion notice must not
    be acknowledged as handled when the lookup failed — eBay would stop
    resending, and the erasure it asked for would never happen with nothing
    recording that it was missed.
    """
    ebay_user_id = (ebay_user_id or "").strip()
    if not ebay_user_id:
        return []
    try:
        eng = _get_engine()
        if eng is None:
            return []
        with Session(eng) as s:
            rows = s.execute(
                select(EbayAccount.user_id)
                .where(EbayAccount.ebay_user_id == ebay_user_id)).all()
            return [r[0] for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: find_users_by_ebay_user_id failed: {exc}")
        return UNAVAILABLE


def _json_text(column, key: str):
    """`column ->> key` as a comparable string, in whichever dialect is in use.

    Postgres renders this as ->> and SQLite as JSON_EXTRACT; both yield NULL
    for an absent key, so `!= ''` drops missing and blank alike.

    Only safe to compare as text for a field the Listing model types as `str`
    — which the two callers below both do (models.Listing.ebay_account and
    .ebay_listing_id, defaulting to ""), and every write goes through the
    model. For a field that could hold a number or a bool, `->>` would render
    it as "0"/"false" and a text comparison would disagree with Python's
    truth test.
    """
    return column[key].as_string()


def stamp_ebay_account(user_id: str, account: str) -> int:
    """Label every eBay-linked record that has no owning account yet.

    Called the moment a DIFFERENT eBay account connects: until then a record's
    owner was simply "whoever was connected", which is unrecoverable once the
    connection changes. Records already labelled are left alone, and records
    with no eBay item id (plain local drafts) are not eBay-scoped at all.
    Returns how many rows were labelled. Never raises.
    """
    account = (account or "").strip()
    if not account:
        return 0
    try:
        eng = _get_engine()
        if eng is None:
            return 0
        stamped = 0
        with Session(eng) as s:
            # Select the rows this can actually stamp, not the whole store.
            # These are the two conditions the loop used to re-check in Python
            # after dragging every listing's JSON blob across the wire — a
            # mirrored store is thousands of them, and on the common case (a
            # reconnect where everything is already labelled) every single one
            # was fetched only to be skipped.
            account_col = _json_text(ListingRecord.data, "ebay_account")
            item_col = _json_text(ListingRecord.data, "ebay_listing_id")
            rows = s.execute(
                select(ListingRecord)
                .where(ListingRecord.user_id == user_id)
                .where(or_(account_col.is_(None), account_col == ""))
                .where(item_col != "")
            ).scalars().all()
            for rec in rows:
                data = dict(rec.data or {})
                # Still re-checked here: the SQL narrows the read, but a
                # non-string value in either field (a number, a nested object)
                # can satisfy the predicate without satisfying this rule.
                if data.get("ebay_account") or not data.get("ebay_listing_id"):
                    continue
                data["ebay_account"] = account
                rec.data = data  # a new dict is what marks the JSON column dirty
                stamped += 1
            if stamped:
                s.commit()
        return stamped
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: stamp_ebay_account failed: {exc}")
        return 0


def count_foreign_listings(user_id: str, account: str) -> int:
    """How many of this user's eBay-linked records belong to some OTHER eBay
    account than `account` — what the UI needs to explain why a just-connected
    store looks like it still holds the previous one's items."""
    account = (account or "").strip()
    try:
        eng = _get_engine()
        if eng is None:
            return 0
        with Session(eng) as s:
            # Counted in the database, not in Python. This runs on
            # /api/ebay/status, which the app calls on every boot, and the
            # Python version fetched every listing's whole JSON blob — a
            # mirrored store is megabytes of it, over a cross-region link,
            # to produce one integer nothing else in the response needs.
            account_col = _json_text(ListingRecord.data, "ebay_account")
            return int(s.execute(
                select(func.count())
                .select_from(ListingRecord)
                .where(ListingRecord.user_id == user_id)
                .where(account_col != "", account_col != account)
            ).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: count_foreign_listings failed: {exc}")
        return 0


def count_unowned_ebay_listings(user_id: str) -> int:
    """eBay-linked records with NO owner recorded at all.

    These predate ownership stamping (imports began labelling in #176, and
    app publishes only started stamping alongside this helper), so after an
    UNDETECTED account switch they are the previous account's listings with
    nothing marking them so — invisible to count_foreign_listings, which
    requires a non-empty label, and skipped by the release endpoint's default
    pass. The UI needs this number to offer the seller the explicit way out.
    """
    try:
        eng = _get_engine()
        if eng is None:
            return 0
        with Session(eng) as s:
            account_col = _json_text(ListingRecord.data, "ebay_account")
            item_col = _json_text(ListingRecord.data, "ebay_listing_id")
            return int(s.execute(
                select(func.count())
                .select_from(ListingRecord)
                .where(ListingRecord.user_id == user_id)
                .where(or_(account_col.is_(None), account_col == ""))
                .where(item_col != "")
            ).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: count_unowned_ebay_listings failed: {exc}")
        return 0


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


def save_marketplace_account(user_id: str, marketplace: str, **fields) -> bool:
    """Create/update a user's connection to one marketplace. Never raises;
    returns whether the write actually landed. `settings` keys MERGE into the
    stored JSON (a reconnect refreshing the shop id must not wipe the user's
    saved shipping/return defaults).

    The return value matters for ROTATING refresh tokens (Etsy invalidates the
    old one the moment it issues a new one): a caller that cannot tell a
    swallowed failure from a success will happily carry on with an access
    token whose refresh token was never stored, and the connection dies
    silently an hour later. db.upsert_listing reports for the same reason.
    """
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
                    elif key == "refresh_token":
                        acct.refresh_token = crypto.encrypt(fields[key])
                    else:
                        setattr(acct, key, fields[key])
            acct.updated_at = _now()
            s.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: save_marketplace_account failed: {exc}")
        return False


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
            out["refresh_token"] = crypto.decrypt(out.get("refresh_token") or "")
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


def plan_spend(free_quota: int, free_used: int, purchased: int,
               amount: int) -> Optional[tuple[int, int]]:
    """Pure arithmetic of one debit: how much comes from the free allowance vs
    the purchased balance. Returns (free_part, paid_part), or None when the
    combined balance can't cover it.

    Lives here, beside token_spend, because token_spend is its only caller and
    a debit rule with two implementations is a debit rule with none: this was a
    copy in services/tokens.py that the billing tests exercised while the row
    lock below ran its own inline version. Re-exported as tokens.plan_spend.
    """
    free_remaining = max(0, free_quota - max(0, free_used))
    if amount <= 0:
        return (0, 0)
    if free_remaining + purchased < amount:
        return None
    free_part = min(amount, free_remaining)
    return (free_part, amount - free_part)


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
            # What is left of the monthly allowance, for the balances this
            # reports back. The SPLIT itself is plan_spend's to decide.
            free_remaining = max(0, free_quota - acct.free_used)
            plan = plan_spend(free_quota, acct.free_used, acct.purchased, cost)
            if plan is None:
                s.commit()  # keep the period rollover even when declining
                return {"ok": False, "reason": "insufficient",
                        "free_remaining": free_remaining, "purchased": acct.purchased}
            free_part, paid_part = plan
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
            # What this spend has ALREADY had given back. Both numbers below
            # depend on it: the clamp was against the spend total, so repeated
            # partial refunds could hand back more than was ever charged, and
            # the ref was keyed on this call's amount alone, so two partial
            # refunds of the SAME size against one spend collided on the unique
            # ref -- the second was rejected by the database and swallowed.
            # A bulk batch does exactly that: it refunds the failed cutouts
            # mid-run and the unused remainder in its finally, and when those
            # two are equal the seller silently lost the second refund.
            already = int(s.execute(
                select(func.coalesce(func.sum(TokenLedger.tokens), 0))
                .where(TokenLedger.kind == "refund",
                       TokenLedger.user_id == user_id,
                       or_(TokenLedger.ref == entry_id,
                           TokenLedger.ref.like(f"{entry_id}:%")))
            ).scalar() or 0)
            remaining = max(0, total - already)
            amount = remaining if units is None else max(0, min(int(units), remaining))
            if amount == 0:
                return False
            paid_back = min(amount, max(0, entry.paid_part - already))
            free_back = amount - paid_back
            acct = _token_account(s, user_id)
            acct.purchased += paid_back
            if free_back and acct.free_period == entry.period:
                acct.free_used = max(0, acct.free_used - free_back)
            acct.updated_at = _now()
            # Full refunds keep the bare entry id, so refund_all stays safe to
            # re-run on every boot. A partial is keyed by where it starts as
            # well as its size, which makes consecutive partials distinct while
            # an exact replay of the same one still collides and no-ops.
            ref = entry_id if units is None else f"{entry_id}:{already}:{amount}"
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


def token_reverse_purchase(ref: str, reason: str = "") -> Optional[dict]:
    """Claw back a purchase whose money went back to the buyer — a Stripe
    refund or a chargeback. Idempotent by a derived ref, so Stripe redelivering
    the event (it does) can't debit twice.

    Returns {"ok", "already", "reversed", "shortfall", "user_id"}, or None on a
    DB error / unknown purchase.

    The balance floors at zero rather than going negative. A negative balance
    would silently swallow the buyer's next legitimate purchase, which turns
    one refund into a second support problem — and for a chargeback the money
    is already gone either way. What was spent before the reversal is recorded
    as `shortfall` so the operator can see what it actually cost them.
    """
    if not ref:
        return None
    try:
        eng = _get_engine()
        if eng is None:
            return None
        with Session(eng) as s:
            purchase = s.execute(
                select(TokenLedger).where(TokenLedger.ref == ref)
            ).scalar_one_or_none()
            if purchase is None or purchase.kind not in ("purchase", "grant"):
                return None
            rev_ref = f"{ref}:reversed"
            if s.execute(select(TokenLedger).where(TokenLedger.ref == rev_ref)
                         ).scalar_one_or_none() is not None:
                return {"ok": True, "already": True, "user_id": purchase.user_id}
            acct = _token_account(s, purchase.user_id)
            amount = max(0, purchase.tokens)
            taken = min(amount, max(0, acct.purchased))
            acct.purchased -= taken
            acct.updated_at = _now()
            s.add(TokenLedger(
                id=_uuid.uuid4().hex, user_id=purchase.user_id, kind="reversal",
                tokens=-taken, paid_part=-taken, ref=rev_ref,
                note=(reason or "purchase reversed")[:255], created_at=_now()))
            try:
                s.commit()
            except Exception:  # noqa: BLE001 - lost the idempotency race
                s.rollback()
                return {"ok": True, "already": True, "user_id": purchase.user_id}
            return {"ok": True, "already": False, "reversed": taken,
                    "shortfall": amount - taken, "user_id": purchase.user_id}
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: token_reverse_purchase failed: {exc}")
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


# --- notifications ---------------------------------------------------------

def _notification_to_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "listing_id": n.listing_id or "",
        "data": n.data or {},
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def add_notification(user_id: str, kind: str, title: str, body: str = "",
                     listing_id: str = "", data: Optional[dict] = None,
                     dedupe_key: Optional[str] = None) -> Optional[dict]:
    """Record one notification. Returns the new row, or None when nothing was
    written — either a row with the same `dedupe_key` already exists (the
    normal, expected case for a re-sync) or there's no DB. Never raises."""
    try:
        eng = _get_engine()
        if eng is None or not user_id:
            return None
        with Session(eng) as s:
            if dedupe_key:
                seen = s.execute(
                    select(Notification).where(Notification.dedupe_key == dedupe_key)
                ).scalar_one_or_none()
                if seen is not None:
                    return None
            row = Notification(
                id=_uuid.uuid4().hex, user_id=user_id, kind=kind[:32],
                title=title[:255], body=body[:512], listing_id=listing_id[:64],
                data=data or {}, dedupe_key=dedupe_key, created_at=_now())
            s.add(row)
            try:
                s.commit()
            except Exception:  # noqa: BLE001 - lost the dedupe race; fine
                s.rollback()
                return None
            return _notification_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: add_notification failed: {exc}")
        return None


def list_notifications(user_id: str, limit: int = 50,
                       unread_only: bool = False) -> list[dict]:
    """Newest first. Never raises."""
    try:
        eng = _get_engine()
        if eng is None or not user_id:
            return []
        with Session(eng) as s:
            q = select(Notification).where(Notification.user_id == user_id)
            if unread_only:
                q = q.where(Notification.read_at.is_(None))
            q = q.order_by(Notification.created_at.desc()).limit(limit)
            return [_notification_to_dict(n) for n in s.execute(q).scalars().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: list_notifications failed: {exc}")
        return []


def unread_notification_count(user_id: str) -> int:
    try:
        eng = _get_engine()
        if eng is None or not user_id:
            return 0
        with Session(eng) as s:
            # COUNT in the database, like count_foreign_listings above and for
            # the same reason: this ran on every /api/notifications poll and
            # dragged one row per unread notification across the cross-region
            # link to compute a length. A synced store mints one of these per
            # sale, so the cost grew with the seller's success.
            return int(s.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id,
                       Notification.read_at.is_(None))
            ).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: unread_notification_count failed: {exc}")
        return 0


def mark_notifications_read(user_id: str,
                            ids: Optional[list[str]] = None) -> int:
    """Mark some (or, with ids=None, all) of a user's notifications read.
    Returns how many changed. Always scoped to the owner. Never raises."""
    try:
        eng = _get_engine()
        if eng is None or not user_id:
            return 0
        with Session(eng) as s:
            q = select(Notification).where(Notification.user_id == user_id,
                                           Notification.read_at.is_(None))
            if ids is not None:
                if not ids:
                    return 0
                q = q.where(Notification.id.in_(list(ids)[:200]))
            rows = s.execute(q).scalars().all()
            for row in rows:
                row.read_at = _now()
            s.commit()
            return len(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"db: mark_notifications_read failed: {exc}")
        return 0


# db_status() round-trips to the DB, and several hot paths call it per
# request (login errors, /api/listings payloads) — a short TTL keeps the
# probe honest about outages without paying a SELECT on every call.
_STATUS_TTL = 30  # seconds
_status_cache: tuple[float, dict] | None = None


def db_status(refresh: bool = False) -> dict:
    """Health probe: is a DB configured, and can we actually reach it?
    Cached briefly (see _STATUS_TTL).

    `refresh` takes a new reading regardless of the cache. It exists for the
    background refresher in main, which keeps this cache warm so that request
    handlers - /api/health above all - are always served from it and never
    pay for the round trip themselves.
    """
    global _status_cache
    if not enabled():
        return {"configured": False, "connected": False}
    if (not refresh and _status_cache
            and _time.time() - _status_cache[0] < _STATUS_TTL):
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
