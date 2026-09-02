"""Recording what went wrong, so something other than a human can read it.

Until this existed the app had no telemetry at all: `flyctl logs` WAS the
telemetry, the retained window was hours, and reading it meant a person running
a workflow by hand. Nothing was queryable, nothing outlived the window, and
nothing outside the machine could tell whether production was healthy.

Four facts about this codebase decided the shape of what follows.

**Severity is not a usable filter.** There are ~240 `log.warning` calls and 7
`log.error` ones, because the house style is to fail soft: `except Exception as
exc:  # noqa: BLE001`, log, carry on. The real failures are at WARNING. Anything
keyed on ERROR would see almost nothing, so capture starts at WARNING and the
question "is this serious" is answered by `severity()` below instead.

**There is one logger.** All 400-odd call sites go through `config.log`
("thryft"), and the two sub-loggers propagate into it. So capture belongs on a
logging HANDLER, not at the call sites — no edit to 400 places, and a call site
added tomorrow is covered without anyone remembering to.

**`logging` hands us the template for free.** Most call sites are
`log.warning("lookup failed (%s) [%s]: %s", doing, ref, exc)`, so `record.msg`
is the same string every time and every varying id is in `record.args`. That
template is the ideal fingerprint basis, which is why redact.py redacts in a
FORMATTER — a filter would have had to destroy it.

**A fingerprint that moves is worse than none.** It must survive a refactor and
a deploy, or the same bug is "new" every morning and the daily job opens the
same pull request forever. So the basis is logger + level + module + function +
template + exception type, and deliberately NOT the line number, the release
sha, the request path, the user or the timestamp. The line number is stored as
a field — useful to read, fatal to hash.

Everything here is best-effort and NEVER raises. That is the deliberate
opposite of db.admin_audit, which raises so an admin action nobody can write
down does not happen. The trade is reversed here: an error that cannot be
recorded is still an error the seller is living through, and turning a handled
failure into an unhandled one to complain about the bookkeeping would be
strictly worse than losing the row.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import queue
import re
import secrets
import sys
import threading
import time as _time
import traceback as _tb
from typing import Optional

from .. import config
from ..redact import scrub

# The current request, for whatever is about to fail inside it. A ContextVar
# rather than a thread local because FastAPI runs async handlers on a shared
# loop: a thread local would hand one request's id to another's error.
#
# `reference` is the 8 hex characters main._support_reference() shows the
# seller and writes into the log line. Reusing it as the request id is what
# ties "the app told me a1b2c3d4" to a row in this table — the only join
# between a complaint and a cause the app has ever had.
_request: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "thryft_request", default=None)

# Guards the obvious loop: recording touches the database, the database logs a
# warning when it cannot be reached, that warning reaches the capture handler,
# which tries to record it. Thread-local (not a ContextVar) on purpose — it
# must cover the synchronous call stack of one attempt, including the
# threadpool where sync route handlers run.
_busy = threading.local()

# Bounded on purpose. A queue that grows without limit turns a database outage
# — when every read logs a warning — into memory exhaustion on a 4GB machine.
# Full means DROP, and the drop is counted and reported: a sink that quietly
# loses rows while claiming to be a record of what happened is worse than no
# sink, because it is believed.
_QUEUE_MAX = 1000
_FLUSH_SECONDS = 5.0
_FLUSH_MAX = 200

_queue: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAX)
_dropped = 0
_dropped_lock = threading.Lock()
_writer_started = False

# Lines that are noise rather than signal. Seeded from the exclusions in
# .github/workflows/fly-logs.yml, which were tuned against the real log
# stream — that noise has been paid for once already. Kept as a reviewable
# constant rather than buried in a condition, per the check_health.py doctrine
# that thresholds belong somewhere a diff can show them.
_NOISE = re.compile(
    r"account-deletion|New SSH session|\.php\b", re.IGNORECASE)

# Order matters: uuid before the generic hex run, hex before bare digits, or
# an earlier replacement gets eaten by a later pattern.
_VARIABLE = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<id>"),
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<id>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b\d[\d.,]*\b"), "<n>"),
    (re.compile(r"'[^']{0,200}'"), "'<v>'"),
    (re.compile(r'"[^"]{0,200}"'), '"<v>"'),
)

# Fingerprint on the first part of the message only. A long traceback tail or
# a third party's verbose error body varies far more than the sentence naming
# the failure, and letting it in splinters one bug across many rows.
_FINGERPRINT_CHARS = 200

# Third-party unreachability. An httpx.ConnectError against eBay is an OUTAGE,
# not a bug in this repository, and nothing in a pull request can fix it.
# Recorded like anything else, but never treated as serious.
_EXTERNAL = frozenset((
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "RemoteProtocolError", "ReadError", "TimeoutException",
    "SSLError", "ProxyError",
))


def normalize(message: object) -> str:
    """`message` with per-occurrence detail removed, for fingerprinting.

    Not a security function — that is redact.scrub. This one exists only so
    that two occurrences of one bug produce one string. It is applied to the
    formatted message for the minority of call sites that use an f-string; a
    `%`-style site never needs it, because its template is already stable.
    """
    try:
        out = message if isinstance(message, str) else str(message)
    except Exception:  # noqa: BLE001
        return "<unprintable>"
    for pattern, replacement in _VARIABLE:
        out = pattern.sub(replacement, out)
    return out[:_FINGERPRINT_CHARS]


# The database column is String(8000). Trim below it so scrubbing, which can
# lengthen a line slightly, cannot push the result back over and get it cut
# by the column — from the wrong end.
TRACEBACK_BUDGET = 6500


def trim_traceback(text: str, budget: int = TRACEBACK_BUDGET) -> str:
    """A long traceback shortened from the MIDDLE, never from the end.

    Plain truncation cuts from the front, which on a traceback throws away
    exactly the part worth having: Python puts the outermost frames first and
    the exception's own type and message LAST. A deep stack — anything through
    Starlette's middleware chain and anyio's task groups is dozens of frames —
    would arrive as a wall of framework internals with the actual error
    missing, which is a row nobody can act on.

    CI found this, not the test author: the runner's anyio produced a deeper
    ExceptionGroup than the development machine's, crossed 8000 characters,
    and the recorded traceback stopped before it reached the RuntimeError it
    was reporting.

    So: keep the head, which names the request and the entry point, keep the
    much larger tail, which is where it broke, and say how much went.
    """
    if len(text) <= budget:
        return text
    head = budget // 5
    tail = budget - head
    dropped = len(text) - budget
    return (text[:head]
            + f"\n\n… {dropped} characters of stack elided …\n\n"
            + text[-tail:])


def fingerprint(*, logger: str = "", level: str = "", module: str = "",
                func: str = "", template: object = "", exc_type: str = "",
                kind: str = "backend") -> str:
    """A stable id for "this bug", 16 hex characters.

    Built from WHERE and WHAT-SHAPE, never from WHEN or WHO. See the module
    docstring for why the line number and the release are excluded even though
    both are stored.
    """
    basis = "\x1f".join((kind, logger or "", level or "", module or "",
                         func or "", exc_type or "", normalize(template)))
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:16]


def severity(*, level: str, exc_type: str, has_traceback: bool,
             message: str) -> str:
    """How seriously to take a row: "high" | "medium" | "low".

    Derived, never `levelname`. The fail-soft convention means a genuine crash
    and a shrug are both logged at WARNING, so the daily triage job keys on
    this instead — a real traceback is the strongest available signal that
    something broke rather than merely disappointed.
    """
    if exc_type in _EXTERNAL:
        return "low"
    if has_traceback or (level or "").upper() in ("ERROR", "CRITICAL"):
        return "high"
    if re.search(r"\b(failed|could not|couldn't|cannot|unavailable|refused"
                 r"|invalid|unexpected)\b", message or "", re.IGNORECASE):
        return "medium"
    return "low"


# The package this repository's code lives in ("backend"). origin() prefers
# the deepest frame under it: a row naming a dependency's file is a row that
# nothing in this tree can be pointed at.
_OWN_PACKAGE = __name__.split(".")[0]


def _is_own(tb) -> bool:
    name = tb.tb_frame.f_globals.get("__name__", "") or ""
    return name == _OWN_PACKAGE or name.startswith(_OWN_PACKAGE + ".")


def origin(exc: Optional[BaseException]) -> tuple[str, str, Optional[int]]:
    """(module, function, line) of the innermost frame IN THIS CODEBASE.

    The alternative, and the first thing tried, was to fingerprint an
    unhandled error on the request path. That is wrong: `/api/listings/abc`
    and `/api/listings/def` are the same bug, and a path with an id in it
    mints a fresh fingerprint per seller. The deepest frame is the actual
    crash site and is the same for every occurrence.

    "Deepest" stops at this package's own frames, though. A bad argument
    into Pillow breaks inside PIL; a client that hangs up mid-upload breaks
    inside starlette. The deepest frame of all then names a dependency's
    file, and the daily triage read every such row as "recorded by an older
    build" because starlette/requests.py is not in the tree. The deepest
    frame of OURS is where the bad call was made, which is the line a fix
    goes on. A traceback with no frame of ours at all (a dependency's own
    thread) still reports its true innermost frame rather than nothing.
    """
    tb = getattr(exc, "__traceback__", None)
    last = own = None
    while tb is not None:
        last = tb
        if _is_own(tb):
            own = tb
        tb = tb.tb_next
    pick = own or last
    if pick is None:
        return "", "", None
    frame = pick.tb_frame
    return (frame.f_globals.get("__name__", "") or "",
            frame.f_code.co_name or "", pick.tb_lineno)


def new_reference() -> str:
    """A fresh support reference. 8 hex characters: short enough to read down
    a phone line, wide enough not to collide within a retention window."""
    return secrets.token_hex(4)


def begin_request(method: str = "", path: str = "",
                  reference: str = "") -> dict:
    """Open a request context and return it. Called once per request."""
    ctx = {"reference": reference or new_reference(), "method": method,
           "path": path, "user_id": ""}
    _request.set(ctx)
    return ctx


def current_request() -> Optional[dict]:
    return _request.get()


def current_reference() -> str:
    return (_request.get() or {}).get("reference") or ""


def note_user(user_id: str) -> None:
    """Attach the signed-in seller to the current request, if there is one.

    The id, never the email: the id finds the account in the console and is
    not itself personal data the way an address is. Called from main._uid,
    the one place the id is already in hand — resolving it here instead would
    add a database read to every request, and auth.current_user now RAISES on
    a database blip, which would turn one Neon hiccup into a failing health
    check on the only machine.
    """
    ctx = _request.get()
    if ctx is not None and user_id:
        ctx["user_id"] = str(user_id)[:64]


def stats() -> dict:
    """What the sink itself is doing. Surfaced in the report so a queue that
    is dropping rows says so instead of looking like a quiet day."""
    return {"queued": _queue.qsize(), "dropped": _dropped,
            "running": _writer_started}


def record(*, kind: str = "backend", level: str = "ERROR",
           message: object = "", exc: Optional[BaseException] = None,
           logger: str = "thryft", module: str = "", lineno: object = "",
           func: str = "", route: str = "", method: str = "",
           status: object = None, reference: str = "",
           template: object = None, sample: Optional[dict] = None
           ) -> Optional[str]:
    """Queue one failure for recording. Returns the fingerprint, or None.

    NEVER raises, never blocks and never touches the database on the calling
    thread — a synchronous Neon INSERT inside a log call would sit on the
    event loop, and these are called from paths that are already going badly.
    """
    if not config.ERROR_CAPTURE_ENABLED:
        return None
    if getattr(_busy, "on", False):
        return None
    _busy.on = True
    try:
        exc_type = type(exc).__name__ if exc is not None else ""
        if exc is not None and not module and not func:
            module, func, tb_line = origin(exc)
            lineno = lineno or tb_line
        text = message if message not in ("", None) else (
            f"{exc_type}: {exc}" if exc is not None else "")
        text = str(text)
        if _NOISE.search(text):
            return None
        trace = ""
        if exc is not None:
            trace = "".join(_tb.format_exception(
                type(exc), exc, exc.__traceback__))
        ctx = _request.get() or {}
        fp = fingerprint(logger=logger, level=level, module=module, func=func,
                         template=template if template is not None else text,
                         exc_type=exc_type, kind=kind)
        payload = {
            "fingerprint": fp,
            "kind": kind,
            "level": (level or "ERROR")[:16],
            "severity": severity(level=level, exc_type=exc_type,
                                 has_traceback=bool(trace), message=text),
            "exc_type": exc_type[:120],
            "message": scrub(text, max_len=2000),
            "traceback": scrub(trim_traceback(trace), max_len=8000),
            "route": (route or ctx.get("path") or "")[:200],
            "method": (method or ctx.get("method") or "")[:8],
            "status": status,
            "reference": (reference or ctx.get("reference") or "")[:16],
            "build": (config.BUILD_SHA or "")[:40],
            "module": (module or "")[:120],
            "func": (func or "")[:120],
            "lineno": lineno,
            "user_id": (ctx.get("user_id") or "")[:64],
            "extra": sample or {},
        }
        try:
            _queue.put_nowait(payload)
        except queue.Full:
            _bump_dropped()
        return fp
    except Exception:  # noqa: BLE001 - recording a failure cannot add one
        return None
    finally:
        _busy.on = False


def _bump_dropped() -> None:
    global _dropped
    with _dropped_lock:
        _dropped += 1


class CaptureHandler(logging.Handler):
    """Mirrors WARNING-and-above from the app logger into error_events.

    Attached to `config.log`, so it sees every module's lines including the
    propagating sub-loggers. It reads `record.module`/`funcName`/`lineno`
    directly, which no formatter can have rewritten, so what it captures does
    not depend on the order handlers were registered in.

    The `sys.exc_info()` read is the reason this is worth doing at all. The
    240 fail-soft call sites say `except Exception as exc: log.warning(...)`
    — no `exc_info=True`, so `record.exc_info` is empty — but inside an except
    block the interpreter still has the live exception, and `sys.exc_info()`
    returns it. So every one of those sites gets a real traceback attached
    without a single one of them being edited. Without this, a warning row is
    a sentence with nothing to act on.

    `handleError` is overridden to do nothing: the default prints to stderr,
    which during a database outage means one line of noise per log call,
    forever.
    """

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # A line the caller already recorded explicitly, with better
            # context than this handler could reconstruct. Without this the
            # catch-all handler's own log line produces a SECOND row for
            # every unhandled error, fingerprinted at the handler rather
            # than at the crash.
            if getattr(record, "errorlog_skip", False):
                return
            exc = None
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
            else:
                live = sys.exc_info()[1]
                # Only when the call really is inside an except block. A
                # stale exception from an outer frame would attach the wrong
                # traceback, so require that logging itself was not the cause.
                if live is not None and not isinstance(live, KeyboardInterrupt):
                    exc = live
            ctx = _request.get() or {}
            # The template, not the formatted line: it is identical across
            # occurrences whenever the call site used %-style args.
            template = record.msg if record.args else record.getMessage()
            _record_from_log(record, exc, template, ctx)
        except Exception:  # noqa: BLE001
            pass

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: D102
        return


def _record_from_log(rec: logging.LogRecord, exc, template, ctx: dict) -> None:
    record(kind="backend",
           level=rec.levelname,
           message=rec.getMessage(),
           template=template,
           exc=exc,
           logger=rec.name,
           module=getattr(rec, "module", "") or "",
           lineno=rec.lineno,
           func=rec.funcName,
           route=ctx.get("path") or "",
           method=ctx.get("method") or "")


def _drain(block_for: float) -> list[dict]:
    """Up to _FLUSH_MAX queued payloads, waiting at most `block_for` for the
    first one."""
    out: list[dict] = []
    try:
        out.append(_queue.get(timeout=block_for))
    except queue.Empty:
        return out
    while len(out) < _FLUSH_MAX:
        try:
            out.append(_queue.get_nowait())
        except queue.Empty:
            break
    return out


def coalesce(events: list[dict]) -> list[dict]:
    """One entry per fingerprint, carrying how many occurrences it stands for.

    A flush of 200 rows from one broken loop becomes one upsert. The FIRST
    occurrence's message and traceback are kept rather than the last: the
    first is the one that happened before anything else started failing in
    response to it.
    """
    merged: dict[str, dict] = {}
    for ev in events:
        held = merged.get(ev["fingerprint"])
        if held is None:
            ev = dict(ev)
            ev["occurrences"] = 1
            merged[ev["fingerprint"]] = ev
        else:
            held["occurrences"] += 1
            # Keep the most recent request context, so "last seen at" points
            # somewhere a person can actually go and look.
            held["reference"] = ev.get("reference") or held.get("reference")
            held["route"] = ev.get("route") or held.get("route")
            if not held.get("traceback") and ev.get("traceback"):
                held["traceback"] = ev["traceback"]
    return list(merged.values())


def flush() -> int:
    """Drain the queue and write it, on the CALLING thread. Returns rows written.

    The writer thread is what production uses; this is the same work done
    synchronously, so a test can assert on a row without sleeping and hoping.
    It is also the honest way to drain at shutdown, which is otherwise where
    anything still queued is simply lost.
    """
    from .. import db

    written = 0
    while True:
        batch = _drain(0.0)
        if not batch:
            return written
        for ev in coalesce(batch):
            if db.record_error_event(**ev):
                written += 1


def _writer_loop() -> None:
    """Drain, coalesce, upsert. One daemon thread, off every request path."""
    from .. import db  # local: db imports config, and this starts at boot

    last_prune = 0.0
    while True:
        try:
            batch = _drain(_FLUSH_SECONDS)
            if batch:
                for ev in coalesce(batch):
                    db.record_error_event(**ev)
            now = _time.time()
            # Once a day, not once a flush. Aggregation already bounds the
            # row count; this bounds how long a fixed bug keeps being listed.
            # The archive goes out first, so what is about to be pruned has
            # already been written down somewhere that outlives the table.
            if now - last_prune > 86400:
                last_prune = now
                from . import logarchive
                logarchive.archive_day()
                db.prune_error_events(config.ERROR_TTL_DAYS)
        except Exception:  # noqa: BLE001 - the writer outlives every failure
            try:
                _time.sleep(_FLUSH_SECONDS)
            except Exception:  # noqa: BLE001
                return


_installed = False


def install() -> None:
    """Attach the capture handler. Idempotent, cheap, starts nothing.

    Lives in main rather than config because importing config is something
    every test and every script does, and it must never acquire a resource as
    a side effect. This only adds a handler that fills an in-memory queue —
    see start_writer for the half that touches the database.
    """
    global _installed
    if _installed or not config.ERROR_CAPTURE_ENABLED:
        return
    _installed = True
    config.log.addHandler(CaptureHandler())


def start_writer() -> None:
    """Start the daemon that drains the queue into the database. Idempotent.

    Deliberately NOT part of install(). A thread started at import time runs
    in every pytest process and every one-off script, where it races whatever
    the caller is about to assert and writes to a database the caller may not
    have meant to touch. Started from main's startup daemons instead, beside
    the reclaim and db-status loops, so it exists exactly when the app is
    serving. Tests use flush(), which does the same work synchronously.
    """
    global _writer_started
    if _writer_started or not config.ERROR_CAPTURE_ENABLED:
        return
    _writer_started = True
    threading.Thread(target=_writer_loop, daemon=True,
                     name="thryft-errorlog").start()
