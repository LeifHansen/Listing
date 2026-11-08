"""Background removal, per seller: switched on, and counted.

Photoroom does the cutting (services/cutout_api) on the app's own account:
Photoroom has no API for creating accounts or keys on a seller's behalf, so
there is no "linked Photoroom account" to make for each seller. What each
seller has instead is a SWITCH, and it starts off. Switching it on in Settings
is where they agree to the photos they choose being sent to Photoroom, and
until they do, no photo of theirs is ever sent there -- the uploader does not
offer the toggle and every route that cuts refuses with a 409.

The switch lives in users.prefs under a reserved key the /api/prefs whitelist
does not include, so the Settings form can never flip it past the consent
dialog. The count lives in bg_removal_usage, one row per seller per month.

Two prices, two jobs. The `image_ai` token charge (1 per photo) is what the
seller pays and is unchanged. The monthly count is the backstop on the app's
Photoroom bill, BG_REMOVAL_MONTHLY_LIMIT photos per seller per month, and it
holds when token billing is off too. It is reserved up front, before the token
charge, and given back for every photo that was never cut -- exactly where the
token refund for that photo happens.

Without a database there are no accounts at all: the install is one person's,
the operator who set the key. The switch is then implied by the key and
nothing is counted, the same way token billing is a no-op there.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

from .. import config, db
from ..config import log
from .cutout_api import UNAVAILABLE
from .tokens import next_reset, period_now

# The users.prefs key. Not in main._PREF_FIELDS, on purpose.
PREF_KEY = "bg_removal"
# Which consent text the seller agreed to, so a change to who receives the
# photos can ask again rather than lean on an agreement to something else.
CONSENT = "photoroom-v1"

PROVIDER = "Photoroom"

OFF_MESSAGE = ("Background removal is off for your account — turn it on in "
               "Settings → Background removal first.")
LOGIN_MESSAGE = ("Log in to remove backgrounds — it is switched on per "
                 "account.")


class Refused(Exception):
    """A cutout this seller may not have right now, with the HTTP status a
    route should answer and a sentence the seller can act on."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def available() -> bool:
    """Whether the app can cut out backgrounds at all (a key is set)."""
    return config.photoroom_ready()


def needs_activation() -> bool:
    """Whether a seller has to switch the feature on: only where there are
    accounts, which is only where there is a database."""
    return db.enabled()


def _record(user_id: str) -> dict:
    """The seller's stored switch. RAISES StorageUnavailable on a read
    failure: "off" is a claim, and a read that fell over is not one."""
    rec = db.get_prefs(user_id).get(PREF_KEY)
    return rec if isinstance(rec, dict) else {}


def is_active(user_id: Optional[str]) -> bool:
    if not needs_activation():
        return available()
    if not user_id:
        return False
    return bool(_record(user_id).get("active"))


def status(user_id: Optional[str]) -> dict:
    """Everything the Settings card and the uploader need."""
    limit = config.BG_REMOVAL_MONTHLY_LIMIT
    out = {
        "available": available(),
        "needs_activation": needs_activation(),
        "active": False,
        "activated_at": None,
        "used": 0,
        "limit": limit,
        "resets_on": next_reset(),
        "provider": PROVIDER,
        "sandbox": config.PHOTOROOM_SANDBOX,
    }
    if not needs_activation():
        out["active"] = available()
        return out
    if not user_id:
        return out
    rec = _record(user_id)
    out["active"] = bool(rec.get("active"))
    out["activated_at"] = rec.get("activated_at")
    used = db.bg_usage(user_id, period_now())
    out["used"] = used if used is not None else 0
    return out


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def activate(user_id: str) -> dict:
    """Switch the feature on for this seller, recording that they agreed to
    their chosen photos going to Photoroom. Raises Refused when there is
    nothing to switch on."""
    if not available():
        raise Refused(UNAVAILABLE, 503)
    if not needs_activation():
        return status(user_id)
    saved = db.save_prefs(user_id, {PREF_KEY: {
        "active": 1, "activated_at": _now_iso(), "consent": CONSENT}})
    if not saved:
        raise Refused("Couldn't save that — log in again and retry.", 409)
    log.info("bg-removal: switched on for %s", user_id)
    return status(user_id)


def deactivate(user_id: str) -> dict:
    if needs_activation():
        db.save_prefs(user_id, {PREF_KEY: {
            "active": 0, "deactivated_at": _now_iso()}})
        log.info("bg-removal: switched off for %s", user_id)
    return status(user_id)


@dataclass
class Reservation:
    """Photos counted against a seller's month and not yet given back.
    `user_id` is None when nothing was counted (no database, or the count
    failed open), and then release() does nothing."""

    user_id: Optional[str]
    period: str
    photos: int

    def release(self, photos: Optional[int] = None) -> None:
        """Give back `photos` (all that remain by default) that were never
        cut out. Never gives back more than was reserved."""
        n = self.photos if photos is None else min(max(0, photos), self.photos)
        if not self.user_id or n <= 0:
            return
        if db.bg_release(self.user_id, self.period, n):
            self.photos -= n


def check(user_id: Optional[str]) -> None:
    """Raise Refused unless this seller may have backgrounds removed at all:
    503 with no key configured, 401 for an anonymous caller where there are
    accounts, 409 when the seller has not switched the feature on. Counts
    nothing -- see reserve()."""
    if not available():
        raise Refused(UNAVAILABLE, 503)
    if not needs_activation():
        return
    if not user_id:
        raise Refused(LOGIN_MESSAGE, 401)
    if not is_active(user_id):
        raise Refused(OFF_MESSAGE, 409)


def reserve(user_id: Optional[str], photos: int) -> Reservation:
    """Check this seller may have `photos` cut out now, and count them.

    Raises Refused for everything check() refuses, and 429 when the photos
    would take the seller past this month's limit.
    A database error while COUNTING fails open (logged), as the token charge
    does: refusing every cutout because a counter hiccuped costs more trust
    than a few uncounted photos.
    """
    check(user_id)
    if not needs_activation():
        return Reservation(None, "", 0)
    period = period_now()
    limit = config.BG_REMOVAL_MONTHLY_LIMIT
    res = db.bg_reserve(user_id, period, photos, limit)
    if res is None:
        log.warning("bg-removal: couldn't count %d photo(s) for %s — "
                    "allowing them uncounted", photos, user_id)
        return Reservation(None, period, 0)
    if not res["ok"]:
        used = res["used"]
        left = max(0, limit - used)
        need = (f" — {left} left, and this needs {photos}" if left else "")
        raise Refused(
            f"You've used {used} of your {limit} background removals this "
            f"month{need}. The allowance resets on {next_reset()}.", 429)
    return Reservation(user_id, period, photos)
