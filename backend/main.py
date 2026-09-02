"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import logging
import os
import random
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import (auth, config, db, ebay_auth, errors, etsy_auth, marketplaces,
               objstore, ratelimit, redact, storage)
from .config import log
from .marketplaces import ebay_provider
from .marketplaces import state as marketplace_state
from .marketplaces.base import PublishContext, PublishOutcome
from .marketplaces.state import STICKY_STATUSES
from .models import (TITLE_MAX_CHARS, ImageOrderRequest, ItemSpecific,
                     Listing, MarketplaceState, PublishRequest,
                     RefineRequest, SessionOnlyRequest)
from .services import (bulk_actions, claude_ai, dirty_fields, duplicates, ebay,
                       ebay_account, ebay_deletion, ebay_notify, ebay_orders,
                       ebay_trading, image_import, images, jobstore,
                       listing_merge, listing_prompt, listing_sync,
                       messages as messages_service, metrics, notifications,
                       orient, owed_refunds, preflight, pricing, promotions,
                       recommender, sync_guard, sync_merge,
                       taxonomy, tokens)
from .services import etsy as etsy_service
from .services import deletion_queue
from .services import policy_terms as ebay_policy_terms
from .services import errorlog
from .services.background import run_in_background


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Everything that has to happen before the first request is served.

    Replaces the two startup hooks this used to register with Starlette's
    deprecated event API, which warned on every boot and every test run. Both
    bodies moved here verbatim, in their original registration order — and
    that order matters: warming the model and probing the bucket are
    fire-and-forget threads, while the job mirrors must be adopted before a
    client can poll for one.

    The names below are defined further down the module. That resolves fine —
    this body runs at startup, not at import — and keeps each hook's own
    documentation next to the machinery it starts.
    """
    _warm_models()
    _adopt_job_mirrors()
    # Erasures an earlier process promised and did not finish. Started here
    # rather than run inline: a backlog is a pass over object storage, and it
    # must not hold up serving. It is a thread rather than a durable worker
    # (that is still open), but the OBLIGATION is durable now, so a process
    # that dies mid-pass leaves the remaining rows for the next one.
    _in_background(_finish_pending_deletions, what="deletion backlog")
    # Money a seller is owed for AI that did not work. Same shape as the
    # deletion backlog above: the obligation outlived the process, so the next
    # one settles it.
    _in_background(_settle_owed_refunds, what="owed refunds")
    yield


app = FastAPI(title="eBay Listing Generator", lifespan=_lifespan)

# The iOS/Android shell bundles the web build (guideline 4.2 forbids a bare
# remote-webview app), so its pages live on capacitor://localhost and call
# this API cross-origin with a Bearer token. Only those app origins are
# allowed; no credentials mode, since the shell never uses the cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.NATIVE_APP_ORIGIN, "capacitor://localhost",
                   "https://localhost", "http://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this the Capacitor shell cannot READ the header, so a crash
    # reported from the native app would carry no reference and could not be
    # joined to the request that caused it.
    expose_headers=["X-Request-Id"],
    max_age=86400,
)


FRONTEND_DIR = config.ROOT_DIR / "frontend" / "dist"

# An inline <script>…</script>. The lookahead excludes anything with a src=,
# which is an EXTERNAL script covered by 'self' — hashing its empty body would
# add a meaningless entry to the policy.
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                               re.S | re.I)


def _inline_script_hashes(html: str) -> list[str]:
    """A CSP sha256 source for every inline script in `html`.

    The bytes hashed are exactly what sits between the tags — leading
    newline, indentation and all — because that is what a browser hashes. A
    stripped or re-indented body yields a hash that simply never matches, and
    nothing but a browser will say so.
    """
    out = []
    for match in _INLINE_SCRIPT_RE.finditer(html):
        body = match.group(1)
        if not body:
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        out.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return out


def build_csp(index_html: Path) -> str:
    """The Content-Security-Policy, with script-src derived from what the app
    actually serves.

    script-src used to carry 'unsafe-inline', which is the allowance that
    matters: with it, an injected `<script>` runs. It was there for one honest
    reason — index.html applies the saved theme before first paint to avoid a
    light-mode flash, and a policy that blanks the app is worse than a partial
    one.

    So the hashes are read from the built index.html AT STARTUP rather than
    written into this file. A hardcoded hash goes stale the first time anyone
    edits that snippet, and the symptom is a white screen in production, for
    everyone, after a green deploy. Reading the served file cannot drift from
    it.

    With no readable index.html there is no frontend to protect and no hash
    that could be right — a dev checkout, or a container where the build has
    not run — so script-src falls back to what it was.

    style-src still allows inline: React sets element styles directly and
    Tailwind emits them, so there is nothing to hash. CSS injection is a real
    but far narrower problem than script execution, and leaving it open
    knowingly beats leaving script-src open to avoid saying so.

    img-src allows https: because listings legitimately show eBay-hosted
    photos (i.ebayimg.com), and data:/blob: because the photo editor works on
    canvas output before anything is uploaded.
    """
    try:
        hashes = _inline_script_hashes(index_html.read_text(encoding="utf-8"))
        script_src = " ".join(["script-src 'self'", *hashes])
    except OSError:
        script_src = "script-src 'self' 'unsafe-inline'"
    return "; ".join((
        "default-src 'self'",
        script_src,
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' https: data: blob:",
        "connect-src 'self' https:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        # Clickjacking: the app has publish and delete buttons, so being framed
        # by another site is never legitimate. X-Frame-Options below says the
        # same thing for browsers that predate frame-ancestors.
        "frame-ancestors 'none'",
    ))


# The response headers a browser needs in order to defend the seller, none of
# which were being sent. Assembled once — they are the same on every response.
_CSP = build_csp(FRONTEND_DIR / "index.html")

# An inbound X-Request-Id is echoed back and written into logs, so its shape
# is checked before it is trusted with either: 8-32 hex characters, which is
# what this app mints and nothing else.
_REFERENCE_RE = re.compile(r"[0-9a-fA-F]{8,32}")

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    # Stops a browser guessing that an uploaded file is HTML and running it.
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Nothing here needs these. `camera=(self)` rather than `()` because the
    # upload buttons use <input capture="environment">, which some browsers
    # gate on the camera policy even though it is only a file picker.
    "Permissions-Policy": ("camera=(self), microphone=(), geolocation=(), "
                           "payment=(), usb=(), interest-cohort=()"),
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach the security headers to every response.

    HSTS is added only when the request actually arrived over HTTPS: sending
    it on a plain-HTTP local run would pin developers' browsers to https://
    localhost, which nothing here serves.
    """
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    forwarded = request.headers.get("x-forwarded-proto", "")
    if request.url.scheme == "https" or forwarded == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains")
    return response


@app.exception_handler(errors.InvalidSessionId)
async def _invalid_session_id(request: Request, exc: errors.InvalidSessionId):
    """A session id outside the accepted form answers 400, not 500.

    Making safe_session_name REJECT rather than rewrite (the fix for the
    ownership bypass) meant a malformed id raised out of whatever handler
    touched storage, and every one of them returned "Internal Server Error".
    Wrong on both counts: the caller sent something bad, which is a 400, and
    dressing it as a server fault buries real 500s under scanner noise —
    these routes need no login, so junk ids arrive constantly.

    Handled centrally for the same reason as StorageUnavailable: a route
    added later cannot forget to.
    """
    log.info("rejected an invalid session id on %s %s",
             request.method, request.url.path)
    return JSONResponse(status_code=400,
                        content={"detail": "That listing id isn't valid."})


@app.exception_handler(errors.StorageUnavailable)
async def _storage_unavailable(request: Request, exc: errors.StorageUnavailable):
    """A write that did not commit answers 503, everywhere, automatically.

    Handled once here rather than at each call site so a command added later
    cannot forget to — the failure mode this replaces was precisely a route
    that reported success because nobody remembered to check.

    503 and not 4xx: the seller did nothing wrong and retrying is the right
    next move. A 404 would send them to reconnect an account that is fine.
    """
    log.warning("storage unavailable on %s %s: %s",
                request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc) or "Storage is unavailable right now — "
                                       "please try again in a moment."},
    )


# The errnos that mean "the volume would not take the write". Everything else
# an OSError can carry is a bug or a genuine I/O fault and keeps its 500.
_OUT_OF_SPACE = {errno.ENOSPC, errno.EDQUOT, errno.EROFS}


@app.exception_handler(OSError)
async def _out_of_space(request: Request, exc: OSError):
    """A full volume answers 507 with a next step, everywhere, automatically.

    Five upload paths already recognised ENOSPC and said so. Every other write
    — the editor's save, both PATCH routes, anything else that reaches
    storage.save_listing — let the OSError out as "Internal Server Error".
    That is wrong the same two ways the invalid-session-id handler above
    documents: the seller is shown a fault with no next step, and a real 500
    gets buried under a condition that is merely operational. It is also the
    failure this deployment is most likely to hit, since one small Fly volume
    holds every seller's photos.

    The message is the one the upload paths already use, and it deliberately
    does not carry `str(exc)` — that names the server's own filesystem.
    """
    if getattr(exc, "errno", None) not in _OUT_OF_SPACE:
        raise exc
    log.error("out of storage on %s %s (errno=%s)",
              request.method, request.url.path, getattr(exc, "errno", None))
    return JSONResponse(
        status_code=507,
        content={"detail": "The server is out of storage space — try again "
                           "shortly."},
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """The last resort: a crash answers with a reference somebody can quote.

    Until this existed, anything nobody handled fell through to Starlette's
    default and became a bare "Internal Server Error" — no reference, no
    record, and nothing tying the seller's report to the traceback uvicorn
    printed. The seller was shown a fault with no next step, which is the same
    complaint the two handlers above were written for.

    Two things about this are easy to get wrong.

    It does NOT swallow. Starlette's ServerErrorMiddleware calls this handler
    to build the response and then re-raises, so uvicorn still logs the
    traceback and TestClient(raise_server_exceptions=True) still raises. The
    handler is additive; no existing test changes behaviour because of it.

    It runs OUTSIDE every @app.middleware("http"), including _security_headers
    — ServerErrorMiddleware is the outermost layer Starlette installs. So the
    headers have to be applied here by hand, or the one response an attacker
    can most easily steer the app into would be the only one served without a
    CSP. test_security_headers.py::test_an_error_response_is_protected_too
    passes today only because a 404 is raised INSIDE the middleware stack.
    """
    reference = errorlog.current_reference() or errorlog.new_reference()
    # error, not exception: ServerErrorMiddleware re-raises after this, so
    # uvicorn prints the traceback anyway and log.exception would put a
    # second copy of it in the Fly window. The row below keeps its own.
    #
    # errorlog_skip stops the capture handler recording this line as well:
    # it would fingerprint on THIS function, so every unhandled error in the
    # app would collapse into one row called "_unhandled". The explicit
    # record() below fingerprints at the innermost frame instead — the place
    # that actually broke.
    log.error("unhandled error on %s %s [%s]: %s", request.method,
              request.url.path, reference, exc,
              extra={"errorlog_skip": True})
    errorlog.record(kind="backend", level="ERROR", exc=exc, status=500,
                    route=request.url.path, method=request.method,
                    reference=reference)
    response = JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. If you need "
                           f"help, quote {reference}."},
    )
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers.setdefault("X-Request-Id", reference)
    return response


class _DropDeletionAcks(logging.Filter):
    """Drop the access-log lines for successfully acked eBay account-deletion
    notifications. eBay sends them 1-2 times a MINUTE, around the clock, and
    they were the bulk of the retained Fly log window — burying the lines that
    matter. Failures (non-2xx) still log. uvicorn's access record args are
    (client_addr, method, full_path, http_version, status_code)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            _client, method, path, _version, status = record.args
            return not (method == "POST"
                        and str(path).startswith("/api/ebay/account-deletion")
                        and 200 <= int(status) < 300)
        except Exception:  # noqa: BLE001 - never eat a line we can't parse
            return True


logging.getLogger("uvicorn.access").addFilter(_DropDeletionAcks())

# Mirror WARNING-and-above into error_events, so the Errors tab and the daily
# triage job have something to read. Installed here, beside the other log
# wiring, rather than in config.py — importing config must never start a
# thread or open a database connection, since every test and every script
# does it. See services/errorlog for why capture is at the handler and not at
# the 400-odd call sites. This only fills an in-memory queue; the thread that
# drains it into Postgres starts with the other daemons in _warm_models.
errorlog.install()

# The frontend is a Vite/React app; serve its build output. (The Dockerfile
# builds it in a node stage; run.sh builds it for local dev.)
@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    """Cache policy: index.html must always revalidate so a deploy is visible
    on the next load, while Vite's content-hashed /assets/ bundles are immutable
    and can cache forever; /media images are content-stable, so let browsers
    keep them a bit (the UI cache-busts edited photos with ?v=)."""
    response = await call_next(request)
    path = request.url.path
    ctype = response.headers.get("content-type", "")
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/media/"):
        # "no-cache" is NOT "don't cache" — it is "cache it, but ask before
        # you use it". A photo is MUTABLE at a stable URL: rotate, crop,
        # auto-clean and background removal all rewrite the same file in
        # place. This used to be max-age=3600, which was only safe as long as
        # the URL changed whenever the bytes did — and it doesn't. The
        # editor's cache-buster is a per-mount counter that starts at 0 again
        # every time the editor opens (imageVersions in useListingForm.js), so
        # a photo rotated at ?v=1 is asked for at ?v=0 on the next open, and
        # the browser answered that from the copy it took BEFORE the rotate.
        # The seller rotated a photo, reopened the listing, and watched it
        # come back sideways -- indistinguishable from a rotation that never
        # saved, though the file on disk was right the whole time.
        # Revalidation costs a 304 with no body (see the media route, which
        # answers them); not revalidating cost the seller their edit.
        response.headers.setdefault("Cache-Control", "no-cache")
    elif ctype.startswith(("text/html", "text/css")) or "javascript" in ctype:
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/api/"):
        # API responses had NO cache directive at all, leaving heuristic
        # caching to browsers and any intermediary. These answers are
        # per-account state -- which eBay account is connected, what the
        # listings are -- and a stale copy of one is how a seller ends up
        # debugging an account switch against yesterday's answer. no-store,
        # not no-cache: there is nothing here worth revalidating.
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def _request_context(request: Request, call_next):
    """Give every request an id, and publish it as X-Request-Id.

    Registered LAST on purpose. With @app.middleware("http") the last one
    registered runs OUTERMOST, so this opens the context before
    _cache_headers, _security_headers or any route can log a line — which is
    the whole point, since a line logged before the context exists carries no
    reference and joins to nothing.

    The id is the same 8 hex characters _support_reference() has always
    minted, and it is now the SAME VALUE the seller is shown. Before this,
    each failure site minted its own, so the reference in a toast joined to
    exactly one log line; it now joins to every line that request emitted,
    plus its row in error_events.

    An inbound X-Request-Id is honoured so the native shell (or a future
    proxy) can carry one end to end. It is not a trusted value and never
    reaches a query — it is a label, validated for shape only.
    """
    inbound = (request.headers.get("x-request-id") or "").strip()
    reference = inbound if _REFERENCE_RE.fullmatch(inbound) else \
        errorlog.new_reference()
    errorlog.begin_request(method=request.method, path=request.url.path,
                           reference=reference)
    response = await call_next(request)
    response.headers.setdefault("X-Request-Id", reference)
    return response


def _sweep_orphans() -> None:
    """Reclaim volume space: delete session dirs on disk that aren't real
    listings (leftover bulk staging + abandoned uploads). Skipped entirely when
    the DB is unavailable, so live listings' images can never be mistaken for
    orphans."""
    ids = db.all_listing_ids()
    if ids is None:  # no DB / read failed — don't risk deleting real images
        return
    # Compare DIR names, not raw ids: session_dir() strips non-alphanumerics,
    # so an imported listing "ebay-123" lives in dir "ebay123" — matching raw
    # ids would sweep every imported listing's photos as orphans.
    dir_names = {storage.session_dir(i).name for i in ids if i}
    removed = storage.sweep_orphan_sessions(dir_names, max_age_seconds=3 * 3600)
    if not removed:
        return
    # The bucket needs the same sweep. /api/upload mirrors photos to R2 before
    # any listing row exists, so an abandoned upload leaves objects that no
    # later cleanup can name — not the id set (no row), not account deletion
    # (it walks listing ids). Without this they accumulate in R2 forever, and
    # survive the uploader deleting their account.
    if objstore.enabled():
        purged = sum(objstore.delete_prefix(objstore.session_prefix(name))
                     for name in removed)
        if purged:
            log.info("sweep: purged %d orphaned R2 object(s)", purged)
    log.info("sweep: removed %d orphaned session dir(s) to reclaim space",
             len(removed))


# How long a source upload / edit snapshot survives before it's reclaimed.
# Nothing reads originals after the optimize pass, and they're the bulk of the
# volume, so they go first and soonest.
_ORIGINALS_TTL = int(os.getenv("ORIGINALS_TTL_HOURS", "12") or "12") * 3600
_HISTORY_TTL = int(os.getenv("HISTORY_TTL_DAYS", "14") or "14") * 86400
# Below this much free space the volume is one batch away from breaking every
# upload ("No space left on device"), so reclaim aggressively — 15-minute
# originals, 1-day history, 1-hour R2 offload.
#
# This has to stay well under the size of the volume itself. At 1 GB it was
# never *below* the threshold on a 1 GB volume: aggressive mode was simply
# always on, so photos were freed off the volume an hour after upload and
# every edit on a slightly-older listing had to go back to R2 for the bytes.
# 250 MB is roughly one full bulk batch of headroom, which is the amount that
# actually predicts an ENOSPC.
_LOW_DISK_BYTES = int(os.getenv("LOW_DISK_MB", "250") or "250") * 1024 * 1024
# How long the housekeeping daemon waits between passes. A volume with room to
# spare only needs the slow pass; one that is low - or that will not answer how
# much is left - has to be revisited soon, because the next bulk batch is what
# fills it. The loop used to sleep three hours either way, so a volume that
# filled mid-batch stayed broken until the next pass happened to come round.
_RECLAIM_INTERVAL = 3 * 3600
_RECLAIM_INTERVAL_LOW = 15 * 60


def _offload_to_r2(max_age_seconds: int, budget: int = 4000,
                   upload_budget: int = 300) -> int:
    """Free local copies of optimized photos that are safely in R2.

    With object storage configured, R2 is where eBay and the browser actually
    read photos from (/media redirects there when the local file is gone), so
    the volume only needs them while a listing is being worked on. Each file
    is verified present in the bucket before its local copy goes — the upload
    path is best-effort, and deleting a photo that never landed would break a
    live listing.

    Photos older than the bucket itself (everything shot before R2 was
    configured) were never uploaded by the request path, so nothing would ever
    make them eligible to be freed. Backfill them here: a file that isn't in
    the bucket is uploaded, re-verified, and then freed on this same pass.
    Uploads carry their own smaller budget — they are far slower than a HEAD,
    and a pass should not run for many minutes. Returns bytes freed."""
    if not objstore.enabled():
        return 0
    freed = checked = uploaded = 0
    try:
        base = config.SESSIONS_DIR
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime):
            opt = d / "optimized"
            if not opt.is_dir():
                continue
            for p in opt.iterdir():
                if checked >= budget:  # bounded work per pass
                    return freed
                try:
                    if not p.is_file() or p.stat().st_mtime > cutoff:
                        continue
                    checked += 1
                    key = objstore.key_for(d.name, p.name)
                    if not objstore.exists(key):
                        if uploaded >= upload_budget:
                            continue
                        uploaded += 1
                        # Backfill, then re-verify: upload() swallows its own
                        # failures, so a non-None return is not proof enough
                        # to delete the only copy of a live listing's photo.
                        if objstore.upload(p, key) is None:
                            continue
                        if not objstore.exists(key):
                            continue
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    freed += size
                except Exception:  # noqa: BLE001 - keep going
                    continue
        if uploaded:
            log.info("reclaim: backfilled %d photo(s) into R2", uploaded)
    except Exception as exc:  # noqa: BLE001
        log.warning("reclaim: R2 offload failed: %s", exc)
    return freed


def reclaim_space(aggressive: bool = False) -> int:
    """Free volume space and return the bytes reclaimed. Runs the orphan sweep,
    prunes source uploads and old edit snapshots, and (when R2 is configured)
    drops local photo copies that the bucket already holds. `aggressive` (used
    when the disk is nearly full, or right after an ENOSPC) shortens the TTLs
    so a wedged volume can recover without a human."""
    _sweep_orphans()
    orig_ttl = 900 if aggressive else _ORIGINALS_TTL      # 15 min when desperate
    hist_ttl = 86400 if aggressive else _HISTORY_TTL      # 1 day when desperate
    freed = storage.prune_originals(orig_ttl) + storage.prune_history(hist_ttl)
    # Dry-run export payloads: debug artifacts, never read back.
    freed += storage.prune_exports(3600 if aggressive else 2 * 86400)
    freed += _offload_to_r2(3600 if aggressive else 7 * 86400)
    if freed:
        log.info("reclaim: freed %.1f MB from the volume (aggressive=%s)",
                 freed / 1e6, aggressive)
    return freed


def _reclaim_plan(free: int) -> tuple[bool, int]:
    """Decide (aggressive, seconds until the next pass) from a free-space reading.

    `free` is storage.disk_free_bytes(), which reports 0 both for a genuinely
    full volume and for a stat it could not take (it swallows the error and
    returns 0). Those are the two states aggressive reclaim exists for, so 0
    has to count as low. Reading it as "no reason to hurry" is what
    `bool(free) and free < limit` did: the guard was meant to say "if we know
    the free space", but its effect was to switch the emergency off at exactly
    the moment it needed to fire.

    A stat that fails on a healthy volume therefore reclaims early rather than
    late. That trade is deliberate: short TTLs cost a round trip to R2 for an
    edit, while being wrong the other way is ENOSPC, which fails every upload.
    """
    low = free < _LOW_DISK_BYTES
    return low, (_RECLAIM_INTERVAL_LOW if low else _RECLAIM_INTERVAL)


def _reclaim_loop() -> None:
    """Housekeeping daemon: reclaim space every few hours, and sooner when the
    volume is running low. Without this the volume only ever got swept at
    startup, so a busy day of bulk batches could fill it mid-flight."""
    while True:
        delay = _RECLAIM_INTERVAL_LOW
        try:
            free = storage.disk_free_bytes()
            aggressive, delay = _reclaim_plan(free)
            freed = reclaim_space(aggressive=aggressive)
            # The one state this daemon cannot fix by itself: the volume is
            # low and there is nothing left to free. Nothing else reports it
            # - reclaim_space only logs when it actually frees something - and
            # with no metrics stack anywhere, this log line is the alert.
            if aggressive and not freed:
                log.warning(
                    "reclaim: volume low (%d MB free) and nothing left to "
                    "reclaim - the volume needs more room", round(free / 1e6))
            # Retried here as well as at startup: an R2 outage during an
            # account deletion would otherwise leave the photos in the bucket
            # until someone happened to redeploy. It also genuinely frees
            # space, which is what this loop is for.
            _finish_pending_deletions()
            # Not about space, but it is the only recurring pass there is and
            # a refund the seller is owed should not wait for a redeploy.
            _settle_owed_refunds()
        except Exception as exc:  # noqa: BLE001 - housekeeping never dies
            log.warning("reclaim loop: %s", exc)
        time.sleep(delay)


# Well under db._STATUS_TTL (30s), so the cache /api/health reads stays inside
# its window and the probe never has to go and fetch it. The gap is the slack:
# at 10s a refresh has 20s to complete before the cache goes stale and a
# request handler would have to take the round trip itself.
_DB_STATUS_REFRESH = 10


def _db_status_loop() -> None:
    """Keep db_status()'s cache warm, off the request path.

    /api/health reports db state, and db_status() only caches for 30s while
    Fly's liveness check runs every 15s - so roughly every other check was
    making a live round trip to Neon inside a 5s timeout. Fly answers a missed
    check by replacing the machine, and with one machine that kills whatever
    batch is running, which is the restart loop fly.toml says was designed
    out. Refreshing on this thread means a slow probe costs a daemon nobody is
    waiting on, and the request handler is always served from cache.
    """
    if not db.enabled():
        return   # nothing to refresh; don't wake 8,640 times a day to no-op
    while True:
        try:
            db.db_status(refresh=True)
        except Exception as exc:  # noqa: BLE001 - housekeeping never dies
            log.warning("db status refresh: %s", exc)
        time.sleep(_DB_STATUS_REFRESH)


def _warm_models() -> None:
    """Startup daemons (don't block uvicorn binding the port): warm the in-house
    background-removal model, resolve the R2 bucket check so /api/health tells
    the truth from the first request, keep the db-status cache warm so the
    liveness probe never blocks on Postgres, and keep the volume from filling
    up."""
    threading.Thread(target=images.warm, daemon=True).start()
    threading.Thread(target=objstore.probe, daemon=True).start()
    threading.Thread(target=_db_status_loop, daemon=True).start()
    threading.Thread(target=_reclaim_loop, daemon=True).start()
    # Drains the error queue into the database. Started here rather than at
    # import, so a pytest run or a one-off script does not get a thread
    # racing whatever it was about to assert.
    errorlog.start_writer()


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _client_ip(request: Request) -> str:
    """The caller's IP. Fly puts the real client in Fly-Client-IP; uvicorn
    runs with --proxy-headers so request.client is already the forwarded
    address, but the explicit header is the one Fly guarantees."""
    return (request.headers.get("Fly-Client-IP")
            or (request.client.host if request.client else "?"))


def _rate_limit_auth(request: Request, bucket: str) -> None:
    """429 when one client floods an auth endpoint (see backend/ratelimit)."""
    ip = _client_ip(request)
    if not ratelimit.check(f"{bucket}:{ip}"):
        log.warning("auth: rate limited %s from %s", bucket, ip)
        raise HTTPException(
            429, "Too many attempts. Wait a few minutes and try again.")


@app.get("/api/health")
def health() -> dict:
    """Liveness, and nothing else.

    This is anonymous and unrate-limited, so what it returns is published to
    anyone who asks. It used to answer with 26 operator-diagnostic keys: the
    running commit, the NAMES of unset environment variables, the R2 bucket
    and URL mode, free disk, which Stripe mode the keys were in, config
    warnings naming near-miss secret names, and the raw exception text from
    the database and object store -- which carries the Neon host and role on
    an auth failure and the R2 account id in its endpoint.

    What stays is liveness plus the handful of FEATURE-availability booleans
    the UI genuinely reads (it hides the AI banner and the category lookups on
    them). Those say what the app can do, which a seller is entitled to know;
    they name no secret, no host, no bucket and no environment variable. The
    rest moved to /api/admin/diagnostics, which is the same data behind a
    token.
    """
    return {
        "ok": True,
        # The commit this image was built from. Deliberately kept public: the
        # deploy gate, deploy.sh and the health-watch alarm all poll it to
        # prove production is running what was shipped (a poisoned builder
        # cache has served an older image here before), and on a public repo
        # a commit sha is not a secret. It is the one operator-ish field whose
        # value outweighs its exposure.
        "build": config.BUILD_SHA or "unknown",
        # Read by the UI: App.jsx's setup banner, useListingForm's category
        # lookups, ShopMode. Capability, not configuration.
        "anthropic_configured": config.anthropic_ready(),
        "ebay_configured": config.ebay_ready(),
        "taxonomy_configured": config.taxonomy_ready(),
    }


def _diagnostics() -> dict:
    """Everything an operator needs to tell "not configured" apart from
    "misconfigured". Served only from the admin route above."""
    return {
        "ok": True,
        # The commit actually running. A deploy can report success while the
        # image serving traffic is older (a poisoned builder cache has done
        # this here before), and without this the only way to tell was to
        # diff response shapes against git history and guess.
        "build": config.BUILD_SHA or "unknown",
        "anthropic_configured": config.anthropic_ready(),
        "ebay_configured": config.ebay_ready(),
        "ebay_missing": config.ebay_status()["missing"],
        "taxonomy_configured": config.taxonomy_ready(),
        "ebay_env": config.EBAY_ENV,
        "ebay_oauth_ready": config.ebay_oauth_ready(),
        "ebay_deletion_endpoint_ready": bool(config.EBAY_VERIFICATION_TOKEN),
        # Etsy, where "configured" is only half the answer: which of Etsy's
        # three access tiers the app is on decides how many shops may connect
        # at all, and the roster is how the operator seats them. Counts, never
        # the addresses — this is a diagnostics endpoint, not a place to hand
        # out the beta's email list to anyone holding the admin token.
        # `etsy_seats: 0` means no ceiling (Commercial Access); a roster
        # larger than the ceiling also gets its own config_warnings() line,
        # because the overflow is refused on Etsy's page rather than here.
        "etsy_configured": config.etsy_oauth_ready(),
        "etsy_access_tier": config.etsy_access_tier(),
        "etsy_seats": config.etsy_seat_ceiling(),
        "etsy_roster": len(config.ETSY_OWNER_EMAILS),
        "etsy_gate_active": config.etsy_gate_active(),
        # adobe_configured = credentials present; adobe_ready = pipeline can
        # actually run (Adobe's APIs need R2 as presigned-URL hand-off storage).
        "adobe_configured": config.adobe_configured(),
        "adobe_ready": config.adobe_ready(),
        "photoroom_configured": config.photoroom_ready(),
        # Photo storage: is the R2 bucket wired up — and if not, exactly which
        # pieces are missing (four credentials sat deployed for a week while a
        # bare `false` here hid that two more vars were expected) — plus how
        # much room is left on the volume (a full one breaks every upload).
        "objstore_configured": objstore.enabled(),
        "objstore_missing": config.r2_missing(),
        "objstore_bucket": config.R2_BUCKET if objstore.enabled() else None,
        "objstore_url_mode": (("public" if config.r2_public_urls() else "presigned")
                              if objstore.enabled() else None),
        "objstore_error": objstore.last_error(),
        "disk_free_mb": round(storage.disk_free_bytes() / 1e6),
        "pixian_configured": config.pixian_ready(),
        # The background-removal engines that will actually run, in order.
        "bg_engines": config.bg_engine_chain(),
        "storage": "r2" if objstore.enabled() else "local",
        # Monetization, reported like every other integration: whether metering
        # is actually on, what's still missing before money can move, and which
        # Stripe mode the keys are in — a test key on a production deploy
        # accepts nothing and otherwise looks identical to a working one.
        "tokens_enabled": config.tokens_enabled(),
        "tokens_missing": config.tokens_missing(),
        "stripe_live_mode": config.stripe_live_mode(),
        # Misconfigurations that look exactly like "not configured yet": a
        # secret set under a name one word off from the one the code reads, or
        # an on/off flag set to something that isn't on. Every `*_missing` list
        # above reports those two cases identically to never having set them,
        # which is how production ran with the paid tier off and a Stripe key
        # visibly deployed. [] means nothing adjacent was found.
        "config_warnings": config.config_warnings(),
        "db": db.db_status(),
        # Erasures this deployment still owes: photos whose account is already
        # deleted, and eBay account-deletion notices acknowledged but not yet
        # carried out. Both are promises already made to somebody, so a number
        # here that does not come back down is the alert. Counts only — the
        # ids belong to people who asked to be forgotten.
        "deletion_backlog": deletion_queue.backlog(),
        # Refunds that did not commit and are still owed. Like the deletion
        # backlog, a number here that does not come back down is a promise
        # already made to somebody — in this case, their money.
        "owed_refunds": owed_refunds.backlog(),
    }


def _require_admin(request: Request) -> None:
    """Fail CLOSED: an unset ADMIN_TOKEN denies rather than admits.

    An absent secret reading as "no check required" is exactly how this
    endpoint would end up public again on a deploy that forgot to set it --
    which is the state it is being moved out of.
    """
    expected = (config.ADMIN_TOKEN or "").strip()
    supplied = (request.headers.get("x-admin-token") or "").strip()
    if not expected or not supplied or not secrets.compare_digest(supplied,
                                                                 expected):
        raise HTTPException(401, "Not authorised.")


@app.get("/api/admin/diagnostics")
def admin_diagnostics(request: Request) -> dict:
    """The deployment detail /api/health used to hand out anonymously."""
    _require_admin(request)
    return _diagnostics()


# --- superadmin console ------------------------------------------------------
#
# The operator console: cross-user reads and a handful of account actions,
# gated by users.role rather than the shared header token above. The two
# doors deliberately coexist: /api/admin/diagnostics keeps working with the
# database down (curl/CI), while everything below authenticates a PERSON,
# so every action can be written down with a name on it.
#
# These handlers live in THIS module on purpose. The ownership guardrail
# (tests/test_every_scoped_route_checks_the_owner.py) AST-scans main.py and
# nothing else — an admin route in a separate module would silently leave
# that scan, which is exactly how the next cross-user read ships unreviewed.
# If main.py is ever split, extend that test's MAIN/FUNCS first.

def _require_superadmin(request: Request) -> dict:
    """The signed-in superadmin, or 404. Fail CLOSED.

    404 rather than 401/403, on purpose: (a) it does not confirm an admin
    surface exists to whoever is probing for one; (b) lib/api.js treats any
    401 as "session expired" and signs the caller out client-side — the
    wrong outcome for a curious logged-in seller who typed /api/admin into
    devtools. A database outage propagates as StorageUnavailable → 503, like
    every other authenticated route: "cannot check" is never "not an admin".
    The role is re-read from the user row on every request (current_user's
    per-request read), so revoking it takes effect immediately — there is no
    role claim inside the 30-day JWT to wait out.
    """
    user = auth.current_user(request)
    if not user or (user.get("role") or "") != "superadmin":
        raise HTTPException(404, "Not found")
    return user


def _audit_admin(admin: dict, request: Request, action: str,
                 target_type: str = "", target_id: str = "",
                 data: Optional[dict] = None) -> str:
    """Write the audit row for an admin action, BEFORE the action runs.

    Raises (→ 503) when it cannot: an admin action that cannot be written
    down does not run. Returns the row id — token grants carry it in their
    ledger `ref`, so the two trails reconcile mechanically.
    """
    return db.admin_audit(admin, action, target_type=target_type,
                          target_id=target_id, ip=_client_ip(request),
                          data=data)


def _admin_cursor(stamp: Optional[str], row_id: Optional[str]) -> Optional[str]:
    """The same opaque "<stamp>|<id>" token _cursor_for mints, for admin
    pages keyed on their own timestamp columns. None when the row cannot
    name a place in the order — the page then honestly offers no button."""
    if not stamp or not row_id:
        return None
    raw = f"{stamp}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@app.get("/api/admin/system")
def admin_system(request: Request) -> dict:
    """_diagnostics(), for the console's System tab. Same payload as
    /api/admin/diagnostics behind the session gate instead of the header."""
    _require_superadmin(request)
    return _diagnostics()


@app.get("/api/admin/overview")
def admin_overview(request: Request, days: int = 30) -> dict:
    """The platform KPIs plus the two obligation backlogs. Reads raise
    rather than answering zeros — the console renders "couldn't check"."""
    _require_superadmin(request)
    if days not in (7, 30, 90):
        days = 30
    kpis = db.admin_platform_kpis(days)
    kpis["deletion_backlog"] = deletion_queue.backlog()
    kpis["owed_refunds"] = owed_refunds.backlog()
    return kpis


@app.get("/api/admin/users")
def admin_users(request: Request, q: str = "", before: str = "",
                limit: int = 50) -> dict:
    _require_superadmin(request)
    limit = max(1, min(limit, 100))
    cursor = _cursor_from(before) if before else None
    # One row more than will be returned, so the answer can say whether it
    # is the whole list — same probe-row trade as /api/listings.
    rows = db.admin_list_users(limit=limit + 1, before=cursor, q=q)
    truncated = len(rows) > limit
    rows = rows[:limit]
    out = {"users": rows,
           "rollups": db.admin_user_rollups([u["id"] for u in rows]),
           "next_cursor": (_admin_cursor(rows[-1].get("created_at"),
                                         rows[-1].get("id"))
                           if truncated and rows else None)}
    try:
        out["total"] = db.admin_count_users()
    except errors.StorageUnavailable:
        # The page is honest without it; a total must never be invented.
        pass
    return out


@app.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: str, request: Request) -> dict:
    _require_superadmin(request)
    detail = db.admin_get_user(user_id)
    if detail is None:
        raise HTTPException(404, "No such account.")
    return {"user": detail}


# The most an admin can hand out in one grant. Not a product limit — a
# typo guard: 1000000 where 1000 was meant is a real balance someone
# spends, and there is no undo that claws back what was already used.
_ADMIN_GRANT_CAP = 100_000


@app.post("/api/admin/users/{user_id}/grant-tokens")
def admin_grant_tokens(user_id: str, request: Request,
                       payload: Optional[dict] = None) -> dict:
    """Credit an account (a support goodwill, a refund made right). The
    ledger row's ref carries the audit row's id, and token_credit's unique
    ref makes a retried grant a no-op rather than a double credit."""
    admin = _require_superadmin(request)
    body = payload or {}
    try:
        amount = int(body.get("tokens"))
    except (TypeError, ValueError):
        raise HTTPException(400, "How many tokens? Send a whole number.")
    if not 1 <= amount <= _ADMIN_GRANT_CAP:
        raise HTTPException(
            400, f"Grants are 1 to {_ADMIN_GRANT_CAP} tokens.")
    note = str(body.get("note") or "").strip()[:200]
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    audit_id = _audit_admin(admin, request, "grant_tokens", "user", user_id,
                            data={"tokens": amount, "note": note})
    res = db.token_credit(user_id, amount, ref=f"admin:{audit_id}",
                          kind="grant",
                          note=note or f"granted by {admin['email']}")
    if res is None:
        raise HTTPException(
            503, "The grant was recorded but could not be applied — it was "
                 "NOT credited. Try again in a moment.")
    return {"ok": True, "granted": amount,
            "already": bool(res.get("already"))}


@app.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(user_id: str, request: Request) -> dict:
    """Force-sign-out one account everywhere (a stolen token, a support
    request). db.revoke_sessions is strict, so success here means the write
    landed."""
    admin = _require_superadmin(request)
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    _audit_admin(admin, request, "revoke_sessions", "user", user_id)
    db.revoke_sessions(user_id)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/disable")
def admin_set_disabled(user_id: str, request: Request,
                       payload: Optional[dict] = None) -> dict:
    """Lock an account out ({"disabled": true}) or back in (false).

    Two refusals: your own account (locking yourself out of the console
    that unlocks accounts), and another superadmin (demote them with
    scripts/grant_superadmin.py --revoke first, so removing an operator is
    a deliberate, audited, out-of-band step rather than a console click).
    Disabling also revokes sessions: the lockout must reach tokens that are
    already minted, not just the next login.
    """
    admin = _require_superadmin(request)
    body = payload or {}
    disabled = body.get("disabled")
    if not isinstance(disabled, bool):
        raise HTTPException(400, 'Send {"disabled": true} or false.')
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't disable your own account.")
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    if (target.get("role") or "") == "superadmin":
        raise HTTPException(
            400, "That account is a superadmin — revoke its role first "
                 "(scripts/grant_superadmin.py --revoke).")
    _audit_admin(admin, request,
                 "disable_account" if disabled else "enable_account",
                 "user", user_id)
    updated = db.set_user_disabled(user_id, disabled)
    if updated is None:
        raise HTTPException(404, "No such account.")
    if disabled:
        db.revoke_sessions(user_id)
    return {"ok": True, "disabled_at": updated.get("disabled_at")}


@app.get("/api/admin/listings")
def admin_listings(request: Request, q: str = "", status: str = "",
                   user_id: str = "", before: str = "",
                   limit: int = 50) -> dict:
    _require_superadmin(request)
    limit = max(1, min(limit, 100))
    cursor = _cursor_from(before) if before else None
    rows = db.admin_list_listings(limit=limit + 1, before=cursor, q=q,
                                  status=status, user_id=user_id)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"listings": rows,
            "next_cursor": (_admin_cursor(rows[-1].get("updated_at"),
                                          rows[-1].get("id"))
                            if truncated and rows else None)}


@app.get("/api/admin/listings/{listing_id}")
def admin_get_listing(listing_id: str, request: Request) -> dict:
    """One listing in full, whoever owns it — the read-only detail behind a
    row in the console's cross-user browse. See the ownership test's EXEMPT
    entry: cross-user is the point here, and the gate above is the check."""
    _require_superadmin(request)
    rec = db.get_listing_strict(listing_id)
    if rec is db.UNAVAILABLE:
        raise HTTPException(
            503, "Couldn't read that listing just now. Try again in a "
                 "moment.")
    if rec is None:
        raise HTTPException(404, "Listing not found")
    return rec


@app.get("/api/admin/ledger")
def admin_ledger_view(request: Request, kind: str = "", user_id: str = "",
                      before: str = "", limit: int = 50) -> dict:
    _require_superadmin(request)
    limit = max(1, min(limit, 200))
    cursor = _cursor_from(before) if before else None
    rows = db.admin_ledger(limit=limit + 1, before=cursor, kind=kind,
                           user_id=user_id)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows,
            "next_cursor": (_admin_cursor(rows[-1].get("created_at"),
                                          rows[-1].get("id"))
                            if truncated and rows else None)}


@app.get("/api/admin/compliance")
def admin_compliance(request: Request) -> dict:
    """The two obligation queues. The counts raise on a read failure (a zero
    here is a claim that nothing is owed), so an outage 503s the tab rather
    than rendering 'Nothing owed' over queue rows nobody could read."""
    _require_superadmin(request)
    return {
        "deletion_backlog": db.count_pending_deletion_notices(),
        "media_purge_backlog": db.count_pending_media_purges(),
        "deletion_notices": db.pending_deletion_notices(100),
        "media_purges": db.pending_media_purges(100),
    }


@app.post("/api/admin/compliance/run")
def admin_run_compliance(request: Request) -> dict:
    """Kick the recovery passes now instead of waiting for the next boot —
    the button an operator presses when the backlog number is not coming
    down. Inline rather than backgrounded so the response can say what
    actually happened."""
    admin = _require_superadmin(request)
    _audit_admin(admin, request, "run_compliance_queue", "system")
    finished = _finish_pending_deletions()
    refunds = _settle_owed_refunds()
    return {"ok": True, "deletions": finished, "refunds_settled": refunds}


def _require_error_feed(request: Request) -> None:
    """The triage job's door. Fails CLOSED, like _require_admin.

    A twin of _require_admin rather than a reuse of it, reading its own
    ERROR_FEED_TOKEN. The distinction is the point: ADMIN_TOKEN also opens
    /api/admin/diagnostics, which reports raw database and object-store
    exception text — the Neon host, the role, the R2 account. A scheduled job
    that reads which bugs are open has no business holding that, and a
    credential in CI is the one most likely to leak.
    """
    expected = (config.ERROR_FEED_TOKEN or "").strip()
    supplied = (request.headers.get("x-error-feed-token") or "").strip()
    if not expected or not supplied or not secrets.compare_digest(supplied,
                                                                 expected):
        raise HTTPException(401, "Not authorised.")


def _error_report(before: str = "", limit: int = 50, since_hours: int = 0,
                  min_severity: str = "", include_resolved: bool = True
                  ) -> dict:
    """The distinct failures, newest-seen first. Shared by both doors below.

    `sink` rides along because a queue that is dropping rows would otherwise
    look exactly like a quiet day — the most dangerous thing a monitor can
    do. It is the lesson check_health.py's docstring records, one layer down:
    an alarm that cannot tell "nothing happened" from "I could not see" is
    worse than no alarm.
    """
    limit = max(1, min(limit, 200))
    cursor = _cursor_from(before) if before else None
    rows = db.error_events_list(limit=limit + 1, before=cursor,
                                since_hours=since_hours,
                                min_severity=min_severity,
                                include_resolved=include_resolved)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"errors": rows,
            "sink": errorlog.stats(),
            "next_cursor": (_admin_cursor(rows[-1].get("last_seen"),
                                          rows[-1].get("id"))
                            if truncated and rows else None)}


@app.get("/api/admin/errors")
def admin_errors(request: Request, before: str = "", limit: int = 50,
                 since_hours: int = 0, severity: str = "",
                 include_resolved: bool = True) -> dict:
    """The console's Errors tab. Session-gated, like the rest of the console."""
    _require_superadmin(request)
    return _error_report(before=before, limit=limit, since_hours=since_hours,
                         min_severity=severity,
                         include_resolved=include_resolved)


@app.get("/api/ops/error-feed")
def ops_error_feed(request: Request, limit: int = 50, since_hours: int = 36,
                   severity: str = "") -> dict:
    """The same report, for the daily triage job. Token-gated.

    Two doors onto one payload, exactly as /api/admin/system and
    /api/admin/diagnostics already coexist: the session door authenticates a
    PERSON, which is right for the console and wrong for a robot that would
    have to hold a human's long-lived session to use it.

    Under /api/ops rather than /api/admin, and that is not cosmetic.
    test_every_console_route_is_gated walks app.routes and requires EVERY
    /api/admin/ path to answer 404 to a non-superadmin — "the next admin route
    is born tested". It carries exactly one exception, /api/admin/diagnostics,
    described in its own docstring as the older door. Adding two more would
    turn a guardrail that cannot be forgotten into a list somebody maintains,
    which is how the next unreviewed cross-user read ships. Machine doors get
    their own prefix instead, and the console's guarantee stays absolute.

    Defaults to a 36-hour window rather than 24: the job runs on a daily cron,
    and a calendar-day read drops anything that happened in the seam between
    one run and the next. Overlap costs nothing, because the fingerprint
    dedupes.
    """
    _require_error_feed(request)
    return _error_report(limit=limit, since_hours=since_hours,
                         min_severity=severity, include_resolved=False)


@app.post("/api/ops/errors/{fingerprint}/fixed")
def ops_error_fixed(fingerprint: str, request: Request,
                    payload: Optional[dict] = None) -> dict:
    """Mark a failure as having a fix proposed, so the job stops proposing one.

    Token-gated, and under /api/ops for the reason the feed above gives. It
    is never cleared automatically — if the bug returns, `last_seen` moves and
    the row surfaces again on its own, which is a fact rather than a guess
    about whether the fix worked.
    """
    _require_error_feed(request)
    pr = str((payload or {}).get("pr") or "")[:200]
    return {"ok": db.mark_error_fixed(fingerprint, pr)}


@app.get("/api/admin/audit")
def admin_audit_view(request: Request, before: str = "",
                     limit: int = 50) -> dict:
    _require_superadmin(request)
    limit = max(1, min(limit, 200))
    cursor = _cursor_from(before) if before else None
    rows = db.admin_audit_list(limit=limit + 1, before=cursor)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows,
            "next_cursor": (_admin_cursor(rows[-1].get("created_at"),
                                          rows[-1].get("id"))
                            if truncated and rows else None)}


# Disk below this and photo work will start failing mid-upload. Reporting it
# as "not ready" is what lets a deploy or a load balancer act on it before a
# seller loses a batch.
READY_MIN_DISK_MB = int(os.getenv("READY_MIN_DISK_MB", "200") or 200)


# A browser crash report. Everything about this route is shaped by one fact:
# it cannot require a session. A throw inside the app shell means there may be
# no session to authenticate with, and the crashes worth hearing about are
# exactly the ones that stop the app working — so an authenticated ingest
# would miss them. That makes it the only unauthenticated WRITE the app has,
# and the guards below are what pay for it.
CLIENT_ERROR_MAX_BYTES = 16 * 1024
# A ceiling across ALL clients, not just each one. The per-IP limit stops one
# broken browser; this stops a spray from many.
CLIENT_ERROR_GLOBAL_MAX = 600


@app.post("/api/client-errors", status_code=202)
async def client_error(request: Request) -> dict:
    """Record a crash the browser saw. Always answers 202, tells you nothing.

    The frontend had no error reporting at all: no boundary, no window.onerror,
    no ingest. A React render crash was a white screen that reached nobody.

    It answers 202 and an empty ok whether it recorded the report, threw it
    away for rate limiting, or could not parse it — deliberately. A 429 would
    tell a prober where the limit is, and the client treats any answer as
    final, which is what stops a failed report becoming the next report. The
    client is never told whether it was heard; it is not a channel.

    Nothing from the body is echoed back, and every field is truncated here as
    well as in the browser — the browser's caps are a courtesy, not a control.
    """
    ok = {"ok": True}
    try:
        raw = request.headers.get("content-length")
        if raw and int(raw) > CLIENT_ERROR_MAX_BYTES:
            return ok
    except (TypeError, ValueError):
        return ok

    ip = _client_ip(request)
    if not ratelimit.check(f"clienterr:{ip}",
                           max_attempts=config.CLIENT_ERROR_MAX_PER_WINDOW):
        return ok
    if not ratelimit.check("clienterr:_all",
                           max_attempts=CLIENT_ERROR_GLOBAL_MAX):
        return ok

    try:
        body = await request.body()
        if len(body) > CLIENT_ERROR_MAX_BYTES:
            return ok
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            return ok
    except Exception:  # noqa: BLE001 - a malformed report is not an incident
        return ok

    def _text(key: str, cap: int) -> str:
        value = payload.get(key)
        return (value if isinstance(value, str) else "")[:cap]

    message = _text("message", 500)
    if not message:
        return ok
    stack = _text("stack", 8000)
    component = _text("component_stack", 4000)
    # Fingerprinted on the message and the component stack, never on the URL:
    # /listing/abc and /listing/def are the same bug. The minified frame is
    # useless for reading but stable within a build, so it separates two
    # different crashes carrying the same message.
    errorlog.record(
        kind="frontend",
        level="ERROR",
        message=message,
        template=f"{_text('name', 120)}: {message}",
        module="browser",
        func=(component.split("\n")[1].strip() if "\n" in component
              else _text("kind", 40)) or "unknown",
        route=_text("url", 200),
        method="CLIENT",
        reference=_text("request_id", 16),
        sample={"stack": redact.scrub(stack, max_len=8000),
                "component_stack": redact.scrub(component, max_len=4000),
                "build": _text("build", 40),
                "reported_as": _text("kind", 40)},
    )
    return ok


@app.get("/api/ready")
def ready(response: Response) -> dict:
    """Readiness, as distinct from /api/health's liveness.

    /api/health answers "is this process up and what is configured"; it says
    200 while the machine is out of disk and the image model has never
    loaded. This answers the question that actually matters to a deploy or a
    client about to send a 12MB photo: can it do the work right now. Anything
    false here returns 503, so it is usable as an orchestrator probe rather
    than something a human has to read.

    Deliberately cheap and side-effect free — no model load, no inference, no
    outbound call. A probe that does real work is a probe that falls over
    under the load it is meant to detect.

    This is also the only PUBLIC payload carrying operational numbers, which
    is deliberate: /api/health went back to liveness plus capability flags so
    it stops publishing the deployment's internals to anyone who asks, and the
    health-watch alarm reads its thresholds from here instead. Everything
    added here has to earn that — a number or a boolean an operator acts on,
    never a name, a host, a bucket or an exception's text.
    """
    free_mb = round(storage.disk_free_bytes() / 1e6)
    database = db.db_status()
    checks = {
        # The volume photos are written to. Everything else is moot without it.
        "storage_writable": storage.writable(),
        "disk_space": free_mb >= READY_MIN_DISK_MB,
        # A configured DB that is unreachable means drafts silently do not
        # persist; no DB configured at all is a valid (filesystem-only) setup.
        "database": (not database.get("configured")) or bool(database.get("connected")),
    }
    engine = images.engine_state()
    # Photo offload, reported OUTSIDE `checks` on purpose.
    #
    # R2 failing is invisible from everywhere else: the module latches itself
    # off, every photo quietly stays on the volume, the volume then cannot be
    # reclaimed, and publishes start failing later for what looks like an
    # unrelated reason. So it has to be visible to the alarm.
    #
    # But it must not flip the 503. Photos still land on the volume and the
    # app is still serving; taking the only machine out of the load balancer
    # over a Cloudflare blip would turn a degradation into an outage.
    #
    # Booleans only. The bucket name, the URL mode, the missing variables'
    # NAMES and the raw error (whose endpoint carries the R2 account id) stay
    # on /api/admin/diagnostics, behind the token.
    objstore_state = {
        # Credentials present. Deliberately NOT objstore.enabled(), which also
        # goes false for the 600s latch: read through enabled(), "nobody ever
        # set this up" and "it broke ten minutes ago" arrive as the same
        # answer, which is what used to send an operator hunting for variables
        # that were all set. This stays true through the latch; `degraded`
        # below owns that case.
        "configured": config.r2_configured(),
        # The latch is tripped right now: photos are not reaching the bucket.
        "degraded": objstore.last_error() is not None,
    }
    ok = all(checks.values())
    if not ok:
        response.status_code = 503
    return {"ready": ok, "checks": checks, "disk_free_mb": free_mb,
            "object_storage": objstore_state, "image_engine": engine}


def _category_query(listing) -> str:
    parts = [listing.brand, listing.title, listing.category_suggestion]
    return " ".join(p for p in parts if p).strip()


def _category_queries(listing) -> list[str]:
    """The queries to try, best first, for a numeric eBay category id.

    One query used to be the whole attempt: brand + title + the AI's category
    path. eBay matches that string as a whole, and a long one carrying model
    numbers, adjectives and a path at once is exactly the kind that comes back
    with NOTHING — which is how a draft ends up showing a perfectly good
    category path beside an empty ID box, and blocked from publishing by a
    field the seller never filled in by hand.

    So: narrow the query rather than give up. The AI's own path is the last
    resort and a good one — it is a category description with none of the
    item's noise in it.
    """
    path = (listing.category_suggestion or "").strip()
    leaf = path.split(">")[-1].strip()
    seen, out = set(), []
    for q in (_category_query(listing), (listing.title or "").strip(), path, leaf):
        key = " ".join(q.lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _resolve_category(listing: Listing) -> None:
    """Auto-resolve a numeric eBay category id for a fresh AI draft (single,
    async, and bulk identify all need this).

    Best-effort — identify must never fail on a taxonomy problem — but never
    silent again. This swallowed every exception with a bare `pass`, so a
    misconfigured or unreachable Taxonomy API produced drafts with no category
    id, no log line, and no way to tell that apart from "eBay had no match".
    Both leave the seller stuck at the same blocked publish, and they have
    opposite fixes.
    """
    if listing.category_id:
        return _fit_condition_to_category(listing)
    if not config.taxonomy_ready():
        log.warning("category: no id resolved — the Taxonomy API needs "
                    "EBAY_CLIENT_ID/EBAY_CLIENT_SECRET, which aren't set")
        return _needs_a_category(listing)
    for query in _category_queries(listing):
        try:
            best = taxonomy.best_category_id(query)
        except Exception as exc:  # noqa: BLE001 - never block identify on this
            log.warning("category: eBay's Taxonomy API failed for %r: %s: %s",
                        query[:120], type(exc).__name__, exc)
            return _needs_a_category(listing)
        if best.get("category_id"):
            listing.category_id = best["category_id"]
            if best.get("path"):
                listing.category_suggestion = best["path"]
            return _fit_condition_to_category(listing)
        log.info("category: no eBay match for %r", query[:120])
    log.warning("category: eBay matched no category for %r — the draft goes "
                "out without an id and can't publish until one is picked",
                (listing.title or "")[:80])
    _needs_a_category(listing)


def _fit_condition_to_category(listing: Listing) -> None:
    """The category decides which conditions exist — so pick the condition
    AFTER the category, not before it.

    The AI grades what it can see in the photos ("used, some wear" -> Used -
    Good) with no idea where the item will be filed, and eBay offers a
    different ladder in nearly every part of the site: Very Good / Good /
    Acceptable exist only in media, the pre-owned grades only in apparel, and
    most of the rest of eBay has one plain "Used". A grade the category
    doesn't offer is not a warning at publish time — it is error 25021 and no
    listing, which is what took out three of the seller's last four.

    So the draft leaves identify carrying a condition its own category
    accepts: the same grade where it exists, the closest one in the same
    new/used family where it doesn't. Best-effort — a lookup that fails leaves
    the AI's grade alone and the preflight says so before anything reaches
    eBay.
    """
    cid = (listing.category_id or "").strip()
    current = (listing.condition or "").strip().upper()
    if not cid.isdigit() or not current or not config.taxonomy_ready():
        return
    try:
        allowed = taxonomy.allowed_condition_enums(cid)
    except Exception as exc:  # noqa: BLE001 - never block identify on this
        log.warning("condition: eBay's condition list for category %s failed: "
                    "%s: %s — leaving %s as it is", cid, type(exc).__name__,
                    exc, current)
        return
    if not allowed or current in allowed:
        return
    fitted = taxonomy.nearest_allowed_condition(current, allowed)
    if fitted and fitted != current:
        log.info("condition: %s isn't offered in category %s (%s) — using %s",
                 current, cid, ", ".join(allowed), fitted)
        listing.condition = fitted
        return
    # Nothing in the item's own family is on offer — a used item in a
    # new-only category. There is no honest substitute to make, so say it on
    # the draft instead of inventing one; the preflight blocks the publish
    # with the same news, and the Condition dropdown shows what eBay allows.
    log.info("condition: %s isn't offered in category %s and nothing there "
             "substitutes for it (%s)", current, cid, ", ".join(allowed))
    note = ("item condition — eBay doesn't offer that condition in this "
            "category; pick one from the Condition list")
    if not any("item condition" in m.lower() for m in listing.missing_info):
        listing.missing_info = [*listing.missing_info, note]


def _needs_a_category(listing: Listing) -> None:
    """Say on the draft itself that its category still needs picking.

    A draft without a category id cannot publish, and until now nothing said
    so until the seller pressed Publish and met a blocker on a field they had
    never been asked to fill — beside a Category box that looked perfectly
    filled in, because the AI's path was in it. missing_info is the list the
    editor already shows as "things to check", which is exactly what this is.
    """
    note = "eBay category — we couldn't match one; pick it from the suggestions"
    if not any("ebay category" in m.lower() for m in listing.missing_info):
        listing.missing_info = [*listing.missing_info, note]


def _tag_text_for(paths: list, aspects: list[dict]) -> str:
    """Zoom-and-transcribe the item's tags when the category is one where the
    facts live ON a tag (any Size-style aspect = clothing/shoes). Sizes are
    what the single-pass read kept getting wrong: a size tag in a normal photo
    is far too small to read, so this targeted close-up pass is what makes
    Size/Material/Country come off the actual tag. Best-effort — a failure
    just means the fill runs without a transcript."""
    if not any("size" in (a.get("name") or "").lower() for a in aspects):
        return ""
    try:
        text = claude_ai.read_tag_text(paths)
        if text:
            log.info("tag read: %d chars transcribed from tag close-ups", len(text))
        return text
    except Exception as exc:  # noqa: BLE001 - tag reading is an enhancement
        log.info("tag read skipped: %s", exc)
        return ""


# eBay's ceiling on values per aspect (mirrored in services/ebay.py).
_MAX_ASPECT_VALUES = 30


def _merge_filled_specifics(listing: Listing, filled: list,
                            aspects: list[dict]) -> int:
    """Merge AI-filled specifics into the listing without touching anything
    the seller answered. Aspect-aware:

    - An aspect the seller entered or confirmed (confidence "", per
      models.ItemSpecific) is left alone entirely.
    - An unanswered SINGLE aspect takes the AI's first value; one that already
      holds a value keeps it.
    - A MULTI aspect (Season, Features, Theme... — eBay's multi-select
      checkboxes) takes ALL the AI's values, and is TOPPED UP with the ones it
      doesn't already hold rather than skipped: a single value left over from
      the first vision pass used to block every further tick, which is why the
      checkbox specifics reached eBay with one box ticked at most.

    Then, on the merged result, the size-type rule: a men's garment sized XXL
    or larger is Size Type "Big & Tall", never "Regular" — eBay rejects that
    pairing. It runs here, after the merge, because the merged Size is what
    decides it, and here rather than only at publish so the seller sees the
    answer in the editor and can change it.

    Returns how many values were added."""
    multi = {a["name"].strip().lower() for a in aspects
             if (a.get("cardinality") or "SINGLE") == "MULTI"}
    have: dict[str, set[str]] = {}
    seller_owned: set[str] = set()
    # Rows that carry a NAME but no value — an identify pass that returned the
    # aspect empty, a value the seller cleared. The fill below reuses them
    # instead of appending a second row for the same aspect: a listing left
    # holding ["Color" = "", "Color" = "Multi-Color"] reads as answered to
    # anything that scans every row (this server) and as EMPTY to anything
    # that stops at the first (the browser's blocker list did), which is how a
    # seller ended up locked out of publishing over a Color they could see
    # filled in. Same aspect, one row.
    blanks: dict[str, int] = {}
    for i, s in enumerate(listing.item_specifics):
        value = (s.value or "").strip()
        k = s.name.strip().lower()
        if not value:
            if k:
                blanks.setdefault(k, i)
            continue
        have.setdefault(k, set()).add(value.lower())
        if not (s.confidence or "").strip():
            seller_owned.add(k)
    grouped: dict[str, list] = {}
    order: list[str] = []
    for f in filled:
        k = f.name.strip().lower()
        if k not in grouped:
            grouped[k] = []
            order.append(k)
        grouped[k].append(f)
    added = 0
    for k in order:
        if k in seller_owned:
            continue
        held = have.get(k, set())
        is_multi = k in multi
        if held and not is_multi:
            continue
        for f in (grouped[k] if is_multi else grouped[k][:1]):
            value = (f.value or "").strip()
            if not value or value.lower() in held or len(held) >= _MAX_ASPECT_VALUES:
                continue
            held.add(value.lower())
            have[k] = held
            blank_at = blanks.pop(k, None)
            if blank_at is None:
                listing.item_specifics.append(f)
            else:
                listing.item_specifics[blank_at] = f
            added += 1
    # Never raises: this runs on the identify path OUTSIDE the enrichment's
    # own try, so an exception here would take the whole draft down over a
    # convenience. A size type is not worth a listing.
    try:
        # Size first — it decides what the size type rule reads.
        taxonomy.fix_size_specifics(listing, aspects)
        taxonomy.apply_big_and_tall(listing, aspects)
    except Exception as exc:  # noqa: BLE001 - a default, never a blocker
        log.info("size defaults skipped: %s", exc)
    return added


def _fill_category_specifics(listing: Listing, image_paths: list) -> Optional[int]:
    """Best-effort: fill eBay's category item specifics (required + recommended)
    from the photos and merge them in without overwriting anything already set.
    Returns how many were added, or None when the enrichment DIDN'T RUN
    (unconfigured, no category, no photos, or it failed) — callers use that to
    decide whether the client-side autofill fallback still has work to do.
    NEVER raises — a listing must still save and publish if this fails.

    This runs server-side during identify (single + bulk) so listings come
    SEO-ready even on the bulk 'list live now' path, which publishes straight
    after identify and would otherwise reach eBay with only the generic
    specifics from the first vision pass (the 'specifics not populating' bug)."""
    if not (config.taxonomy_ready() and config.anthropic_ready()):
        return None
    if not listing.category_id:
        return None
    try:
        aspects = taxonomy.item_aspects(listing.category_id).get("aspects", [])
        paths = [p for p in image_paths if p.is_file()]
        if not aspects or not paths:
            return None
        filled = claude_ai.fill_aspects(paths, listing, aspects,
                                        tag_text=_tag_text_for(paths, aspects))
    except Exception as exc:  # noqa: BLE001 - enrichment is optional
        log.info("specifics enrich skipped (cat=%s): %s", listing.category_id, exc)
        return None
    added = _merge_filled_specifics(listing, filled, aspects)
    if added:
        log.info("specifics enrich: cat=%s added=%d", listing.category_id, added)
    _pair_aspects(listing, aspects)
    return added


# Aspect names that mean "who made this". A wrong maker is worse than a blank
# one, so these only get filled by the double-layer check below.
_MAKER_ASPECT_NAMES = {"brand", "maker", "manufacturer"}
# Placeholder values that mean the maker is effectively unknown.
_GENERIC_MAKERS = {"", "unbranded", "unknown", "generic", "n/a", "none",
                   "no brand", "handmade", "does not apply"}


def _maker_targets(listing: Listing) -> tuple[bool, list[str]]:
    """(brand is effectively blank, maker-ish aspects still unfilled) — the
    two things the maker hunt exists to fill. Both empty = skip the hunt."""
    brand_missing = (listing.brand or "").strip().lower() in _GENERIC_MAKERS
    have = {s.name.strip().lower() for s in listing.item_specifics if s.value.strip()}
    unfilled: list[str] = []
    try:
        if listing.category_id and config.taxonomy_ready():
            for a in taxonomy.item_aspects(listing.category_id).get("aspects", []):
                name = (a.get("name") or "").strip()
                if name.lower() in _MAKER_ASPECT_NAMES and name.lower() not in have:
                    unfilled.append(name)
    except Exception:  # noqa: BLE001 - aspects are optional context here
        pass
    return brand_missing, unfilled


def _apply_maker(listing: Listing, found: dict, brand_missing: bool,
                 unfilled: list[str]) -> None:
    """Write a verifier-confirmed maker onto the listing."""
    maker = found["maker"]
    if brand_missing:
        listing.brand = maker
    for name in unfilled:
        listing.item_specifics.append(ItemSpecific(name=name, value=maker))
    # The maker is settled now — drop stale "verify the brand" style nags.
    listing.missing_info = [m for m in listing.missing_info
                            if not any(w in m.lower()
                                       for w in ("brand", "maker", "manufacturer"))]
    log.info("maker id: '%s' confirmed (%s) — evidence: %s",
             maker, found.get("confidence"), (found.get("evidence") or "")[:120])


def _fill_maker(listing: Listing, image_paths: list) -> bool:
    """Best-effort maker/manufacturer identification (double-layer check).

    The generic identify pass is told never to guess, so Brand / Maker /
    Manufacturer are rarely filled. When they're missing, run the dedicated
    two-layer ID in claude_ai.identify_maker (hunt, then adversarial verify —
    like a reverse-image lookup with a second opinion) and only write a maker
    both layers agree on. NEVER raises. Returns True if anything was set."""
    if not config.anthropic_ready():
        return False
    brand_missing, unfilled = _maker_targets(listing)
    if not (brand_missing or unfilled):
        return False  # maker already known — don't burn two vision calls
    try:
        paths = [p for p in image_paths if p.is_file()]
        found = claude_ai.identify_maker(paths, listing)
    except Exception as exc:  # noqa: BLE001 - enrichment is optional
        log.info("maker id skipped: %s", exc)
        return False
    if not found:
        return False
    _apply_maker(listing, found, brand_missing, unfilled)
    return True


def _identify_chain() -> str:
    """Which post-identify enrichment chain runs: 'v2' (default) consolidates
    tag reading + specifics + the maker hunt into one vision call (plus a
    conditional maker verify); 'v1' is the original multi-call chain, kept
    wired as an instant rollback (IDENTIFY_CHAIN=v1)."""
    return os.getenv("IDENTIFY_CHAIN", "v2").strip().lower() or "v2"


def _pair_aspects(listing: Listing, aspects: list[dict]) -> None:
    """Make the specifics eBay pairs with each other agree. Never raises —
    the draft is worth more than the tidy-up."""
    try:
        for name, was, now in taxonomy.fit_paired_aspects(listing, aspects):
            log.info("aspect pairing: %s %s -> %s (cat=%s)",
                     name, was or "(blank)", now, listing.category_id)
    except Exception as exc:  # noqa: BLE001 - a convenience, never a blocker
        log.info("aspect pairing skipped (cat=%s): %s", listing.category_id, exc)


def _enrich_listing_v2(listing: Listing, image_paths: list, tags: list,
                       progress=None) -> Optional[int]:
    """Chain v2 enrichment: ONE consolidated vision call fills the category's
    item specifics AND hunts the maker, with zoomed crops of the tags the
    identify pass located passed inline as ground truth (no separate locate or
    transcribe round-trips). The adversarial maker verify still runs as its
    own call, but only when a candidate actually emerged. Returns how many
    specifics were added, or None when enrichment didn't run. NEVER raises."""
    if not (config.taxonomy_ready() and config.anthropic_ready()):
        return None
    if not listing.category_id:
        return None
    try:
        paths = [p for p in image_paths if p.is_file()]
        aspects = taxonomy.item_aspects(listing.category_id).get("aspects", [])
        if not aspects or not paths:
            return None
        crops = claude_ai.tag_crops(paths, tags) if tags else []
        brand_missing, unfilled = _maker_targets(listing)
        if progress:
            progress("specifics")
        filled, candidate = claude_ai.fill_aspects_combined(
            paths, listing, aspects, tag_crop_blocks=crops,
            want_maker=brand_missing or bool(unfilled))
    except Exception as exc:  # noqa: BLE001 - enrichment is optional
        log.info("specifics enrich skipped (cat=%s): %s", listing.category_id, exc)
        return None
    added = _merge_filled_specifics(listing, filled, aspects)
    if added:
        log.info("specifics enrich: cat=%s added=%d", listing.category_id, added)
    # eBay pairs some aspects with others — a Size Type has to be one it
    # publishes beside the Size on the tag. Done here, on the draft, so the
    # seller reads the answer in the editor and can still change it; the
    # publish path applies the same rule again for drafts made before this.
    _pair_aspects(listing, aspects)
    if candidate:
        # Re-check what's still missing AFTER the merge — "Brand" is itself an
        # aspect, so the fill above may have just answered it, and the verify
        # call (and a duplicate Brand entry) would be wasted.
        brand_missing, unfilled = _maker_targets(listing)
        if brand_missing or unfilled:
            if progress:
                progress("maker")
            try:
                found = claude_ai.verify_maker(
                    paths, listing, candidate["maker"],
                    candidate.get("evidence", ""))
            except Exception as exc:  # noqa: BLE001 - maker is best-effort
                log.info("maker verify skipped: %s", exc)
                found = None
            if found:
                _apply_maker(listing, found, brand_missing, unfilled)
    return added


def _specifics_were_filled(added: Optional[int]) -> bool:
    """Whether the server-side enrichment actually put specifics on the draft.

    This is the editor's cue to stand its own autofill fallback down, so the
    same vision passes don't run twice seconds apart and charge the seller for
    both. It is emphatically NOT "did the enrichment run": `added` is None when
    it never ran and 0 when it ran and filled nothing — the vision pass came
    back empty, or every value it proposed was dropped as illegal for its
    aspect. Reporting that empty pass as "filled" stood the fallback down too,
    and left the seller with every required specific blank and nothing on
    either side going back for them.
    """
    return bool(added)


def _enrich_listing(listing: Listing, image_paths: list, tags: list = None,
                    progress=None) -> Optional[int]:
    """Post-identify enrichment (item specifics + maker), routed by
    IDENTIFY_CHAIN. `progress(phase)` (optional) reports stage names for job
    heartbeats. Returns specifics added, or None when enrichment didn't run."""
    if _identify_chain() == "v1":
        if progress:
            progress("specifics")
        added = _fill_category_specifics(listing, image_paths)
        if progress:
            progress("maker")
        _fill_maker(listing, image_paths)
        return added
    return _enrich_listing_v2(listing, image_paths, tags or [], progress=progress)


def _uid(request: Request):
    user = auth.current_user(request)
    # The one choke point where the seller's id is already resolved. Doing
    # this in the request middleware instead would add a database read to
    # every asset fetch — and auth.current_user RAISES StorageUnavailable on
    # a database blip, which would turn one Neon hiccup into a failing
    # liveness probe on the only machine.
    errorlog.note_user(user["id"] if user else "")
    return user["id"] if user else None


# --- AI token gate (monetization) ------------------------------------------
# Every AI endpoint charges up front through these and refunds on failure
# ("only pay for AI that worked"). When billing is off (no TOKENS_ENABLED /
# no DB) they are no-ops, so dev and self-hosted installs stay free.

def _charge_uid(uid: str, feature: str, units: int = 1):
    """Debit a logged-in user. Returns the spend record for tokens.refund(),
    or None when billing is off / the DB failed open. Raises 402 when broke."""
    res = tokens.spend(uid, feature, units)
    if res is not None and not res.get("ok"):
        raise HTTPException(402, tokens.insufficient_message(res))
    return res


def _charge_ai(request: Request, feature: str, units: int = 1):
    """Token gate for a request-context AI endpoint. 401s anonymous callers
    when billing is on — balances are per-account, so metered AI requires a
    login (the logged-out flows keep working wherever billing is off)."""
    if not tokens.enabled():
        return None
    uid = _uid(request)
    if uid is None:
        raise HTTPException(
            401, "Log in to use AI features — your token balance is per account.")
    return _charge_uid(uid, feature, units)


def _assert_session_owner(session_id: str, request: Request) -> None:
    """404 when this session's saved listing belongs to a DIFFERENT user.
    Session ids appear in media URLs and can leak, so possession of an id
    must not grant write access. Unsaved or unowned (anonymous) sessions
    pass — the app supports logged-out flows.

    Fails CLOSED on a database outage. This check is the only thing standing
    between a leaked session id and write access to someone else's photos,
    and it answers from the database — so if a read failure were treated like
    "no such listing", one Neon blip would quietly disable the guard on every
    session-scoped endpoint at once, while the rest of the app (on-disk
    sessions, /media) kept serving. A brief 503 is the right trade.
    """
    rec = db.get_listing_strict(session_id)
    if rec is db.UNAVAILABLE:
        raise HTTPException(
            503, "Can't verify who this listing belongs to right now — "
                 "please try again in a moment.")
    if rec and rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")


# Moved to services/background.py so marketplace providers share it; the
# local name keeps every existing call site unchanged.
_in_background = run_in_background


def _ensure_local(session_id: str, name: str, path: Path) -> bool:
    """Make sure the optimized photo exists on the volume, pulling it back from
    R2 if the reclaim pass already freed the local copy.

    Viewing a photo survives the local copy being gone — /media just redirects
    to the bucket — but every edit (crop, rotate, straighten) opens the file on
    disk, so those started 404ing with "that photo isn't on the server anymore"
    on listings that were merely a little old. The bytes still exist; fetch
    them back instead of telling the user to re-upload."""
    if path.is_file():
        return True
    if not objstore.enabled():
        return False
    if objstore.restore(objstore.key_for(session_id, name), path):
        log.info("rehydrate: pulled %s/%s back from R2", session_id, name)
        return True
    return False


def _purge_session_images(session_id: str) -> None:
    """Delete a session's photos (local disk + R2). RAISES if it could not.

    For ERASURE — the deletion queue and the eBay account-deletion purge. Both
    decide whether an obligation is finished from whether this returns:
    `db.finish_media_purge` drops the debt the moment a purge raises nothing,
    and its docstring says so. This used to catch everything, and
    `objstore.delete_prefix` under it answered `0` for an unreachable bucket,
    so an erasure that never happened was recorded as done and the photos
    stayed in R2 for ever with the account that owned them already gone.

    One function was serving two callers with opposite needs. Cleanup after a
    merge, a delete or a sale wants the tolerant twin below: there a failed
    tidy-up must not fail the seller's request, and the sweep will find the
    objects later anyway.

    The R2 side deletes by PREFIX, not by walking the local directory. The
    reclaim pass offloads photos to the bucket and unlinks the local copies
    (see _offload_to_r2), so for any listing older than the offload TTL the
    local dir is empty and a name-by-name delete removed nothing at all.
    """
    if objstore.enabled():
        objstore.delete_prefix_strict(objstore.session_prefix(session_id))
    d = storage.session_dir(session_id)
    if d.exists():
        # ignore_errors is deliberate and is NOT the swallow above: the
        # bucket is the authority on what a session still has stored, and a
        # local copy that will not unlink is disk to reclaim, not an erasure
        # obligation -- the object it mirrors is already gone.
        shutil.rmtree(d, ignore_errors=True)


def _purge_session_images_best_effort(session_id: str) -> None:
    """The same cleanup, for callers where failing it would cost more than
    leaving the photos: after a merge, a delete, a bulk delete, or a sale.

    None of those is an erasure promise. The listing's own row is going or
    gone, the photos are reclaimable storage rather than something owed, and
    the orphan sweep reaches them later. Failing the seller's merge over a
    bucket that blinked would be the worse trade.
    """
    try:
        _purge_session_images(session_id)
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail the request
        log.warning("cleanup: image purge failed for %s: %s", session_id, exc)


def _finish_pending_deletions() -> dict:
    """Carry out erasures an earlier run promised and did not finish.

    Both halves — a seller's deleted photos and an acknowledged eBay
    account-deletion notice — record what is owed BEFORE doing the work, so
    that a crash in between is recoverable. Nothing was reading those records
    back. This is what reads them.
    """
    return deletion_queue.run_pending(purge_media=_purge_session_images)


def _settle_owed_refunds() -> int:
    """Pay back refunds an earlier attempt could not commit.

    A refund that failed because the database was unreachable used to vanish:
    the seller was charged, the AI failed, and nothing recorded the debt. The
    record lives on the volume (see services/owed_refunds for why not the
    database), and this is what drains it.
    """
    return owed_refunds.settle()


# --- auth ------------------------------------------------------------------

@app.post("/api/auth/signup")
def auth_signup(request: Request, response: Response, payload: dict) -> dict:
    _rate_limit_auth(request, "signup")
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not db.enabled():
        raise HTTPException(400, "Accounts require a database (set DATABASE_URL).")
    user = auth.signup(email, password)
    if user is db.EMAIL_TAKEN:
        raise HTTPException(409, "An account with that email already exists")
    if not user:
        raise HTTPException(
            503, "Account service is temporarily unavailable (database error). "
                 "Please try again shortly.")
    auth.set_session_cookie(response, user["id"], secure=request.url.scheme == "https")
    return {"user": user, "token": auth.make_token(user["id"])}


@app.post("/api/auth/login")
def auth_login(request: Request, response: Response, payload: dict) -> dict:
    _rate_limit_auth(request, "login")
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not db.enabled():
        raise HTTPException(400, "Accounts require a database (set DATABASE_URL).")
    user = auth.login(email, password)
    if not user:
        if not db.db_status().get("connected"):
            raise HTTPException(
                503, "Account service is temporarily unavailable (database "
                     "error). Please try again shortly.")
        raise HTTPException(401, "Invalid email or password")
    auth.set_session_cookie(response, user["id"], secure=request.url.scheme == "https")
    return {"user": user, "token": auth.make_token(user["id"])}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict:
    """Sign out THIS browser. The token itself stays valid until it expires —
    see /api/auth/logout-everywhere for the one that cancels it."""
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/auth/logout-everywhere")
def auth_logout_everywhere(request: Request, response: Response) -> dict:
    """Cancel every session token this account has, including this one.

    Clearing the cookie ends nothing for anyone else holding a copy of the
    token: it is self-contained and good for 30 days. A shared or borrowed
    device, a browser profile left signed in, a token out of a backup or a
    log — all of them kept working, and the seller had no way to end it. This
    is that way.

    It raises rather than reporting a failure as success (db.revoke_sessions
    is strict). Telling someone their other sessions are gone when the write
    never landed is the worst outcome available: they stop looking, and
    whoever holds the token keeps it.
    """
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    db.revoke_sessions(user["id"])
    # This browser too. Anything else would leave the seller looking at a
    # screen that says everything is signed out while it demonstrably is not.
    auth.clear_session_cookie(response)
    log.info("sessions revoked: user=%s", user["id"])
    return {"ok": True,
            "message": "Signed out everywhere. Sign in again to keep using "
                       "Thryft Shop on this device."}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"user": auth.current_user(request)}


# --- AI tokens (monetization) ----------------------------------------------

@app.get("/api/tokens")
def tokens_status(request: Request) -> dict:
    """Balance, feature costs, packs, and the next free reset — everything the
    balance chip and the buy dialog render. Anonymous callers get the catalog
    without a balance."""
    return tokens.status(_uid(request))


@app.get("/api/tokens/history")
def tokens_history(request: Request) -> dict:
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in to see your token history.")
    return {"entries": db.token_history(uid)}


@app.post("/api/tokens/checkout")
def tokens_checkout(request: Request, payload: dict) -> dict:
    """Start a Stripe Checkout for a token pack; returns the payment URL."""
    if not tokens.enabled():
        raise HTTPException(400, "Token billing isn't enabled on this server.")
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in to buy tokens.")
    pack_id = str(payload.get("pack_id", "")).strip()
    try:
        url = tokens.create_checkout(uid, pack_id, _base_url(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - stripe/network problem
        # Never Stripe's own words. They are written for whoever integrated
        # the API, not for the person buying: "No such price: price_1ABC",
        # "Invalid API Key provided: sk_live_51H4x***". The last of those puts
        # a fragment of a live secret in a toast, on the screen where someone
        # is trying to give us money.
        reference = _support_reference()
        log.warning("tokens: checkout failed for %s [%s]: %s",
                    uid, reference, exc)
        # The reference goes IN the sentence rather than beside it: the
        # client reads `detail` as a string (lib/api.js), so a structured body
        # here renders as "[object Object]" in the toast.
        #
        # "you have not been charged" is a fact, not a reassurance: creating a
        # Checkout Session moves no money. It is also the one thing the log
        # cannot tell them and the thing they will actually be wondering.
        raise HTTPException(502, (
            "We couldn't start your purchase just now, and you have not been "
            "charged. Try again in a moment — if it keeps happening, quote "
            f"{reference} to support.")) from exc
    return {"url": url}


@app.get("/api/tokens/confirm")
def tokens_confirm(request: Request, session_id: str = "") -> dict:
    """Post-redirect fallback credit: verifies the Checkout session with
    Stripe and credits idempotently (the webhook may have won the race)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in to confirm your purchase.")
    try:
        res = tokens.confirm_checkout(uid, session_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        reference = _support_reference()
        log.warning("tokens: confirm failed for %s [%s]: %s",
                    uid, reference, exc)
        # The opposite reassurance from checkout above: here the money may
        # already have moved, so the honest thing to say is that the tokens
        # are still coming. They are — the Stripe webhook credits the same
        # session idempotently, and this route is only the redirect fallback.
        raise HTTPException(502, (
            "We couldn't confirm your purchase just now. If the payment went "
            "through, your tokens will be credited automatically — quote "
            f"{reference} to support if they don't appear shortly.")) from exc
    res["balance"] = tokens.status(uid)
    return res


@app.post("/api/tokens/webhook")
async def tokens_webhook(request: Request) -> dict:
    """Stripe webhook (checkout.session.completed). Signature-verified against
    the raw body; a DB outage returns 503 so Stripe retries the delivery."""
    payload = await request.body()
    try:
        # handle_webhook verifies the signature and then writes the credit
        # through db.token_credit - a synchronous SELECT ... FOR UPDATE +
        # INSERT + COMMIT against Neon. Called directly it blocks the event
        # loop for the whole round trip, stalling every other request on this
        # single-machine app, the liveness check included. Stripe delivers
        # these unattended and retries on 5xx, so nobody is watching when it
        # happens.
        return await run_in_threadpool(
            tokens.handle_webhook, payload,
            request.headers.get("Stripe-Signature", ""))
    except PermissionError as exc:
        raise HTTPException(400, "Invalid signature") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "Temporarily unavailable — retry") from exc

@app.get("/api/account/summary")
def account_summary(request: Request) -> dict:
    """What deleting this account would destroy — shown in the confirm dialog
    so nobody deletes blind. Counting live listings separately matters: those
    stay up on eBay after deletion, and a seller must know that before they
    lose the tools to manage them.

    `counted` says whether the numbers can be trusted. Silently showing "0
    live listings" would suppress exactly the warning this endpoint exists to
    give, so an unreachable database is reported as unknown, not as zero --
    both when db_status says it is down and when the read itself fails.
    """
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    counted = db.db_status().get("connected", False)
    # Counted in SQL, not measured off a page. These used to be
    # `len(db.list_listings(limit=LIST_CAP))` and a Python sum over the same
    # rows, which fetched every listing's whole JSON blob across a
    # cross-region link to produce two integers -- and told a seller with more
    # records than the cap that they were about to erase fewer than they have,
    # live ones included. No cap fixes that; a count either counts or it is a
    # floor with nothing saying so.
    total = live = 0
    try:
        if counted:
            total = db.count_listings(user["id"])
            live = db.count_listings(user["id"], statuses=("published", "live"))
    except errors.StorageUnavailable:
        # db_status said connected and the read still failed. The endpoint's
        # own contract answers that: unknown, not zero. Zero here would
        # suppress the "these stay live on eBay after you delete" warning the
        # dialog exists to give.
        total, live, counted = 0, 0, False
    return {
        "email": user.get("email", ""),
        "counted": counted,
        "listings": total,
        "live_listings": live,
        "ebay_connected": bool((db.get_ebay_account(user["id"]) or {}).get("refresh_token")),
    }


@app.post("/api/account/delete")
def account_delete(request: Request, response: Response, payload: dict) -> dict:
    """Permanently delete the signed-in account and everything keyed to it.

    Deliberately a POST (not DELETE): some mobile webviews and corporate
    proxies drop DELETE bodies, and this one carries the password.

    Deleting means deleting — the account row, the eBay connection (our stored
    refresh token goes with it), every listing, and every photo on disk and in
    R2. What we cannot delete is anything on eBay's side: listings published
    there stay live under the seller's own eBay account, and the authorization
    grant is theirs to revoke in eBay's settings. Both facts are stated in the
    confirm dialog and the privacy policy rather than quietly assumed.
    """
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    uid = user["id"]

    # Password-guessing here is the same attack as on /login, with a worse
    # payoff for the victim — throttle it the same way.
    _rate_limit_auth(request, "account-delete")

    # Re-authenticate: a leaked session token must not be enough to erase an
    # account. (A password is always set — signup is the only way in.)
    password = str(payload.get("password", ""))
    stored = db.get_password_hash(uid)
    if not stored:
        raise HTTPException(
            503, "Account service is temporarily unavailable. Please try again shortly.")
    if not auth.verify_password(password, stored):
        raise HTTPException(401, "That password doesn't match. Try again.")

    listing_ids = db.delete_user(uid)
    if listing_ids is None:
        raise HTTPException(
            503, "Couldn't delete your account just now — nothing was changed. "
                 "Please try again in a moment.")

    # Photos last, and only after the rows are really gone: one background
    # sweep for the whole account (a thread per listing would spawn thousands
    # on a synced store and exhaust the 1GB machine), so a slow R2 delete never
    # holds the response or fails a deletion that already happened.
    #
    # It drains the whole queue rather than just this account's listings, and
    # that is deliberate. delete_user recorded these listings as owed inside
    # its own transaction, so they are in the queue either way; running the
    # queue is what CLEARS them, and picking up anything an earlier run left
    # behind costs nothing here. If this thread dies part-way, the rows it did
    # not reach are still owed and the next pass finds them — which is the
    # whole difference from what this used to be.
    _in_background(_finish_pending_deletions, what="account-delete cleanup")

    auth.clear_session_cookie(response)
    log.info("account deleted: user=%s listings=%d", uid, len(listing_ids))
    return {"ok": True, "deleted_listings": len(listing_ids)}

# --- eBay connect (Sign in with eBay) --------------------------------------

# The token cache, per-user creds bundle, promotion helpers and the whole
# publish pipeline moved to marketplaces/ebay_provider.py; these same-named
# wrappers keep every existing /api/ebay/* route below unchanged.


def _ebay_creds_for(request: Request):
    """Build live eBay creds for the logged-in user, or None if not connected."""
    return ebay_provider.creds_for(_uid(request))


EBAY_NONCE_COOKIE = "ebay_oauth_nonce"
NATIVE_RETURN_COOKIE = "thryft_oauth_return"  # "app" when the flow started in the shell
RETURN_ORIGIN_COOKIE = "thryft_oauth_origin"  # the app origin the flow started on


@app.post("/api/auth/connect-ticket")
def connect_ticket(request: Request) -> dict:
    """A 60-second single-purpose credential for starting an OAuth connect
    flow from the native shell, where the connect NAVIGATION can carry
    neither the Bearer header nor the cross-origin session cookie. Scoped so
    a leaked URL is useless for anything but opening a connect screen."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    return {"ticket": auth.make_ticket(uid, "connect")}


def _connect_uid(request: Request, ticket: str) -> Optional[str]:
    """Who is starting this connect flow: the session (web) or a ticket
    (native shell's full-page navigation)."""
    return _uid(request) or (auth.verify_ticket(ticket, "connect") if ticket else None)


def _mark_native_flow(resp, request: Request, native: str) -> None:
    """Remember that this OAuth flow began inside the native shell, so the
    callback can send the webview back into the app instead of leaving the
    user stranded on the website. Rides a cookie exactly like the nonce."""
    if str(native).lower() in ("1", "true", "yes"):
        resp.set_cookie(NATIVE_RETURN_COOKIE, "app", max_age=600, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")


def _request_origin(request: Request) -> str:
    """The origin this request was addressed to, as scheme://host[:port]."""
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _offsite_connect(request: Request, uid: str, path: str, native: str):
    """Send a connect flow to the origin the marketplace will return it to.

    A marketplace callback URL names ONE origin (config.OAUTH_ORIGIN). A flow
    started anywhere else sets its CSRF nonce cookie on a host the callback
    never reaches, so it can only ever fail — and fail as "expired", which
    reads like the seller's fault. Rather than let that happen, start the flow
    on the right origin in the first place and remember where to put the seller
    back afterwards.

    The hop carries a 60-second connect ticket because the session cookie is
    host-only and will not cross. Returns None when there is nothing to do:
    OAUTH_ORIGIN unset, already on it, or an origin this app does not claim.
    """
    here = _request_origin(request)
    if not config.OAUTH_ORIGIN or here == config.OAUTH_ORIGIN:
        return None
    # Host is client-controlled — Fly forwards whatever it is handed — so an
    # unrecognised one is never worth a redirect, let alone a return trip.
    if not config.oauth_return_ok(here):
        return None
    params = {"ticket": auth.make_ticket(uid, "connect"), "return_to": here}
    if str(native).lower() in ("1", "true", "yes"):
        params["native"] = "1"
    log.info("connect: bouncing %s to the OAuth origin, returning to %s",
             path, here)
    return RedirectResponse(
        f"{config.OAUTH_ORIGIN}{path}?{urlencode(params)}")


def _mark_return_origin(resp, request: Request, return_to: str) -> None:
    """Remember which of this app's origins to hand the seller back to.

    Validated on the way IN, never on the way out: the value is only ever one
    of config.APP_ORIGINS, so the redirect _finish_connect builds from it
    cannot be pointed at someone else's site.
    """
    origin = (return_to or "").strip().rstrip("/")
    if origin and origin != _request_origin(request) and config.oauth_return_ok(origin):
        resp.set_cookie(RETURN_ORIGIN_COOKIE, origin, max_age=600,
                        httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")


def _finish_connect(request: Request, path: str):
    """End an OAuth flow: plain redirect on the web; when the flow started in
    the native shell, an interstitial that navigates the webview back to the
    app's own origin. The interstitial (JS + a visible button) is used instead
    of a bare 302 to a custom scheme because WKWebView handles an in-page
    navigation to capacitor:// more reliably than a server redirect."""
    if request.cookies.get(NATIVE_RETURN_COOKIE) != "app":
        # A flow that began on another of this app's origins goes home to it,
        # rather than leaving the seller on the callback host — where their
        # session cookie does not exist, so they would appear logged out on a
        # hostname they never chose. Re-checked against APP_ORIGINS because a
        # cookie is the one thing here the browser could have been given
        # elsewhere.
        origin = request.cookies.get(RETURN_ORIGIN_COOKIE, "")
        if config.oauth_return_ok(origin):
            resp = RedirectResponse(f"{origin}{path}")
            resp.delete_cookie(RETURN_ORIGIN_COOKIE)
            return resp
        return RedirectResponse(path)
    target = f"{config.NATIVE_APP_ORIGIN}{path}"
    resp = HTMLResponse(f"""<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Returning to Thryft Shop…</title></head>
<body style="font-family:-apple-system,sans-serif;display:grid;place-items:center;min-height:90vh;text-align:center">
<div><p>All done — heading back to the app…</p>
<p><a href="{target}" style="display:inline-block;padding:12px 20px;border-radius:10px;background:#2563eb;color:#fff;text-decoration:none">Return to Thryft Shop</a></p></div>
<script>location.replace({json.dumps(target)});</script>
</body></html>""")
    resp.delete_cookie(NATIVE_RETURN_COOKIE)
    resp.delete_cookie(RETURN_ORIGIN_COOKIE)
    return resp


@app.get("/api/ebay/connect")
def ebay_connect(request: Request, ticket: str = "", native: str = "",
                 return_to: str = ""):
    if not config.ebay_oauth_ready():
        raise HTTPException(400, "eBay OAuth not configured (EBAY_CLIENT_ID/SECRET/RUNAME).")
    uid = _connect_uid(request, ticket)
    if not uid:
        raise HTTPException(401, "Log in before connecting eBay.")
    # eBay resolves EBAY_RUNAME to ONE accepted URL, so the callback lands on
    # one origin whatever this request was addressed to. Start the flow there.
    offsite = _offsite_connect(request, uid, "/api/ebay/connect", native)
    if offsite is not None:
        return offsite
    import secrets as _secrets
    nonce = _secrets.token_urlsafe(24)
    resp = RedirectResponse(ebay_auth.authorize_url(state=auth.make_state(uid, nonce)))
    # Bind the flow to this browser: the callback requires this cookie to match
    # the nonce embedded in the signed state (CSRF protection). Lax so it rides
    # the top-level redirect back from eBay.
    resp.set_cookie(EBAY_NONCE_COOKIE, nonce, max_age=600, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    _mark_native_flow(resp, request, native)
    _mark_return_origin(resp, request, return_to)
    return resp


@app.get("/api/ebay/callback")
def ebay_callback(request: Request, code: str = "", state: str = ""):
    verified = auth.verify_state(state)
    if not code or not verified:
        # An unreadable state is a signature that no longer verifies (SECRET_KEY
        # rotated), a stale link, or a tampered one. All three want the same
        # thing from the seller: start again.
        log.warning("ebay callback: %s", "no code" if not code
                    else "state did not verify")
        return _finish_connect(request, "/?ebay=error&why=expired")
    uid, nonce = verified
    # The nonce in the signed state must match the cookie set at connect time,
    # so a callback can only bind an eBay account to the browser that started
    # the flow (blocks CSRF authorization-code injection).
    cookie_nonce = request.cookies.get(EBAY_NONCE_COOKIE, "")
    if not cookie_nonce or cookie_nonce != nonce:
        log.warning("ebay callback: nonce mismatch (uid=%s) — cookie %s", uid,
                    "missing" if not cookie_nonce else "did not match")
        return _finish_connect(request, "/?ebay=error&why=expired")
    try:
        # NOT `tokens` — that is the billing module, imported at the top of
        # this file. Shadowing it here left the handler one added
        # tokens.spend() call away from an AttributeError on a dict, inside a
        # try whose except turns anything at all into a bare "/?ebay=error".
        grant = ebay_auth.exchange_code(code)
        access = grant["access_token"]
        policies = ebay_auth.fetch_policies_and_location(access)
        # Record WHICH eBay account this is, so the user can confirm they
        # connected the right one (best-effort — never block connect on it).
        ident = {"user_id": "", "username": "", "email": ""}
        try:
            ident = ebay_auth.identity_display(ebay_auth.fetch_user_identity(access))
        except Exception as exc:  # noqa: BLE001
            log.warning(f"ebay: identity fetch failed on connect: {exc}")
        existing = db.get_ebay_account(uid) or {}
        prev_user = (existing.get("ebay_username") or "").strip()
        new_user = (ident["username"] or "").strip()
        save_kwargs = {
            "refresh_token": grant["refresh_token"],
        }
        # Only write identity we actually learned. The fetch above is
        # best-effort and 403s on connections made before the identity scope
        # was granted, so it hands back "" far more often than it hands back a
        # wrong name — and "" is not a name, it is "we don't know". Writing it
        # anyway erased a username the app already had, and a blank username
        # makes listing_sync.belongs_to scope nothing (every account's listings
        # look like this one's) while count_foreign_listings reports every
        # labelled record as foreign. db.save_ebay_account skips None, so None
        # is how "leave what's there" is spelled.
        save_kwargs["ebay_username"] = new_user or None
        save_kwargs["ebay_email"] = ident["email"] or None
        # eBay's immutable account id, under the same "None means leave what's
        # there" rule as the name above. This is what an account-deletion
        # notice arrives carrying, and the only identifier that survives a
        # seller renaming themselves.
        save_kwargs["ebay_user_id"] = (ident.get("user_id") or "").strip() or None
        # Keep a saved policy/location choice only when it still EXISTS on the
        # account that just connected. Business-policy ids belong to one
        # seller: eBay rejects another's outright, and a listing published with
        # one fails for a reason the seller can't see in any field.
        #
        # This used to be decided from the account NAME, keeping everything
        # whenever the name was unreadable — which is the common case, since
        # connections made before the identity scope was granted 403 on it. A
        # seller who switched accounts then carried the old account's shipping,
        # payment, return and location ids straight into the new one.
        # Attribute access, not positional unpacking. reconcile_account_settings
        # returns a 3-field Reconciled NamedTuple; unpacking it into two names
        # raised ValueError here on EVERY connect, which the blanket except
        # below turned into a bare "?ebay=error". Nothing was ever saved, so no
        # seller could connect at all.
        reconciled = ebay_account.reconcile_account_settings(
            access, existing, policies)
        conclusive = reconciled.conclusive
        save_kwargs.update(reconciled.changes)
        switched = bool(prev_user and new_user and prev_user != new_user)
        if switched or ebay_account.settings_were_dropped(save_kwargs, existing):
            # A different store. Label everything already here as the previous
            # account's, so syncs and publishes stop treating those listings as
            # this account's (see services/listing_sync.belongs_to).
            #
            # Dropped settings alone say the store changed, not who it was: a
            # policy the seller deleted looks the same, and the previous name
            # is often unreadable. UNKNOWN_ACCOUNT records that honestly rather
            # than inventing a username the sweeps would compare against.
            marked = db.stamp_ebay_account(
                uid, prev_user or listing_sync.UNKNOWN_ACCOUNT)
            log.info("ebay connect: account switch for uid=%s (%s -> %s); "
                     "labelled %d existing listing(s)", uid,
                     prev_user or "?", new_user or "?", marked)
            # The old ZIP belonged to the old store; create_on_ebay re-reads it
            # from eBay when it's blank.
            save_kwargs.setdefault("ship_from_postal", "")
        db.save_ebay_account(uid, **save_kwargs)
        # This connect just reconciled the account-scoped ids against the
        # account that authorised, so the publish path need not redo it at
        # once -- but ONLY if eBay actually answered for every one of them.
        #
        # Neither lookup raises when it fails, so a connect made during an eBay
        # outage keeps the previous account's ids and looks, from here, exactly
        # like a connect where everything already matched. Marking that
        # verified would suppress the publish-path repair for the whole TTL in
        # precisely the case the repair exists for -- and because a seller's
        # instinct is to disconnect and reconnect, every retry would re-arm the
        # suppression and they would never reach it at all.
        if conclusive:
            ebay_account.note_verified(uid)
            # Only a conclusive pass may claim eBay has none of something —
            # same rule the publish path applies (ebay_provider._with_current_policies).
            ebay_account.note_absent(uid, reconciled.absent)
        # Name the account in the redirect. "eBay connected!" is true of the
        # wrong store too, and a seller who has just been handed a different
        # account than the one they picked has no other signal until their old
        # store's listings start importing. The username is a public seller
        # name, not a secret.
        landing = "/?ebay=connected"
        if new_user:
            landing += "&as=" + quote(new_user, safe="")
        resp = _finish_connect(request, landing)
        resp.delete_cookie(EBAY_NONCE_COOKIE)
        return resp
    except ebay_auth.OAuthError as exc:
        # eBay told us why. Pass the bucket to the UI so the seller is told
        # whether to try again or that it is not theirs to fix, and keep
        # eBay's own words in the log for whoever has to fix it.
        log.warning("ebay: connect refused for uid=%s: %s | %s", uid, exc,
                    exc.description)
        return _finish_connect(request, f"/?ebay=error&why={exc.reason}")
    except httpx.RequestError as exc:
        log.warning("ebay: connect could not reach eBay for uid=%s: %s", uid, exc)
        return _finish_connect(request, "/?ebay=error&why=network")
    except db.StorageUnavailable as exc:
        # eBay authorised, but the connection did not commit. Saying
        # "connected" here is the worst outcome available: the grant is real,
        # so the seller has no reason to doubt it, and every later publish
        # fails on a screen that says they are connected. Named separately
        # from the catch-all below so the UI can say "try again" rather than
        # "something went wrong".
        log.warning("ebay: connect authorised but did not persist for uid=%s: %s",
                    uid, exc)
        return _finish_connect(request, "/?ebay=error&why=storage")
    except Exception:  # noqa: BLE001
        # Anything left — a DB write, a bug. Without the traceback there is
        # nothing to tell these apart afterwards, and a connect that won't
        # stick is the one problem a seller cannot debug from the UI.
        log.exception("ebay: connect callback failed for uid=%s", uid)
        return _finish_connect(request, "/?ebay=error&why=unknown")


@app.get("/api/ebay/status")
def ebay_status(request: Request) -> dict:
    uid = _uid(request)
    acct = db.get_ebay_account(uid) if uid else None
    connected = bool(acct and acct.get("refresh_token"))
    # Which server-side OAuth vars are absent (names only, never values) — so
    # "the button does nothing" is diagnosable from the UI instead of guessed.
    oauth_missing = [name for name, val in (
        ("EBAY_CLIENT_ID", config.EBAY_CLIENT_ID),
        ("EBAY_CLIENT_SECRET", config.EBAY_CLIENT_SECRET),
        ("EBAY_RUNAME", config.EBAY_RUNAME),
    ) if not val]
    return {
        "oauth_ready": config.ebay_oauth_ready(),
        "oauth_missing": oauth_missing,
        "connected": connected,
        "env": config.EBAY_ENV,
        # eBay label purchasing (Logistics API) is limited-release; the
        # shipping dialog leads with Pirate Ship until it's enabled.
        "labels_enabled": config.EBAY_LOGISTICS_ENABLED,
        # Buyer messages (Message API) is limited-release too. The inbox icon
        # keys its visibility off THIS, not off /api/messages, because status
        # loads at boot — so the icon never flashes in and back out.
        "messaging_enabled": config.EBAY_MESSAGING_ENABLED,
        # Which eBay account is linked (empty for connections made before the
        # identity scope was added — reconnecting fills it in).
        "username": (acct.get("ebay_username") or "") if connected else "",
        "email": (acct.get("ebay_email") or "") if connected else "",
        "policies": {
            "fulfillment": bool(acct and acct.get("fulfillment_policy_id")),
            "payment": bool(acct and acct.get("payment_policy_id")),
            "return": bool(acct and acct.get("return_policy_id")),
            "location": bool(acct and acct.get("merchant_location_key")),
        } if connected else {},
        # Listings still here from an eBay account that ISN'T the connected
        # one. They're excluded from every eBay call, but they're visible, so
        # the UI has to be able to explain them rather than let them read as
        # "the new account somehow has my old items".
        "foreign_listings": (db.count_foreign_listings(
            uid, acct.get("ebay_username") or "") if connected and uid else 0),
        # eBay-linked records with no owner recorded — they predate ownership
        # stamping, so after an undetected switch they are the old account's
        # items wearing no label. Reported separately because only the seller
        # can say whose they are (see release_foreign_listings).
        "unowned_listings": (db.count_unowned_ebay_listings(uid)
                             if connected and uid else 0),
    }


@app.post("/api/ebay/release-foreign-listings")
def release_foreign_listings(request: Request,
                             payload: Optional[dict] = None) -> dict:
    """Unlink every listing belonging to a previously-connected eBay account.

    The records stay — they're the seller's own work, photos and all — but the
    eBay item id, source and live status come off, so they become ordinary
    local drafts of the current account instead of ghosts of the old store.
    Nothing is deleted, and nothing is touched on eBay.

    `include_unowned` additionally unlinks eBay-linked records with NO owner
    recorded. Those predate ownership stamping, so after an undetected switch
    the app cannot tell them from the connected account's own imports — which
    is why they never release by default, and why releasing them is the
    seller's explicit call: only they know whether the store behind those
    items is still the one connected. Publishes stamp the owner now, so this
    legacy pool only ever shrinks.
    """
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    include_unowned = bool((payload or {}).get("include_unowned"))
    acct = db.get_ebay_account(uid) or {}
    connected = (acct.get("ebay_username") or "").strip()
    # Selected in SQL, OLDEST first, rather than filtered out of the newest
    # LIST_CAP records. The banner that offers this button counts in SQL over
    # the whole store (db.count_foreign_listings), and a record from a
    # previous account is by definition old -- so the page read here was
    # precisely the wrong end of the store: the seller was told twelve
    # listings were linked to the old account, pressed the button, and was
    # told seven were unlinked, every time. One row past the cap, so a run
    # that did part of the job can say so.
    candidates = db.list_releasable_listings(uid, connected, include_unowned,
                                             limit=RELEASE_CAP + 1)
    released = unowned = 0
    remaining = 0
    for rec in candidates:
        data = rec.get("listing") or {}
        # The SQL is a superset; this is still where the decision is made.
        if not ebay_account.releasable(data, connected, include_unowned):
            continue
        if released >= RELEASE_CAP:
            # Each release is a write, and a request that walks an entire
            # store outlives the gateway. Bounded, and the remainder reported
            # rather than left looking like a finished run.
            remaining += 1
            continue
        legacy = not listing_sync.account_of(data)

        if db.mutate_listing_data(rec["id"], ebay_account.unlink_ebay,
                                  status="draft", user_id=uid) is not None:
            released += 1
            if legacy:
                unowned += 1
    log.info("release-foreign-listings: uid=%s released=%d (unowned=%d, "
             "remaining=%d, connected=%s)", uid, released, unowned, remaining,
             connected or "?")
    return {"released": released, "released_unowned": unowned,
            "remaining": remaining}


@app.get("/api/ebay/policies")
def get_ebay_policies(request: Request) -> dict:
    """The connected seller's eBay business policies + which ones are set as
    this account's defaults. These are eBay's 'templates' for shipping,
    payment, and returns; a listing's offer references them."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first to load your policies.")
    lists = ebay_auth.list_business_policies(creds["access_token"])
    acct = db.get_ebay_account(_uid(request)) or {}
    return {
        "policies": lists,
        "selected": {
            "fulfillment_policy_id": acct.get("fulfillment_policy_id", ""),
            "payment_policy_id": acct.get("payment_policy_id", ""),
            "return_policy_id": acct.get("return_policy_id", ""),
        },
        "location_set": bool(acct.get("merchant_location_key")),
        "ship_from_postal": acct.get("ship_from_postal", ""),
        "manage_url": "https://www.bizpolicy.ebay.com/businesspolicy/manage",
    }


@app.get("/api/ebay/shipping-services")
def shipping_services() -> dict:
    """The catalog of eBay shipping services a seller can one-tap into a
    fulfillment policy (static; no auth needed)."""
    return {"services": ebay_auth.SHIPPING_SERVICES}


_UNREVIEWED = (
    "Review the policy terms before we create them on your eBay account. "
    "They are shown to buyers and eBay holds you to them.")


def _accepted(payload: Optional[dict]) -> bool:
    """Did the seller actually say yes to the terms?

    Strictly `True`, not truthiness: a stale client sending `"no"`, a
    half-filled form sending `0`, or a request with the key missing entirely
    are all the ABSENCE of an answer, and absence is not consent — least of
    all to a 30-day return window and a dispatch deadline eBay scores.
    """
    return (payload or {}).get("accept_terms") is True


@app.get("/api/ebay/policy-preview")
def ebay_policy_preview(service_code: str = "",
                        return_days: Optional[int] = None,
                        return_payer: str = "",
                        immediate_pay: bool = True) -> dict:
    """Exactly what "Create my policies" would commit the seller to.

    A business policy is a public promise -- dispatch time, return window, who
    pays return postage -- attached to every listing that references it, and
    eBay scores the seller against it. The app chose all of it behind one
    button and the seller learned the terms by reading them back off eBay.

    Static and side-effect free by construction: it builds the request bodies
    and describes them, without an access token, a network call or any part of
    the account's daily quota. That is deliberate. A preview that needed eBay
    to be reachable would be unavailable exactly when the seller is retrying,
    and the retry is where the unconsidered "yes" gets clicked.
    """
    return ebay_policy_terms.describe(
        service_code=service_code, return_days=return_days,
        return_payer=return_payer, immediate_pay=bool(immediate_pay))


@app.post("/api/ebay/ensure-policy")
def ensure_policy(request: Request, payload: dict) -> dict:
    """Find — or create — a fulfillment policy for any catalog shipping
    service, and make it the account default if none is set yet."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    svc = ebay_auth.service_by_code(str(payload.get("service_code", "")))
    if not svc:
        raise HTTPException(400, "Unknown shipping service.")
    # Same commitment as the three-at-once button -- a dispatch deadline eBay
    # scores the seller on -- so it is gated the same way. Leaving one door
    # open would just move where the unreviewed policy gets made.
    if not _accepted(payload):
        raise HTTPException(400, _UNREVIEWED)
    try:
        pol = ebay_auth.ensure_service_policy(creds["access_token"], svc)
    except ebay_auth.PolicyLookupUnavailable as exc:
        # eBay could not be asked what the account already has. 503 and not
        # 502: nothing is wrong with the request, and the seller should retry
        # rather than be told eBay refused something. Creating anyway is what
        # left duplicate policies on real accounts.
        raise HTTPException(503, str(exc)) from exc
    except ebay_auth.AccountApiError as exc:
        raise HTTPException(
            502, f"eBay couldn't create the policy: {exc.description}") from exc
    except httpx.HTTPError as exc:
        # Same rule as the ship-from save below: eBay's own refusals answer
        # through AccountApiError above with eBay's description, so this arm
        # is only the transport case — and its str() carries the API base,
        # the path and a status line.
        raise _lookup_failed("create that policy on your eBay account",
                             exc, status=503) from exc
    if pol.get("id") and not creds.get("fulfillment_policy_id"):
        db.save_ebay_account(creds["_uid"], fulfillment_policy_id=pol["id"])
    return pol


@app.post("/api/ebay/ensure-all-policies")
def ensure_all_policies(request: Request, payload: Optional[dict] = None) -> dict:
    """Give the account the three business policies a publish needs.

    Being opted into the policy program is necessary and not sufficient: the
    account still needs one shipping, one payment and one return policy before
    eBay will accept a listing. Only the fulfillment half of that existed here
    (ensure-policy, one shipping service at a time) — payment and return could
    be listed and never created, so a seller with none was sent to Seller Hub
    to hand-build two policies whose contents this app already assumes.

    Each is found-or-created independently and each result says which it was,
    so a partial success is legible rather than an all-or-nothing failure. Ids
    are saved as the account defaults only where none is set: a seller who
    deliberately chose a policy keeps it.
    """
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    # Before the account is read and before anything is saved: an unreviewed
    # request must not be able to leave a half-written trail either.
    if not _accepted(payload):
        raise HTTPException(400, _UNREVIEWED)
    token, opts = creds["access_token"], (payload or {})
    svc = (ebay_auth.service_by_code(str(opts.get("service_code", "")))
           or ebay_auth.service_by_code("USPSGroundAdvantage"))
    steps = {
        "fulfillment": lambda: ebay_auth.ensure_service_policy(token, svc),
        "payment": lambda: ebay_auth.ensure_payment_policy(token),
        "return": lambda: ebay_auth.ensure_return_policy(
            token,
            days=int(opts.get("return_days") or ebay_auth.DEFAULT_RETURN_DAYS),
            payer=str(opts.get("return_payer")
                      or ebay_auth.DEFAULT_RETURN_PAYER)),
    }
    out: dict = {"policies": {}, "errors": {}}
    save: dict = {}
    for kind, run in steps.items():
        try:
            pol = run()
        except (ebay_auth.AccountApiError, ebay_auth.PolicyLookupUnavailable,
                httpx.HTTPError) as exc:
            # One policy eBay won't make must not cost the other two. The most
            # common reason is the account not being opted in yet, which is a
            # different button on the same screen.
            #
            # PolicyLookupUnavailable belongs here rather than aborting the
            # run: it means this ONE kind could not be checked, and the other
            # two may still be answerable. It is reported per-kind like any
            # other failure, so the seller is told to retry that one instead
            # of silently getting a duplicate.
            detail = getattr(exc, "description", "") or str(exc)
            log.warning("ensure-all-policies: %s failed for uid=%s: %s",
                        kind, _uid(request), detail)
            out["errors"][kind] = detail[:300]
            continue
        out["policies"][kind] = pol
        field = f"{kind}_policy_id"
        if pol.get("id") and not creds.get(field):
            save[field] = pol["id"]
    if save:
        db.save_ebay_account(creds["_uid"], **save)
        # Deliberately NOT note_verified() here. That starts the whole account's
        # EBAY_POLICY_VERIFY_TTL clock, and the clock is the only thing gating
        # _with_current_policies -- which is the only thing that ever repairs a
        # policy id left behind by a previously connected seller. This route
        # establishes provenance only for the slots it FILLED (`save` is
        # populated exactly where the account had no id yet); an id that was
        # already set is left untouched and never compared against the
        # connected account. Starting the clock on that basis would suppress
        # the cross-account repair for a full TTL over ids whose owner was
        # never checked -- and it did so even when two of the three lookups had
        # just failed. One extra verification round-trip is the cheaper side of
        # that trade.
    out["ok"] = not out["errors"]
    out["created"] = sorted(k for k, v in out["policies"].items()
                            if v.get("created"))
    return out


def _preflight_issues(request: Request, listing: Listing, mode: str) -> list[dict]:
    """Run the full pre-publish checklist for this user's account state."""
    return ebay_provider.preflight_issues(_uid(request), listing, mode)


@app.post("/api/publish-preflight")
async def publish_preflight(req: PublishRequest, request: Request) -> dict:
    """The full 'ready to publish?' checklist, without touching the listing.
    The legacy shape ({ok, issues}) carries the eBay checklist; when the
    request targets more marketplaces, their checklists ride along under
    by_marketplace so the editor can jump to marketplace-specific fixes.

    `issues` is empty when the request deselects eBay. Reporting eBay's
    checklist for an Etsy-only publish blocked sellers on fields Etsy never
    wants (package weight, an eBay category) — a publish the /api/publish
    fan-out would have accepted, since it only runs the providers it was
    given.
    """
    targets = [k for k in dict.fromkeys(
        (key or "").strip().lower() for key in req.marketplaces) if k]
    mode = req.mode or "live"
    # No selection at all = the legacy single-eBay client.
    ebay_targeted = not targets or "ebay" in targets
    others = [k for k in targets if k != "ebay"]
    named = [(k, marketplaces.get(k)) for k in others]
    named = [(k, p) for k, p in named if p is not None]
    uid = _uid(request) if named else None
    # Every marketplace's checklist is read-only HTTP against that
    # marketplace, so run them concurrently: preflight costs the slowest
    # checklist rather than the sum of all of them.
    jobs = []
    if ebay_targeted:
        jobs.append(run_in_threadpool(_preflight_issues, request, req.listing, mode))
    jobs += [run_in_threadpool(p.preflight, uid, req.listing, mode)
             for _k, p in named]
    results = await asyncio.gather(*jobs)
    issues = results[0] if ebay_targeted else []
    out = {"ok": not preflight.errors_only(issues), "issues": issues}
    if named:
        by = {k: res for (k, _p), res
              in zip(named, results[1:] if ebay_targeted else results)}
        out["by_marketplace"] = by
        out["ok"] = out["ok"] and not any(
            preflight.errors_only(v) for v in by.values())
    return out


@app.post("/api/ebay/diagnose-block")
def ebay_diagnose_block(req: PublishRequest, request: Request) -> dict:
    """Why is eBay refusing to list? Every question we can ask, answered raw.

    Error 240 names no cause, so a seller whose listings all fail has nothing
    to act on and no way to hand anyone evidence. This runs the whole
    diagnosis on demand — no publish, nothing created:

      * payments-program status and selling privileges (the account answers);
      * a dry run of THIS draft through eBay's Verify call, which returns the
        exact error the real publish would have hit, warnings included;
      * the same dry run with plain wording, which is what separates "your
        account is held" from "eBay dislikes the words in this listing".

    Everything eBay said comes back verbatim under `ebay`, so the answer can
    be read here, quoted to eBay Customer Service, or pasted into a bug report.
    """
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "eBay is not connected for this account.")
    _assert_session_owner(req.session_id, request)
    session_id, listing = req.session_id, req.listing
    out: dict = {"env": config.EBAY_ENV,
                 "ebay_username": creds.get("ebay_username") or "",
                 "checks": {}, "ebay": {}}

    try:
        program = ebay_auth.fetch_payments_program(creds["access_token"])
        out["checks"]["payments_program"] = program
    except Exception as exc:  # noqa: BLE001 - report the failure, don't raise
        out["checks"]["payments_program"] = {"error": _ebay_error_text(exc)}
    out["checks"]["privileges"] = ebay_auth.fetch_privileges(creds["access_token"])
    programs = ebay_auth.opted_in_programs(creds["access_token"])
    out["checks"]["opted_in_programs"] = sorted(programs) if programs else programs

    verify = listing_sync.verifier(
        creds["access_token"],
        ebay.image_urls_for(session_id, listing, _base_url(request)), creds)
    if verify is None:
        out["ebay"]["verify"] = {
            "ran": False,
            "why": ("No ship-from ZIP is saved, and eBay won't check a listing "
                    "without one — set it in Settings → Listing settings."),
        }
        return out
    out["ebay"]["verify"] = _verify_report(verify, listing)
    out["ebay"]["verify_plain_wording"] = _verify_report(
        verify, ebay_account.plain_wording(listing))
    accepted_plain = out["ebay"]["verify_plain_wording"]["accepted"]
    refused_real = not out["ebay"]["verify"]["accepted"]
    out["verdict"] = (
        "eBay accepts this listing as it stands." if not refused_real else
        "eBay refuses this listing but accepts the same one with plain "
        "wording — the words are the cause, not the account."
        if accepted_plain else
        "eBay refuses this listing AND the same listing with plain wording — "
        "the hold is on the account, not on anything in the listing.")
    return out


def _ebay_error_text(exc: Exception) -> str:
    """One line naming what eBay said, HTTP body included where there is one."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        return f"{resp.status_code}: {str(getattr(resp, 'text', ''))[:400]}"
    return f"{type(exc).__name__}: {exc}"[:400]


def _verify_report(verify, listing) -> dict:
    """One dry run, reported without interpretation."""
    try:
        verify(listing)
    except Exception as exc:  # noqa: BLE001 - the answer IS the exception
        return {"ran": True, "accepted": False,
                "title": (listing.title or "")[:80],
                "error_code": str(getattr(exc, "code", "") or ""),
                "message": str(exc)[:600],
                "detail": str(getattr(exc, "detail", "") or "")[:600]}
    return {"ran": True, "accepted": True, "title": (listing.title or "")[:80]}


@app.post("/api/ebay/opt-in-policies")
def ebay_opt_in_policies(request: Request) -> dict:
    """Turn on eBay's business-policy program for the connected account.

    Business policies are a seller PROGRAM, not a default. Until an account is
    opted in, every policy list comes back empty and every policy id is
    rejected — the "my dropdowns are empty and I can't publish" state, with
    nothing on screen naming the cause. The app could read the program's
    status but never set it, so the only fix was a Seller Hub page the seller
    had to be told to find.

    eBay documents the opt-in as taking up to 24 hours and returns no payload,
    so a success here means "eBay has the request", never "your policies are
    ready". The response says which, because promising the second and
    delivering the first is worse than not offering the button.
    """
    creds = _ebay_creds_for(request)
    if not creds or not creds.get("access_token"):
        raise HTTPException(400, "Connect eBay first.")
    token = creds["access_token"]
    already = ebay_auth.opted_in_programs(token)
    if already is not None and ebay_auth.SELLING_POLICY_MANAGEMENT in already:
        return {"ok": True, "already": True, "pending": False,
                "message": "Business policies are already switched on for this "
                           "eBay account."}
    try:
        ebay_auth.opt_in_to_program(token, ebay_auth.SELLING_POLICY_MANAGEMENT)
    except ebay_auth.AccountApiError as exc:
        log.warning("ebay opt-in refused for uid=%s: %s | %s", _uid(request),
                    exc, exc.description)
        raise HTTPException(
            502, "eBay wouldn't switch business policies on for this account. "
                 "You can turn them on directly in eBay: Seller Hub → Account "
                 "→ Business policies.") from exc
    log.info("ebay: business-policy opt-in requested for uid=%s", _uid(request))
    return {"ok": True, "already": False, "pending": True,
            "message": "Asked eBay to switch business policies on. eBay can "
                       "take up to 24 hours, after which your shipping, "
                       "payment and return policies will show up here."}


@app.get("/api/ebay/account-overview")
def ebay_account_overview(request: Request) -> dict:
    """A live mirror of the seller's most-updated eBay account settings —
    business policies (with the current defaults), ship-from locations, opted-in
    programs, and managed-payments status. Best-effort; {connected: false} when
    eBay isn't linked."""
    creds = _ebay_creds_for(request)
    if not creds or not creds.get("access_token"):
        return {"connected": False}
    try:
        ov = ebay_auth.account_overview(creds["access_token"])
    except Exception as exc:  # noqa: BLE001 - never fail the page
        log.warning("account-overview failed: %s", exc)
        ov = {}
    acct = db.get_ebay_account(_uid(request)) or {}
    ov["connected"] = True
    ov["account"] = {
        "username": acct.get("ebay_username", ""),
        "email": acct.get("ebay_email", ""),
        "marketplace": config.EBAY_MARKETPLACE_ID,
    }
    ov["selected"] = {
        "fulfillment_policy_id": creds.get("fulfillment_policy_id", ""),
        "payment_policy_id": creds.get("payment_policy_id", ""),
        "return_policy_id": creds.get("return_policy_id", ""),
        "merchant_location_key": creds.get("merchant_location_key", ""),
    }
    return ov


@app.post("/api/ebay/policies")
def set_ebay_policies(request: Request, payload: dict) -> dict:
    """Save the account's default shipping/payment/return policy selections and
    (optionally) a ship-from ZIP, which we use to create the eBay inventory
    location that publishing requires."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    fields = {
        k: str(payload.get(k) or "")
        for k in ("fulfillment_policy_id", "payment_policy_id", "return_policy_id")
        if k in payload
    }
    postal = str(payload.get("ship_from_postal") or "").strip()
    if "ship_from_postal" in payload and not postal:
        # Clearing it is allowed and has to actually clear. The saved
        # merchantLocationKey stays: publishing needs a ship-from ZIP, and with
        # no typed one create_on_ebay reads it off the seller's eBay location
        # via that key. Dropping both would leave the account unable to publish
        # for having emptied a text box.
        fields["ship_from_postal"] = ""
    elif postal:
        creds = _ebay_creds_for(request)
        if not creds:
            raise HTTPException(400, "Connect eBay first to set a ship-from location.")
        try:
            key = ebay_auth.ensure_inventory_location(creds["access_token"], postal)
        except httpx.HTTPError as exc:
            # Not a 400. A 400 is a claim about what the seller typed, and the
            # UI acts on it: the field goes red and the ZIP is what they will
            # change. A request that never reached eBay says nothing about the
            # ZIP — so it is a 503, and the raw error (which carries the API
            # base, the path and a status line) goes to the log instead.
            raise _lookup_failed("save your ship-from ZIP with eBay",
                                 exc, status=503) from exc
        except Exception as exc:  # noqa: BLE001 - eBay's own refusal
            raise HTTPException(
                400, f"eBay rejected that ship-from location: {exc}") from exc
        fields["merchant_location_key"] = key
        fields["ship_from_postal"] = postal
    if not fields:
        raise HTTPException(400, "No settings provided.")
    db.save_ebay_account(uid, **fields)
    return {"ok": True, "selected": fields}


@app.get("/api/profile")
def get_profile(request: Request) -> dict:
    """The logged-in user's profile + eBay connection summary for Settings."""
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    acct = db.get_ebay_account(user["id"]) or {}
    connected = bool(acct.get("refresh_token"))
    return {
        "user": {"email": user["email"],
                 "display_name": user.get("display_name", "")},
        "ebay": {
            "connected": connected,
            "username": acct.get("ebay_username", "") if connected else "",
            "email": acct.get("ebay_email", "") if connected else "",
            "ship_from_postal": acct.get("ship_from_postal", ""),
            "policies_set": bool(acct.get("fulfillment_policy_id")
                                 and acct.get("payment_policy_id")
                                 and acct.get("return_policy_id")),
            "location_set": bool(acct.get("merchant_location_key")),
        },
    }


@app.post("/api/profile")
def save_profile(request: Request, payload: dict) -> dict:
    """Save profile customizations (currently: display name)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    display_name = str(payload.get("display_name", "")).strip()[:80]
    updated = db.update_user(uid, display_name=display_name)
    if not updated:
        raise HTTPException(503, "Couldn't save your profile — try again shortly.")
    return {"ok": True, "user": {"email": updated["email"],
                                 "display_name": updated["display_name"]}}


# Prefs the client may set, with sane bounds. Everything is optional; empty
# string / 0 means "no default".
_PREF_FIELDS = {
    "package_weight_lb": (float, 0, 150),
    "package_weight_oz": (float, 0, 15.9),
    "package_length_in": (float, 0, 120),
    "package_width_in": (float, 0, 120),
    "package_height_in": (float, 0, 120),
    "quantity": (int, 1, 999),
    "condition": (str, None, None),  # "" = let the AI decide
    # How the AI prices drafts and comp suggestions: "quick_flip" (low end,
    # sell fast), "median" (typical market), "long_sale" (high end, patient).
    "pricing_strategy": (str, None, None),
    # Promote every newly published listing at eBay's recommended ad rate.
    # OFF unless the seller explicitly saved it on: Promoted Listings Standard
    # takes a percentage of the sale, and neither silence nor an unreadable
    # preference is agreement to a fee (see ebay_provider.auto_promote_enabled,
    # which is where that decision is actually made). This comment used to say
    # "Missing = ON", which is what the code did before P1-06 — left here it is
    # an invitation to restore it.
    "auto_promote": (int, 0, 1),
    # Accept Best Offer on every new listing, with no minimum: eBay sends the
    # seller every offer to accept or decline. OFF unless explicitly saved on
    # — see services/listing_sync.offers_enabled, which is where the decision
    # is actually made, and which also explains why it never touches a listing
    # that is already live.
    "allow_offers": (int, 0, 1),
}
_PRICING_STRATEGIES = {"", "quick_flip", "median", "long_sale"}


@app.get("/api/prefs")
def get_prefs(request: Request) -> dict:
    """The user's new-listing defaults (weight/dims/quantity/condition)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    return {"prefs": db.get_prefs(uid)}


@app.post("/api/prefs")
def save_prefs(request: Request, payload: dict) -> dict:
    """Save new-listing defaults. Only known fields are stored, clamped to
    sane ranges; they pre-fill every future AI draft."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    clean: dict = {}
    for key, (typ, lo, hi) in _PREF_FIELDS.items():
        if key not in payload:
            continue
        raw = payload.get(key)
        if typ is str:
            value = str(raw or "").strip()[:40]
            if key == "pricing_strategy" and value not in _PRICING_STRATEGIES:
                continue  # unknown strategy — ignore rather than store garbage
            clean[key] = value
            continue
        try:
            val = typ(float(raw or 0))
        except (TypeError, ValueError):
            val = typ(0) if typ is float else lo
        clean[key] = min(max(val, lo), hi) if val else val
    if not clean:
        raise HTTPException(400, "No settings provided.")
    merged = db.save_prefs(uid, clean)
    # `clean` is non-empty by the check above, so a merge that landed always
    # comes back non-empty: `{}` here means the write did not happen. It used
    # to be reported as `{"ok": true}` unless there was no database AT ALL,
    # which left one case answering success -- the row gone between the
    # session check and this write. A failed write now raises from db.py; this
    # covers the rest.
    if not merged:
        raise HTTPException(
            503,
            "No database configured — defaults need DATABASE_URL set."
            if not db.enabled()
            else "We couldn’t save your defaults just now — nothing was "
                 "changed. Try again in a moment.")
    return {"ok": True, "prefs": merged}


# Moved to marketplaces/ebay_provider.py with the publish pipeline; the local
# names keep the sync/promotion routes below unchanged.
_auto_promote_enabled = ebay_provider.auto_promote_enabled
_promote = ebay_provider.promote


def _load_prefs(uid: Optional[str]) -> dict:
    """The user's saved prefs, or {} — fetched once per logical operation and
    passed into the helpers below, so one identify doesn't pay the same
    Postgres round trip twice (and a bulk batch doesn't pay it per item).

    Best-effort deliberately, and the one place it is right to be: these
    values PRE-FILL a draft the seller is about to edit. Missing them costs
    them a typed weight, where refusing would fail the identify they are
    waiting on. `GET /api/prefs` — the screen that REPORTS what is saved —
    takes the strict read instead.
    """
    if not uid:
        return {}
    return db.get_prefs_best_effort(uid) or {}


def _pricing_strategy(uid: Optional[str], prefs: Optional[dict] = None) -> str:
    """The account's pricing strategy ("" when unset/anonymous). Never raises."""
    if not uid:
        return ""
    try:
        if prefs is None:
            prefs = _load_prefs(uid)
        value = str(prefs.get("pricing_strategy") or "")
        return value if value in _PRICING_STRATEGIES else ""
    except Exception:  # noqa: BLE001 - prefs are optional
        return ""


# How far under the market a fresh draft's own price may sit before the comps
# overrule it. The identify pass prices from the PHOTOS ALONE — it never sees a
# comparable listing — so its number is a guess, and the guess it makes when it
# cannot pin the item down is a low one. That is the expensive direction to be
# wrong in: an underpriced listing sells within the hour at a number nobody can
# take back, and a hand-signed piece drafted at $85 is gone before the seller
# reads the draft.
#
# 0.6 = "less than 60% of what the cheapest quarter of comparable listings
# ask". Deliberately not 1.0: comps are ASKING prices on a keyword match, a
# fair draft often sits under them on purpose (condition, a quick-flip
# strategy), and overruling every such draft would make the number no more
# honest, just higher. This fires on the drafts that are wrong by an order of
# magnitude, not the ones that are merely keen.
UNDERPRICE_RATIO = float(os.getenv("UNDERPRICE_RATIO", "0.6") or 0.6)
# The kill switch for the lookup itself: one eBay Browse call per drafted
# item, which a 50-item bulk batch multiplies. On by default — a draft priced
# without ever asking the market is the bug this exists to close.
DRAFT_PRICE_COMPS = (os.getenv("DRAFT_PRICE_COMPS", "1").strip().lower()
                     not in ("0", "false", "no", "off"))
# How much of the title the fallback comp search keeps when the whole thing
# matched nothing. Five words is the identifying head the title rule puts
# first (brand/artist, model or work, what the thing is) without the specifics
# that make a search return zero.
_COMP_QUERY_WORDS = 5


# --- research: what the item IS, looked up rather than remembered ----------
#
# The identify pass is a memory test over photos. It has now cost this seller
# two items in the expensive direction: a hand-signed Fanch Ledan lithograph
# drafted as a "Fanch Ledan style lithograph" at $85, and a genuine Beatles
# butcher cover called a "replica" that sold for $22 and was worth $7,000+.
# Both are the same failure — the model half-recognized something, hedged, and
# the hedge became the listing.
#
# So a draft that shows any sign of turning on an identification now gets
# looked UP (claude_ai.research_item: Claude with the server-side web search
# tool over the same photos). What comes back is advice under strict rules,
# below: it may name a work, fill a blank, raise a price and raise a question.
# It may never downgrade an item, lower a price, or overwrite something the
# seller can already see is right.
#
# OFF unless asked for. The lookup is a second vision call with up to
# RESEARCH_MAX_SEARCHES web searches inside it, and it runs INSIDE the
# identify request — the seller (or the whole bulk batch) waits on it. The
# gate below catches most of a thrift store ("edition", "rare", "book",
# "record", "glass"...), so the morning it shipped on "auto" every identify
# went from seconds to a minute or more and the app read as broken. Set
# RESEARCH_PASS=auto (or always) to turn it on; the fix that lets it run
# without holding the draft hostage is the one that earns "auto" back.
RESEARCH_PASS = os.getenv("RESEARCH_PASS", "off").strip().lower() or "off"

# Words in a draft that mean an identification decides the price. Any of them
# and the item gets looked up: this is the "is this the expensive one?" list.
_RESEARCH_SIGNALS = (
    "signed", "signature", "autograph", "inscribed", "numbered", "edition",
    "limited", "artist proof", "lithograph", "serigraph", "etching",
    "screenprint", "silkscreen", "giclee", "original painting", "oil on",
    "acrylic on", "watercolor", "sculpture", "provenance", "authenticity",
    "coa", "first edition", "first pressing", "first printing",
    "promo", "misprint", "error", "variant", "rookie", "graded", "psa",
    "hallmark", "sterling", "14k", "18k", "karat", "antique", "rare",
)
# ...and the hedges themselves, which are the model saying "I am not sure"
# in the one place it costs the most.
_RESEARCH_HEDGES = (
    "style of", " style ", "attributed", "manner of", "after ", "replica",
    "reproduction", "repro", "tribute", "homage", "copy of", "knockoff",
    "bootleg", "not original", "unauthorized", "unsigned",
)
# Categories where the same photo can be a $20 item or a $7,000 one.
_RESEARCH_CATEGORIES = (
    "art", "print", "painting", "poster", "sculpture", "collectible",
    "memorabilia", "autograph", "record", "vinyl", "music", "trading card",
    "sports card", "coin", "currency", "stamp", "antique", "comic", "book",
    "jewelry", "watch", "pottery", "glass",
)


def _research_reason(listing: Listing, observations: str = "") -> str:
    """Why this draft needs looking up, or "" when it doesn't.

    Deliberately NOT "is the price high" — the whole problem is that the
    valuable items came back cheap. The gate is "does an identification decide
    what this is worth", which is a property of the item and of how sure the
    draft sounds, never of the number on it.
    """
    if RESEARCH_PASS == "off":
        return ""
    if RESEARCH_PASS == "always":
        return "always"
    haystack = " ".join([
        listing.title or "", listing.brand or "", listing.description or "",
        listing.condition_description or "", observations or "",
        " ".join(f"{s.name} {s.value}" for s in (listing.item_specifics or [])),
    ]).lower()
    for hedge in _RESEARCH_HEDGES:
        if hedge in haystack:
            return f"the draft hedged ({hedge.strip()!r})"
    for signal in _RESEARCH_SIGNALS:
        if signal in haystack:
            return f"attribution decides the price ({signal!r})"
    category = (listing.category_suggestion or "").lower()
    for landmine in _RESEARCH_CATEGORIES:
        if landmine in category:
            return f"category where a variant changes everything ({landmine!r})"
    return ""


def _research_draft(listing: Listing, image_paths: list,
                    observations: str = "", confidence: str = "") -> Optional[dict]:
    """Look a drafted item up and fold the findings in, in place.

    What the research may do:
      * name the item — replace a HEDGED title with the researched one, and
        fill an empty brand;
      * raise the price to the bottom of the researched range (never lower it,
        and never touch a price already above that floor);
      * add what the seller has to check, including the trap this exists for:
        the more valuable variant the photos cannot rule out.

    What it may never do: downgrade an item, lower a price, overwrite a title
    that is already specific, or replace the seller's own words. It is a
    second opinion with a source list, not an appraiser.

    Returns the research dict when something was applied, else None. Never
    raises — a draft is worth more than a lookup.
    """
    reason = _research_reason(listing, observations)
    if not reason:
        return None
    if not config.anthropic_ready():
        return None
    # A first pass that was SURE and hedged nothing has earned its answer;
    # research still runs on everything else, including "high" confidence on a
    # landmine category, because that is where a confident wrong answer costs
    # the most.
    log.info("research: looking up %r — %s (first pass was %s confidence)",
             (listing.title or "")[:60], reason, confidence or "unstated")
    try:
        paths = [p for p in image_paths if p.is_file()]
        found = claude_ai.research_item(paths, listing, observations=observations)
    except Exception as exc:  # noqa: BLE001 - best-effort by contract
        log.info("research failed: %s", exc)
        return None
    if not found:
        return None

    notes: list[str] = []
    conf = str(found.get("confidence") or "low").strip().lower()
    identified = str(found.get("identified") or "").strip()
    researched_title = str(found.get("title") or "").strip()[:TITLE_MAX_CHARS]
    hedged = any(h in (listing.title or "").lower() for h in _RESEARCH_HEDGES)

    # The title. A hedged title is the one thing research is allowed to
    # overwrite outright — "Fanch Ledan style lithograph" is not a title worth
    # protecting. Anything else only gets a suggestion the seller can take.
    if researched_title and conf in ("medium", "high"):
        if hedged:
            log.info("research: title %r -> %r", listing.title, researched_title)
            listing.title = researched_title
        elif researched_title.lower() != (listing.title or "").lower():
            notes.append(f"The lookup suggests this title: “{researched_title}”.")
    maker = str(found.get("maker") or "").strip()
    if maker and not (listing.brand or "").strip() and conf in ("medium", "high"):
        listing.brand = maker[:65]

    # The price, one direction only.
    low = found.get("value_low")
    high = found.get("value_high")
    try:
        low = float(low) if low is not None else None
        high = float(high) if high is not None else None
    except (TypeError, ValueError):
        low = high = None
    if low and low > 0:
        basis = str(found.get("value_basis") or "researched comparables")[:120]
        span = f"${_money(low)}" + (f"-${_money(high)}" if high and high > low else "")
        if listing.price is None or float(listing.price) < low:
            was = listing.price
            listing.price = round(low, 2)
            notes.append(
                f"Price raised to the bottom of what this looks worth ({span}, "
                f"{basis})" + (f" — the photos alone suggested ${_money(was)}."
                               if was is not None else "."))
        else:
            notes.append(f"For reference, the lookup puts this at {span} ({basis}).")

    # The trap. This is the whole point of the pass: the version of this item
    # that is worth 100x, and what would tell it apart.
    trap = str(found.get("high_value_variant") or "").strip()
    if trap:
        notes.append(f"CHECK BEFORE LISTING — {trap}")
    for item in (found.get("verify") or [])[:3]:
        text = str(item or "").strip()
        if text:
            notes.append(f"Verify: {text}")
    if identified and conf == "low" and not trap:
        notes.append(f"The lookup wasn't sure either — its best reading: {identified}")
    sources = [str(u).strip() for u in (found.get("sources") or [])[:3] if u]
    if sources and notes:
        notes.append("Looked up from: " + ", ".join(sources))
    if notes:
        listing.missing_info = [*(listing.missing_info or []), *notes]
    return found if notes else None


def _price_against_comps(listing: Listing, uid: Optional[str] = None,
                         prefs: Optional[dict] = None) -> Optional[dict]:
    """Check a fresh AI draft's price against live eBay comps, in place.

    Two things this fixes, both of them the same bug from opposite ends:

      * the AI returned no price at all (the prompt now asks for null rather
        than a guess when the value turns on an attribution it cannot confirm)
        — the market fills it in; and
      * the AI returned a price far under what comparable listings ask, which
        is what "I could not really identify this" looks like in a number.

    It never LOWERS a draft's price: a high number is the seller's to reduce
    after it fails to sell, and the market only ever gets a vote here on the
    side that cannot be undone. Whatever it changes is said out loud in
    missing_info, so the editor flags it for a human instead of quietly
    swapping one confident-looking number for another.

    Best-effort and silent on failure: no eBay credentials, no comparable
    listings, or a lookup that errors all leave the draft exactly as drafted.
    Returns the comp suggestion it used, or None when nothing changed.
    """
    if not (DRAFT_PRICE_COMPS and config.taxonomy_ready()):
        return None
    title = (listing.title or "").strip()
    if not title:
        return None
    strategy = _pricing_strategy(uid, prefs)

    def _ask(query: str) -> dict:
        try:
            data = pricing.suggest(query, category_id=listing.category_id or None,
                                   condition=listing.condition or None,
                                   strategy=strategy)
        except Exception as exc:  # noqa: BLE001 - a draft beats a comp
            log.info("draft price check skipped for %r: %s", query[:60], exc)
            return {}
        return (data or {}).get("suggestion") or {}

    # A good title is a bad search. The title rule packs it with the words that
    # identify THIS one piece — artist, work, edition, size — and a keyword
    # search for all of them together matches nothing, which is exactly the
    # answer a valuable one-off produces. So a full-title miss falls back to
    # the head of the title, which by that same rule is the maker/artist and
    # the item itself: "Fanch Ledan hand signed lithograph", not "... Interior
    # with Matisse 84/250 framed 24x30".
    best = _ask(title)
    query = title
    if not best.get("price"):
        head = " ".join(title.split()[:_COMP_QUERY_WORDS])
        if head and head != title:
            best, query = _ask(head), head
    market = best.get("price")
    low = best.get("low")
    if not market or market <= 0:
        return None
    was = listing.price
    if was is None:
        note = (f"Confirm the price — the AI wouldn't put a number on this one. "
                f"Comparable eBay listings ask ${_money(low or market)}-"
                f"${_money(best.get('high') or market)}.")
    elif low and float(was) < float(low) * UNDERPRICE_RATIO:
        note = (f"Confirm the price — the photos alone suggested "
                f"${_money(was)}, but comparable eBay listings ask "
                f"${_money(low)}-${_money(best.get('high') or market)}.")
    else:
        return None                       # the draft's own number stands
    listing.price = round(float(market), 2)
    # In front of the seller, not just in the log: this is a number that
    # changed under them, on the field where being wrong is unrecoverable.
    listing.missing_info = [*(listing.missing_info or []), note]
    log.info("draft price: %r %s -> %.2f from %d comps (%s)", query[:60],
             "none" if was is None else f"{float(was):.2f}",
             listing.price, best.get("count") or 0, best.get("basis") or "")
    return best


def _money(value) -> str:
    """A price for a sentence: no decimals when it doesn't need them."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{number:,.0f}" if number == int(number) else f"{number:,.2f}"


def _apply_listing_defaults(listing: Listing, uid: Optional[str],
                            prefs: Optional[dict] = None) -> Listing:
    """Fill gaps in a fresh AI draft from the user's saved defaults — the
    fields the photos can't tell us (package weight/dims, quantity) plus an
    explicit condition override. Never touches a field the AI populated."""
    if not uid:
        return listing
    if prefs is None:
        prefs = _load_prefs(uid)
    if not prefs:
        return listing
    def _f(key):  # noqa: E306
        try:
            return float(prefs.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    if (listing.package_weight_lb or 0) + (listing.package_weight_oz or 0) <= 0 \
            and _f("package_weight_lb") + _f("package_weight_oz") > 0:
        listing.package_weight_lb = _f("package_weight_lb")
        listing.package_weight_oz = _f("package_weight_oz")
    dims = ("package_length_in", "package_width_in", "package_height_in")
    if all((getattr(listing, d) or 0) <= 0 for d in dims) \
            and all(_f(d) > 0 for d in dims):
        for d in dims:
            setattr(listing, d, _f(d))
    if int(prefs.get("quantity") or 0) > 1 and (listing.quantity or 1) <= 1:
        listing.quantity = int(prefs["quantity"])
    # Condition is an explicit "always use this" override (the Settings UI
    # defaults it to 'Let the AI decide' = empty).
    if (prefs.get("condition") or "").strip():
        listing.condition = str(prefs["condition"]).strip()
    return listing


@app.post("/api/profile/sync-ebay")
def sync_profile_from_ebay(request: Request) -> dict:
    """Auto-pull profile info from the connected eBay account: identity
    (username/email), business policies, and inventory location."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    access = creds["access_token"]
    fields: dict = {}
    try:
        ident = ebay_auth.identity_display(ebay_auth.fetch_user_identity(access))
        fields["ebay_username"] = ident["username"]
        fields["ebay_email"] = ident["email"]
        if ident.get("user_id"):
            fields["ebay_user_id"] = ident["user_id"]
    except Exception as exc:  # noqa: BLE001 - identity scope may be missing
        log.info("profile sync: identity fetch failed for %s: %s", uid, exc)
    # Only fill policy/location gaps — never overwrite explicit selections.
    acct = db.get_ebay_account(uid) or {}
    discovered = ebay_auth.fetch_policies_and_location(access)
    for key, val in discovered.items():
        if val and not acct.get(key):
            fields[key] = val
    if fields:
        db.save_ebay_account(uid, **fields)
    # Default the display name to the eBay username if none is set yet.
    user = auth.current_user(request) or {}
    if fields.get("ebay_username") and not user.get("display_name"):
        db.update_user(uid, display_name=fields["ebay_username"])
    return get_profile(request)


@app.post("/api/ebay/disconnect")
def ebay_disconnect(request: Request) -> dict:
    """Unlink the current user's eBay account so they can connect a different
    one (or the correct one, if the wrong account got linked)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    # Keep saved policy/location prefs so reconnecting the same account restores
    # them; a different account overwrites them on connect (see the callback).
    ebay_account.forget_verified(uid)
    db.disconnect_ebay_account(uid)
    return {"ok": True}


def _support_reference() -> str:
    """A short id tying what the seller was told to what the logs recorded.

    Short because someone has to read it out or paste it into an email. It is
    not a secret and identifies nothing on its own — it exists so mapping a
    failure to a product state does not throw the evidence away.

    It now returns the CURRENT REQUEST's id rather than minting a fresh one
    per failure. Same alphabet, same width, and every existing call site is
    unchanged — but the reference a seller quotes now identifies the whole
    request, so it joins to every line that request logged and to its row in
    error_events, instead of to the single line at one failure site.

    Falls back to a fresh value off-request (a background sweep, a startup
    task), where there is no context to belong to.
    """
    return errorlog.current_reference() or errorlog.new_reference()


def _lookup_failed(doing: str, exc: Exception, status: int = 502) -> HTTPException:
    """A marketplace lookup that failed, said in a sentence a seller can use.

    P2-07's rule, applied to the four lookups the editor makes on nearly every
    listing. They handed `raise_for_status()`'s own words straight into a
    toast:

        eBay Taxonomy API error: Client error '401 Unauthorized' for url
        'https://api.ebay.com/commerce/taxonomy/v1/category_tree/0/
        get_category_suggestions?q=vintage+levis' For more information check:
        https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401

    — the deployment's API base, the exact call, the seller's own query and an
    MDN link, in place of anything they could do. It also read identically for
    a rate limit, an expired application token and a network blip, which are
    three different waits.

    The detail is not discarded; it goes to the log under `reference`, which
    comes back in the message so support can join the two. Same shape as the
    payments check and the token checkout above.

    `doing` names the marketplace on purpose. That is not the implementation
    detail this exists to remove — a seller listing on eBay knows the app
    talks to eBay, and knowing whose side the problem is on is most of what
    they wanted. What goes is the URL, the status line and the MDN link.
    """
    reference = _support_reference()
    log.warning("lookup failed (%s) [%s]: %s", doing, reference, exc)
    # The reference rides IN the sentence: lib/api.js reads `detail` as a
    # string, so a structured body renders as "[object Object]".
    return HTTPException(status, (
        f"We couldn't {doing} just now. Try again in a moment — if it keeps "
        f"happening, quote {reference} to support."))


@app.get("/api/ebay/payments-status")
def ebay_payments_status(request: Request) -> dict:
    """Live check of the connected eBay account's payments onboarding.

    Answers "did my bank account link actually work?": eBay reports the
    account as OPTED_IN to the payments program once payout setup is done.

    The answer is a product STATE, not eBay's HTTP response. This used to
    return the deployment's eBay environment, a raw status code and eBay's
    entire response body, and Settings put all three in a toast:

        Couldn't verify payments setup (production): eBay API error: 500
        {"errors":[{"errorId":20403,"domain":"ACCESS", ...}]}

    None of which a seller can act on. Worse, it did not distinguish the three
    answers that lead to three different buttons — wait, finish your bank
    setup, or reconnect the account — and `production` is deployment
    configuration on a route any signed-in seller can call.

    The raw detail is not discarded; it goes to the log under `reference`,
    which comes back to the seller so support can join the two.
    """
    if not _uid(request):
        raise HTTPException(401, "Log in first.")
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "eBay is not connected for this account.")
    try:
        program = ebay_auth.fetch_payments_program(creds["access_token"])
    except Exception as exc:  # noqa: BLE001 - mapped below, never re-raised
        return _payments_failure_state(exc)

    status = str(program.get("status", "")).upper()
    if status == "OPTED_IN":
        return {"state": "ready", "opted_in": True,
                "message": "Payouts are set up on eBay — you can publish live "
                           "listings."}
    return {
        "state": "action_required", "opted_in": False,
        "message": "eBay hasn't finished setting up your payouts. Finish it "
                   "in eBay Seller Hub under Payments — bank verification "
                   "can take a day or two.",
    }


def _payments_failure_state(exc: Exception) -> dict:
    """Map a failed payments check to a state, and log what actually happened.

    The states exist because they lead to different next actions, and telling
    them apart is the whole value: "reconnect eBay" and "finish payout setup"
    are different buttons, and sending a seller to the wrong one costs them a
    support round trip.
    """
    reference = _support_reference()
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", 0) or 0
    log.warning("payments check failed [%s]: status=%s detail=%s",
                reference, status or type(exc).__name__,
                (getattr(resp, "text", "") or str(exc))[:600])

    if status in (401, 403):
        return {"state": "reconnect_required", "opted_in": False,
                "reference": reference,
                "message": "eBay wouldn't accept the connection. Reconnect "
                           "eBay in Settings and check again."}
    if status == 429 or status >= 500 or resp is None:
        # `resp is None` is a transport failure — a timeout or a refused
        # connection — which is the same answer as eBay being down: unknown,
        # try again. It is deliberately NOT reported as a problem with the
        # seller's account, which is what a generic error message implies.
        return {"state": "unavailable", "opted_in": False,
                "reference": reference,
                "message": "We couldn't reach eBay to check your payouts. "
                           "Nothing has changed — try again in a moment."}
    return {"state": "contact_support", "opted_in": False,
            "reference": reference,
            "message": ("eBay gave an answer we don't recognise. Nothing has "
                        f"changed. If it keeps happening, quote {reference} "
                        "to support.")}


# --- eBay marketplace account deletion notifications ------------------------
# eBay requires every *Production* keyset to expose this endpoint (developer
# portal -> Alerts & Notifications). eBay first validates it with a GET
# challenge, then POSTs a notification whenever an eBay user requests account
# deletion; we must ack with a 2xx.

def _deletion_endpoint_url(request: Request) -> str:
    """The endpoint URL eBay hashes: as registered in the portal, no query."""
    if config.EBAY_DELETION_ENDPOINT:
        return config.EBAY_DELETION_ENDPOINT
    return str(request.url.remove_query_params("challenge_code"))


@app.get("/api/ebay/account-deletion")
def ebay_account_deletion_challenge(request: Request, challenge_code: str = "") -> dict:
    """Answer eBay's endpoint-validation challenge.

    eBay calls GET <endpoint>?challenge_code=... and expects
    {"challengeResponse": sha256(challengeCode + verificationToken + endpointUrl)}.
    """
    if not config.EBAY_VERIFICATION_TOKEN:
        raise HTTPException(
            503,
            "EBAY_VERIFICATION_TOKEN is not set. Set it to the same value you "
            "entered on eBay's Alerts & Notifications page.",
        )
    if not challenge_code:
        raise HTTPException(400, "Missing challenge_code query parameter.")
    digest = hashlib.sha256(
        (challenge_code + config.EBAY_VERIFICATION_TOKEN
         + _deletion_endpoint_url(request)).encode("utf-8")
    ).hexdigest()
    return {"challengeResponse": digest}


@app.post("/api/ebay/account-deletion")
async def ebay_account_deletion_notice(request: Request) -> Response:
    """Verify, durably accept, and act on an account-deletion notification.

    This used to parse the body for a log line and return 200 to anything at
    all — no signature check, no use of the userId, and no deletion. That is
    both a compliance failure (eBay requires an app that stores eBay data to
    process these) and a trap: the moment such a handler starts deleting, an
    unauthenticated public URL becomes a remote account-wipe primitive. So
    verification and erasure land together, verification first.

    The order below is the whole design:

      1. Verify the signature over the RAW bytes. 412 if it does not verify —
         eBay's documented code for a failed validation.
      2. Record the notice durably. If that write fails, answer 503: eBay
         resends until it gets a 2xx and stops afterwards, so acknowledging a
         notice we did not record is a promise with nothing behind it.
      3. Only then answer 200, and erase.

    A redelivery of a notice already recorded is acknowledged immediately —
    eBay resends as a matter of routine, and doing the work twice is neither
    needed nor safe.
    """
    raw = await request.body()
    signature = request.headers.get("x-ebay-signature", "")

    try:
        if not ebay_notify.verify(raw, signature):
            log.warning("ebay: rejected an unsigned/invalid account-deletion "
                        "notice from %s", request.client.host
                        if request.client else "?")
            return Response(status_code=412)
    except ebay_notify.KeyUnavailable as exc:
        # eBay's key could not be fetched. That is not a forged notice and
        # must not be refused as one: 503 asks eBay to send it again.
        log.warning("ebay: cannot verify deletion notice right now: %s", exc)
        return Response(status_code=503)

    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001 - signed but unparseable
        log.warning("ebay: signed account-deletion notice was not valid JSON")
        return Response(status_code=400)

    notif_id = ebay_deletion.notification_id_of(payload)
    subject = ebay_deletion.subject_of(payload)
    if not notif_id or not subject:
        # Signed by eBay but missing what identifies it. Refusing is right:
        # acknowledging would retire a notice we cannot act on.
        log.warning("ebay: deletion notice missing notificationId or userId")
        return Response(status_code=400)

    try:
        seen = db.record_deletion_notice(
            notif_id, subject, ebay_deletion.payload_digest(raw))
    except db.StorageUnavailable as exc:
        log.warning("ebay: could not record deletion notice %s: %s",
                    notif_id, exc)
        return Response(status_code=503)

    if seen == "duplicate":
        log.debug("ebay: deletion notice %s already accepted", notif_id)
        return Response(status_code=200)

    # Accepted. The erasure runs after the response so eBay is not held on a
    # multi-listing media purge; the row above is what makes that safe to do
    # out of band.
    def _erase() -> None:
        result = ebay_deletion.purge(subject, purge_media=_purge_session_images)
        db.finish_deletion_notice(notif_id, result["state"],
                                  result.get("error", ""))
        log.info("ebay deletion %s: state=%s users=%d listings=%d",
                 notif_id, result["state"], result["users"], result["listings"])

    _in_background(_erase, what="eBay account-deletion purge")
    return Response(status_code=200)


# Caps raised on request. A high bound stays so a pathological huge upload
# can't OOM the box; per-image dimension downscale (MAX_WORK_SIDE) bounds pixel
# memory regardless.
MAX_UPLOAD_FILES = 40   # per single listing (eBay itself accepts up to 24 live)
MAX_BULK_FILES = 250    # per bulk batch (many items) — the supported batch size
MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # per file
# ...and per BATCH. The per-file cap alone left the product unbounded: 250
# files x 60MB is 15GB of writes that a 1GB volume accepts one file at a time
# until it fills, breaking every other seller's upload on the way. 400MB is
# comfortably above a real batch -- the client re-encodes to 2000px before
# upload, so 250 photos land around 250MB -- and well under the free space the
# volume actually has.
MAX_BULK_BATCH_BYTES = 400 * 1024 * 1024


@app.post("/api/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
    pipeline: str = Form("false"),
    notes: str = Form(""),
) -> dict:
    """Accept images, optimize them, and return a session id.

    remove_bg: when "true", each photo's background is removed and replaced
    with a solid white canvas before the usual optimization pass.

    pipeline: when "true", the request returns as soon as the originals are
    saved — optimization AND the identify chain then run as one background
    job ({session_id, job_id}; poll /api/bulk/status/{job_id}). The seller
    watches per-stage progress instead of a request that blocks through the
    whole photo pass; the old synchronous shape is unchanged when absent.

    notes: the seller's own comma-separated hints about what is in the photos
    ("one vintage ralph lauren polo, two lacoste polos different size color").
    Saved with the session rather than consumed here, so every later re-run of
    the identify chain — Start over most of all — gets the same hints the
    first pass did.
    """
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_UPLOAD_FILES} per listing)")

    run_pipeline = str(pipeline).lower() in ("true", "1", "yes", "on")
    if run_pipeline and not config.anthropic_ready():
        raise HTTPException(
            400, "ANTHROPIC_API_KEY not configured; cannot identify images.")
    strip_bg = str(remove_bg).lower() in ("true", "1", "yes", "on")
    # Uploading + optimizing stays free; the AI background removal toggle is
    # metered per photo. Charged before any disk work so a broke/logged-out
    # caller gets a clean 402/401 instead of a half-done upload.
    spent = await run_in_threadpool(_charge_ai, request, "image_ai", units=len(files)) if strip_bg else None

    session_id = storage.new_session_id()
    orig = storage.original_dir(session_id)
    await run_in_threadpool(storage.save_notes, session_id,
                            listing_prompt.clean_seller_notes(notes))
    try:
        for i, f in enumerate(files):
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                await run_in_threadpool(tokens.refund, spent)
                raise HTTPException(
                    400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
            suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
            # Off the event loop: a 40-photo batch is hundreds of MB of
            # synchronous write() syscalls, and while they run nothing else on
            # the machine is served — including the /media requests eBay itself
            # makes at publish time. (The Pillow pass below was already
            # offloaded for the same reason; this loop was missed.)
            await run_in_threadpool(
                (orig / f"src_{i:03d}{suffix}").write_bytes, data)
    except OSError as exc:
        # Disk full / write failure — same friendly answer as the bulk path,
        # not a raw 500; drop the partial session rather than leaving an orphan.
        # No cutout ever ran, so the background-removal charge goes back too.
        await run_in_threadpool(tokens.refund, spent)
        await run_in_threadpool(storage.purge_session, session_id)
        log.error("upload: disk write failed (%s)", exc)
        raise HTTPException(
            507, "The server is out of storage space — try again shortly.") from exc

    if run_pipeline:
        # Everything slow — the Pillow/cutout pass and the identify chain —
        # runs as one background job; the client polls it with per-stage
        # progress. The identify charge is taken up front like
        # /api/identify-async does, so a broke caller 402s here (with the
        # bg-removal charge given back) instead of after the photo work.
        uid = _uid(request)
        try:
            identify_spent = await run_in_threadpool(_charge_ai, request, "identify")
        except HTTPException:
            await run_in_threadpool(tokens.refund, spent)
            await run_in_threadpool(storage.purge_session, session_id)
            raise
        job_id = storage.new_session_id()
        await run_in_threadpool(_register_bulk_job, job_id, {
            "id": job_id, "kind": "pipeline", "phase": "optimizing",
            "done": False, "error": None, "result": None,
            # The identify charge was taken above, before any work ran, and
            # is only ever refunded in full. Written down so a machine that is
            # killed rather than stopped doesn't take the only record of it
            # with it — see _settle_interrupted_jobs. The background-removal
            # charge deliberately is NOT here: it can be refunded in PART (one
            # photo's worth per failed cutout), and a partial refund is keyed
            # in the ledger by its amount, so settling one from a mirror after
            # the process died holding the running total can pay twice.
            "_refunds": tokens.receipts(identify_spent),
        }, uid=uid)
        threading.Thread(
            target=_run_pipeline_job,
            args=(job_id, session_id, uid, strip_bg, spent, identify_spent),
            daemon=True,
        ).start()
        log.info("pipeline job %s: started (session=%s, photos=%d)",
                 job_id, session_id, len(files))
        return {"session_id": session_id, "job_id": job_id}

    # Pillow work is CPU-bound and the R2 push is blocking I/O; run both off
    # the event loop so photo processing doesn't stall every other request.
    opt_results = await run_in_threadpool(
        images.optimize_all, orig, storage.optimized_dir(session_id), strip_bg)
    optimized = storage.list_optimized(session_id)
    if not optimized:
        await run_in_threadpool(tokens.refund, spent)
        errs = "; ".join(r["error"] for r in opt_results if r.get("error"))
        raise HTTPException(
            400,
            "Could not process the uploaded image(s)"
            + (f": {errs}" if errs else ". Unsupported or corrupt file format."),
        )
    # Photos whose cutout failed (engine down / out of credits) kept their
    # background — give those tokens back.
    bg_failed = sum(1 for r in opt_results if r.get("bg_error") or r.get("error"))
    if spent and bg_failed:
        await run_in_threadpool(tokens.refund, spent, units=bg_failed * tokens.COSTS.get("image_ai", 1))
    # Mirror the optimized images to R2, but don't make the user wait for it:
    # the photos are already on the volume and /media serves the local copy,
    # so nothing on screen or on the way to eBay needs the bucket to have them
    # yet. Anything this misses (a restart mid-push) gets picked up by the
    # reclaim pass, which uploads what the bucket is missing before it frees
    # anything.
    _in_background(objstore.upload_optimized, session_id,
                   storage.optimized_dir(session_id), optimized,
                   what="R2 push (upload)")
    return {
        "session_id": session_id,
        "optimized": optimized,
        "optimize_results": opt_results,
    }


@app.post("/api/upload-more/{session_id}")
async def upload_more(
    session_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
) -> dict:
    """Add more photos to an existing listing. Optimizes each new file into the
    session with non-colliding names and returns the new filenames, so the
    client can append them to the listing's image order."""
    await run_in_threadpool(_assert_session_owner, session_id, request)
    if not files:
        raise HTTPException(400, "No files uploaded")
    existing = storage.list_optimized(session_id)
    if len(existing) + len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"That would exceed {MAX_UPLOAD_FILES} photos on this listing.")
    strip_bg = str(remove_bg).lower() in ("true", "1", "yes", "on")
    # Keep the spend record: every failure path below has to give the tokens
    # back, exactly as /api/upload does ("only pay for AI that worked").
    spent = await run_in_threadpool(_charge_ai, request, "image_ai", units=len(files)) if strip_bg else None

    start = max((storage.image_index(n) for n in existing), default=-1) + 1

    orig = storage.original_dir(session_id)
    opt_dir = storage.optimized_dir(session_id)
    # Save every new file first, so the orientation pass can judge them all in
    # one batched call rather than one call per photo.
    staged: list[tuple[int, Path]] = []
    for j, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            await run_in_threadpool(tokens.refund, spent)
            raise HTTPException(
                400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
        idx = start + j
        suffix = Path(f.filename or f"add_{idx}").suffix or ".jpg"
        src = orig / f"add_{idx:03d}{suffix}"
        try:
            await run_in_threadpool(src.write_bytes, data)
        except OSError as exc:
            await run_in_threadpool(tokens.refund, spent)
            raise HTTPException(
                507, "The server is out of storage space — try again shortly.") from exc
        staged.append((idx, src))
    rotations = await run_in_threadpool(
        orient.detect_rotations, [src for _idx, src in staged])
    # One batched call so added photos share the same worker pool as the main
    # upload path, instead of one serial threadpool round-trip per photo.
    results = await run_in_threadpool(
        images.optimize_batch,
        [(src, opt_dir / f"img_{idx:03d}.jpg", rotations.get(src.name, 0))
         for idx, src in staged],
        strip_bg)
    new_names: list[str] = []
    # Photos whose cutout failed (engine down / out of credits) kept their
    # background, so they owe nothing — counted the same way /api/upload does.
    bg_failed = 0
    for (idx, src), res in zip(staged, results):
        if res.get("error"):  # skip a bad file, keep the rest
            log.warning("upload-more: couldn't process %s: %s",
                        src.name, res["error"])
            bg_failed += 1
            continue
        new_names.append(f"img_{idx:03d}.jpg")
        if res.get("bg_error"):
            bg_failed += 1
    if not new_names:
        await run_in_threadpool(tokens.refund, spent)
        raise HTTPException(400, "Could not process the uploaded image(s).")
    if spent and bg_failed:
        await run_in_threadpool(tokens.refund, spent, units=bg_failed * tokens.COSTS.get("image_ai", 1))
    _in_background(objstore.upload_optimized, session_id, opt_dir, new_names,
                   what="R2 push (upload-more)")
    log.info("upload-more: session=%s added=%d", session_id, len(new_names))
    # optimize_results carries each photo's bg_error, exactly as /api/upload
    # returns it. It was computed here already -- the token refund above counts
    # it -- and then dropped on the floor, so a photo that kept its background
    # reached the seller with nothing said about it. The refund is not the
    # message; it is invisible.
    return {"added": new_names, "optimized": storage.list_optimized(session_id),
            "optimize_results": [
                {"file": f"img_{idx:03d}.jpg", "bg_error": res.get("bg_error")}
                for (idx, _src), res in zip(staged, results)
                if not res.get("error") and res.get("bg_error")]}


@app.post("/api/edit-image")
async def edit_image(
    request: Request,
    session_id: str = Form(...),
    name: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """Overwrite one optimized image with a user-edited version (from the
    in-browser background clean-up tool). Re-encodes through Pillow to a clean
    JPEG so eBay always gets a valid file, and re-pushes to R2 if configured.

    session_id/name are form fields (not URL path segments) so an empty value
    can't fall through to the static handler and surface as an opaque 405.
    """
    session_id = (session_id or "").strip()
    name = (name or "").strip()
    if not session_id or not name:
        log.warning("edit-image: missing session_id=%r or name=%r", session_id, name)
        raise HTTPException(400, "Lost track of which photo to save — reopen the clean-up editor.")
    await run_in_threadpool(_assert_session_owner, session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    # Guard against path traversal in `name`.
    if opt_dir not in path.parents or not await run_in_threadpool(_ensure_local, session_id, name, path):
        log.warning("edit-image: image not found (session=%s name=%s)", session_id, name)
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Edited image too large")

    def _save() -> None:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        # Every edit is a new version — the outgoing working copy is snapshot
        # to history first (never destroyed), then atomically replaced so a
        # concurrent reader (eBay fetching /media, or a thumbnail request)
        # never sees a half-written JPEG.
        storage.snapshot_image(session_id, name)
        tmp = path.with_name(path.name + ".tmp")
        img.save(tmp, "JPEG", quality=88, optimize=True)
        os.replace(tmp, path)

    try:
        await run_in_threadpool(_save)
    except Exception as exc:  # noqa: BLE001
        log.warning("edit-image: could not process (session=%s name=%s): %s", session_id, name, exc)
        raise HTTPException(400, f"Could not process the edited image: {exc}") from exc
    # When R2 is the source eBay fetches from, a failed re-push means the live
    # listing would keep the OLD photo — surface it instead of reporting success.
    if objstore.enabled():
        url = await run_in_threadpool(
            objstore.upload, path, objstore.key_for(session_id, name))
        if not url:
            log.warning("edit-image: R2 re-push failed (session=%s name=%s)", session_id, name)
            raise HTTPException(
                502, "Saved locally, but couldn’t update the stored copy eBay "
                     "uses. Try saving again in a moment.")
    db.touch_listing(session_id)  # bump updated_at so list thumbnails refetch
    log.info("edit-image saved: session=%s name=%s", session_id, name)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------------
# Photo studio: AI-assisted clean-up + smart crop for the in-browser editor.
# All three endpoints accept an optional `file` (the editor's current canvas,
# including unsaved brush strokes); without it they read the saved photo.
# Nothing here writes to disk — the editor previews the result and saves via
# /api/edit-image, so every AI action stays reviewable and cancellable.
# ---------------------------------------------------------------------------

def _studio_guard(request: Request) -> None:
    """Shared entry check for the four photo-studio endpoints. They run the
    local rembg model, which is serialized behind one process-wide inference
    lock, so an unthrottled caller stalls every seller's photo work at once.
    They deliberately are NOT token-metered — the border re-check fires
    automatically after every crop and save, so charging it would bill people
    for ordinary editing — which makes a rate ceiling the only brake."""
    ip = _client_ip(request)
    if not ratelimit.check(f"studio:{ip}", max_attempts=ratelimit.STUDIO_MAX_CALLS):
        log.warning("studio: rate limited %s", ip)
        raise HTTPException(
            429, "Too many photo operations at once. Wait a moment and retry.")


def _studio_load(request: Request, session_id: str, name: str,
                 data: Optional[bytes]):
    from io import BytesIO
    from PIL import Image

    if data:
        img = Image.open(BytesIO(data))
        img.load()
        return img
    session_id = (session_id or "").strip()
    name = (name or "").strip()
    if not session_id or not name:
        raise HTTPException(400, "Lost track of which photo this is — reopen the editor.")
    # Loading BY SESSION reaches a stored photo, so it needs the same
    # ownership check every other session-scoped route makes. Session ids are
    # not secrets — they appear in the public /media URLs handed to eBay — so
    # holding one must not let a caller run the studio against someone else's
    # photos. The inline-`file` path above is the editor's own canvas blob and
    # touches nothing stored, so it stays open to the logged-out flows.
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_path(session_id).resolve()  # read-only: no mkdir
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents or not _ensure_local(session_id, name, path):
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")
    return Image.open(path)


def _data_url(img, fmt: str = "JPEG") -> str:
    import base64
    from io import BytesIO

    buf = BytesIO()
    if fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.save(buf, "JPEG", quality=88, optimize=True)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


@app.post("/api/rotate-image")
async def rotate_image(payload: dict, request: Request) -> dict:
    """Quick-rotate an optimized photo 90° clockwise, in place. Atomic replace
    + R2 re-push, mirroring /api/edit-image, so eBay always fetches the
    rotated copy."""
    session_id = str(payload.get("session_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not session_id or not name:
        raise HTTPException(400, "session_id and name are required")
    await run_in_threadpool(_assert_session_owner, session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents or not await run_in_threadpool(_ensure_local, session_id, name, path):
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")

    def _rotate() -> None:
        from PIL import Image
        storage.snapshot_image(session_id, name)  # rotations version too
        with Image.open(path) as img:
            rotated = img.convert("RGB").transpose(Image.Transpose.ROTATE_270)
        tmp = path.with_name(path.name + ".tmp")
        # No optimize=True here: the two-pass encode nearly doubles the time
        # for a few KB — a one-tap rotate should feel instant. Quality 95
        # rather than the pipeline's 90 because this re-encodes an ALREADY
        # encoded JPEG: four taps round the compass used to mean four
        # generations of loss stacked on a photo the seller only meant to
        # turn upright.
        rotated.save(tmp, "JPEG", quality=95)
        os.replace(tmp, path)

    try:
        await run_in_threadpool(_rotate)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't rotate that photo: {exc}") from exc
    # The R2 push is AWAITED, unlike the bookkeeping below it. This used to be
    # fire-and-forget on a daemon thread with failures only logged, and that is
    # what made manual rotation look unreliable: the local file was rotated and
    # /media served it, so the tile looked right — while R2 still held the old
    # orientation. Then the machine recycles, _ensure_local pulls the photo
    # back down, and the rotation is simply gone. Worse, eBay is handed the R2
    # public URL at publish, so the sideways photo is the one that goes live.
    # A rotate is not done until the copy the world sees is rotated too.
    if objstore.enabled():
        try:
            await run_in_threadpool(
                objstore.upload, path, objstore.key_for(session_id, name))
        except Exception as exc:  # noqa: BLE001 - the local file IS rotated
            log.warning("rotate: R2 push failed for %s/%s: %s",
                        session_id, name, exc)
            raise HTTPException(
                502, "The photo was rotated here but the copy we publish from "
                     "didn't update. Try the rotation again in a moment."
            ) from exc
    _in_background(db.touch_listing, session_id, what="rotate touch")
    return {"ok": True}


@app.post("/api/image/auto-clean")
async def image_auto_clean(
    request: Request,
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """AI clean-up: re-detect the subject and whiten everything outside it.
    Returns the cleaned image for the editor to preview (not saved yet)."""
    _studio_guard(request)
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(request, session_id, name, data)
        return {"ok": True, "image": _data_url(images.auto_clean(img))}

    spent = await run_in_threadpool(_charge_ai, request, "image_ai")
    try:
        return await run_in_threadpool(_run)
    except Exception:
        await run_in_threadpool(tokens.refund, spent)
        raise


@app.post("/api/image/remove-bg")
async def image_remove_bg(
    request: Request,
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Full background removal composited onto pure white — Photoroom by
    default, Adobe Photoshop's Remove Background as the backup, the in-house
    model when neither is configured. Returns the processed image for the
    editor to preview (not saved)."""
    _studio_guard(request)
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(request, session_id, name, data)
        out, engine = images.remove_background_white(img)
        # engine = which remover actually ran — the editor names it so a
        # misconfigured key can't hide behind a silently-degraded result.
        return {"ok": True, "image": _data_url(out), "engine": engine}

    spent = await run_in_threadpool(_charge_ai, request, "image_ai")
    try:
        return await run_in_threadpool(_run)
    except images.CutoutBusy as exc:
        # 503, not 500 and not 422: nothing is wrong with the photo or the
        # server, the one inference slot was occupied. Retry-After makes that
        # machine-readable instead of leaving the client to guess.
        await run_in_threadpool(tokens.refund, spent)
        raise HTTPException(503, str(exc),
                            headers={"Retry-After": "20"}) from exc
    except ValueError as exc:
        # Cutout failure OR an Adobe/Photoroom problem (bad credentials / out
        # of credits / rate limit) — the message tells the user exactly which.
        await run_in_threadpool(tokens.refund, spent)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        await run_in_threadpool(tokens.refund, spent)
        raise


@app.post("/api/image/smart-crop")
async def image_smart_crop(
    request: Request,
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Crop to the detected subject with a clean margin, padded to a square.
    Returns the cropped image for preview, or applied=False if the frame is
    already tight (so the UI can say so instead of degrading the photo)."""
    _studio_guard(request)
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(request, session_id, name, data)
        cropped = images.smart_crop(img)
        if cropped is None:
            return {"ok": True, "applied": False,
                    "message": "Already nicely framed — no crop needed."}
        return {"ok": True, "applied": True, "image": _data_url(cropped)}

    spent = await run_in_threadpool(_charge_ai, request, "image_ai")
    try:
        res = await run_in_threadpool(_run)
    except Exception:
        await run_in_threadpool(tokens.refund, spent)
        raise
    if not res.get("applied"):  # nothing changed — don't charge for a no-op
        await run_in_threadpool(tokens.refund, spent)
    return res


@app.post("/api/identify/{session_id}")
def identify(session_id: str, request: Request) -> dict:
    """Run Claude vision over the optimized images and draft a listing."""
    if not config.anthropic_ready():
        raise HTTPException(
            400, "ANTHROPIC_API_KEY not configured; cannot identify images."
        )
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_dir(session_id)
    names = storage.list_optimized(session_id)
    if not names:
        raise HTTPException(404, "No optimized images found for this session.")
    paths = [opt_dir / n for n in names]
    spent = _charge_ai(request, "identify")
    try:
        result = claude_ai.identify(paths, names,
                                    strategy=_pricing_strategy(_uid(request)),
                                    notes=storage.load_notes(session_id))
    except errors.StorageUnavailable:
        # Refund first, then let it keep its own name: an AI error message
        # sends the seller to retry the model or re-shoot their photos, and
        # neither is the problem. The central handler already says the right
        # thing about storage.
        tokens.refund(spent)
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        tokens.refund(spent)
        code, message = claude_ai.ai_error_message(exc)
        log.warning("identify failed (session=%s): %s", session_id, exc)
        raise HTTPException(code, message) from exc
    _apply_listing_defaults(result.listing, _uid(request))
    _resolve_category(result.listing)
    # Research BEFORE comps: it can rewrite a hedged title into the real one,
    # and the comp search is only as good as the title it searches for.
    _research_draft(result.listing, paths, result.raw_observations,
                    result.confidence)
    # After the category: comps are sharper filtered to it. See
    # _price_against_comps — the photos alone never see a comparable listing.
    _price_against_comps(result.listing, _uid(request))
    storage.save_listing(session_id, result.listing)
    db.upsert_listing(session_id, result.listing.model_dump(), status="draft", user_id=_uid(request))
    return result.model_dump()


@app.post("/api/autofill-specifics/{session_id}")
def autofill_specifics(session_id: str, req: PublishRequest, request: Request) -> dict:
    """Fill eBay's required/recommended item specifics for the listing's
    category from the product photos — choosing fixed-value aspects from eBay's
    own allowed values, and ticking every value that applies on the multi-select
    ("checkbox") ones — and merge them in without overwriting anything the
    seller already set."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    _assert_session_owner(session_id, request)
    listing = req.listing
    if not listing.category_id:
        raise HTTPException(400, "Pick an eBay category first — specifics are per category.")
    if not config.taxonomy_ready():
        raise HTTPException(400, "eBay taxonomy not configured (need EBAY_CLIENT_ID/SECRET).")
    try:
        aspects = taxonomy.item_aspects(listing.category_id).get("aspects", [])
    except Exception as exc:  # noqa: BLE001
        # The same taxonomy call /api/item-aspects makes, so the same answer:
        # httpx's words name the API base, the path and the category id.
        raise _lookup_failed("load eBay's item specifics for that category",
                             exc) from exc
    opt_dir = storage.optimized_dir(session_id)
    names = listing.images or storage.list_optimized(session_id)
    paths = [opt_dir / n for n in names if (opt_dir / n).is_file()]
    if not paths:
        raise HTTPException(400, "This listing's photos aren't on the server anymore.")
    spent = _charge_ai(request, "specifics")
    try:
        filled = claude_ai.fill_aspects(paths, listing, aspects,
                                        tag_text=_tag_text_for(paths, aspects))
    except Exception as exc:  # noqa: BLE001
        tokens.refund(spent)
        code, message = claude_ai.ai_error_message(exc)
        log.warning("autofill-specifics failed (session=%s): %s", session_id, exc)
        raise HTTPException(code, message) from exc
    # Merge: keep the seller's existing non-empty values; add the rest
    # (aspect-aware — MULTI aspects may take several values).
    added = _merge_filled_specifics(listing, filled, aspects)
    storage.save_listing(session_id, listing)
    # _sticky_status, not a second hand-written copy of the rule. This was the
    # one status write that re-implemented it, and it listed only
    # ("published", "ended") -- so autofill silently demoted a "live", "sold"
    # or "unlisted" record to "draft", dropping it out of the Sold and Finds
    # tabs. Autofill also runs by itself when the editor opens
    # (IdentifyResult.specifics_autofilled), so nobody had to click anything.
    db.upsert_listing(session_id, listing.model_dump(),
                      status=_sticky_status(db.get_listing_best_effort(session_id)),
                      user_id=_uid(request))
    log.info("autofill-specifics: session=%s added=%d", session_id, added)
    return {"item_specifics": [s.model_dump() for s in listing.item_specifics], "added": added}


@app.post("/api/enrich/{session_id}")
def enrich_listing(session_id: str, req: PublishRequest, request: Request) -> dict:
    """Fill in everything this ONE listing can still be filled in with, from
    its own photos — the last step before it is published.

    This is "Enrich all" (the dashboard's bulk fill, /api/listings/enrich)
    applied to a single draft, and deliberately on THIS side of the publish.
    There, the same work has to land on a listing eBay is already showing:
    the record has to carry a category eBay agrees with, its photos have to
    still be on the server (an imported listing's live on eBay and have to be
    downloaded first), the seller has to be connected, and the fill then has
    to survive a ReviseItem — every one of which is a way for it to come back
    "skipped" with the blanks still blank. A draft has none of those
    problems: nothing is live yet, the photos are right here, and the answer
    is saved locally.

    What it fills, in one pass:
      * an eBay category, when the draft has none (specifics are per
        category, so nothing else can run until this is settled);
      * every category item specific the photos can answer — required ones
        first — chosen from eBay's own allowed values;
      * the maker/brand, double-checked against the photos.
    Anything the seller already wrote is left exactly as it is: this only
    ever fills blanks.

    Takes the listing in the request body rather than reading the saved copy,
    so edits still open in the editor are enriched (and are not overwritten
    by an older save). Returns the whole listing back for the form to adopt.
    """
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    _assert_session_owner(session_id, request)
    listing = req.listing
    # A missing category is a blocker the seller would otherwise have to go
    # and clear by hand before this could do anything — and the same
    # best-effort resolve a fresh draft gets can usually settle it. Done
    # BEFORE the charge: an enrichment with no category has nothing to fill
    # and must not be billed for finding that out.
    if not listing.category_id:
        _resolve_category(listing)
    if not listing.category_id:
        raise HTTPException(
            400, "Pick an eBay category first — the details eBay asks for "
                 "depend on it.")
    opt_dir = storage.optimized_dir(session_id)
    names = listing.images or storage.list_optimized(session_id)
    paths = [opt_dir / n for n in names if (opt_dir / n).is_file()]
    if not paths:
        raise HTTPException(400, "This listing's photos aren't on the server anymore.")
    spent = _charge_ai(request, "specifics")
    try:
        added = _enrich_listing(listing, paths)
    except Exception as exc:  # noqa: BLE001 - the charge must not outlive it
        tokens.refund(spent)
        code, message = claude_ai.ai_error_message(exc)
        log.warning("enrich failed (session=%s): %s", session_id, exc)
        raise HTTPException(code, message) from exc
    if added is None:
        # Never ran — no taxonomy, no model, or no aspects published for this
        # category. `_enrich_listing` swallows its own failures, so this is
        # the return value rather than an exception, and nothing was earned.
        tokens.refund(spent)
        raise HTTPException(
            400, "The AI couldn't read eBay's details for that category — "
                 "nothing was filled in, and nothing was charged.")
    # Blanks the fill has now answered stop asking. Same rule the bulk enrich
    # applies, so a draft filled here and one filled from the dashboard end
    # up in the same state.
    settled = _drop_answered_missing_info(listing)
    # Saved the way every other save saves. _restore_server_state is not
    # optional bookkeeping here: it keeps the client's copy of the
    # server-owned publish state from erasing the real one, and it marks what
    # changed. A listing that is ALREADY live is revised with only its dirty
    # fields, so specifics filled in here and left unmarked would be saved
    # locally and never reach eBay when the seller presses Update — the exact
    # silence _enrich_one had to mark_dirty around, avoided by going through
    # the same path a save does.
    prev = _restore_server_state(session_id, listing)
    storage.save_listing(session_id, listing)
    db.upsert_listing(session_id, listing.model_dump(),
                      status=_sticky_status(prev), user_id=_uid(request))
    log.info("enrich: session=%s added=%d settled=%d", session_id, added, settled)
    return {"listing": listing.model_dump(), "added": added, "settled": settled}


def _taxonomy_guard(request: Request) -> None:
    """Shared brake on the eBay lookups that need no login.

    Category suggestions, item aspects and price comps all call eBay with the
    APPLICATION token — that is why they need no seller, and why an
    unauthenticated flood spends an allowance shared by every seller. The
    answers are cached, but the cache is bounded, so distinct queries evict it
    and force a live call each.

    ONE budget across all three: they draw on the same eBay allowance, so
    metering them separately would let a caller spend it three times over.
    """
    ip = _client_ip(request)
    if not ratelimit.check(f"taxonomy:{ip}",
                           max_attempts=ratelimit.TAXONOMY_MAX_CALLS):
        log.warning("taxonomy: rate limited %s", ip)
        raise HTTPException(
            429, "Too many lookups at once. Wait a moment and try again.")


@app.post("/api/category-suggestions")
def category_suggestions(payload: dict, request: Request) -> dict:
    """Return ranked eBay category suggestions for a free-text query."""
    _taxonomy_guard(request)
    if not config.taxonomy_ready():
        raise HTTPException(
            400,
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured; cannot resolve "
            "categories. Add them to .env to enable automatic category IDs.",
        )
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(400, "query is required")
    try:
        return taxonomy.suggest(query, limit=int(payload.get("limit", 5)))
    except Exception as exc:  # noqa: BLE001
        raise _lookup_failed("look up eBay's categories", exc) from exc


@app.post("/api/price-suggestions")
def price_suggestions(payload: dict, request: Request) -> dict:
    """Market-price suggestion for the listing from live eBay comps.

    Uses the same application token as taxonomy (no seller login needed).
    Sources are pluggable — see services/pricing.py. The headline suggestion
    honors the account's pricing strategy (Quick Flip / Median / Long Sale).
    """
    _taxonomy_guard(request)
    if not config.taxonomy_ready():
        raise HTTPException(
            400,
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured; cannot look "
            "up market prices.",
        )
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(400, "query is required")
    try:
        return pricing.suggest(
            query,
            category_id=str(payload.get("category_id") or "").strip() or None,
            condition=str(payload.get("condition") or "").strip() or None,
            strategy=_pricing_strategy(_uid(request)),
        )
    except errors.StorageUnavailable:
        # eBay was fine. Relabelling this as an eBay failure sends the seller
        # to look at the wrong thing, and wraps an internal sentence inside a
        # sentence about eBay — the shape P2-07 exists to stop.
        raise
    except Exception as exc:  # noqa: BLE001
        raise _lookup_failed("check eBay for comparable prices", exc) from exc



# A background save/refine/autofill must never DEMOTE a listing's lifecycle
# status: image edits auto-save, bulk publish saves, and flattening
# published/live/ended/sold/unlisted to "draft" made live listings vanish
# from Active, resurrected sold ones as drafts, and pulled Shop-Mode finds
# out of the Finds tab (architect findings #3 and #7). The tuple itself now
# lives in marketplaces/state.py, shared with the per-marketplace state merge.
_STICKY_STATUSES = STICKY_STATUSES


def _sticky_status(rec: Optional[dict], default: str = "draft") -> str:
    """The status to persist on a save/refine: never demote a listing that is
    already live/sold/ended (see _STICKY_STATUSES). Takes the record the
    caller already read — each db.get_listing is a cross-region round trip."""
    cur = (rec or {}).get("status")
    return cur if cur in _STICKY_STATUSES else default


def _restore_server_state(session_id: str, listing: Listing,
                          prev_rec: Optional[dict] = None) -> dict:
    """Replace the client's per-marketplace publish state with the stored one.

    The server owns `marketplaces`, its legacy `ebay_listing_id` mirror, and
    the identity fields in state.SERVER_OWNED_FIELDS: only publish/end/sync
    write them. Any client round-trip can be stale — a
    second browser tab, or the editor's image-edit auto-save whose copy was
    loaded before a publish — and honoring a stale copy ERASES live listing
    ids. The damage is silent and expensive: the next publish finds no
    existing id and creates a DUPLICATE live listing instead of revising, and
    End listing can no longer find the remote item.

    Returns the record it read so callers don't have to re-query.
    """
    rec = prev_rec if prev_rec is not None else (db.get_listing(session_id) or {})
    stored = rec.get("listing") or {}
    states, ebay_id = marketplace_state.owned_state_from(
        stored, listing.ebay_listing_id)
    listing.marketplaces = {k: MarketplaceState(**v) for k, v in states.items()}
    listing.ebay_listing_id = ebay_id
    # `marketplaces` was protected here; `source` was not, and it decides
    # whether the next publish revises the live listing or creates a second
    # one. Same list the publish path restores from.
    changed = marketplace_state.restore_server_fields(listing, stored)
    if changed:
        log.info("save: kept server-owned %s for session=%s",
                 ", ".join(changed), session_id)
    # Record what the SELLER changed, by diffing against the copy just read.
    # A revise sends only these: every other field this app holds is a
    # snapshot of eBay taken at the last sync, and re-sending a snapshot
    # overwrites whatever eBay has now — which may be newer. Done here rather
    # than at each call site because this runs on every save path, and a save
    # that skipped it would leave the seller's edit unsent.
    dirty_fields.accumulate(listing, stored)
    return rec


@app.post("/api/refine")
def refine(req: RefineRequest, request: Request) -> dict:
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    # Authorize before billing: never charge for a request we're about to 404.
    _assert_session_owner(req.session_id, request)
    spent = _charge_ai(request, "refine")
    try:
        updated = claude_ai.refine(req.listing, req.prompt)
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        tokens.refund(spent)
        code, message = claude_ai.ai_error_message(exc)
        log.warning("refine failed (session=%s): %s", req.session_id, exc)
        raise HTTPException(code, message) from exc
    # One read serves both the server-owned state and the sticky status.
    prev = _restore_server_state(req.session_id, updated)
    storage.save_listing(req.session_id, updated)
    db.upsert_listing(req.session_id, updated.model_dump(),
                      status=_sticky_status(prev), user_id=_uid(request))
    return updated.model_dump()


@app.post("/api/save/{session_id}")
def save_listing(session_id: str, listing: Listing, request: Request) -> dict:
    _assert_session_owner(session_id, request)
    prev = _restore_server_state(session_id, listing)
    storage.save_listing(session_id, listing)
    # Checked, like the PATCH route directly below: `db.upsert_listing`
    # swallows its failures, and "saved" is the one word this route says. A
    # seller told their work is saved closes the tab. Called unconditionally
    # and only ENFORCED when there is a database — `db.enabled() and not
    # db.upsert_listing(...)` short-circuits, so without one the write would
    # never run at all.
    landed = db.upsert_listing(session_id, listing.model_dump(),
                               status=_sticky_status(prev), user_id=_uid(request))
    if db.enabled() and not landed:
        raise errors.StorageUnavailable(
            "Couldn't save your changes just now. They're still on screen — "
            "try again in a moment.")
    return {"saved": True}


# What a card-level control may change without opening the editor. Small on
# purpose: a patch route that accepts anything is a full replace with extra
# steps, and the caller can then send every field and reintroduce the very
# lost update this exists to prevent.
_PATCHABLE = ("fulfillment_policy_id", "category_id", "category_suggestion",
              "price", "quantity", "condition")


@app.patch("/api/listings/{session_id}")
def patch_listing(session_id: str, payload: dict, request: Request) -> dict:
    """Change named fields on a listing, leaving the rest as stored.

    `POST /api/save/{id}` is a full REPLACE and has to be — clearing a
    subtitle means sending the listing without one. The problem is what got
    built on top: a card that changes a shipping policy or a category does it
    by spreading the whole listing it happens to be holding, which came from
    the last /api/listings load. A title fixed in the editor in another tab,
    or anything a background sync pulled in since, is overwritten by that
    older copy the moment somebody picks from a dropdown.

    Same reasoning as PATCH .../images/order above, which exists because "a
    reorder could overwrite a title edit made in another tab with a stale
    copy". A patch says what changed and nothing else, so there is no stale
    copy to send.

    The merged listing comes back so the caller can refresh from the answer
    rather than from the copy it already had — which is the copy that was
    stale.
    """
    _assert_session_owner(session_id, request)
    rec = db.get_listing(session_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")

    changes = {k: v for k, v in (payload or {}).items() if k in _PATCHABLE}
    if not changes:
        raise HTTPException(
            400, "Nothing to change. Send one of: "
                 + ", ".join(_PATCHABLE) + ".")

    merged = dict(rec.get("listing") or {})
    merged.update(changes)
    try:
        listing = Listing(**merged)
    except Exception as exc:  # noqa: BLE001 - a bad value is the caller's
        raise HTTPException(400, f"That value isn't valid: {exc}") from exc
    # Marked so the next revise actually carries it: a live listing's shipping
    # policy changed from a card has to reach eBay, and a revise only sends
    # fields the seller is known to have edited.
    listing.mark_dirty(*changes)

    storage.save_listing(session_id, listing)
    data = listing.model_dump()
    if db.enabled() and not db.upsert_listing(
            session_id, data, status=_sticky_status(rec),
            user_id=rec.get("user_id")):
        raise errors.StorageUnavailable(
            "Couldn't save that change just now. Try again in a moment.")
    return {"ok": True, "listing": data}


def _listing_image_order(session_id: str,
                         rec: Optional[dict]) -> Optional[list[str]]:
    """The photo order this listing is SAVED with, or None when it is saved
    nowhere yet.

    The database row first, then the on-disk draft. The disk fallback is not
    belt-and-braces: a database is optional (README: set DATABASE_URL "to
    persist every listing"), and even where one is configured a row can be
    absent because an earlier upsert never landed. With the row as the only
    source, "no row" reads as "this listing has no photos" — which is what
    turned every reorder on such a listing into the 409 below, telling the
    seller their photos had "changed somewhere else" when nothing had
    changed anywhere.
    """
    stored = [str(n) for n in ((rec or {}).get("listing") or {}).get("images") or []]
    if stored:
        return stored
    disk = storage.load_listing(session_id)
    if disk is None:
        return None
    return [str(n) for n in (disk.get("images") or [])]


def _save_image_order(session_id: str, rec: Optional[dict],
                      images: list[str], user_id: Optional[str]) -> list[str]:
    """Persist `images` as the listing's photo order, and nothing else.

    RAISES rather than reporting a save that recorded nothing: the caller's
    client has already moved (or removed) the photo on screen, so a write
    that quietly went nowhere is the seller dragging their main photo into
    place and finding it moved again after a reload.
    """
    def _set_order(data: dict) -> dict:
        data["images"] = list(images)
        return data

    if rec:
        # A row exists, so it is the truth. Written under the row lock, like
        # every other write that shares a listing with publish's background
        # threads — and a failure is reported, not papered over with the disk
        # copy, because the row is what the next page load reads.
        data = db.mutate_listing_data(session_id, _set_order,
                                      status=_sticky_status(rec), user_id=user_id)
        if data is None:
            raise errors.StorageUnavailable(
                "Couldn't update this listing's photos just now — nothing has "
                "changed. Try again in a moment.")
        # Keep the on-disk copy in step; eBay is served photos in this order.
        try:
            storage.save_listing(session_id, Listing(**data))
        except Exception as exc:  # noqa: BLE001 - the DB row is the truth
            log.warning("images: disk mirror not updated for %s: %s",
                        session_id, exc)
        return [str(n) for n in (data.get("images") or images)]

    # No row: the on-disk draft is the listing (see _listing_image_order).
    disk = storage.load_listing(session_id)
    if disk is None:
        raise errors.StorageUnavailable(
            "Couldn't update this listing's photos just now — this listing "
            "isn't saved yet. Try again in a moment.")
    data = _set_order(dict(disk))
    try:
        storage.save_listing(session_id, Listing(**data))
    except Exception as exc:  # noqa: BLE001 - nothing was persisted; say so
        log.warning("images: could not write the order for %s: %s", session_id, exc)
        raise errors.StorageUnavailable(
            "Couldn't update this listing's photos just now — nothing has "
            "changed. Try again in a moment.") from exc
    return [str(n) for n in (data.get("images") or images)]


@app.patch("/api/listings/{session_id}/images/order")
def reorder_images(session_id: str, req: ImageOrderRequest,
                   request: Request) -> dict:
    """Persist the photo order, and nothing else.

    Reordering used to ride on POST /api/save with the WHOLE listing attached,
    which made a drag do two things it should never do: ship every field the
    editor happens to be holding (so a reorder could overwrite a title edit
    made in another tab with a stale copy), and depend on that whole payload
    validating. A dedicated endpoint means a drag persists a list of names.

    The new order has to be a PERMUTATION of the stored one. That is the
    guard that matters: a client working from a stale list would otherwise
    DELETE the photos missing from it, and "my photos vanished when I dragged
    one" is a considerably worse bug than the one being fixed. A mismatch is
    409 — the client refetches rather than forcing its stale view through.

    What the guard compares against is the listing as it is SAVED — row,
    on-disk draft, or (for a session whose draft predates either) the
    optimized files themselves. Comparing against the database row alone made
    the guard fire on listings that had drifted from it for reasons that were
    never the client's doing, and the seller was told their photos had changed
    somewhere else when they had not changed at all.
    """
    _assert_session_owner(session_id, request)
    rec = db.get_listing(session_id) or {}
    stored = _listing_image_order(session_id, rec)
    if not stored:
        # Never saved with a photo list of its own: the files on the volume
        # are the only record of what this session holds.
        stored = storage.list_optimized(session_id)
    wanted = [str(n).strip() for n in req.images if str(n).strip()]
    if sorted(wanted) != sorted(stored):
        log.info("reorder rejected: session=%s sent=%d stored=%d",
                 session_id, len(wanted), len(stored))
        raise HTTPException(
            409, "This listing's photos changed somewhere else — reopen it "
                 "and try the reorder again.")
    if wanted == stored:
        return {"images": stored}

    saved = _save_image_order(session_id, rec, wanted, _uid(request))
    log.info("reorder: session=%s %d photos", session_id, len(saved))
    return {"images": saved}


@app.post("/api/item-aspects")
def item_aspects(payload: dict, request: Request) -> dict:
    """Required + recommended item specifics eBay defines for a category."""
    _taxonomy_guard(request)
    if not config.taxonomy_ready():
        raise HTTPException(400, "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured.")
    cid = str(payload.get("category_id", "")).strip()
    if not cid:
        raise HTTPException(400, "category_id is required")
    try:
        return taxonomy.item_aspects(cid)
    except Exception as exc:  # noqa: BLE001
        raise _lookup_failed("load eBay's item specifics for that category",
                             exc) from exc


@app.post("/api/item-conditions")
def item_conditions(payload: dict, request: Request) -> dict:
    """The conditions eBay allows for a category (prevents publish error 25021).
    Uses the connected seller's token when available, else the app token."""
    if not config.taxonomy_ready():
        raise HTTPException(400, "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured.")
    cid = str(payload.get("category_id", "")).strip()
    if not cid:
        raise HTTPException(400, "category_id is required")
    creds = _ebay_creds_for(request)
    token = creds.get("access_token") if creds else None
    try:
        return {**taxonomy.item_conditions(cid, access_token=token),
                "checked": True}
    except Exception as exc:  # noqa: BLE001 - optional enhancement; fail soft
        # Fail soft, but not silently: an empty list reads as "eBay puts no
        # condition requirement on this category", which is a claim, and the
        # editor would then offer conditions eBay will reject at publish time
        # (error 25021 — the whole reason this lookup exists). `checked` is
        # the same flag the price lookup and the notifications bell use.
        log.info("item-conditions(cat=%s) failed: %s", cid, exc)
        return {"conditions": [], "checked": False}


@app.post("/api/delete-image")
def delete_image(payload: dict, request: Request) -> dict:
    """Remove one optimized image from a session (local disk + R2)."""
    session_id = str(payload.get("session_id", "")).strip()
    name = str(payload.get("name", "")).strip()
    if not session_id or not name:
        raise HTTPException(400, "session_id and name are required")
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents:  # path-traversal guard
        raise HTTPException(400, "Invalid image name")
    # Out of the LISTING first, then off the disk — and if the listing can't
    # be written, nothing is deleted at all.
    #
    # This route used to unlink the file and stop there, which left the photo
    # in the saved listing forever: a reload brought the deleted tile back
    # pointing at bytes that no longer existed, a publish handed eBay a photo
    # that 404s, and — because the editor's list and the stored list could
    # never agree again — every later reorder was refused as a listing that
    # "changed somewhere else". Bulk mode already knew this and followed its
    # delete with a whole-listing save; doing it here means both callers get
    # it, without a stale full-listing payload riding along (the very thing
    # PATCH .../images/order exists to avoid).
    rec = db.get_listing(session_id) or {}
    stored = _listing_image_order(session_id, rec)
    images = None
    if stored is not None:
        images = [n for n in stored if n != name]
        if images != stored:
            images = _save_image_order(session_id, rec, images, _uid(request))
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(500, f"Couldn't delete the image: {exc}") from exc
    # R2 mirror delete is a network round-trip the user shouldn't wait on —
    # the local file (which /media serves first) is already gone.
    if objstore.enabled():
        _in_background(objstore.delete, objstore.key_for(session_id, name),
                       what="delete-image R2")
    log.info("delete-image: session=%s name=%s", session_id, name)
    remaining = storage.list_optimized(session_id)
    # `images` is the listing's own order with the photo gone — what the
    # editor should now be holding, and what the next reorder is checked
    # against. A session with no saved listing has only its files to report.
    return {"ok": True, "images": images if images is not None else remaining,
            "remaining": remaining}


# ---------- Bulk mode: one photo dump -> many listings ----------
# Claude vision accepts at most 100 images per request; bigger piles (bulk
# takes up to MAX_BULK_FILES photos) are grouped in chunks of this size.
BULK_GROUP_CHUNK = 100

# Job status lives in services/jobstore: in memory, mirrored to disk so a
# machine that goes away mid-batch (an OOM-kill during background removal, a
# deploy) leaves a record saying so, instead of a status id that 404s forever
# while the client sits on a progress bar. Aliased under the names the workers
# below have always used.
_register_bulk_job = jobstore.register
_bulk_set = jobstore.update


class _BatchStopped(Exception):
    """The seller stopped this batch (POST /api/bulk/cancel/{job_id})."""


def _stop_if_cancelled(job_id: str) -> None:
    """Leave the batch if it has been called off. Placed only between whole
    units of work — a photo, an item — because stopping inside one would throw
    away AI the seller has already been charged for."""
    if jobstore.cancel_requested(job_id):
        raise _BatchStopped()


# How many times one batch may be picked up again. A batch that dies, resumes,
# and dies again is not unlucky — it is hitting something it will keep hitting
# (a photo that OOMs the box, most likely), and a machine that restarts into
# the same doomed batch forever never gets to serve anyone else.
BULK_MAX_RESUMES = int(os.getenv("BULK_MAX_RESUMES", "2") or 2)
# The phases a batch can be picked up from by simply running it again. All of
# them are BEFORE the first draft is written, which is what makes that safe:
# re-running them creates nothing, so there is nothing to duplicate and no
# second charge to make.
#
# "identifying" is resumable too, but not by re-running: each finished item
# is already saved as a draft and its AI already billed, so the batch writes
# its plan down as it goes (the grouping, each item as it lands, the one in
# flight) and a restart continues from the first item without a draft. See
# _drafting_plan. A mirror from before the plan was written down — no
# grouping to continue from — keeps the honest "run the rest again" message.
_RESUMABLE_PHASES = ("uploading", "optimizing", "grouping")
_DRAFTING_PHASE = "identifying"


def _compact_item(item: dict) -> dict:
    """The part of a drafted item worth mirroring: enough to rebuild the
    queue's row from disk after a restart, and nothing that is already in
    the item's own listing.json."""
    return {"session_id": item.get("session_id"), "name": item.get("name"),
            "status": item.get("status"), "error": item.get("error"),
            "title": item.get("title")}


def _bulk_items_from_disk(done: list[dict]) -> list[dict]:
    """The queue rows for items finished before a restart, rebuilt from their
    saved listings. The same shape _run_bulk_job produces, so the poller
    cannot tell a resumed batch from one that never stopped."""
    items: list[dict] = []
    for rec in done:
        sid = str(rec.get("session_id") or "")
        listing = storage.load_listing(sid) if sid else None
        photos = (listing or {}).get("images") or []
        items.append({
            "session_id": sid, "name": rec.get("name") or "",
            "status": rec.get("status") or "draft", "error": rec.get("error"),
            "listing_id": None,
            "thumb": f"/media/{sid}/optimized/{photos[0]}" if photos else "",
            "listing": listing,
            "title": (rec.get("title") or (listing or {}).get("title")
                      or rec.get("name") or ""),
        })
    return items


def _drafting_plan(record: dict, staging: str) -> Optional[dict]:
    """What a batch interrupted while drafting needs to carry on, or None.

    None means the honest "run the rest again" message stands: no plan was
    written (a mirror from before this existed), or the pile the remaining
    items' photos are copied from is gone — swept, or on a volume that did
    not come back. list_optimized is read-only on purpose: asking must never
    re-create the tree the orphan sweep just removed.

    The item in flight is the one judgement call. Its draft can land and the
    process die before the job ticks, in which case its listing.json is on
    disk and it is finished (and billed) — drafting it again would duplicate
    both. With photos in its session but no draft, it is finished IN that
    session, without a second charge: the first one bought nothing, and the
    receipt died with the process. With neither, it never really started and
    is drafted like any other remaining item.
    """
    names = [n for n in (record.get("_names") or []) if isinstance(n, str)]
    groups = [g for g in (record.get("_groups") or [])
              if isinstance(g, dict) and isinstance(g.get("indices"), list)]
    if not names or not groups:
        return None
    if not storage.list_optimized(staging):
        return None
    done = [dict(d) for d in (record.get("_done") or [])
            if isinstance(d, dict) and d.get("session_id")]
    if len(done) > len(groups):
        return None
    inflight = record.get("_inflight") or None
    if inflight:
        listing = storage.load_listing(inflight)
        if listing is not None and len(done) < len(groups):
            group = groups[len(done)]
            done.append({"session_id": inflight, "name": group.get("name") or "",
                         "status": "draft", "error": None,
                         "title": listing.get("title") or group.get("name") or ""})
            inflight = None
        elif listing is not None or not storage.list_optimized(inflight):
            inflight = None
    return {"names": names, "groups": groups, "done": done,
            "inflight": inflight, "items": _bulk_items_from_disk(done)}


def _settle_interrupted_jobs(records: list[dict]) -> None:
    """Deal with the jobs whose process went away: finish them, or pay them back.

    Two outcomes, and they are mutually exclusive on purpose. A photo batch
    that can be picked up again is picked up, and its charge stands because
    the work is about to be delivered. Everything else is settled in money:
    the AI charges these jobs took UP FRONT bought nothing, and the receipts
    for them died with the process that held them, so nobody was ever going to
    give them back. That is a seller paying twice for one listing.

    Refunding is safe to attempt on every boot, including for a job already
    refunded on the last one: a full refund is keyed in the ledger by the
    spend's own entry id, so the database rejects the second one instead of
    paying twice (see tokens.refund_all).

    What this does NOT settle is the per-photo background-removal charge —
    see tokens.receipts for why a partially refundable charge cannot be
    settled from a mirror. For a batch that is the smaller number anyway, and
    resuming means it usually gets delivered rather than abandoned.
    """
    resumed = _resume_interrupted_batches(records)
    for record in records:
        if record.get("id") in resumed:
            continue  # the work is being finished, so the charge was earned
        given_back = tokens.refund_all(record.get("_refunds"))
        if given_back:
            log.info("job %s: refunded %d up-front AI charge(s) — the restart "
                     "killed it before it delivered", record.get("id"), given_back)


def _resume_interrupted_batches(records: list[dict]) -> set[str]:
    """Restart photo batches whose process died before they drafted anything,
    and return the ids of the ones actually picked up.

    A bulk batch does all of its photo work up front and only then starts
    drafting, so a machine that goes away during background removal — a
    deploy, an OOM, a platform replacing the VM — used to take the whole batch
    with it: every cutout it had finished was thrown away and the seller was
    told to run it again from scratch. The optimized photos were on the volume
    the entire time. This picks the batch back up and optimize_all skips
    straight past what is already done.
    """
    resumed: set[str] = set()
    for record in records:
        job_id, staging = record.get("id"), record.get("_staging_id")
        # kind is absent on photo batches; pipeline/identify/ebay-import jobs
        # each have their own worker and their own resume story.
        if record.get("kind") or not job_id or not staging:
            continue
        phase = record.get("phase")
        drafting = phase == _DRAFTING_PHASE
        if phase not in _RESUMABLE_PHASES and not drafting:
            continue
        if record.get("_cancel"):
            # Stopped on purpose. The restart is not a second chance to run
            # work the seller has already said they don't want.
            continue
        resumes = int(record.get("_resumes") or 0)
        if resumes >= BULK_MAX_RESUMES:
            log.warning("bulk %s: interrupted %d time(s) — not picking it up "
                        "again", job_id, resumes)
            continue
        uid = record.get("_uid")
        strip_bg = bool(record.get("_strip_bg"))
        if drafting:
            plan = _drafting_plan(record, staging)
            if plan is None:
                continue
            _register_bulk_job(job_id, {
                "id": job_id, "phase": _DRAFTING_PHASE, "done": False,
                "error": None, "items": plan["items"],
                "total_items": len(plan["groups"]),
                "current": min(len(plan["done"]) + 1, len(plan["groups"])),
                "total_photos": record.get("total_photos") or len(plan["names"]),
                "resumed": True, "remove_bg": strip_bg,
                "_staging_id": staging, "_strip_bg": strip_bg,
                "_resumes": resumes + 1,
                "_names": plan["names"], "_groups": plan["groups"],
                "_done": plan["done"], "_inflight": plan["inflight"],
            }, uid=uid)
            threading.Thread(
                target=_run_bulk_job, args=(job_id, staging, strip_bg, uid),
                kwargs={"resumed": True, "resume_from": plan}, daemon=True,
            ).start()
            resumed.add(job_id)
            log.info("bulk %s: resuming drafting after a restart (attempt %d, "
                     "%d of %d items already saved)", job_id, resumes + 1,
                     len(plan["done"]), len(plan["groups"]))
            continue
        # session_dir, NOT storage.original_dir: that one calls ensure_session
        # and would CREATE the tree it is being asked about — re-making the
        # very directories the orphan sweep just removed, and reporting a
        # swept batch as resumable every time.
        originals = storage.session_dir(staging) / "original"
        if not originals.is_dir() or not any(originals.iterdir()):
            # Swept, or the volume it lived on is gone. Nothing to resume from,
            # and re-running would just optimize an empty directory.
            continue
        _register_bulk_job(job_id, {
            "id": job_id, "phase": "optimizing", "done": False,
            "error": None, "items": [], "total_items": 0, "current": 0,
            "total_photos": record.get("total_photos") or 0,
            "resumed": True,
            # Public: the queue's progress text says what is actually running.
            "remove_bg": strip_bg,
            "_staging_id": staging, "_strip_bg": strip_bg,
            "_resumes": resumes + 1,
        }, uid=uid)
        threading.Thread(
            target=_run_bulk_job, args=(job_id, staging, strip_bg, uid),
            kwargs={"resumed": True}, daemon=True,
        ).start()
        resumed.add(job_id)
        log.info("bulk %s: resuming after a restart (attempt %d, phase was %s)",
                 job_id, resumes + 1, record.get("phase"))
    return resumed


def _adopt_job_mirrors() -> None:
    """Re-adopt mirrored jobs before the first request is served, so a client
    that polls straight through a restart is told its batch was interrupted
    rather than being handed a 404 for a job that once existed — then pick
    back up the batches that can still be finished, and refund the charges of
    the ones that cannot."""
    interrupted = jobstore.adopt_mirrors()
    # Off the startup path: resuming re-enters the photo pipeline, and uvicorn
    # must be answering its health check long before that finishes.
    threading.Thread(target=_settle_interrupted_jobs,
                     args=(interrupted,), daemon=True).start()


def _run_bulk_job(job_id: str, staging_id: str, strip_bg: bool,
                  uid: Optional[str], resumed: bool = False,
                  resume_from: Optional[dict] = None) -> None:
    """Background worker: optimize -> group -> per-item identify. Every item
    lands as a draft for review; publishing is always an explicit choice.

    `resumed` marks a batch being picked back up after the machine went away
    mid-run (see _resume_interrupted_batches). The only thing it changes is
    billing: the background-removal tokens were already taken on the first
    attempt, so this run must not charge for them a second time. It also can't
    refund them — the receipt that made a refund possible died with the
    process — but the work is being finished rather than abandoned, which is
    what that charge was for. Everything else re-runs as normal, because a
    resume of the photo phases restarts nothing that had drafted anything.

    `resume_from` is a batch picked up while DRAFTING (see _drafting_plan):
    the grouping and photo order it was working from, the items already
    saved, and the one in flight. The photo pass and the grouping are skipped
    — the pile is still on the volume, the plan says how it was split — and
    drafting continues from the first item without a draft. The in-flight
    item is finished in the session that already holds its photos, without
    being charged again."""
    prefs = _load_prefs(uid)                   # one DB read for the whole batch
    strategy = _pricing_strategy(uid, prefs)
    auto_promote = _auto_promote_enabled(uid)  # ditto
    billing = tokens.enabled() and uid is not None
    # The seller's hints for this pile, saved with the staging session by the
    # endpoint. Read from disk rather than passed in so a batch RESUMED after
    # a restart is grouped with the same hints as the first attempt — the
    # resume re-runs grouping, and grouping is where they matter most.
    notes = storage.load_notes(staging_id)
    # Background-removal billing, tracked across the whole job: what was
    # debited up front, and how much has already gone back. A batch that never
    # reaches the end returns the whole un-refunded remainder (see finally) —
    # the abort case is the common one, since the grouping call raises on any
    # Anthropic 429/overload, and it used to keep the entire charge for zero
    # delivered work. db.token_refund does not remember earlier partial
    # refunds, so the running total has to be kept here.
    bg_spent = None
    bg_charged = 0
    bg_refunded = 0
    n_photos = 0
    delivered = False
    # Declared out here so a batch the seller stops can still report the items
    # it had drafted by then — they are saved listings, not a partial result.
    items: list[dict] = []
    # Where drafting starts, and the item a restart cut off mid-draft. A fresh
    # batch starts at the first item with nothing in flight.
    start, inflight = 0, None
    try:
        if resume_from is not None:
            # Picked back up mid-draft. The pile is still on the volume and
            # the plan says how it was split, so neither the photo pass nor
            # the grouping runs again; what is left is the drafting.
            names = list(resume_from["names"])
            groups = list(resume_from["groups"])
            done = list(resume_from.get("done") or [])
            inflight = resume_from.get("inflight") or None
            opt_dir = storage.optimized_dir(staging_id)
            items = _bulk_items_from_disk(done)
            start = len(done)
            _bulk_set(job_id, total_items=len(groups), total_photos=len(names),
                      items=list(items), _names=names, _groups=groups,
                      _done=[_compact_item(it) for it in items],
                      _inflight=inflight)
        else:
            # Bulk background removal is metered per photo, charged before the
            # engines run. Not enough tokens -> photos are kept as-is (with a
            # visible reason) rather than failing the whole batch.
            if strip_bg and billing and not resumed:
                n_photos = sum(1 for p in storage.original_dir(staging_id).iterdir()
                               if p.is_file())
                bg_spent = tokens.spend(uid, "image_ai", units=n_photos)
                if bg_spent is not None and not bg_spent.get("ok"):
                    strip_bg = False
                    _bulk_set(job_id, bg_error=(
                        "Not enough tokens for background removal — photos were "
                        "kept as shot. " + tokens.insufficient_message(bg_spent)))
                    bg_spent = None
                elif bg_spent is not None:
                    # What was actually debited, so the settlement below can give
                    # back exactly the unused remainder. db.token_refund does not
                    # remember earlier partial refunds — a partial refund followed
                    # by a full one would pay the user twice — so the accounting
                    # has to live here.
                    bg_charged = tokens.cost("image_ai", n_photos)
            _stop_if_cancelled(job_id)
            _bulk_set(job_id, phase="optimizing", current=0)
            opt_results = images.optimize_all(
                storage.original_dir(staging_id), storage.optimized_dir(staging_id),
                strip_bg,
                progress=lambda done, total: _bulk_set(job_id, current=done,
                                                       total_photos=total),
                # The photo pass is where a long batch spends its time, so the stop
                # has to reach inside it rather than waiting for the whole pile.
                should_stop=lambda: jobstore.cancel_requested(job_id))
            # Surface a background-removal failure (out of credits, bad key, rate
            # limit) on the job so the UI can say WHY the photos came back with
            # their backgrounds intact — silence here reads as "the feature is
            # broken" when the photo was deliberately kept unchanged.
            # A photo that failed to process AT ALL (corrupt/truncated file, a
            # decoder that gave up) got no cutout either and owes nothing — count
            # it the same way /api/upload does. Photos the batch never even looked
            # at (a .mov in the pile: optimize_all filters by extension) never
            # reach opt_results, and the settlement below returns those.
            bg_failed = [r for r in opt_results
                         if r.get("bg_error") or r.get("error")]
            # Files the charge counted but optimize_all never looked at — it takes
            # only known image extensions, while the charge counts everything in
            # the staging dir, so a video or PDF in the pile was billed and could
            # not even fail visibly.
            unprocessed = max(0, n_photos - len(opt_results))
            owed_now = (len(bg_failed) + unprocessed) * tokens.COSTS.get("image_ai", 1)
            if bg_failed:
                reason = next((r["bg_error"] for r in bg_failed if r.get("bg_error")), None)
                _bulk_set(job_id, bg_failed=len(bg_failed),
                          **({"bg_error": reason} if reason else {}))
            if owed_now:
                # Photos that kept their background weren't the AI they paid for.
                #
                # Counted only if it actually LANDED. `bg_refunded` is subtracted
                # from the final settlement below, so counting a refund that did
                # not commit would shrink the remainder by money the seller never
                # got. A failure here is also queued for a later pass (see
                # services/owed_refunds), and db.token_refund caps every refund at
                # what the spend has left — so the two paths converge on the right
                # total and neither can over-pay.
                if tokens.refund(bg_spent, units=owed_now):
                    bg_refunded += owed_now
            names = storage.list_optimized(staging_id)
            if not names:
                _bulk_set(job_id, done=True, error="No usable photos in the upload.")
                return
            opt_dir = storage.optimized_dir(staging_id)

            _bulk_set(job_id, phase="grouping", total_photos=len(names), current=0)
            # One unreadable photo must not sink the whole batch (it did: a
            # truncated upload failed the job right here) — drop it and group
            # the rest.
            thumbs, readable = [], []
            for n in names:
                try:
                    thumbs.append(images.thumb_jpeg(opt_dir / n))
                    readable.append(n)
                except Exception as exc:  # noqa: BLE001 - skip one bad photo
                    log.warning("bulk %s: skipping unreadable photo %s: %s",
                                job_id, n, exc)
            names = readable
            if not names:
                _bulk_set(job_id, done=True, error="No usable photos in the upload.")
                return
            # Group in API-sized chunks. Resellers shoot item-by-item, so photos of
            # the same item land in the same chunk except right at a boundary —
            # worst case a boundary item shows up as two entries to merge by hand.
            groups: list[dict] = []
            for base in range(0, len(thumbs), BULK_GROUP_CHUNK):
                _stop_if_cancelled(job_id)
                part = claude_ai.group_photos(
                    thumbs[base:base + BULK_GROUP_CHUNK], notes=notes)["groups"]
                groups.extend({"name": g["name"],
                               "indices": [base + i for i in g["indices"]]}
                              for g in part)
            # The plan, written down before the first draft: this is what lets a
            # restart continue from the next item instead of ending the batch.
            _bulk_set(job_id, total_items=len(groups), _names=names, _groups=groups,
                      _done=[], _inflight=None)

        for gi in range(start, len(groups)):
            group = groups[gi]
            # Between items, never inside one: an item that has started has
            # already been charged for, so it is finished and saved.
            _stop_if_cancelled(job_id)
            # Every finished item written down as it lands, so a restart here
            # continues from the next one rather than drafting these again.
            _bulk_set(job_id, phase="identifying", current=gi + 1,
                      items=list(items),
                      _done=[_compact_item(it) for it in items], _inflight=None)
            # The item a restart cut off already has its photos in a session
            # of its own, and its AI was charged for. Finish it there, and
            # don't charge twice for what the first attempt never delivered.
            charged = bool(inflight) and gi == start
            if charged:
                sid = inflight
                item_dir = storage.optimized_dir(sid)
                item_names = storage.list_optimized(sid)
            else:
                sid = storage.new_session_id()
                item_dir = storage.optimized_dir(sid)
                item_names = []
                for j, idx in enumerate(group["indices"]):
                    src = opt_dir / names[idx]
                    dst_name = f"img_{j:03d}.jpg"
                    shutil.copyfile(src, item_dir / dst_name)
                    item_names.append(dst_name)
                objstore.upload_optimized(sid, item_dir, item_names)
                # Each item inherits the WHOLE pile's hints, not just the line
                # it was grouped under: the split is the model's guess, and the
                # identify prompt already tells it to use only the lines
                # matching the photos in front of it. The staging session
                # (where they live now) is purged when this batch ends, so
                # without the copy a "Start over" on any of these drafts loses
                # them. An item picked back up after a restart already has its
                # copy from the first attempt.
                storage.save_notes(sid, notes)

            item = {"session_id": sid, "name": group["name"], "status": "draft",
                    "error": None, "listing_id": None,
                    "thumb": f"/media/{sid}/optimized/{item_names[0]}"}
            # Each bulk item is one AI draft — same token price as a single
            # listing. Out of tokens mid-batch: the item keeps its photos as a
            # stub draft (retryable via "Start over" after a top-up), no AI
            # spend happens for it, and the batch keeps going so the count of
            # what's left is honest.
            # Named BEFORE the charge, once its photos are in place: a restart
            # between here and the draft landing finishes this item in this
            # session, charged once.
            _bulk_set(job_id, _inflight=sid)
            spent = (tokens.spend(uid, "identify")
                     if billing and not charged else None)
            if spent is not None and not spent.get("ok"):
                stub = Listing(images=item_names, missing_info=[
                    "Out of AI tokens when this item's turn came — your photos "
                    "are safe. Top up (or wait for the monthly reset), then "
                    "use Start over to run the AI."])
                storage.save_listing(sid, stub)
                db.upsert_listing(sid, stub.model_dump(), status="draft", user_id=uid)
                item.update({"status": "error", "listing": stub.model_dump(),
                             "title": group["name"],
                             "error": tokens.insufficient_message(spent)})
                _bulk_set(job_id, tokens_exhausted=True)
                items.append(item)
                continue
            try:
                result = claude_ai.identify([item_dir / n for n in item_names],
                                            item_names, strategy=strategy,
                                            notes=notes)
                listing = _apply_listing_defaults(result.listing, uid, prefs)
                # Carry the account's Promote default onto the draft itself, so
                # the queue card shows what will actually happen at publish
                # rather than an unchecked box that promotes anyway.
                listing.promote = listing.promote or auto_promote
                _resolve_category(listing)
                # Fill item specifics (and the maker) up front so the draft the
                # seller reviews carries real specifics, not just the generic
                # first pass — one consolidated call on chain v2.
                _enrich_listing(listing, [item_dir / n for n in item_names],
                                tags=result.tags)
                _research_draft(listing, [item_dir / n for n in item_names],
                                result.raw_observations, result.confidence)
                _price_against_comps(listing, uid, prefs)
                storage.save_listing(sid, listing)
                db.upsert_listing(sid, listing.model_dump(), status="draft", user_id=uid)
                item["listing"] = listing.model_dump()
                item["title"] = listing.title
            except Exception as exc:  # noqa: BLE001 - one bad item shouldn't kill the batch
                tokens.refund(spent)
                log.warning("bulk %s: item %d failed: %s", job_id, gi, exc)
                item["status"] = "error"
                item["error"] = str(exc)
                item["listing"] = None
                item["title"] = group["name"]
            items.append(item)

        delivered = True
        _bulk_set(job_id, phase="done", done=True, items=items, current=len(groups),
                  _done=[_compact_item(it) for it in items], _inflight=None)
        log.info("bulk %s: %d photos -> %d items", job_id, len(names), len(items))
    except (_BatchStopped, images.Stopped):
        # The seller stopped this batch. Every item it had already drafted was
        # saved as it finished, so this reports them rather than throwing them
        # away; the rest never ran, so it never charged. The job is already
        # marked done by the request itself (see jobstore.request_cancel) —
        # this is the worker agreeing, and handing over what it got to.
        #
        # `delivered` here is about the background-removal charge, which was
        # taken per photo up front: cutouts that rode out on a saved draft were
        # delivered and stand, and a stop before the first draft delivered
        # nothing, so the `finally` below gives the whole remainder back.
        delivered = bool(items)
        _bulk_set(job_id, phase="stopped", done=True, cancelled=True,
                  items=items, current=len(items))
        log.info("bulk %s: stopped by the seller after %d item(s)",
                 job_id, len(items))
    except OSError as exc:  # disk-level failure — reclaim, then say so plainly
        if exc.errno == errno.ENOSPC:
            freed = reclaim_space(aggressive=True)
            log.warning("bulk %s hit a full volume; reclaimed %.1f MB",
                        job_id, freed / 1e6)
            _bulk_set(job_id, done=True, error=(
                "The server ran out of photo storage mid-batch. Space has been "
                "reclaimed automatically — please run this batch again."))
        else:
            log.warning("bulk %s failed: %s", job_id, exc)
            _bulk_set(job_id, done=True, error=f"Bulk processing failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - job-level failure
        log.warning("bulk %s failed: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=f"Bulk processing failed: {exc}")
    finally:
        # Give back whatever the batch charged for and never delivered. On an
        # abort that is the whole un-refunded remainder; on a completed batch
        # it is the photos the pile contained but optimize_all never processed
        # (a video or PDF among the photos), which were charged and can't fail
        # visibly because they never appear in the results at all.
        if bg_spent is not None and not delivered:
            owed = bg_charged - bg_refunded
            if owed > 0:
                tokens.refund(bg_spent, units=owed)
                log.info("bulk %s: refunded %d unused background-removal token(s)",
                         job_id, owed)
        # Staging photos were only needed to optimize + split into per-item
        # sessions; drop them so the volume doesn't grow with every batch.
        storage.purge_session(staging_id)


@app.post("/api/bulk/upload")
async def bulk_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
    notes: str = Form(""),
) -> dict:
    """Bulk mode: accept a photo dump spanning multiple items, then process in
    the background (poll /api/bulk/status/{job_id}). Every item queues as a
    draft for review — publishing stays an explicit, per-listing action.

    notes: the seller's comma-separated inventory of the pile. Saved with the
    staging session, which is also what makes it survive a restart — a resumed
    batch re-reads it from disk exactly like the first run did."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    # The worker thread can't return a 401, so the login requirement (billing
    # is per-account) is enforced before the upload is accepted.
    if tokens.enabled() and _uid(request) is None:
        raise HTTPException(
            401, "Log in to use AI features — your token balance is per account.")
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(
            400, f"Too many photos ({len(files)}) — bulk mode takes up to "
                 f"{MAX_BULK_FILES} at a time. Split the pile and run a second batch.")

    staging_id = storage.new_session_id()
    orig = storage.original_dir(staging_id)
    await run_in_threadpool(storage.save_notes, staging_id,
                            listing_prompt.clean_seller_notes(notes))
    batch_bytes = 0
    try:
        for i, f in enumerate(files):
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
            # Checked BEFORE the write, so the batch stops at the budget
            # instead of discovering it as an ENOSPC that has already taken
            # the volume down for everyone else.
            batch_bytes += len(data)
            if batch_bytes > MAX_BULK_BATCH_BYTES:
                raise HTTPException(
                    413, f"That batch is over {MAX_BULK_BATCH_BYTES // (1024 * 1024)}MB "
                         "of photos in total. Split the pile and run a second batch.")
            suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
            # Same reason as /api/upload — and more so here: a 250-photo
            # batch is on the order of a gigabyte of blocking writes.
            await run_in_threadpool(
                (orig / f"src_{i:03d}{suffix}").write_bytes, data)
    except OSError as exc:
        # Disk full / write failure — clean up the partial staging and report it
        # clearly instead of a raw 500. Old orphans are swept on restart.
        await run_in_threadpool(storage.purge_session, staging_id)
        log.error("bulk upload: disk write failed (%s)", exc)
        raise HTTPException(
            507, "The server is low on storage right now, so the upload couldn't "
                 "be saved. Space is reclaimed automatically — try again in a "
                 "minute, or delete a few old listings to free some up.") from exc

    # Capture per-request context now — the worker thread has no Request.
    uid = _uid(request)
    job_id = storage.new_session_id()
    strip_bg = str(remove_bg).lower() in ("true", "1", "yes", "on")
    await run_in_threadpool(_register_bulk_job, job_id, {
        "id": job_id, "phase": "uploading", "done": False,
        "error": None, "items": [], "total_items": 0, "current": 0,
        "total_photos": len(files),
        # Public: the queue's progress text claimed backgrounds were being
        # removed on every batch, including the ones that never asked for it.
        "remove_bg": strip_bg,
        # Mirrored (see jobstore.MIRROR_FIELDS) so a restart can pick this
        # batch back up instead of throwing away the photo work it finished.
        "_staging_id": staging_id, "_strip_bg": strip_bg, "_resumes": 0,
    }, uid=uid)
    threading.Thread(
        target=_run_bulk_job,
        args=(job_id, staging_id, strip_bg, uid),
        daemon=True,
    ).start()
    log.info("bulk %s: started (%d files)", job_id, len(files))
    return {"job_id": job_id}


@app.get("/api/bulk/status/{job_id}")
def bulk_status(job_id: str, request: Request) -> Response:
    """A job's live status — or, for one the server no longer has running, the
    mirrored record of how it ended (see services/jobstore). A batch cut short
    by a restart reports itself done with the reason, so the client can settle
    instead of polling an id that will never answer.

    None from the store means "no such job" OR "not yours"; both answer 404, so
    the reply never confirms that someone else's id exists. The body is served
    pre-serialized: a 30-item batch is a ~1MB dict and every client polls this
    every 1.5s, on the same shared CPUs running photo inference.
    """
    body = jobstore.snapshot_json(job_id, _uid(request))
    if body is None:
        raise HTTPException(404, "Unknown bulk job.")
    return Response(content=body, media_type="application/json")


@app.post("/api/bulk/cancel/{job_id}")
def bulk_cancel(job_id: str, request: Request) -> dict:
    """Stop a running batch.

    The seller's way off a batch that is taking longer than they are willing to
    wait — or one that has stopped moving. It settles the job for the client
    straight away and asks the worker to stand down at its next checkpoint
    (between photos, between items), so nothing is abandoned mid-item and
    nothing is charged for work that never runs.

    Nothing is deleted: every item the batch had already drafted is a saved
    listing and stays in Drafts. 404 covers both an unknown id and someone
    else's, exactly as the status endpoints do.
    """
    outcome = jobstore.request_cancel(job_id, _uid(request))
    if outcome is None:
        raise HTTPException(404, "Unknown bulk job.")
    # "It had already finished" is not a failure — the seller asked for it to
    # be over, and it is. Say which happened so the UI can word it honestly.
    return {"ok": True, "stopped": outcome == "stopping",
            "already_finished": outcome == "finished"}


@app.get("/api/bulk/status/{job_id}/brief")
def bulk_status_brief(job_id: str, request: Request) -> Response:
    """Just "is this batch finished?" — the same status without the drafted
    items. The app shell watches a running batch from every screen so its
    "a bulk batch is processing" banner stops the moment the batch ends; the
    full body is ~1MB and is for the queue screen that actually renders it."""
    body = jobstore.brief_json(job_id, _uid(request))
    if body is None:
        raise HTTPException(404, "Unknown bulk job.")
    return Response(content=body, media_type="application/json")


def _run_identify_job(job_id: str, session_id: str, uid: Optional[str],
                      spent: Optional[dict] = None) -> None:
    """Background worker for a single-item identify. Claude vision over several
    photos can take long enough that a synchronous request outlives the
    proxy/browser timeout ('server taking too long'); running it as a job the
    client polls avoids that entirely and still saves the draft when done.
    `spent` is the token charge taken by the endpoint — refunded on failure."""
    try:
        opt_dir = storage.optimized_dir(session_id)
        names = storage.list_optimized(session_id)
        if not names:
            # No AI ran, so the up-front charge goes back. This fires when the
            # session's photos disappear between the endpoint's check and this
            # thread's read — a concurrent delete of the last photo, or the
            # reclaim pass offloading the local copies — and it billed a full
            # draft for an error message.
            tokens.refund(spent)
            _bulk_set(job_id, done=True, error="No optimized images found for this session.")
            return
        def _beat(phase: str) -> None:
            # Stage heartbeats: the client resets its poll deadline whenever
            # the phase advances, so a legitimately long chain isn't declared
            # dead at a fixed wall-clock cutoff.
            _bulk_set(job_id, phase=phase, beat=time.time())

        prefs = _load_prefs(uid)  # once — strategy and defaults both read it
        _beat("identifying")
        # The seller's hints were saved with the session at upload time, so
        # they are read here rather than passed in: this worker also runs
        # "Start over", which re-drafts months later with no request to carry
        # them — and a re-run that has forgotten what the seller told it makes
        # exactly the mistake they typed the note to prevent.
        result = claude_ai.identify([opt_dir / n for n in names], names,
                                    strategy=_pricing_strategy(uid, prefs),
                                    notes=storage.load_notes(session_id))
        _apply_listing_defaults(result.listing, uid, prefs)
        _beat("category")
        _resolve_category(result.listing)
        # Fill the category's item specifics (and hunt the maker) so the draft
        # is SEO-ready — one consolidated call on chain v2, the original
        # multi-call chain on IDENTIFY_CHAIN=v1.
        added = _enrich_listing(result.listing, [opt_dir / n for n in names],
                                tags=result.tags, progress=_beat)
        # Tell the editor whether the server-side fill actually FILLED
        # anything, so its own autofill effect doesn't re-run (and re-charge)
        # work that is already done — and does run when the pass came back
        # empty. This was `added is not None`, which reported an empty pass as
        # filled and stood the fallback down over a draft with every required
        # specific still blank.
        result.specifics_autofilled = _specifics_were_filled(added)
        _beat("research")
        _research_draft(result.listing, [opt_dir / n for n in names],
                        result.raw_observations, result.confidence)
        _price_against_comps(result.listing, uid, prefs)
        storage.save_listing(session_id, result.listing)
        db.upsert_listing(session_id, result.listing.model_dump(), status="draft", user_id=uid)
        _bulk_set(job_id, done=True, phase="done", result=result.model_dump())
    except errors.StorageUnavailable as exc:
        # Same as the synchronous route: refund, and report it as what it is
        # rather than as an AI failure the seller could act on.
        tokens.refund(spent)
        log.warning("identify job %s: storage unavailable: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        tokens.refund(spent)
        log.warning("identify job %s failed: %s", job_id, exc)
        reason = claude_ai.ai_error_message(exc)[1]
        # The upload must never vanish: without a DB record the photos are
        # invisible in the app AND the orphan sweep deletes the session dir
        # within hours — "my shirt never saved as a draft". Save a stub draft
        # so the photos land in Drafts and Start over can retry the AI.
        try:
            names = storage.list_optimized(session_id)
            if names and not db.get_listing_best_effort(session_id):
                stub = Listing(images=names, missing_info=[
                    f"The AI couldn't identify this item ({reason}). "
                    "Your photos are safe — use Start over to retry, or fill "
                    "the listing in by hand."])
                storage.save_listing(session_id, stub)
                db.upsert_listing(session_id, stub.model_dump(),
                                  status="draft", user_id=uid)
                log.info("identify job %s: saved stub draft for session=%s",
                         job_id, session_id)
        except Exception as exc2:  # noqa: BLE001 - stub save is best-effort
            log.warning("identify job %s: couldn't save stub draft: %s",
                        job_id, exc2)
        _bulk_set(job_id, done=True, error=reason)


def _run_pipeline_job(job_id: str, session_id: str, uid: Optional[str],
                      strip_bg: bool, bg_spent: Optional[dict],
                      identify_spent: Optional[dict]) -> None:
    """Background worker for the single-listing upload pipeline: optimize the
    photos (with live per-photo progress) and then run the identify chain, as
    ONE job the client polls. The upload endpoint returns the moment the
    originals are on disk, so the seller watches real stages advance instead
    of holding a silent multi-minute request open."""
    try:
        _bulk_set(job_id, phase="optimizing", current=0, beat=time.time())
        opt_dir = storage.optimized_dir(session_id)
        opt_results = images.optimize_all(
            storage.original_dir(session_id), opt_dir, strip_bg,
            progress=lambda done, total: _bulk_set(
                job_id, current=done, total_photos=total, beat=time.time()))
        optimized = storage.list_optimized(session_id)
        if not optimized:
            tokens.refund(bg_spent)
            tokens.refund(identify_spent)
            errs = "; ".join(r["error"] for r in opt_results if r.get("error"))
            _bulk_set(job_id, done=True, error=(
                "Could not process the uploaded image(s)"
                + (f": {errs}" if errs else ". Unsupported or corrupt file format.")))
            return
        # Photos whose cutout failed (engine down / out of credits) kept their
        # background — give those tokens back, exactly as /api/upload does.
        bg_failed = sum(1 for r in opt_results
                        if r.get("bg_error") or r.get("error"))
        if bg_spent and bg_failed:
            tokens.refund(bg_spent, units=bg_failed * tokens.COSTS.get("image_ai", 1))
        # The upload summary the old synchronous response carried (per-photo
        # results for the rotation/bg toasts, the photo list) rides on the job.
        _bulk_set(job_id, upload={"optimized": optimized,
                                  "optimize_results": opt_results})
        _in_background(objstore.upload_optimized, session_id, opt_dir,
                       optimized, what="R2 push (pipeline)")
    except OSError as exc:
        tokens.refund(bg_spent)
        tokens.refund(identify_spent)
        if getattr(exc, "errno", None) == errno.ENOSPC:
            freed = reclaim_space(aggressive=True)
            log.warning("pipeline %s hit a full volume; reclaimed %.1f MB",
                        job_id, freed / 1e6)
        log.warning("pipeline %s: optimize failed: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=(
            "The server ran out of photo storage — try again shortly."))
        return
    except Exception as exc:  # noqa: BLE001 - job-level failure must surface
        tokens.refund(bg_spent)
        tokens.refund(identify_spent)
        log.warning("pipeline %s: optimize failed: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=f"Photo processing failed: {exc}")
        return
    # Photos are ready — hand off to the identify chain (it owns the
    # identify-charge refund on failure, stub-draft rescue, and done/result).
    _run_identify_job(job_id, session_id, uid, identify_spent)


@app.post("/api/identify-async/{session_id}")
def identify_async(session_id: str, request: Request) -> dict:
    """Start a background identify; poll /api/bulk/status/{job_id} for the
    result. Same outcome as POST /api/identify, but it never holds a long
    synchronous request open, so slow vision calls can't time out the browser."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured; cannot identify images.")
    _assert_session_owner(session_id, request)
    if not storage.list_optimized(session_id):
        raise HTTPException(404, "No optimized images found for this session.")
    uid = _uid(request)
    spent = _charge_ai(request, "identify")  # up front: a broke caller 402s here
    job_id = storage.new_session_id()
    _register_bulk_job(job_id, {
        "id": job_id, "kind": "identify", "phase": "identifying",
        "done": False, "error": None, "result": None,
        "_refunds": tokens.receipts(spent),
    }, uid=uid)
    threading.Thread(
        target=_run_identify_job, args=(job_id, session_id, uid, spent), daemon=True,
    ).start()
    log.info("identify job %s: started (session=%s)", job_id, session_id)
    return {"job_id": job_id}


@app.post("/api/shelf-scan")
async def shelf_scan(request: Request, files: list[UploadFile] = File(...)) -> dict:
    """Shop Mode 'Scan a shelf': the client samples frames from a recorded
    video and posts them here; Claude flags items worth a closer look. No
    pricing, no persistence — pure triage."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    if not files:
        raise HTTPException(400, "No frames provided.")
    frames: list[bytes] = []
    for f in files[:8]:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "A frame was too large.")
        if data:
            frames.append(data)
    if not frames:
        raise HTTPException(400, "No readable frames.")
    spent = await run_in_threadpool(_charge_ai, request, "shelf_scan")
    try:
        result = await run_in_threadpool(claude_ai.scan_shelf, frames)
    except Exception as exc:  # noqa: BLE001
        await run_in_threadpool(tokens.refund, spent)
        raise HTTPException(502, f"Shelf scan failed: {exc}") from exc
    log.info("shelf scan: %d frames -> %d candidates", len(frames),
             len(result.get("items", [])))
    return result


@app.post("/api/inventory/add")
def inventory_add(req: PublishRequest, request: Request) -> dict:
    """Shop Mode 'Buy': save a scanned item to the user's unlisted inventory
    (status='unlisted'), so it shows up in the Sell dashboard to finish + list
    later. Reuses the listing record; mode is ignored."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in to save items to your inventory.")
    _assert_session_owner(req.session_id, request)
    storage.save_listing(req.session_id, req.listing)
    # Shop Mode's "Buy" is a decision made in a shop, on this answer: the
    # seller taps it and then pays for the item. A write that did not land
    # means the thing they just bought is not in the dashboard they will look
    # for it in, and `ok: true` gave them no reason to check.
    landed = db.upsert_listing(req.session_id, req.listing.model_dump(),
                               status="unlisted", user_id=uid)
    if db.enabled() and not landed:      # see the note in merge_listings
        raise errors.StorageUnavailable(
            "Couldn't save that to your inventory just now. Try again in a "
            "moment.")
    log.info("inventory add: session=%s user=%s", req.session_id, uid)
    return {"ok": True, "id": req.session_id}


# One cap for "the whole store, mirrored": every consumer of the list (the
# grid, sync reconciliation, insights, promote-all) has to see at least what
# the import brought in, or it silently hides part of the store.
#
# It has to stay ahead of the import, not match it: at 600 — with the active
# import capped at 300 + 100 sold + 100 ended — a seller with 616 active
# listings lost the overflow twice over, once on import and again on read.
LIST_CAP = int(os.getenv("LISTING_LIST_CAP", "3000") or "3000")
# How many listings one unlink pass may release. Each one is its own
# write, so an unbounded pass over a switched-account store outlives the
# gateway; the remainder is reported so a second press finishes the job.
RELEASE_CAP = int(os.getenv("EBAY_RELEASE_CAP", "500") or "500")

# Fields dropped from each record's `listing` by GET /api/listings ONLY.
#
# A realistic record -- description, twelve photos, eighteen item specifics,
# a synced shadow -- serialises to about 6.2 KB, so a seller at LIST_CAP was
# downloading ~17.8 MiB on the app's busiest route, on a phone. Just under
# half of that is `remote_shadow`: a complete second copy of the listing,
# recording what eBay last said it contained. It is the base the three-way
# merge reconciles against and nothing outside the server has ever read it.
# `dirty_fields`, the set of fields edited since that shadow, is the same.
#
# Deliberately an omission list rather than an allowlist. An allowlist names
# what the client may see, so it fails by dropping a field the UI needs, and
# that failure is silent and looks like missing data. This fails the other
# way: a new field is merely bigger than it had to be.
#
# GET /api/listings/{id} is NOT projected -- opening one listing still answers
# with everything, which is where the editor and the merge dialog read from.
# tests/test_the_list_does_not_ship_the_sync_ledger.py holds both halves, and
# fails if the frontend ever starts referencing one of these names.
LIST_OMITTED_LISTING_FIELDS = ("remote_shadow", "dirty_fields")


def _projected_for_list(rec: dict) -> dict:
    """`rec` without the sync ledger. Copies rather than mutating: the dict
    under "listing" belongs to the row it was loaded from."""
    listing = rec.get("listing")
    if not isinstance(listing, dict):
        return rec
    if not any(k in listing for k in LIST_OMITTED_LISTING_FIELDS):
        return rec
    trimmed = {k: v for k, v in listing.items()
               if k not in LIST_OMITTED_LISTING_FIELDS}
    return {**rec, "listing": trimmed}


def _cursor_for(rec: dict) -> Optional[str]:
    """The opaque token naming one row, for the page that follows it.

    Base64url of "<updated_at>|<id>" — encoded so the timestamp's colons and
    offset sign survive a query string untouched, and opaque so nobody starts
    hand-assembling one. It is the server's own words handed back; the read it
    feeds is scoped by `user_id` exactly like every other, so a cursor says
    WHERE to start and never whose store to start in.

    None when the row cannot name a place in the order. The column is
    non-nullable so this is defensive, but the failure it prevents is the loud
    kind: a blank half mints a token the next request rejects as malformed —
    a 400 in the middle of a walk the seller started. No cursor degrades
    honestly instead: the page still says it was cut, and the button that
    could not have worked is simply not offered.
    """
    stamp, rid = rec.get("updated_at"), rec.get("id")
    if not stamp or not rid:
        return None
    raw = f"{stamp}|{rid}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _cursor_from(token: str) -> tuple[datetime, str]:
    """Parse one, or raise 400.

    Refused rather than ignored. An ignored cursor answers with page one,
    which the client reads as the listings that FOLLOW the ones it has — so
    the store looks like it ends where it began, which is the bug paging
    exists to fix, arriving through the fix.
    """
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode()
        stamp, sep, last_id = raw.partition("|")
        if not sep or not last_id:
            raise ValueError("no separator")
        when = datetime.fromisoformat(stamp)
        # Same rule as everywhere else a stored timestamp is read: a naive one
        # is UTC, not local, or the comparison silently moves the page edge.
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when, last_id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            400, "That listing page link is no longer valid — reload the page "
                 "to start from the top.") from exc


@app.get("/api/listings")
def listings(request: Request, limit: int = LIST_CAP,
             before: str = "") -> dict:
    """History of the current user's saved listings (most recent first)."""
    user = auth.current_user(request)
    # Clamped like /api/notifications does, and for both of its reasons: a
    # caller-supplied ?limit had no ceiling (so one request could ask for every
    # listing JSON blob the store holds) and no floor (?limit=-1 is a Postgres
    # error, which db.list_listings used to swallow into [] -- an empty store
    # reported as a 200; it now raises, and the clamp keeps it from arising).
    limit = max(1, min(limit, LIST_CAP))
    # One row more than will be returned, so the answer can say whether it is
    # the WHOLE store. Without that, a seller past the cap gets a page
    # indistinguishable from a complete one -- and the counts, the tabs, the
    # dashboard groups, the duplicate advisory and the checkboxes a bulk
    # reprice runs over are all built on it, none of them able to tell they
    # are working from a partial view.
    #
    # Same rule as the awaiting-shipment list and the sampled status sweep: an
    # answer that could not show everything says so. A probe row rather than a
    # COUNT(*) because this is the busiest route in the app, and because the
    # question the seller has is "is this all of them?" -- `truncated` answers
    # that honestly, where a total this endpoint does not have would have to be
    # invented.
    # The last row of the previous page, when the client is walking older
    # ones. Empty means "from the top", which is what a client sends on first
    # load and is not a malformed cursor.
    cursor = _cursor_from(before) if before else None
    rows = (db.list_listings(limit=limit + 1, user_id=user["id"], before=cursor)
            if user else [])
    items, truncated = rows[:limit], len(rows) > limit
    # And, only for the seller who is actually past the cap, how many there
    # are. The probe row above still decides -- free, on every load -- so
    # nobody under the cap pays for this; the rare seller over it gets
    # "3,000 of 4,812" instead of "there are more", which is the difference
    # between a page that admits it was cut and one that says what from.
    #
    # Tolerant on purpose, and the only read on this route that is: the answer
    # is already honest without a total, so a count that could not be taken
    # costs the sentence a number rather than costing the seller a truth. A
    # guessed one would do the opposite.
    total = None
    if truncated:
        try:
            total = db.count_listings(user["id"])
        except errors.StorageUnavailable as exc:
            log.info("listings: couldn't count the store for user=%s: %s",
                     user["id"], exc)
    return {"listings": [_projected_for_list(r) for r in items],
            "db": db.db_status(), "authed": bool(user),
            "truncated": truncated, "total": total,
            # Only when there IS a next page. A cursor on the last page is how
            # a client loops for ever.
            "next_cursor": _cursor_for(items[-1]) if truncated and items
                           else None}


def _live_ebay_id_map(items: list) -> dict:
    """{eBay listing id: our record id} for the user's live listings."""
    out = {}
    for it in items:
        if it.get("status") in ("published", "live"):
            eid = (it.get("listing") or {}).get("ebay_listing_id")
            if eid:
                out[str(eid)] = it["id"]
    return out


def _metrics_by_record_id(creds: Optional[dict], items: list,
                          status: Optional[dict] = None) -> dict:
    """eBay views/watchers for the user's live listings, keyed by OUR listing
    record id. Best-effort — {} when eBay isn't connected / scope not granted.
    Pass a `status` dict to also learn whether the traffic report was readable
    ({traffic_ok, needs_reconnect}), so blank numbers can be explained."""
    id_by_ebay = _live_ebay_id_map(items)
    if not creds or not id_by_ebay:
        return {}
    try:
        raw = metrics.listing_metrics(creds, list(id_by_ebay), status)
    except Exception as exc:  # noqa: BLE001 - metrics never break a request
        log.info("listing metrics unavailable: %s", exc)
        return {}
    return {id_by_ebay[eid]: m for eid, m in raw.items() if eid in id_by_ebay}


def _rates_by_record_id(creds: Optional[dict], items: list) -> dict:
    """eBay's recommended ad rate for the user's live listings, keyed by OUR
    record id. Best-effort — {} when unavailable."""
    id_by_ebay = _live_ebay_id_map(items)
    if not creds or not id_by_ebay:
        return {}
    try:
        raw = promotions.suggested_ad_rates(creds, list(id_by_ebay))
    except Exception as exc:  # noqa: BLE001 - recommendations are optional
        log.info("ad-rate recommendations unavailable: %s", exc)
        return {}
    return {id_by_ebay[eid]: r for eid, r in raw.items() if eid in id_by_ebay}


def _promoted_record_ids(creds: Optional[dict], items: list) -> tuple[set, bool]:
    """(record ids with an ACTIVE eBay ad, did eBay actually answer).

    Ours OR ads created directly in Seller Hub, so we never suggest promoting
    an item that is already promoted. The flag matters because promoting costs
    the seller a percentage of the sale: an empty set from a failed lookup is
    not evidence that nothing is promoted, and recommending a purchase on that
    basis is how a seller who promotes in Seller Hub gets invited to pay for a
    second ad during an eBay outage.
    """
    if not creds:
        return set(), False
    ads, known = promotions.active_ads_status(creds)
    if not ads:
        return set(), known
    promoted = set()
    for it in items:
        if it.get("status") not in ("published", "live"):
            continue
        listing = it.get("listing") or {}
        eid = str(listing.get("ebay_listing_id") or "")
        sku = ebay.sku_for(it["id"])
        if (eid and eid in ads) or (sku and sku in ads):
            promoted.add(it["id"])
    return promoted, known


@app.get("/api/ebay/listing-metrics")
def listing_metrics_route(request: Request) -> dict:
    """eBay views/impressions/watchers for the user's live listings, keyed by
    our listing record id. Empty when eBay isn't connected."""
    user = auth.current_user(request)
    if not user:
        return {"metrics": {}}
    # Best effort: no numbers is a thinner panel, not a wrong statement, and
    # it decides nothing that gets written. Live only, because
    # `_live_ebay_id_map` drops everything else -- reading the whole page to
    # keep the live rows left a big store's older live listings with no
    # numbers at all.
    items = db.list_listings_best_effort(limit=LIST_CAP, user_id=user["id"],
                                         statuses=("published", "live"))
    status: dict = {}
    by_id = _metrics_by_record_id(_ebay_creds_for(request), items, status)
    return {"metrics": by_id,
            "traffic_ok": bool(status.get("traffic_ok")),
            "needs_reconnect": bool(status.get("needs_reconnect"))}


@app.get("/api/ebay/duplicates")
def duplicate_listings(request: Request) -> dict:
    """Live listings that look like the same item listed more than once.

    Cleanup for the duplicates the old publish race left behind: those are two
    real eBay listings, so the app can't merge them — only the seller can
    decide which to end. This finds the likely pairs and hands over the
    evidence. Nothing is ended here; see /api/ebay/end-listing, one at a time.

    Never raises — a failure here must not take the Dashboard with it.
    """
    user = auth.current_user(request)
    if not user:
        return {"groups": [], "total": 0}
    try:
        # duplicates.find skips anything not live on its first line, so ask
        # for live ones: on a big store the unfiltered page is the wrong rows.
        groups = duplicates.find(
            db.list_listings(limit=LIST_CAP, user_id=user["id"],
                             statuses=duplicates.LIVE_STATUSES))
    except Exception as exc:  # noqa: BLE001 - advisory feature, never fatal
        log.warning("duplicate scan failed for user=%s: %s", user["id"], exc)
        return {"groups": [], "total": 0}
    return {"groups": groups, "total": len(groups),
            "listings": sum(len(g["listings"]) for g in groups)}


@app.get("/api/insights")
def insights(request: Request) -> dict:
    """Ranked 'what to do next' actions across the signed-in user's listings —
    finish drafts, promote/reprice stale live ones. Folds in
    eBay views/watchers and recommended ad rates when available. Returns an empty
    list for logged-out users. Never raises."""
    user = auth.current_user(request)
    if not user:
        return {"recommendations": [], "bulk_caps": _bulk_caps()}
    try:
        items = db.list_listings(limit=LIST_CAP, user_id=user["id"])
        creds = _ebay_creds_for(request)
        metrics_by_id = _metrics_by_record_id(creds, items)
        rates_by_id = _rates_by_record_id(creds, items)
        promoted_ids, promotion_known = _promoted_record_ids(creds, items)
        # limit=50: the dashboard groups these by category now, so each group
        # should show its full membership — the old flat list capped at 8.
        return {"recommendations": recommender.recommendations(
            items, metrics_by_id=metrics_by_id, rates_by_id=rates_by_id,
            promoted_ids=promoted_ids, promotion_known=promotion_known,
            limit=50),
            # What one tap on a group can actually reach in a single run — the
            # group renders its button, so it has to know. See _bulk_caps.
            "bulk_caps": _bulk_caps()}
    except Exception as exc:  # noqa: BLE001 - insights must never break the app
        log.warning("insights failed for user=%s: %s", user["id"], exc)
        return {"recommendations": [], "bulk_caps": _bulk_caps()}


@app.post("/api/ebay/promote")
def promote_one(payload: dict, request: Request) -> dict:
    """One-click promote a single LIVE listing via Promoted Listings Standard,
    using the given ad rate, else eBay's recommended rate, else the default."""
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user or not creds:
        raise HTTPException(400, "Connect eBay first.")
    lid = str(payload.get("listing_id") or "").strip()
    rec = db.get_listing(lid)
    if not rec or (rec.get("user_id") and rec["user_id"] != user["id"]):
        raise HTTPException(404, "Listing not found")
    if rec.get("status") not in ("published", "live"):
        raise HTTPException(400, "Only live listings can be promoted.")
    listing = Listing(**(rec.get("listing") or {}))
    try:
        rate = float(payload.get("ad_rate_percent") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        rate = _rates_by_record_id(creds, [rec]).get(lid) or 0
    status = _promote(lid, listing, creds, rate=rate)
    if status.get("promoted"):
        storage.save_listing(lid, listing)
        db.upsert_listing(lid, listing.model_dump(), status=rec.get("status"),
                          user_id=user["id"])
    return {"ok": bool(status.get("promoted")), "ad_rate": listing.ad_rate_percent,
            "needs_reconnect": bool(status.get("needs_reconnect")),
            "message": status.get("message")}


@app.post("/api/ebay/promote-all")
def promote_all(request: Request) -> dict:
    """Promote every live, not-yet-promoted listing at eBay's recommended rate
    (falling back to the default). Best-effort per item; stops early and asks the
    user to reconnect if the token lacks ad permissions."""
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user or not creds:
        raise HTTPException(400, "Connect eBay first.")
    items = [i for i in db.list_listings(limit=LIST_CAP, user_id=user["id"],
                                        statuses=("published", "live"))
             if not (i.get("listing") or {}).get("promote")]
    rates = _rates_by_record_id(creds, items)
    promoted = 0
    needs_reconnect = False
    for it in items:
        listing = Listing(**(it.get("listing") or {}))
        status = _promote(it["id"], listing, creds, rate=rates.get(it["id"]))
        if status.get("promoted"):
            storage.save_listing(it["id"], listing)
            db.upsert_listing(it["id"], listing.model_dump(), status=it.get("status"),
                              user_id=user["id"])
            promoted += 1
        elif status.get("needs_reconnect"):
            needs_reconnect = True
            break
    return {"promoted": promoted, "total": len(items), "needs_reconnect": needs_reconnect}


# How many listings one bulk price run touches. Each is a serial eBay revise;
# this keeps the request inside the gateway's patience, and whatever is left
# over comes back as `deferred` for the seller to run again.
BULK_PRICE_CAP = int(os.getenv("BULK_PRICE_CAP", "40") or "40")
# How many ids one bulk request may NAME. Above BULK_PRICE_CAP so a
# selection bigger than one pass is still accepted and deferred rather
# than refused, and far below anything that makes an `IN (...)` a
# problem. Same role as the 200 the bulk delete already applies.
BULK_SELECT_CAP = int(os.getenv("BULK_SELECT_CAP", "200") or "200")


@app.post("/api/ebay/lower-prices")
def lower_prices(payload: dict, request: Request) -> dict:
    """Lower the price of several live listings by one percentage, and push each
    change to eBay — the Dashboard's "Lower prices" suggestion group applied in
    one go instead of opening a dozen listings to make the same edit.

    The caller names the listings (the group's own membership), so this can
    never widen to the seller's whole store. Per listing: skipped when it isn't
    live or the cut wouldn't change the price, failed with eBay's reason when
    the revise is rejected, and neither stops the rest of the run.
    """
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user or not creds:
        raise HTTPException(400, "Connect eBay first.")
    try:
        percent = bulk_actions.validate_percent(payload.get("percent"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ids = [str(i).strip() for i in (payload.get("listing_ids") or []) if str(i).strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(400, "Pick at least one listing to reprice.")
    # Bounded, because the lookup below is BY id: an unbounded body used to
    # cost nothing (the store read was capped and this was only a filter) and
    # now becomes an unbounded `IN (...)`, which is a way to make one request
    # expensive for everyone. Nothing useful is lost -- a pass reprices at
    # most BULK_PRICE_CAP of them either way, and the remainder is already
    # reported as deferred for a second pass.
    if len(ids) > BULK_SELECT_CAP:
        raise HTTPException(
            400, f"That's too many listings for one go — pick up to "
                 f"{BULK_SELECT_CAP} and run it again for the rest.")
    wanted = set(ids)
    # The ids, asked for. This used to read the seller's newest LIST_CAP
    # records and filter -- every one of their JSON blobs across a
    # cross-region link to keep at most BULK_PRICE_CAP of them, and, worse,
    # anything outside that window came back to the seller as "Listing not
    # found." The list is newest-first, so on a store past the cap a ticked
    # listing only has to be edited past by 3,000 others between opening the
    # screen and pressing the button. Ownership is enforced inside the read.
    mine = db.get_listings(ids, user["id"])
    # Each listing is its own revise round-trip to eBay, run one after another.
    # Past this many the request outlives the gateway and the seller sees a
    # timeout instead of a result, so the run is bounded and the remainder is
    # reported for a second pass rather than silently dropped.
    records, deferred = mine[:BULK_PRICE_CAP], mine[BULK_PRICE_CAP:]
    provider = marketplaces.get("ebay")

    def _apply(rec: dict) -> dict:
        if rec.get("status") not in ("published", "live"):
            return {"skip": "No longer live on eBay."}
        data = rec.get("listing") or {}
        new_price = bulk_actions.lower_price(data.get("price"), percent)
        if new_price is None:
            return {"skip": "Price is already at the floor for a bulk drop."}
        listing = Listing(**data)
        was = listing.price
        listing.price = new_price
        # This edit never passes through a save, so there is no diff for
        # dirty_fields to find — and a revise only carries fields marked as
        # changed. Unmarked, this would send eBay an empty revise: the record
        # would show the new price, the seller would be told it worked, and
        # the listing would still be at the old one.
        listing.mark_dirty("price")
        # Through the provider, so each listing takes whichever revise path it
        # belongs to (Trading for store listings, the Inventory API for the
        # older app-published ones) and the record's status is written by the
        # same code a single publish uses.
        outcome = provider.publish(
            PublishContext(session_id=rec["id"], listing=listing, mode="live",
                           base_url=_base_url(request), uid=user["id"],
                           prev_record=rec),
            creds)
        if not outcome.ok:
            return {"message": outcome.message or "eBay rejected the new price."}
        return {"ok": True, "was": was, "now": new_price}

    result = bulk_actions.run(records, _apply)
    # Listings the client asked for that aren't the seller's (or are gone) are
    # reported rather than silently dropped from the totals.
    missing = wanted - {r["id"] for r in mine}
    for rid in sorted(missing):
        result.skipped.append({"listing_id": rid, "title": "this listing",
                               "message": "Listing not found."})
    log.info("bulk lower-prices: user=%s percent=%s changed=%d skipped=%d "
             "failed=%d deferred=%d", user["id"], percent, len(result.changed),
             len(result.skipped), len(result.failed), len(deferred))
    return {"percent": percent, "deferred": len(deferred), **result.as_dict()}


# How many listings one enrich run touches. Far below the price cap above
# because the work per listing is a different order of magnitude: a Claude
# vision pass over that listing's photos (plus, for an imported one, a
# download of those photos first) and then an eBay revise. Whatever is left
# over comes back as `deferred` for the seller to run again.
BULK_ENRICH_CAP = int(os.getenv("BULK_ENRICH_CAP", "25") or "25")


def _bulk_caps() -> dict:
    """How many listings ONE tap on a suggestion group's bulk button reaches,
    keyed by the recommendation type that carries the button.

    Both bulk actions cap a single run and hand the remainder back as
    `deferred` (see the two constants above). The dashboard had no way to know
    that, so it promised the whole group: a 46-listing "Fill in details" asked
    the seller to confirm 46, quoted the AI cost of 46 — and then ran 25 and
    reported "1 of 25" against a group badge reading 46. The caps ride along
    with the recommendations so the group can say what this pass will actually
    do before the seller agrees to spend anything on it.
    """
    return {"specifics": BULK_ENRICH_CAP, "lower_price": BULK_PRICE_CAP}


# The enrichment runs as a background job, one per account at a time: it
# spends AI credits per listing, and two tabs firing it at the same store
# would pay twice to fill the same blanks.
# user id -> job id of the enrich currently running for them.
_ENRICH_JOBS: dict[str, str] = {}
_ENRICH_LOCK = threading.Lock()


def _drop_answered_missing_info(listing: Listing) -> int:
    """Drop the 'a human should check this' notes an enrichment has answered.

    `missing_info` is the whole reason the dashboard says "Fill in details",
    so a listing whose blanks were just filled has to stop asking — otherwise
    the button that filled them leaves its own suggestion on screen and reads
    as a no-op.

    A note is answered when an item specific (or the brand) of that name now
    holds a value: "exact model number" by a filled Model, "size" by Size.
    Whole words only — "Type" must not answer "typewriter model" — and a note
    nothing filled is KEPT, because a blank the AI could not settle is still
    a real one and silencing it would be the more expensive lie.

    Returns how many notes were settled.
    """
    filled = {s.name.strip().lower() for s in listing.item_specifics
              if (s.value or "").strip() and s.name.strip()}
    if (listing.brand or "").strip():
        filled.add("brand")
    if not filled:
        return 0
    kept = [note for note in listing.missing_info
            if not any(re.search(rf"\b{re.escape(name)}\b", note.lower())
                       for name in filled)]
    settled = len(listing.missing_info) - len(kept)
    listing.missing_info = kept
    return settled


def _enrich_one(rec: dict, uid: str, creds: Optional[dict], base_url: str,
                note_charge=None) -> dict:
    """Fill ONE listing's blanks: eBay's recommended item specifics for its
    category, read off its own photos, plus the maker double-check — the same
    enrichment a fresh AI draft gets, applied to a listing that is already
    live. Pushed to eBay when the listing is live, saved locally otherwise.

    `note_charge(receipts)` (optional) is told about the AI charge while it is
    outstanding, and told `None` once it has been settled in this process —
    so a restart mid-listing can pay back a charge that bought nothing rather
    than leaving the seller to spot it (see _settle_interrupted_jobs).

    Returns a bulk_actions outcome: {"ok": True, ...}, {"skip": reason}, or
    {"message": why it failed}.
    """
    rid = rec["id"]
    status = rec.get("status") or ""
    if status in ("sold", "ended"):
        # A settled listing is an archive record: its photos are purged on
        # sale, and eBay will not revise a finished item. Relist is the verb
        # for those, and it is a different suggestion.
        return {"skip": "Sold and ended listings can't be revised."}
    if status in ("published", "live") and not creds:
        # Checked BEFORE the charge: filling in a live listing that we then
        # cannot revise leaves the buyer-facing page exactly as blank as it
        # was, and billing for that is billing for nothing the seller can see.
        return {"skip": "Connect eBay first — this one is live there."}
    # An imported listing's photos live on eBay, and the AI reads files, so
    # adopt them the way the editor does before it will open one. Adoption
    # WRITES the local filenames onto the record, which is why it happens
    # before the model is built: a Listing read a moment earlier carries an
    # empty `images`, and the save below would put that straight back over
    # the photos this just downloaded.
    _adopt_imported_images(rid, rec)
    listing = Listing(**(rec.get("listing") or {}))
    # Item specifics are per category, so a listing without one has nothing to
    # fill. Imported listings usually do carry eBay's own category; the ones
    # that don't get the same best-effort resolve a fresh draft gets, which is
    # what keeps this a one-click action instead of a detour through the editor.
    _resolve_category(listing)
    if not listing.category_id:
        return {"skip": "No eBay category yet — open it and pick one."}
    names = listing.images or storage.list_optimized(rid)
    opt_dir = storage.optimized_dir(rid)
    paths = [opt_dir / n for n in names if (opt_dir / n).is_file()]
    if not paths:
        return {"skip": "This listing's photos aren't on the server anymore."}

    before_brand = (listing.brand or "").strip()
    spent = _charge_uid(uid, "specifics")
    if note_charge:
        note_charge(tokens.receipts(spent))
    try:
        try:
            added = _enrich_listing(listing, paths)
        except Exception:
            tokens.refund(spent)
            raise
        if added is None:
            # The enrichment never ran (no taxonomy, no model, no aspects for
            # this category). `_enrich_listing` swallows its own failures, so
            # "didn't run" is the return value rather than an exception — and
            # nothing was earned, so the charge goes back.
            tokens.refund(spent)
            return {"skip": "The AI couldn't read eBay's details for that category."}
        settled = _drop_answered_missing_info(listing)
        if not added and not settled:
            # The AI did run and found nothing the photos could answer, which
            # is a real (billable) answer — the same one the single-listing
            # autofill gives. What it is not is done.
            return {"skip": "Nothing the photos could answer — this one needs you."}

        if status in ("published", "live") and creds:
            # A revise carries only what is marked changed (see dirty_fields),
            # and this edit never passed through a save, so nothing marked it.
            # Unmarked, eBay would receive an empty revise: the record would
            # show the new specifics, the seller would be told it worked, and
            # the live listing would still be blank — the exact silence this
            # button exists to end.
            listing.mark_dirty("item_specifics")
            if (listing.brand or "").strip() != before_brand:
                listing.mark_dirty("brand")
            outcome = marketplaces.get("ebay").publish(
                PublishContext(session_id=rid, listing=listing, mode="live",
                               base_url=base_url, uid=uid, prev_record=rec),
                creds)
            if not outcome.ok:
                return {"message": outcome.message or "eBay rejected the new details."}
            return {"ok": True, "added": added, "settled": settled, "pushed": True}
        # A draft (or a seller with no eBay connection): the fill is still
        # worth keeping, it just has nowhere to go yet.
        storage.save_listing(rid, listing)
        db.upsert_listing(rid, listing.model_dump(), status=_sticky_status(rec),
                          user_id=uid)
        return {"ok": True, "added": added, "settled": settled, "pushed": False}
    finally:
        # Settled either way by the time we get here: earned by a fill that
        # landed, or handed back above. Anything still recorded as outstanding
        # would be refunded a second time on the next boot.
        if note_charge:
            note_charge(None)


def _run_enrich_job(job_id: str, records: list[dict], uid: str,
                    creds: Optional[dict], base_url: str,
                    deferred: int = 0) -> None:
    """Background worker for "Enrich all". One vision pass per listing means a
    dozen of them take minutes — far longer than a browser (or the proxy in
    front of us) holds a request open — so this is a job the client polls, and
    every listing it finishes is saved as it goes rather than at the end."""
    result = bulk_actions.BulkResult()
    stopped = ""
    finished = 0
    try:
        for i, rec in enumerate(records):
            title = ((rec.get("listing") or {}).get("title")
                     or rec.get("title") or "this listing")
            jobstore.update(job_id, phase="enriching", current=i,
                            current_title=title[:80])
            try:
                outcome = _enrich_one(
                    rec, uid, creds, base_url,
                    note_charge=lambda r: jobstore.update(job_id, _refunds=r)) or {}
            except HTTPException as exc:
                # Out of AI credits (402) or logged out from under the job.
                # Whatever is already filled is filled; say where it stopped
                # instead of reporting the rest as failures.
                stopped = str(exc.detail)
                break
            except Exception as exc:  # noqa: BLE001 - one listing can't sink the run
                log.warning("enrich failed for %s: %s", rec.get("id"), exc)
                result.failed.append({"listing_id": rec.get("id") or "",
                                      "title": title, "message": str(exc)[:200]})
                finished = i + 1
                continue
            if outcome.get("skip"):
                result.skipped.append({"listing_id": rec.get("id") or "",
                                       "title": title, "message": outcome["skip"]})
            elif outcome.get("ok"):
                result.changed.append({"listing_id": rec.get("id") or "",
                                       "title": title,
                                       **{k: v for k, v in outcome.items() if k != "ok"}})
            else:
                result.failed.append({
                    "listing_id": rec.get("id") or "", "title": title,
                    "message": outcome.get("message") or "Couldn't fill this one in."})
            finished = i + 1
        filled = sum(int(c.get("added") or 0) for c in result.changed)
        log.info("bulk enrich %s: user=%s listings=%d changed=%d skipped=%d "
                 "failed=%d specifics=%d deferred=%d", job_id, uid, len(records),
                 len(result.changed), len(result.skipped), len(result.failed),
                 filled, deferred)
        jobstore.update(job_id, done=True, phase="done", current=finished,
                        result={"deferred": deferred, "filled": filled,
                                "stopped": stopped, **result.as_dict()})
    except Exception as exc:  # noqa: BLE001 - the job must always answer
        reference = _support_reference()
        log.warning("enrich job %s failed for user=%s [%s]: %s",
                    job_id, uid, reference, exc)
        jobstore.update(job_id, done=True, phase="failed", error=(
            "We couldn't finish filling these in. Try again in a moment — if "
            f"it keeps happening, quote {reference} to support."))
    finally:
        with _ENRICH_LOCK:
            if _ENRICH_JOBS.get(uid) == job_id:
                _ENRICH_JOBS.pop(uid, None)


@app.post("/api/listings/enrich")
def enrich_listings(payload: dict, request: Request) -> dict:
    """Fill in the blanks on several listings at once — the dashboard's "Fill
    in details" group applied in one go instead of opening a dozen listings to
    make the same edit on each.

    Per listing: eBay's recommended item specifics for its category, read off
    its own photos, merged in WITHOUT touching anything the seller already
    wrote, and pushed to eBay when the listing is live.

    The caller names the listings (the group's own membership), so this can
    never widen to the seller's whole store — which matters more here than it
    does for a price drop, because every listing this touches spends AI
    credits. Returns {"job_id"} immediately; poll /api/bulk/status/{job_id}.
    """
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    ids = [str(i).strip() for i in (payload.get("listing_ids") or []) if str(i).strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(400, "Pick at least one listing to fill in.")
    # Bounded for the same reason the price drop is: the lookup below is BY
    # id, and an unbounded body is an unbounded `IN (...)`. Nothing useful is
    # lost — a pass enriches at most BULK_ENRICH_CAP of them either way, and
    # the remainder comes back as deferred for a second run.
    if len(ids) > BULK_SELECT_CAP:
        raise HTTPException(
            400, f"That's too many listings for one go — pick up to "
                 f"{BULK_SELECT_CAP} and run it again for the rest.")
    uid = user["id"]
    with _ENRICH_LOCK:
        running = _ENRICH_JOBS.get(uid)
        if running:
            snap = jobstore.snapshot(running, uid)
            if snap and not snap.get("done"):
                # Two tabs, or a double tap. Hand back the job already running
                # rather than paying a second time to fill the same blanks.
                return {"job_id": running, "running": True, "total": 0,
                        "deferred": 0}
            _ENRICH_JOBS.pop(uid, None)
    # Ownership is enforced inside the read, and an id the seller doesn't own
    # is simply absent from the answer (see db.get_listings).
    mine = db.get_listings(ids, uid)
    records, deferred = mine[:BULK_ENRICH_CAP], mine[BULK_ENRICH_CAP:]
    if not records:
        raise HTTPException(404, "None of those listings are here anymore.")
    creds = _ebay_creds_for(request)
    base_url = _base_url(request)
    job_id = storage.new_session_id()
    with _ENRICH_LOCK:
        _ENRICH_JOBS[uid] = job_id
    jobstore.register(job_id, {
        "id": job_id, "kind": "enrich", "phase": "enriching", "done": False,
        "error": None, "current": 0, "total_items": len(records),
    }, uid=uid)
    threading.Thread(
        target=_run_enrich_job,
        args=(job_id, records, uid, creds, base_url, len(deferred)),
        daemon=True,
    ).start()
    log.info("bulk enrich %s: started for user=%s listings=%d deferred=%d",
             job_id, uid, len(records), len(deferred))
    return {"job_id": job_id, "running": True, "total": len(records),
            "deferred": len(deferred)}


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: str, request: Request) -> dict:
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    # Enforce ownership for listings that belong to an account.
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    # Deliberately no photo adoption here. This used to download every remote
    # photo, write them to disk, upsert the row and start an R2 upload — on a
    # read. A prefetch, a retry, a crawler, a link preview or a double-click
    # each started that work, two opens raced over one directory, and a GET
    # could fail or bill storage for something the seller never asked for.
    # storage.py states the rule. Adoption is now POST prepare-for-editing.
    #
    # `conflicts` is the raw map on the record turned into something the
    # editor can render: what was held back from eBay and what the two sides
    # say. Pure formatting of data already in `rec`, so this stays a read.
    return {**rec,
            "conflicts": sync_merge.describe_conflicts(
                (rec.get("listing") or {}).get("conflicts"))}


@app.post("/api/listings/{listing_id}/prepare-for-editing")
def prepare_for_editing(listing_id: str, request: Request) -> dict:
    """Copy an imported listing's eBay-hosted photos into app storage.

    The editor works only on images the app owns — ebayimg URLs never reach
    it — so this has to happen before an imported listing can be edited. It
    used to happen invisibly on the first GET; now the seller asks for it,
    which is what makes the cost (up to 24 downloads and 48 files) something
    they chose.

    Idempotent: a listing whose photos are already local is reported ready
    without fetching anything.
    """
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    names = _adopt_imported_images(listing_id, rec)
    return {"ok": True, "images": names,
            "listing": (rec.get("listing") or {})}


@app.post("/api/listings/{listing_id}/resolve-conflict")
def resolve_conflict(listing_id: str, payload: dict, request: Request) -> dict:
    """Settle one field the seller and eBay both changed.

    The sync records these and sends neither value, which is right — picking
    one silently is how a Seller Hub fix gets overwritten. What was missing is
    the way to answer. Until this exists, a conflicted field is an edit that
    never reaches eBay and never explains itself.

    `choice` is "mine" or "ebay". Keeping the local value queues it for the
    next revise, so answering actually pushes it; taking eBay's writes it in
    and asks for nothing. Either way the base moves to eBay's current value —
    see services/sync_merge.resolve for why that is true even for "mine".
    """
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")

    listing = Listing(**(rec.get("listing") or {}))
    try:
        sync_merge.resolve(listing, str(payload.get("field", "")),
                           str(payload.get("choice", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    data = listing.model_dump()
    storage.save_listing(listing_id, listing)
    # Strict, unlike the ordinary save: an answer the seller gave that did not
    # commit must not be reported as settled. They would move on believing the
    # question closed and find the same one waiting after the next sync -- and
    # a "keep mine" that never persisted means their value is still not going
    # to eBay, which is the silence this whole route exists to end.
    #
    # Only when a database is configured at all: without one, disk above IS
    # the record, and the row this could not write does not exist to want.
    if db.enabled() and not db.upsert_listing(
            listing_id, data, status=rec.get("status"),
            user_id=rec.get("user_id")):
        raise errors.StorageUnavailable(
            "Couldn't save that choice just now. Try again in a moment.")
    return {"ok": True, "listing": data,
            "conflicts": sync_merge.describe_conflicts(listing.conflicts),
            "message": ("Saved. It'll go to eBay the next time you update "
                        "this listing."
                        if payload.get("choice") == "mine"
                        else "Saved — this listing now matches eBay.")}


def _adopt_imported_images(listing_id: str, rec: dict) -> list[str]:
    """Copy an imported eBay listing's EPS-hosted photos into app storage so
    they're editable exactly like uploaded ones (the app owns every editable
    image; ebayimg URLs never reach the browser editor). Returns the local
    filenames, or [] when there was nothing to do.

    Best-effort: on failure the record is left unchanged and the UI falls back
    to the read-only eBay photo strip."""
    listing = rec.get("listing") or {}
    if (listing.get("source") or "") != "ebay" or not listing.get("image_urls"):
        return []
    if listing.get("images"):
        return list(listing["images"])
    if (rec.get("status") or "") == "sold":
        # Archived — its session dir is purged on sale; adopting here would
        # re-download photos for a listing that can't be edited anyway.
        return []
    # A previous run may have imported the files but failed the DB write.
    names = storage.list_optimized(listing_id) \
        or image_import.import_listing_images(listing_id, listing["image_urls"])
    if not names:
        return []
    # Mirror to R2 like uploads do — otherwise imported listings are the one
    # kind of session the offload sweep can never free from the volume.
    _in_background(objstore.upload_optimized, listing_id,
                   storage.optimized_dir(listing_id), names,
                   what="adopted-import R2 push")
    listing["images"] = names
    rec["listing"] = listing
    try:
        db.upsert_listing(listing_id, listing, status=rec.get("status") or "published",
                          user_id=rec.get("user_id"))
        storage.save_listing(listing_id, Listing(
            **{k: v for k, v in listing.items() if k in Listing.model_fields}))
    except Exception as exc:  # noqa: BLE001 - files are on disk; a retry redoes the DB
        log.warning("image import: couldn't persist adopted photos for %s: %s",
                    listing_id, exc)
    return names


# Fields that describe THIS sale and THIS eBay item, and so must never ride
# along into the fresh draft a relist creates: carrying any of them over
# would make the copy look like the sold listing it came from — and let a
# publish try to revise an eBay item that is already finished.
_SALE_ONLY_FIELDS = {
    "ebay_listing_id": "", "sku": "", "source": "", "ebay_start_time": "",
    "view_url": "", "watch_count": 0, "sold_quantity": 0,
    "sold_price": None, "sold_at": "",
}


@app.post("/api/listings/{listing_id}/relist")
def relist_listing(listing_id: str, request: Request) -> dict:
    """Copy a settled (sold / ended) listing into a BRAND-NEW draft.

    A sold listing is an archive record: what one finished sale was. The app
    no longer lets it be edited back onto eBay (/api/publish refuses it), so
    selling another of the same item is a NEW listing — which is what this
    makes. The draft carries the copy, the specifics and whatever photos
    survive; every field belonging to the finished sale (item id, sale price,
    sale date, per-marketplace state) is cleared. The sold record itself is
    left exactly as it was.

    Photos: a sale purges the session's images to reclaim storage, so a
    relist of an app-created listing usually starts with none — `photos: 0`
    says so, and the editor opens on the upload card. Listings imported from
    eBay keep their eBay-hosted `image_urls`, which the copy carries as-is.
    """
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    uid = _uid(request)
    if rec.get("user_id") and rec["user_id"] != uid:
        raise HTTPException(404, "Listing not found")

    data = dict(rec.get("listing") or {})
    data.update(_SALE_ONLY_FIELDS)
    data["marketplaces"] = {}
    listing = Listing(**{k: v for k, v in data.items() if k in Listing.model_fields})

    new_id = storage.new_session_id()
    # Photos are COPIED, never moved: the sold record keeps whatever it still
    # has, and a copy that fails leaves the seller a draft to re-upload into
    # rather than no draft at all.
    src_dir = storage.optimized_dir(listing_id)
    names = [n for n in (listing.images or storage.list_optimized(listing_id))
             if (src_dir / n).is_file()]
    copied: list[str] = []
    if names:
        dst_dir = storage.optimized_dir(new_id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            try:
                shutil.copyfile(src_dir / name, dst_dir / name)
            except OSError as exc:
                raise HTTPException(
                    507, "The server is out of storage space — try again shortly.") from exc
            copied.append(name)
        objstore.upload_optimized(new_id, dst_dir, copied)
    listing.images = copied

    storage.save_listing(new_id, listing)
    # Same rule, lower stakes: nothing is deleted here, but answering with a
    # new draft id that is not in the store sends the client to a listing that
    # does not exist.
    landed = db.upsert_listing(new_id, listing.model_dump(),
                               status="draft", user_id=uid)
    if db.enabled() and not landed:      # see the note in merge_listings
        raise errors.StorageUnavailable(
            "Couldn't create the new draft just now. Try again in a moment.")
    log.info("relist: %s -> new draft %s (%d photo(s), %d eBay-hosted) user=%s",
             listing_id, new_id, len(copied), len(listing.image_urls or []), uid)
    return {"ok": True, "id": new_id, "from": listing_id,
            "photos": len(copied) + len(listing.image_urls or []),
            "listing": listing.model_dump()}


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: str, request: Request) -> dict:
    """Remove a saved listing/draft and clean up its files. A missing (or
    not-owned) listing is a 404. One DB round-trip — delete_listing does its
    own ownership check — and the disk/R2 cleanup runs in the background, so
    the button doesn't hang on a cold database + file I/O."""
    if not db.delete_listing(listing_id, _uid(request)):
        raise HTTPException(404, "Listing not found")
    _in_background(_purge_session_images_best_effort, listing_id,
                       what="delete cleanup")
    log.info("listing deleted: id=%s user=%s", listing_id, _uid(request))
    return {"ok": True}


@app.post("/api/listings/bulk-delete")
def bulk_delete_listings(payload: dict, request: Request) -> dict:
    """Mass-delete listings (drafts) in ONE request: each id is deleted with
    the same per-row ownership check as the single delete; file/R2 cleanup for
    all of them runs in the background. Ids that don't exist or aren't owned
    are skipped and reported back."""
    ids = [str(s).strip() for s in (payload.get("ids") or []) if str(s).strip()]
    ids = list(dict.fromkeys(ids))[:200]
    if not ids:
        raise HTTPException(400, "No listings selected.")
    uid = _uid(request)
    # Each id on its own: the documented behaviour is that ids which do not
    # exist or are not owned are skipped and reported back, and a row the
    # database refused belongs in the same list rather than sinking the other
    # nineteen. What must not happen -- and is why the reader below is
    # separate -- is that one being reported as "not yours".
    deleted = []
    refused = []
    for lid in ids:
        try:
            if db.delete_listing(lid, uid):
                deleted.append(lid)
        except errors.StorageUnavailable as exc:
            log.warning("bulk delete: couldn't remove %s: %s", lid, exc)
            refused.append(lid)
    for lid in deleted:
        _in_background(_purge_session_images_best_effort, lid,
                           what="bulk-delete cleanup")
    log.info("bulk delete: %d/%d removed user=%s", len(deleted), len(ids), uid)
    return {"ok": True, "deleted": deleted,
            "skipped": [i for i in ids if i not in deleted]}


# --- Merging duplicate drafts ------------------------------------------------
# One item photographed twice leaves a bulk batch as two half-right drafts, so
# consolidating them is a decision, not a button: which draft is the master,
# and — where the drafts disagree — whose entry survives. Both steps run the
# same validation and the same field walk (services/listing_merge.py), so the
# review screen can promise exactly what the merge delivers.

def _merge_ids(payload: dict, request: Request) -> tuple[str, list[str]]:
    """The master and the drafts being consolidated into it, ownership-checked.
    The master is never also a source, and a draft named twice is one draft."""
    target_id = str(payload.get("target_id") or "").strip()
    raw_sources = [str(s).strip() for s in (payload.get("source_ids") or [])]
    source_ids = [s for s in dict.fromkeys(raw_sources) if s and s != target_id]
    if not target_id or not source_ids:
        raise HTTPException(400, "Pick a target and at least one duplicate to merge.")
    _assert_session_owner(target_id, request)
    for sid in source_ids:
        _assert_session_owner(sid, request)
    return target_id, source_ids


def _merge_records(target_id: str,
                   source_ids: list[str]) -> tuple[dict, list[tuple[str, dict]]]:
    """The master's record, and each draft to consolidate in merge order.

    A live listing is refused at either end: as the master because a published
    listing is not a draft to pour photos into, as a source because merging
    deletes it here while it stays up on eBay with nothing left pointing at it.
    """
    trec = db.get_listing(target_id)
    if not trec:
        raise HTTPException(404, "Listing not found")
    if trec.get("status") in ("published", "live"):
        raise HTTPException(400, "Merge into a draft — this target is already live on eBay.")
    sources: list[tuple[str, dict]] = []
    for sid in source_ids:
        srec = db.get_listing(sid) or {}
        if srec.get("status") in ("published", "live"):
            raise HTTPException(
                400, "One of the drafts you picked is already live on eBay — "
                     "end that listing first, then merge.")
        sources.append((sid, srec.get("listing") or {}))
    return trec, sources


def _merge_row(listing_id: str, data: dict) -> dict:
    """One draft as the review screen lists it."""
    return {"listing_id": listing_id,
            "title": str(data.get("title") or "").strip(),
            "photos": len(data.get("images") or storage.list_optimized(listing_id))}


@app.post("/api/listings/merge/preview")
def merge_listings_preview(payload: dict, request: Request) -> dict:
    """A merge, worked out but not performed: with `target_id` as the master,
    every field these drafts disagree about — each entry on offer and which
    draft it came from — plus the blanks on the master a duplicate can fill in.

    Writes nothing. The seller's answers go to /api/listings/merge as
    `field_choices`, and re-previewing with a different `target_id` is how the
    master changes."""
    target_id, source_ids = _merge_ids(payload, request)
    trec, sources = _merge_records(target_id, source_ids)
    tdata = trec.get("listing") or {}
    consolidating = [_merge_row(sid, data) for sid, data in sources]
    return {"ok": True, "target_id": target_id,
            "target": _merge_row(target_id, tdata),
            "sources": consolidating,
            "added_photos": sum(row["photos"] for row in consolidating),
            **listing_merge.review([(target_id, tdata), *sources])}


@app.post("/api/listings/merge")
def merge_listings(payload: dict, request: Request) -> dict:
    """Merge duplicate drafts into one listing. `target_id` names the master:
    every source listing's photos are appended to it (order preserved), the
    fields named in `field_choices` ({field key: the listing id whose entry
    wins}, both straight out of /api/listings/merge/preview) are written over
    the master's, any field the master left blank is filled from the first
    duplicate that has one, and the sources are then deleted (DB + disk + R2).
    The fix-up for bulk grouping splitting one item's photos into several
    draft listings.

    Sent without `field_choices` — every client from before the review step —
    each of the master's own entries stands, exactly as it used to; only its
    blanks fill in."""
    target_id, source_ids = _merge_ids(payload, request)
    trec, sources = _merge_records(target_id, source_ids)
    uid = _uid(request)

    data, applied = listing_merge.resolve(
        [(target_id, trec.get("listing") or {}), *sources],
        payload.get("field_choices"))
    listing = Listing(**data)
    tdir = storage.optimized_dir(target_id)
    tdir.mkdir(parents=True, exist_ok=True)

    base = list(listing.images) or storage.list_optimized(target_id)
    nxt = max((storage.image_index(n)
               for n in base + storage.list_optimized(target_id)), default=-1) + 1
    added: list[str] = []
    for sid, s_listing in sources:
        sdir = storage.optimized_dir(sid)
        for n in (s_listing.get("images") or storage.list_optimized(sid)):
            src = sdir / n
            if not src.is_file():
                continue  # photo already lost from disk — skip, keep merging
            dst_name = f"img_{nxt:03d}.jpg"
            try:
                shutil.copyfile(src, tdir / dst_name)
            except OSError as exc:
                raise HTTPException(
                    507, "The server is out of storage space — try again shortly.") from exc
            added.append(dst_name)
            nxt += 1

    listing.images = base + added
    if added:
        objstore.upload_optimized(target_id, tdir, added)
    storage.save_listing(target_id, listing)
    # BEFORE the destructive half, and checked. db.upsert_listing swallows its
    # failures by design, so this write could fail while the deletes below
    # went ahead regardless -- leaving the seller with the sources gone, their
    # photos purged, the master still on its pre-merge record, and `ok: true`
    # on the screen. Unlike almost everything else here that is not
    # recoverable by trying again: there is nothing left to try it on.
    #
    # The order is the fix, not the check. Same strict rule as PATCH
    # /api/listings/{id}, applied where it decides whether anything may be
    # deleted at all.
    landed = db.upsert_listing(target_id, listing.model_dump(),
                               status="draft", user_id=uid)
    # Called unconditionally and only ENFORCED when there is a database:
    # `db.enabled() and not db.upsert_listing(...)` short-circuits, so
    # without one the write never happens at all. Here that would skip the
    # save and still delete the sources.
    if db.enabled() and not landed:
        raise errors.StorageUnavailable(
            "Couldn't save the merged listing just now, so nothing was "
            "merged or removed. Try again in a moment.")
    # Sources are consumed: remove their records and reclaim their storage.
    #
    # `removed` is a list the client ACTS on — it drops those cards — so it
    # reports the deletes that actually happened rather than the ones that
    # were attempted. A source whose delete failed is still a real listing;
    # saying it is gone hides a duplicate the seller was told had been
    # consumed. Its photos stay too: they are the only copy left of it.
    removed: list[str] = []
    for sid in source_ids:
        try:
            removed_it = db.delete_listing(sid, uid)
        except errors.StorageUnavailable as exc:
            # Same outcome as a refusal: the source and its photos stay, and
            # the caller is told which ones did not go. Raising here would
            # abandon the sources after this one with the merge already
            # committed.
            log.warning("merge: couldn't remove source %s: %s", sid, exc)
            removed_it = False
        if not removed_it:
            log.warning("merge: couldn't remove source %s — leaving it and "
                        "its photos alone", sid)
            continue
        removed.append(sid)
        _purge_session_images_best_effort(sid)
    log.info("merged %d/%d listing(s) into %s (+%d photos, %d field(s) carried over) user=%s",
             len(removed), len(source_ids), target_id, len(added), len(applied), uid)
    return {"ok": True, "added": len(added), "removed": removed,
            "applied": applied, "listing": listing.model_dump()}


@app.post("/api/publish")
def publish(req: PublishRequest, request: Request) -> JSONResponse:
    """Publish orchestrator.

    No `marketplaces` in the request (every pre-multi client) → the legacy
    single-eBay path: the eBay provider (Trading-vs-Inventory routing,
    imported revise/relist, preflight, promotion — moved verbatim to
    marketplaces/ebay_provider.py) returns its legacy JSON body, returned
    here untouched.

    With `marketplaces` → fan out to each named provider independently: one
    marketplace failing never blocks or rolls back the others, and the
    response carries a per-marketplace result map.
    """
    if req.mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    _assert_session_owner(req.session_id, request)
    uid = _uid(request)
    prev_rec = db.get_listing(req.session_id) or {}
    # A sold listing is an archive record, not a draft: it says what one
    # finished sale was, and republishing it in place would overwrite that
    # history with a second listing's life (and, for an imported item, ask
    # eBay to revise an item that has already ended). Selling another of the
    # same thing is a NEW listing — POST /api/listings/{id}/relist makes one
    # from this record's copy and photos, leaving the sale intact.
    if (prev_rec.get("status") or "") == "sold":
        raise HTTPException(
            409, "This listing has sold — it's archived under Inactive. "
                 "Use Relist as new listing to sell another one.")

    # The server owns per-marketplace state: whatever map the client sent is
    # replaced with the stored record's before anything reads it (or gets
    # mirrored to disk), so a stale browser tab can never wipe another
    # marketplace's listing id.
    _restore_server_state(req.session_id, req.listing, prev_rec)
    storage.save_listing(req.session_id, req.listing)

    targets: list[str] = []
    for key in req.marketplaces:
        key = (key or "").strip().lower()
        if key and key not in targets:
            targets.append(key)

    def _ctx(own_listing: bool = False) -> PublishContext:
        # own_listing gives the provider its own deep copy: concurrent
        # providers each mutate listing.marketplaces, and the fold below reads
        # outcomes + a fresh DB record, never these working copies.
        listing = req.listing.model_copy(deep=True) if own_listing else req.listing
        return PublishContext(
            session_id=req.session_id, listing=listing, mode=req.mode,
            base_url=_base_url(request), uid=uid, prev_record=prev_rec)

    if not targets or targets == ["ebay"]:
        # Legacy path — byte-identical responses; the provider already
        # persisted the record exactly as the old inline code did (including
        # racing rules around the background EPS refresh), so no second
        # upsert here.
        provider = marketplaces.get("ebay")
        outcome = provider.publish(_ctx(), provider.creds_for(uid))
        return JSONResponse(outcome.raw)

    def _publish_one(key: str) -> PublishOutcome:
        provider = marketplaces.get(key)
        if provider is None:
            return PublishOutcome(ok=False, message=f"Unknown marketplace '{key}'.")
        try:
            return provider.publish(_ctx(own_listing=True),
                                    provider.creds_for(uid))
        except HTTPException as exc:
            return PublishOutcome(ok=False, message=str(exc.detail))
        except Exception as exc:  # noqa: BLE001 - isolate marketplace failures
            log.warning("%s publish crashed: session=%s: %s",
                        key, req.session_id, exc)
            return PublishOutcome(
                ok=False, message=f"{provider.label} publish failed: {exc}")

    # Fan out CONCURRENTLY: each marketplace is its own network pipeline (eBay
    # ingests photo URLs, Etsy uploads photo bytes), so a multi-marketplace
    # publish costs the slowest one instead of the sum. Failure isolation is
    # per-provider inside _publish_one; the same-listing duplicate guard stays
    # intact (only the eBay provider takes it, per listing, not per thread).
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        outcomes = dict(zip(targets, pool.map(_publish_one, targets)))

    # Fold every outcome into the record under the row lock. The read and the
    # write have to be one critical section: providers (eBay especially) write
    # the same row from inside publish, including from a background thread that
    # can still be running here — a plain re-read-then-upsert loses whichever
    # side commits first. (Concurrent providers make that race likelier, not
    # rarer: each one is writing its own marketplace's state at the same time.)
    top = marketplace_state.derive_top_status(
        prev_rec.get("status") or "", outcomes, req.mode)

    def _fold(data: dict) -> dict:
        for key, outcome in outcomes.items():
            marketplace_state.merge_state(data, key, outcome)
        return data

    data = db.mutate_listing_data(req.session_id, _fold, status=top, user_id=uid)
    if data is None:
        # No row yet (or no DB): fall back to creating one from the request.
        data = _fold(req.listing.model_dump())
        db.upsert_listing(req.session_id, data, status=top, user_id=uid)

    live = [k for k, o in outcomes.items() if o.ok and o.status == "published"]
    failed = [k for k, o in outcomes.items() if not o.ok]
    dry = [k for k, o in outcomes.items() if o.dry_run]

    def _label(key: str) -> str:
        p = marketplaces.get(key)
        return p.label if p else key

    def _names(keys: list[str]) -> str:
        labels = [_label(k) for k in keys]
        return " and ".join(part for part in
                            [", ".join(labels[:-1]), labels[-1]] if part)

    if live:
        message = f"Live on {_names(live)}."
        if failed:
            message += f" {_names(failed)} didn't make it — details below."
    elif failed:
        message = (f"{_names(failed)} rejected the listing — "
                   "fix the issues below and try again.")
    elif req.mode == "draft":
        message = "Draft saved."
    elif dry:
        message = f"Dry run only — connect {_names(dry)} to post for real."
    else:
        message = "Nothing was published."

    return JSONResponse({
        "multi": True,
        "mode": req.mode,
        "published": bool(live),
        "message": message,
        "results": {
            key: {
                "ok": o.ok,
                "published": o.ok and o.status == "published",
                "dry_run": o.dry_run,
                "listing_id": o.listing_id,
                "url": o.url,
                "message": o.message,
                "issues": o.issues,
                # Only when true, like the two below it: a marketplace that
                # refused the listing says nothing here, and the clients read
                # its absence as "this outcome is known". What it means when
                # present is in PublishOutcome — the seller must check that
                # marketplace before publishing again, not fix a field.
                **({"outcome_unknown": True} if o.outcome_unknown else {}),
                **({"promote_status": o.raw["promote_status"]}
                   if o.raw.get("promote_status") else {}),
                **({"record_warning": o.raw["record_warning"]}
                   if o.raw.get("record_warning") else {}),
            } for key, o in outcomes.items()
        },
    })


@app.post("/api/ebay/end-listing")
def end_listing(req: SessionOnlyRequest, request: Request) -> dict:
    """End (withdraw) this session's live eBay listing. The listing stays in
    the app as status 'ended' so it can be edited and relisted later."""
    rec = db.get_listing(req.session_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    creds = _ebay_creds_for(request)
    # Ending goes through EndItem, which needs the seller's own token. The
    # env-configured single-tenant credentials used to serve here via
    # withdrawOffer; they are the OPERATOR's, and with the Inventory engine
    # gone there is nothing they could end that this app created.
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    listing = Listing(**(rec.get("listing") or {}))
    # Never end another eBay account's listing: the item id on this record was
    # minted by a store that isn't connected any more, and EndItem would either
    # fail confusingly or (worse) act on the wrong seller's item.
    #
    # named_account_of, not account_of: a record whose owner could not be named
    # carries the UNKNOWN_ACCOUNT sentinel, and comparing that as if it were a
    # username refuses the ending with "this is on your other eBay account
    # (@previous account)" — a store that does not exist and cannot be
    # reconnected, leaving the listing un-endable here for good. Only a NAMED
    # other account is evidence of a different store. The publish path already
    # draws the line here (marketplaces/ebay_provider); this one did not.
    owner = listing_sync.named_account_of(listing)
    connected = (creds or {}).get("ebay_username", "")
    if creds and owner and connected and owner != connected:
        raise HTTPException(
            400, f"This listing is on your other eBay account (@{owner}) — "
                 f"you're connected as @{connected}. Reconnect that account "
                 "to end it.")
    try:
        # One ending for every listing: EndItem. The old branch sent
        # non-imported records to withdrawOffer, but everything this app
        # publishes goes out through Trading and is stamped source="ebay", so
        # that branch only ever served Inventory-API listings from an older
        # build. A record with no item id has nothing on eBay to end.
        if not listing.ebay_listing_id:
            res = {"ended": False, "not_live": True,
                   "message": "This listing isn't on eBay — nothing to end."}
        else:
            res = listing_sync.end(creds["access_token"], listing)
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    if res.get("ended") or res.get("not_live"):
        # Ending can discover the listing already finished on eBay — and how.
        # A sale files it under Sold (with the same notification + storage
        # reclaim as every other sold path); anything else lands in Inactive.
        new_status = "sold" if res.get("status") == "sold" else "ended"
        data = rec.get("listing") or {}
        if new_status == "sold" and creds:
            # Record the amount it actually went for, not the asking price.
            sales = listing_sync.recent_sales(creds["access_token"])
            data = listing_sync.stamp_sale(
                data, sales.get(str(data.get("ebay_listing_id") or "")),
                mark_now=True)
        # Checked, because the branch below DELETES the photos. Same rule as
        # the merge: nothing is destroyed for a status change that did not
        # happen, or the record goes on saying the listing is live with
        # nothing left to edit or relist it from.
        landed = db.upsert_listing(req.session_id, data,
                                   status=new_status, user_id=_uid(request))
        if db.enabled() and not landed:
            log.error("end-listing: eBay ended %s but the status write failed "
                      "— photos kept", req.session_id)
            raise errors.StorageUnavailable(
                "It came off eBay, but we couldn't update your copy here. "
                "Refresh in a moment — don't end it again.")
        if new_status == "sold" and rec.get("status") != "sold":
            notifications.notify_sold(
                _uid(request) or rec.get("user_id"), req.session_id, data,
                sold_quantity=data.get("sold_quantity") or 0)
            _purge_session_images_best_effort(req.session_id)
        res = {**res, "status": new_status}
    return res


# How many still-live listings one status sweep re-checks. Each is its own
# eBay round trip, so this is the per-run share of the account's daily quota
# the background sweep is allowed to spend; the rest of the store is covered
# by later syncs, because the sample is random rather than the first N.
SWEEP_SAMPLE = int(os.getenv("EBAY_SWEEP_SAMPLE", "100") or "100")


@app.post("/api/ebay/sync-listings")
def sync_listings(request: Request, payload: Optional[dict] = None) -> dict:
    """Reconcile our 'live' listings with eBay: a sold item is auto-archived
    (status 'sold', its photos purged to reclaim storage), a listing that
    otherwise disappeared flips to 'ended', and missing eBay item ids are
    backfilled. Definitive answers only — an API blip changes nothing.

    `force` (body) runs the full sweep — that's the manual "Sync store"
    button. Without it the per-item sweeps are rate-limited per account (see
    _SWEEP_COOLDOWN); the cheap finished-list reconcile always runs, so an
    item that ended or sold on eBay still moves on the very next sync.
    """
    creds = _ebay_creds_for(request)
    user = auth.current_user(request)
    if not (creds or config.ebay_ready()) or not user:
        return {"checked": 0, "changed": 0}
    force = bool((payload or {}).get("force"))
    # Only the connected account's listings. A record left behind by a
    # previously-connected eBay account is another seller's item as far as this
    # token is concerned — and since eBay answers item lookups for anyone, a
    # sweep over it doesn't fail, it just keeps reporting the old store as
    # live and healthy under the new account.
    # The whole creds bundle, not just the username: ownership is decided on
    # eBay's immutable account id where the record carries one, and falls back
    # to the name only for records too old to have it (listing_sync.owns).
    account = creds or {}
    # Live listings, asked for as live listings. This used to read the newest
    # LIST_CAP records of ANY kind and drop the rest here -- so on a store
    # whose recent records are mostly drafts, the older live listings fell off
    # the end and never reached the sweep at all, for as long as the store
    # stayed that shape. A sale or an ending on eBay went unnoticed here
    # indefinitely. The filter moves the boundary from "the newest 3,000
    # records" to "the first 3,000 LIVE ones", which for any real store is all
    # of them -- and `capped` now says the truer thing.
    #
    # One row past the cap, so the answer can tell a sweep that covered the
    # whole store from one that could not even READ it: `partial` was computed
    # from the sample alone, so a capped sync reported itself complete.
    rows = db.list_listings(limit=LIST_CAP + 1, user_id=user["id"],
                            statuses=("published", "live"))
    capped = len(rows) > LIST_CAP
    live = [i for i in rows[:LIST_CAP]
            if listing_sync.owns(i.get("listing") or {}, account)]
    changed = 0
    # First, the cheap sweep that scales to any store: eBay's own sold/unsold
    # lists name every item that finished recently, so a listing that ended
    # (or sold) ON eBay moves off Active on this very sync — it never has to
    # wait for its turn under the per-item caps below.
    handled: set[str] = set()
    if live and creds:
        try:
            got, handled = listing_sync.reconcile_recent(
                creds["access_token"], user["id"], live, account=account)
            changed += got
        except Exception as exc:  # noqa: BLE001 - sync is best-effort
            log.info("ebay sync: finished-list reconcile failed: %s", exc)
    live = [i for i in live if i["id"] not in handled]
    # One sweep for every record. The old code split these in two: imported
    # listings went through the Trading API, and "app-created" ones through an
    # Inventory offer lookup keyed by SKU. That second path only ever served
    # listings the Inventory engine published, and it is gone — everything this
    # app puts live now goes out through Trading and carries an item id.
    #
    # Capped so one sync click can't fan out into hundreds of eBay calls —
    # SAMPLED, not sliced, so on a store bigger than the cap every record still
    # gets its turn across a few syncs instead of the same first N being
    # re-checked forever.
    #
    # Ask for the cooldown only when there is actually something to sweep.
    # sweep_due() STARTS the cooldown on the call that says yes, so asking
    # first and finding nothing to do spent the whole six hours on zero eBay
    # calls — and the next sync, the one with real work, was refused. The
    # cooldown exists to ration eBay's daily quota, so it should only ever be
    # consumed by a run that spends some of it.
    if live and not sync_guard.sweep_due(user["id"], force):
        # Cooled down: the finished-list reconcile above already ran (and is
        # what actually moves ended/sold records), so skip the per-item sweeps
        # rather than spending the account's daily eBay quota on a background
        # poll.
        log.debug("ebay sync: per-item sweeps skipped (cooldown) for user=%s",
                  user["id"])
        live = []
    # A sweep is one eBay call per listing, so a big store is deliberately
    # only sampled. Randomly, so every listing gets its turn across successive
    # syncs rather than the same first 100 being re-checked forever.
    #
    # The count is REPORTED rather than left implicit. This is the pass a
    # "full sync" appears to run, and answering only `checked` let a caller
    # read partial coverage as complete -- so the response now says how many
    # were eligible and whether it covered all of them. Nothing may describe
    # this as a full sync while `partial` is true.
    eligible = len(live)
    if eligible > SWEEP_SAMPLE:
        live = random.sample(live, SWEEP_SAMPLE)
    if live and creds:
        try:
            changed += listing_sync.refresh_statuses(
                creds["access_token"], user["id"], live, account=account)
        except Exception as exc:  # noqa: BLE001 - sync is best-effort
            log.info("ebay sync: status refresh failed: %s", exc)
    if changed:
        log.info("ebay sync: %d listing(s) updated for user=%s",
                 changed, user["id"])
    # `archived` used to ride along here, counted by the per-item Inventory
    # loop that no longer exists. refresh_statuses does the archiving now and
    # reports only a change count, and nothing read the field — the frontend
    # uses `changed` alone — so it is gone rather than reported as a constant 0.
    return {"checked": len(live) + len(handled), "changed": changed,
            # What the sweep COULD have covered, and whether it did. `checked`
            # alone reads as "that is the whole store" on a store where it is
            # a sample of it.
            "eligible": eligible + len(handled),
            # True when the sweep did not cover everything, for EITHER reason:
            # it sampled, or the store is bigger than one read of it. Nothing
            # may describe this as a full sync while it is set, and `eligible`
            # is what the read could see rather than what the seller has --
            # a number this route cannot know without counting the store.
            "partial": eligible > len(live) or capped,
            "sample_size": SWEEP_SAMPLE}


# Bounds one import run. A store bigger than this imports across repeated
# syncs rather than tying up a single request indefinitely.
IMPORT_LIMIT = listing_sync.ACTIVE_LIMIT


# The store mirror runs as a background job, one per account at a time.
# user id -> job id of the import currently running for them.
_IMPORT_JOBS: dict[str, str] = {}
_IMPORT_LOCK = threading.Lock()


def _run_import_job(job_id: str, token: str, uid: str,
                    account: "str | dict" = "") -> None:
    """Background worker for the store mirror. One GetItem per listing means a
    real store takes minutes — far longer than a browser (or the proxy in
    front of us) will hold a request open, which is why this is a job the
    client polls rather than the response to the POST."""
    # Every update writes the job's status mirror to disk, and a 2500-listing
    # store ticks twice per listing — so the count is published on a beat
    # rather than on every item. A phase change and the last tick of a phase
    # always get through, so the client never sits on a stale "142 of 380".
    beat = {"at": 0.0, "phase": ""}

    def _progress(phase: str, done: int, total: int) -> None:
        now = time.monotonic()
        if phase == beat["phase"] and done < total and now - beat["at"] < 0.5:
            return
        beat.update(at=now, phase=phase)
        jobstore.update(job_id, phase=phase, current=done, total_items=total)
    try:
        result = listing_sync.import_active(
            token, uid, limit=IMPORT_LIMIT, on_progress=_progress,
            account=account)
        jobstore.update(job_id, done=True, phase="done", error=None, **result)
    except ebay_trading.TradingError as exc:
        jobstore.update(job_id, done=True, phase="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface a clear reason
        # The TradingError arm above carries eBay's own mapped sentence. This
        # one is whatever was thrown, and for an httpx failure that is the API
        # base, the path and a status line — rendered in the import panel.
        reference = _support_reference()
        log.warning("import-listings failed for user=%s [%s]: %s",
                    uid, reference, exc)
        jobstore.update(job_id, done=True, phase="failed", error=(
            "We couldn't import your eBay listings just now. Try again in a "
            f"moment — if it keeps happening, quote {reference} to support."))
    finally:
        with _IMPORT_LOCK:
            if _IMPORT_JOBS.get(uid) == job_id:
                _IMPORT_JOBS.pop(uid, None)


@app.post("/api/ebay/import-listings")
def import_listings(request: Request) -> dict:
    """Start pulling the seller's ENTIRE active eBay store into the app.

    The Inventory API only knows about listings this app published, so listings
    created on eBay directly (or with another tool) are fetched through the
    Trading API instead. Imported listings become normal records the seller can
    open, edit, and push back — see services/listing_sync.

    Returns {"job_id"} immediately; poll /api/ebay/import-status/{job_id} for
    progress and the final counts. A second call while one is still running
    hands back the SAME job rather than starting a parallel import — two tabs
    (or a reload mid-sync) would otherwise double the eBay calls this spends.
    """
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    if not creds:
        raise HTTPException(400, "Connect eBay first — Settings → Connect eBay.")
    if not db.enabled():
        raise HTTPException(503, "No database configured — imported listings need "
                                 "DATABASE_URL set.")
    uid = user["id"]
    with _IMPORT_LOCK:
        running = _IMPORT_JOBS.get(uid)
        if running:
            snap = jobstore.snapshot(running, uid)
            if snap and not snap.get("done"):
                return {"job_id": running, "running": True}
            _IMPORT_JOBS.pop(uid, None)
        job_id = storage.new_session_id()
        _IMPORT_JOBS[uid] = job_id
    jobstore.register(job_id, {
        "id": job_id, "kind": "ebay-import", "phase": "listing", "done": False,
        "error": None, "current": 0, "total_items": 0,
    }, uid=uid)
    threading.Thread(
        target=_run_import_job,
        # The whole creds bundle, not just the username — the same argument the
        # status sweep passes, and for the same reason. Ownership is decided on
        # eBay's immutable account id wherever the record carries one
        # (listing_sync.owns), and every listing this app publishes carries it.
        # Handed a name alone, the sync matched NONE of its own records, so it
        # re-imported each of them as an "ebay-<item>" mirror on every run —
        # the duplicate pairs, one Thryft and one eBay, of the same listing.
        args=(job_id, creds["access_token"], uid, creds),
        daemon=True,
    ).start()
    log.info("import-listings %s: started for user=%s", job_id, uid)
    return {"job_id": job_id, "running": True}


@app.get("/api/ebay/import-status/{job_id}")
def import_status(job_id: str, request: Request) -> Response:
    """A store import's live progress, or the mirrored record of how it ended
    (see services/jobstore) — an import cut short by a restart reports itself
    done with the reason, so the client settles instead of polling forever."""
    body = jobstore.snapshot_json(job_id, _uid(request))
    if body is None:
        raise HTTPException(404, "Unknown import job.")
    return Response(content=body, media_type="application/json")


# --- notifications ----------------------------------------------------------

@app.get("/api/notifications")
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
    uid = _uid(request)
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


@app.post("/api/notifications/read")
def notifications_mark_read(request: Request, payload: dict) -> dict:
    """Mark notifications read: {"ids": [...]} for specific ones, or
    {"all": true} for everything unread."""
    uid = _uid(request)
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

@app.get("/api/messages")
def messages_list(request: Request, marketplace: str = "",
                  limit: int = 25) -> dict:
    """The merged inbox: {conversations, unread, sources, available, reason}.

    ALWAYS 200, never raises. A header icon polls this every minute, and the
    smoke test fails the build on any failed request — so an eBay outage has
    to read as an empty inbox that explains itself, not as a 502 storm.
    `sources` drives the marketplace toggle and is populated even when a
    source has nothing to give.
    """
    uid = _uid(request)
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


@app.get("/api/messages/{conversation_id}")
def messages_thread(conversation_id: str, request: Request,
                    limit: int = 50) -> dict:
    """One conversation: {conversation, messages} oldest-first.

    User-initiated, so this one fails honestly rather than soft-emptying.
    """
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    try:
        return messages_service.get_conversation(
            uid, conversation_id, limit=max(1, min(limit, 200)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - adapter errors carry the reason
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/messages/send")
def messages_send(request: Request, payload: dict) -> dict:
    """Reply into a conversation: {"conversation_id": ..., "text": ...}.

    Returns the refreshed thread, so the client renders what the marketplace
    actually stored rather than the optimistic bubble it drew.
    """
    uid = _uid(request)
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


@app.post("/api/messages/read")
def messages_mark_read(request: Request, payload: dict) -> dict:
    """Mark one conversation read. Best-effort: the badge re-syncs on the next
    poll, so a marketplace that refuses this never becomes an error the seller
    has to look at."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    cid = str(payload.get("conversation_id") or "").strip()
    return {"ok": bool(cid) and messages_service.mark_read(uid, cid)}



# --- sold orders + shipping labels ------------------------------------------

def _orders_creds(request: Request) -> dict:
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first — Settings → Connect eBay.")
    return creds


def _listing_map_by_item_id(uid: str) -> dict:
    """{ebay item id: (our record id, package fields)} from the user's listing
    records, so order exports/quotes can pre-fill the weight the seller
    already entered and link an order back to its listing."""
    out = {}
    # Best effort: this only pre-fills a weight the seller can type, so an
    # unreadable store costs them a field, not a wrong answer.
    for rec in db.list_listings_best_effort(limit=LIST_CAP, user_id=uid):
        listing = rec.get("listing") or {}
        item_id = str(listing.get("ebay_listing_id") or "")
        if not item_id:
            continue
        pkg = {k: listing.get(f"package_{k}") or 0
               for k in ("weight_lb", "weight_oz", "length_in", "width_in",
                         "height_in")}
        out[item_id] = (rec["id"], pkg if any(pkg.values()) else None)
    return out


def _attach_packages(uid: str, orders: list[dict]) -> list[dict]:
    """Ride each order with the matching listing's package + our record id."""
    by_item = _listing_map_by_item_id(uid)
    for order in orders:
        for li in order.get("line_items") or []:
            rid, pkg = by_item.get(li.get("legacy_item_id") or "", (None, None))
            if pkg and "package" not in order:
                order["package"] = pkg
            if rid and "listing_record_id" not in order:
                order["listing_record_id"] = rid
    return orders


@app.get("/api/ebay/orders")
def ebay_orders_awaiting(request: Request) -> dict:
    """Orders still waiting to ship, with ship-to addresses and (when the
    matching listing recorded one) the package weight/dims pre-filled."""
    creds = _orders_creds(request)
    try:
        page = ebay_orders.awaiting_page(creds["access_token"])
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    # `total` and `partial` ride along because this is the list a seller reads
    # to decide what still has to be packed: a page of 50 out of 80 read as
    # the whole pile leaves thirty orders unshipped, and eBay scores late
    # dispatch.
    return {"orders": _attach_packages(creds["_uid"], page["orders"]),
            "total": page["total"], "partial": page["partial"]}


@app.get("/api/ebay/orders/for-listing/{listing_id}")
def ebay_order_for_listing(listing_id: str, request: Request) -> dict:
    """The awaiting-shipment order for one of OUR listing records (matched by
    its eBay item id) — how a sold notification jumps straight to shipping.
    {"order": null} when it's already shipped or not found."""
    creds = _orders_creds(request)
    rec = db.get_listing(listing_id)
    if not rec or (rec.get("user_id") and rec["user_id"] != creds["_uid"]):
        raise HTTPException(404, "Listing not found")
    item_id = str((rec.get("listing") or {}).get("ebay_listing_id") or "")
    if not item_id:
        return {"order": None}
    try:
        order = ebay_orders.order_for_item(creds["access_token"], item_id)
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    if order:
        _attach_packages(creds["_uid"], [order])
    return {"order": order}


@app.post("/api/ebay/shipping-quote")
def ebay_shipping_quote(request: Request, payload: dict) -> dict:
    """Live eBay label rates for one order: {order_id, package{weight_lb,
    weight_oz, length_in, width_in, height_in}, ship_from{...}}. The ship-from
    address is remembered in prefs so it's a one-time entry."""
    creds = _orders_creds(request)
    order_id = str(payload.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(400, "No order id given.")
    ship_from = dict(payload.get("ship_from") or {})
    # Best-effort: the remembered address only FILLS GAPS in the one the
    # caller sent, so an unreadable row costs a re-typed address rather than
    # a refused quote. What it must not do is silently quote from a
    # half-filled address, which is why the caller's own fields win and the
    # postal code falls back to the account's.
    saved = db.get_prefs_best_effort(creds["_uid"]).get("ship_from") or {}
    for key, val in saved.items():  # payload wins; saved fills the gaps
        ship_from.setdefault(key, val)
    ship_from.setdefault("postal_code", creds.get("ship_from_postal") or "")
    if payload.get("ship_from"):
        # Remembering it is a convenience on the way to the quote the seller
        # actually asked for; failing to remember must not lose them the
        # quote. They re-type it next time, which is the old behaviour.
        try:
            db.save_prefs(creds["_uid"], {"ship_from": ship_from})
        except errors.StorageUnavailable as exc:
            log.info("shipping quote: couldn't remember the ship-from "
                     "address: %s", exc)
    try:
        order = ebay_orders.get_order(creds["access_token"], order_id)
        quote = ebay_orders.create_shipping_quote(
            creds["access_token"], order, payload.get("package") or {}, ship_from)
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    return quote


@app.post("/api/ebay/shipping-label")
def ebay_shipping_label(request: Request, payload: dict) -> dict:
    """Buy the chosen rate: {shipping_quote_id, rate_id}. eBay generates the
    label (returned as a download URL + our proxy path) and uploads the
    tracking number to the order itself."""
    creds = _orders_creds(request)
    try:
        res = ebay_orders.purchase_label(
            creds["access_token"],
            str(payload.get("shipping_quote_id") or ""),
            str(payload.get("rate_id") or ""))
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    if res.get("shipment_id"):
        res["download_path"] = f"/api/ebay/shipping-label/{res['shipment_id']}"
    return res


@app.get("/api/ebay/shipping-label/{shipment_id}")
def ebay_shipping_label_download(shipment_id: str, request: Request):
    """Proxy the purchased label PDF so the browser can just open/print it
    (the raw eBay URL needs the API auth header a browser can't send)."""
    creds = _orders_creds(request)
    try:
        pdf = ebay_orders.download_label(creds["access_token"], shipment_id)
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition":
            f'inline; filename="ebay-label-{shipment_id[:24]}.pdf"'})


@app.post("/api/ebay/mark-shipped")
def ebay_mark_shipped(request: Request, payload: dict) -> dict:
    """Attach an outside tracking number (e.g. from a Pirate Ship label) to an
    order: {order_id, tracking_number, carrier}. Flips the order to shipped on
    eBay and emails the buyer."""
    creds = _orders_creds(request)
    try:
        return ebay_orders.mark_shipped(
            creds["access_token"],
            str(payload.get("order_id") or "").strip(),
            str(payload.get("tracking_number") or "").strip(),
            str(payload.get("carrier") or "USPS").strip())
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/shipping/pirateship.csv")
def pirate_ship_export(request: Request, order_id: str = ""):
    """Awaiting-shipment orders as a Pirate Ship-importable CSV (recipient
    address + per-row weight/dims from the matching listings). Upload it at
    pirateship.com → Ship → Import, buy the labels there, then paste each
    tracking number back via mark-shipped. `order_id` narrows it to one."""
    creds = _orders_creds(request)
    try:
        orders = ebay_orders.awaiting_shipment(creds["access_token"])
    except ebay_orders.OrdersError as exc:
        raise HTTPException(502, str(exc)) from exc
    if order_id:
        orders = [o for o in orders if o["order_id"] == order_id]
        if not orders:
            raise HTTPException(404, "That order isn't awaiting shipment.")
    by_item = _listing_map_by_item_id(creds["_uid"])
    packages = {}
    for order in orders:
        for li in order.get("line_items") or []:
            _rid, pkg = by_item.get(li.get("legacy_item_id") or "", (None, None))
            if pkg:
                packages[order["order_id"]] = pkg
                break
    csv_text = ebay_orders.pirate_ship_csv(orders, packages)
    return Response(content=csv_text, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="pirate-ship-orders.csv"'})


def _file_etag(st: os.stat_result) -> str:
    """The same ETag Starlette's FileResponse sends, computed up front so the
    304 check below compares against exactly what the browser was given."""
    base = f"{st.st_mtime}-{st.st_size}"
    return f'"{hashlib.md5(base.encode(), usedforsecurity=False).hexdigest()}"'


def _is_unmodified(request: Optional[Request], etag: str, mtime: float) -> bool:
    """Whether the client already holds this exact file.

    /media is served no-cache (see _cache_headers), so browsers revalidate on
    every view. Starlette sets ETag and Last-Modified on a FileResponse but
    never answers a conditional request with a 304 — without this, "always
    revalidate" would mean "always re-download every photo", which on a
    24-photo listing is the wrong trade. With it, an unchanged photo costs a
    header round trip and no body.
    """
    if request is None:
        return False
    if_none_match = request.headers.get("if-none-match", "")
    if if_none_match:
        # A list, and possibly weak validators ("W/" prefixed) — a match on
        # any entry means the client is current.
        return any(tag.strip().removeprefix("W/") == etag
                   for tag in if_none_match.split(","))
    since = request.headers.get("if-modified-since", "")
    if since:
        try:
            # Whole seconds on both sides: HTTP dates have no sub-second
            # precision, so a raw float mtime always looks newer.
            return int(mtime) <= int(parsedate_to_datetime(since).timestamp())
        except (TypeError, ValueError):
            return False
    return False


@app.get("/media/{session_id}/optimized/{name}")
def media(session_id: str, name: str, request: Request = None, v: str = ""):
    opt_dir = storage.optimized_path(session_id).resolve()  # read-only: no mkdir
    path = (opt_dir / name).resolve()
    # Guard against path traversal in `name` (e.g. "../../etc/passwd").
    if opt_dir not in path.parents:
        raise HTTPException(404, "Not found")
    if path.is_file():
        st = path.stat()
        etag = _file_etag(st)
        if _is_unmodified(request, etag, st.st_mtime):
            return Response(status_code=304, headers={
                "etag": etag,
                "last-modified": formatdate(st.st_mtime, usegmt=True),
            })
        return FileResponse(path)
    # Local file gone (e.g. freed by the R2 offload) — fall back to R2.
    if objstore.enabled():
        key = objstore.key_for(session_id, name)
        if config.r2_public_urls():
            # Carry the client's cache-bust version onto the public URL,
            # otherwise the CDN can keep serving a pre-edit copy (e.g. a
            # photo rotated after upload).
            url = objstore.public_url(key)
            safe_v = "".join(c for c in v if c.isalnum())[:24]
            if safe_v:
                url += ("&" if "?" in url else "?") + "v=" + safe_v
            return RedirectResponse(url)
        # Presigned mode: never bolt extra params onto the URL (they'd break
        # the signature), and cap how long browsers may cache the redirect —
        # the default /media policy (1h) could outlive the signature and
        # strand clients on an expired URL.
        url = objstore.url_for(key, expires=3600)
        if url:
            return RedirectResponse(
                url, headers={"Cache-Control": "private, max-age=300"})
        # Inside this branch, None means the PRESIGN failed — the object store
        # is configured, so "there is nothing here" is not what happened. 404
        # is a claim about the photo, and the caller that acts hardest on it is
        # eBay: this URL is what a publish hands over as <PictureURL>, and its
        # ingestion reads 404 as "no such photo" and drops or rejects it, where
        # a 5xx is something it comes back for.
        log.warning("media: couldn't sign a URL for %s", key)
        raise HTTPException(
            503, "That photo is temporarily unavailable — try again shortly.")
    raise HTTPException(404, "Not found")


# Clean URLs for the static policy pages (StaticFiles only serves them under
# their exact .html filenames). These are linked from eBay's app settings, the
# App Store listing, and — since a native app has no address bar — from inside
# the app's own Settings screen.
@app.get("/privacy-policy")
def privacy_policy():
    return FileResponse(FRONTEND_DIR / "privacy-policy.html")


@app.get("/terms")
def terms():
    return FileResponse(FRONTEND_DIR / "terms.html")


@app.get("/about")
def about():
    return FileResponse(FRONTEND_DIR / "about.html")


# --- marketplaces (generic connect/status plumbing) ------------------------
# Registered AFTER every literal /api/ebay/* route on purpose: FastAPI matches
# in registration order, so eBay keeps its original handlers (with their
# account-preserving policy logic) and the {marketplace} patterns serve
# everything registered later (Etsy, Depop, ...).


@app.get("/api/marketplaces")
def marketplace_roster(request: Request) -> dict:
    """Every registered marketplace + this user's connection state — drives
    the Settings connection cards and the publish-target chips in one call."""
    uid = _uid(request)
    out = []
    for p in marketplaces.all_providers():
        status = p.account_status(uid)
        # "Coming soon": integration built, access still pending on the
        # marketplace's side (Depop's partner review). Self-clears once the
        # credentials land, so the UI needs no second deploy.
        soon, soon_note = marketplaces.coming_soon(p)
        # The other kind of "not yet": configured and working, but the
        # marketplace itself won't let THIS seller authorize it (Etsy's
        # seller-app wall). Per-user, so it can't be folded into coming_soon.
        pending, pending_note = marketplaces.access_pending(p, uid)
        out.append({
            "key": p.key,
            "label": p.label,
            "oauth_ready": p.oauth_ready(),
            "oauth_missing": p.oauth_missing(),
            "coming_soon": soon,
            "coming_soon_note": soon_note,
            "access_pending": pending,
            "access_pending_note": pending_note,
            "connected": bool(status.get("connected")),
            "username": status.get("username", ""),
            "env": status.get("env", "production"),
            "supports": p.supports(),
        })
    return {"marketplaces": out}


# Etsy-specific: the Settings pickers for the shop's shipping profiles and
# return policies (Etsy's analog of /api/ebay/policies), and the AI category
# suggestion. Literal paths, so they must sit above the {marketplace} routes.
@app.get("/api/etsy/settings-options")
def etsy_settings_options(request: Request) -> dict:
    provider = marketplaces.get("etsy")
    creds = provider.creds_for(_uid(request))
    if not creds:
        raise HTTPException(400, "Connect Etsy first.")
    try:
        profiles = etsy_auth.list_shipping_profiles(
            creds["access_token"], creds["shop_id"])
        policies = etsy_auth.list_return_policies(
            creds["access_token"], creds["shop_id"])
    except Exception as exc:  # noqa: BLE001
        # `str(exc)` here is httpx's, so it carries the Etsy API base, the
        # path and the seller's own shop_id. Same rule as the eBay lookups.
        raise _lookup_failed("load your Etsy shop's shipping and return "
                             "options", exc) from exc
    settings = creds.get("settings") or {}
    return {
        "shipping_profiles": profiles,
        "return_policies": policies,
        "selected": {
            "shipping_profile_id": str(settings.get("shipping_profile_id") or ""),
            "return_policy_id": str(settings.get("return_policy_id") or ""),
        },
    }


@app.post("/api/etsy/settings-options")
def save_etsy_settings_options(request: Request, payload: dict) -> dict:
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    fields = {k: str(payload.get(k) or "")
              for k in ("shipping_profile_id", "return_policy_id")
              if k in payload}
    if not fields:
        raise HTTPException(400, "No settings provided.")
    # The answer is a claim about a write. P0-06's rule, on a route that kept
    # its own copy of the old behaviour: a save that did not land must not
    # come back as `{"ok": true}`, or the seller closes Settings believing
    # their shipping profile is chosen and every Etsy publish goes out with
    # the old one.
    if not db.save_marketplace_account(uid, "etsy", settings=fields):
        raise errors.StorageUnavailable(
            "Couldn't save your Etsy settings just now. Try again in a "
            "moment.")
    return {"ok": True, "selected": fields}


@app.post("/api/etsy/suggest-taxonomy/{session_id}")
def etsy_suggest_taxonomy(session_id: str, request: Request, payload: dict) -> dict:
    """Best Etsy category for this listing: cheap keyword shortlist over the
    cached seller taxonomy, then one small Claude pick."""
    if not config.etsy_oauth_ready():
        raise HTTPException(400, "Etsy isn't configured on the server.")
    _assert_session_owner(session_id, request)
    listing = Listing(**(payload.get("listing") or {}))
    try:
        return etsy_service.suggest_taxonomy(listing)
    except Exception as exc:  # noqa: BLE001
        raise _lookup_failed("work out an Etsy category for this listing",
                             exc) from exc


def _flow_cookie(marketplace: str) -> str:
    return f"{marketplace}_oauth_flow"


def _marketplace_or_404(marketplace: str):
    provider = marketplaces.get(marketplace)
    if provider is None:
        raise HTTPException(404, "Unknown marketplace")
    return provider


@app.get("/api/{marketplace}/connect")
def marketplace_connect(marketplace: str, request: Request,
                        ticket: str = "", native: str = "",
                        return_to: str = ""):
    provider = _marketplace_or_404(marketplace)
    if not provider.oauth_ready():
        raise HTTPException(400, f"{provider.label} OAuth not configured "
                                 f"(set {', '.join(provider.oauth_missing())}).")
    uid = _connect_uid(request, ticket)
    if not uid:
        raise HTTPException(401, f"Log in before connecting {provider.label}.")
    # Refuse before the redirect, not after: a marketplace that only lets
    # certain accounts authorize it (Etsy, pre-Commercial Access) says so on
    # its OWN page, off-site, with no callback to us — so a seller sent there
    # lands on a dead end we never hear about and cannot explain.
    #
    # Back into the app rather than a raw 403: the Settings button is already
    # disabled for these sellers, so the way to arrive here is a roster
    # fetched before the gate went up — and stranding them on an error page is
    # the exact thing the gate exists to prevent.
    pending, _ = marketplaces.access_pending(provider, uid)
    if pending:
        return _finish_connect(request, f"/?connect_pending={marketplace}")
    # Etsy and Depop match redirect_uri exactly, so — like eBay's RuName — the
    # callback comes back to one origin. Start the flow on it, not here.
    offsite = _offsite_connect(request, uid, f"/api/{marketplace}/connect", native)
    if offsite is not None:
        return offsite
    import secrets as _secrets
    nonce = _secrets.token_urlsafe(24)
    url, flow = provider.authorize_url(auth.make_state(uid, nonce))
    resp = RedirectResponse(url)
    # Bind the flow to this browser, exactly like the eBay nonce cookie: the
    # callback requires the nonce here to match the one in the signed state
    # (CSRF protection). Flow secrets (e.g. Etsy's PKCE code_verifier) ride
    # along — httponly, 10 minutes, this browser only.
    resp.set_cookie(_flow_cookie(marketplace),
                    json.dumps({"nonce": nonce, **flow}),
                    max_age=600, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")
    _mark_native_flow(resp, request, native)
    _mark_return_origin(resp, request, return_to)
    return resp


@app.get("/api/{marketplace}/callback")
def marketplace_callback(marketplace: str, request: Request,
                         code: str = "", state: str = ""):
    provider = _marketplace_or_404(marketplace)
    verified = auth.verify_state(state)
    if not code or not verified:
        return _finish_connect(request, f"/?connect_error={marketplace}")
    uid, nonce = verified
    try:
        flow = json.loads(request.cookies.get(_flow_cookie(marketplace), "") or "{}")
    except ValueError:
        flow = {}
    # The nonce in the signed state must match the cookie set at connect time,
    # so a callback can only bind an account to the browser that started the
    # flow (blocks CSRF authorization-code injection).
    if not flow.get("nonce") or flow.get("nonce") != nonce:
        log.warning("%s callback: nonce mismatch (uid=%s)", marketplace, uid)
        return _finish_connect(request, f"/?connect_error={marketplace}")
    try:
        fields = provider.exchange_code(code, flow)
    except Exception as exc:  # noqa: BLE001 - the redirect is the error surface
        log.warning("%s connect failed (uid=%s): %s", marketplace, uid, exc)
        return _finish_connect(request, f"/?connect_error={marketplace}")
    # Same reasoning as the eBay callback: the marketplace authorised, so the
    # grant is real and the seller has no reason to doubt a screen that says
    # "connected" -- while every later publish fails on it. A connection that
    # did not commit is a failed connect, and this is the one moment the
    # seller is still here to retry it.
    if not db.save_marketplace_account(uid, marketplace, **fields):
        log.warning("%s connect authorised but did not persist (uid=%s)",
                    marketplace, uid)
        return _finish_connect(request, f"/?connect_error={marketplace}")
    # Connecting a different account of the same marketplace must not leave the
    # previous account's access token cached against this user id.
    forget = getattr(provider, "forget_cached_creds", None)
    if callable(forget):
        forget(uid)
    # _finish_connect (not a bare RedirectResponse): the native shell needs the
    # ticket handoff this does.
    resp = _finish_connect(request, f"/?connected={marketplace}")
    resp.delete_cookie(_flow_cookie(marketplace))
    return resp


@app.get("/api/{marketplace}/status")
def marketplace_status(marketplace: str, request: Request) -> dict:
    provider = _marketplace_or_404(marketplace)
    return provider.account_status(_uid(request))


@app.post("/api/{marketplace}/disconnect")
def marketplace_disconnect(marketplace: str, request: Request) -> dict:
    provider = _marketplace_or_404(marketplace)
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    provider.disconnect(uid)
    return {"ok": True}


@app.post("/api/{marketplace}/end-listing")
def marketplace_end_listing(marketplace: str, req: SessionOnlyRequest,
                            request: Request) -> dict:
    """End this session's live listing on ONE marketplace. eBay keeps its
    original /api/ebay/end-listing route (registered earlier, so it wins);
    this generic one serves every other provider. The record's top-level
    status only becomes 'ended' when nothing is live anywhere anymore."""
    provider = _marketplace_or_404(marketplace)
    rec = db.get_listing(req.session_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    uid = _uid(request)
    creds = provider.creds_for(uid)
    if not creds:
        raise HTTPException(400, f"Connect {provider.label} first.")
    data = rec.get("listing") or {}
    ctx = PublishContext(
        session_id=req.session_id,
        listing=Listing(**{k: v for k, v in data.items()
                           if k in Listing.model_fields}),
        mode="live", base_url=_base_url(request), uid=uid, prev_record=rec)
    try:
        res = provider.end(ctx, creds)
    except NotImplementedError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    states = data.setdefault("marketplaces", {})
    entry = dict(states.get(marketplace) or {})
    entry["status"] = "ended"
    entry["error"] = ""
    states[marketplace] = entry
    still_live = any((v or {}).get("status") == "published"
                     for v in states.values())
    # A pre-multi eBay listing may be live without an entry in the map.
    if (not still_live and data.get("ebay_listing_id")
            and not states.get("ebay")
            and rec.get("status") in ("published", "live")):
        still_live = True
    prev_status = rec.get("status") or ""
    if prev_status in ("published", "live"):
        new_status = prev_status if still_live else "ended"
    else:
        new_status = prev_status or "draft"
    # The marketplace really did end the listing, so `ok` is not the lie —
    # the lost write is. The record would still say `published`, and the app
    # would go on offering to revise and repromote something that is gone.
    # Same check the eBay end route already makes.
    landed = db.upsert_listing(req.session_id, data, status=new_status,
                               user_id=uid or rec.get("user_id"))
    if db.enabled() and not landed:
        raise errors.StorageUnavailable(
            f"{provider.label} ended the listing, but we couldn't record it "
            "just now — refresh in a moment to see the right state.")
    return {"ok": True, **res}


# Serve the frontend (index.html + assets) at the root.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
