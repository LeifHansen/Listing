"""Secret and PII removal for anything a log line or an error row will carry.

This lives here, apart from both config.py and services/errorlog.py, for the
same reason errors.py does: it must have NO dependencies and do NO import-time
work. config.py attaches the filter below to the stdout handler, and
services/errorlog.py scrubs before it writes a row — and services/errorlog.py
imports db, which imports config. A shared helper anywhere further up that
chain is an import cycle.

What this is FOR. Until now nothing scrubbed anything: client IPs, seller ids,
Stripe payment-intent ids, eBay item ids and the raw text of third-party
exceptions all reached stdout verbatim, and an httpx error carries the full
request URL with its query string. That was survivable while `flyctl logs` was
the only reader and the retained window was hours. It stops being survivable
the moment error text is persisted in a database, served over an API and read
by an automated job.

The precedent this follows is crypto.py, which logs `type(exc).__name__` and
never the value it failed on. The rule here is the same one stated differently:
a line has to stay diagnosable, so the SHAPE survives and only the value goes.
`sk_live_51H4x...` becomes `sk_live_<redacted>` rather than disappearing — an
operator reading it still knows a live Stripe key was in play.

Deliberately NOT a guarantee that no secret can ever reach a log. It is a
backstop under the per-call-site discipline, not a replacement for it: a novel
credential format nobody wrote a pattern for still gets through. Adding one
here is cheap; relying on this instead of thinking at the call site is not.
"""
from __future__ import annotations

import logging
import re

# Ordered: the specific credential formats run BEFORE the generic long-token
# sweep, so a Stripe key is labelled as one instead of vanishing into <token>.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JSON Web Tokens — the session cookie's own format.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
     "<jwt>"),
    # Stripe. Keeping the prefix matters: live and test are a different
    # incident, and "which key was this" is the first question asked.
    (re.compile(r"\b((?:sk|pk|rk|whsec)_(?:live|test)_)[A-Za-z0-9]{4,}"),
     r"\1<redacted>"),
    # AWS/R2 access key ids.
    (re.compile(r"\bAKIA[0-9A-Z]{12,}\b"), "<access-key>"),
    # Authorization headers and their friends, however they are spelled.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
     r"\1 <redacted>"),
    # Credentials in a query string or a key=value dump. httpx exceptions
    # carry the whole URL, which is how an OAuth code reaches a log at all.
    (re.compile(r"(?i)\b(access_token|refresh_token|client_secret|api[_-]?key"
                r"|authorization|password|passwd|secret|signature|token|code)"
                r"(\s*[=:]\s*)(\"?)[^\s,&\"'}]{4,}"),
     r"\1\2\3<redacted>"),
    # Email addresses. Sellers' addresses reach logs through eBay and Etsy
    # payloads as well as our own auth paths.
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    # Bare IPv4. Both the seller's address and, occasionally, a machine's.
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    # A long unbroken hex run is an encryption key, a digest or a token. Set
    # at 32 so it cannot swallow a git sha (which is worth keeping) or an
    # 8-character support reference (which is the whole point of having one).
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<hex>"),
)

# Below this, truncation costs more than it saves; above it, one pathological
# traceback can dominate a row. Applied after scrubbing so a secret cannot
# survive by sitting past the cut.
MAX_LEN = 8000


def scrub(text: object, max_len: int = MAX_LEN) -> str:
    """`text` with known credential and PII shapes replaced, and truncated.

    Never raises and never returns None: it is called from logging handlers
    and exception handlers, where an error of its own would replace the
    failure being reported with a less useful one.
    """
    try:
        out = text if isinstance(text, str) else str(text)
    except Exception:  # noqa: BLE001 - a __str__ that raises is not our problem
        return "<unprintable>"
    try:
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
    except Exception:  # noqa: BLE001 - a scrub that fails must not leak the raw
        return "<unscrubbable>"
    if len(out) > max_len:
        out = out[:max_len] + f"… (+{len(out) - max_len} chars)"
    return out


class RedactingFormatter(logging.Formatter):
    """A formatter that scrubs the finished line.

    A formatter rather than a filter, for three reasons worth writing down.

    A filter would have to rewrite `record.msg` and clear `record.args` to
    reach the secret at all — the secret is almost always in the ARGS, since
    the house style is `log.warning("...: %s", exc)`. That mutation destroys
    the `%`-format TEMPLATE, and the template is exactly what
    services/errorlog.py fingerprints on: `"lookup failed (%s) [%s]: %s"` is
    identical across every occurrence, while the interpolated message is not.
    Redacting for safety would have broken deduplication.

    It also mutates a record other handlers share. uvicorn's handlers and the
    capture handler see the same object, so the result would depend on the
    order handlers happened to be registered in.

    A formatter touches only the string it returns. Nothing else can tell.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            return scrub(super().format(record), max_len=MAX_LEN)
        except Exception:  # noqa: BLE001 - never drop a line over formatting
            return "<unformattable log record>"
