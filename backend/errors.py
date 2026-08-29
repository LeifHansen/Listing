"""Typed failures the API layer maps to status codes.

These live here, apart from the modules that raise them, for one specific
reason: FastAPI's exception handlers are registered against the class OBJECT.
Anything that re-imports or reloads the raising module mints a fresh class,
and the handler silently stops matching — the route then returns a 500, or
worse, whatever the surrounding `except Exception` decided to say. A module
with no dependencies and no import-time work is never reloaded, so the
identity holds.
"""
from __future__ import annotations


class StorageUnavailable(Exception):
    """A write could not be committed, so nothing may claim it succeeded.

    Deliberately NOT a subclass of ValueError or LookupError: both are already
    mapped to 4xx responses elsewhere, and inheriting either would quietly
    reclassify a database outage as the seller's mistake. This is a 503 — "try
    again shortly" — not a 400 or a 404.

    Telling those apart matters to the person on the other end. A 404 on a
    connection that exists sends the seller to reconnect an account that is
    fine, which cannot help and re-arms the same failure on the next attempt.
    """
