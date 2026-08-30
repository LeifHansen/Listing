"""Refunds that did not commit, kept until they do.

"Only pay for AI that worked" is the promise: when the AI fails, the charge is
given back. `db.token_refund` returns False when that write did not happen,
and `tokens.refund` used to throw the answer away. So a database blip in the
refund window meant the seller was charged, the AI failed, the refund never
landed, and nothing anywhere recorded that it was owed.

The existing recovery (`main._settle_interrupted_jobs`) only covers jobs whose
PROCESS died. A job that finished normally with a failed refund was never
revisited by anything.

The debt goes on the VOLUME rather than into the database, and that is the
whole design. The reason a refund fails is almost always that the database is
unreachable — a debt recorded there would fail for the same reason, at the
same moment, and the recovery would be no more durable than the thing it is
recovering. services/jobstore.py mirrors job status to the volume for exactly
this reason.

Retrying is safe by construction rather than by bookkeeping: a full refund is
keyed in the ledger by the spend's own entry id, and a partial one by entry id
plus amount, so the database rejects a second attempt instead of paying the
seller twice. That means this file needs no "already settled" flag of its own
and a crash mid-settle costs nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import config, db
from ..config import log

_DIR: Optional[Path] = config.DATA_DIR / "owed-refunds"


def _dir() -> Optional[Path]:
    if _DIR is None:
        return None
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        return _DIR
    except Exception as exc:  # noqa: BLE001 - the volume is the fallback
        log.warning("owed-refunds: can't use %s: %s", _DIR, exc)
        return None


def _name(entry_id: str, units: Optional[int]) -> str:
    """One file per DEBT, not per spend.

    A full refund and a partial one against the same ledger entry are
    different amounts that the ledger keys apart, so they are two debts here
    too. Two identical failures collapse onto one file, which is what stops a
    retried failure becoming two retries of the same money.
    """
    safe = "".join(c for c in str(entry_id) if c.isalnum() or c in "-_")[:80]
    return f"{safe}.{'full' if units is None else int(units)}.json"


def owe(receipt: Optional[dict], units: Optional[int] = None) -> None:
    """Record that this refund still has to happen. Never raises.

    It runs on a failure path, often inside a `finally`. Throwing here would
    replace a lost refund with a lost response, which helps nobody.
    """
    if not receipt or not receipt.get("ok") or not receipt.get("entry_id"):
        # A declined or un-metered spend bought nothing and owes nothing.
        return
    root = _dir()
    if root is None:
        log.warning("owed-refunds: NOT recorded for entry %s — no writable "
                    "volume; this refund is lost", receipt.get("entry_id"))
        return
    try:
        (root / _name(receipt["entry_id"], units)).write_text(json.dumps({
            "entry_id": str(receipt["entry_id"]),
            "user_id": str(receipt.get("user_id") or ""),
            "units": units,
        }))
    except Exception as exc:  # noqa: BLE001
        log.warning("owed-refunds: couldn't record entry %s: %s",
                    receipt.get("entry_id"), exc)


def pending() -> list[dict]:
    """Every refund still owed. `[]` when there is nowhere to read from."""
    root = _dir()
    if root is None:
        return []
    out = []
    try:
        for path in sorted(root.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001 - one unreadable file, not the pass
                continue
            if data.get("entry_id"):
                data["_path"] = str(path)
                out.append(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("owed-refunds: couldn't list: %s", exc)
    return out


def settle() -> int:
    """Pay back whatever is still owed. Returns how many succeeded.

    Never raises: this runs from the housekeeping loop and at startup, and a
    failure here must not take either down.
    """
    done = 0
    for debt in pending():
        try:
            paid = db.token_refund(debt["user_id"], debt["entry_id"],
                                   units=debt.get("units"))
        except Exception as exc:  # noqa: BLE001 - one debt, not the pass
            log.warning("owed-refunds: %s still failing: %s",
                        debt["entry_id"], exc)
            continue
        if not paid:
            # Kept. The ledger rejects a duplicate, so a debt that is actually
            # already settled costs one no-op call per pass — cheaper than the
            # alternative, which is deleting a refund the seller never got.
            continue
        try:
            Path(debt["_path"]).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("owed-refunds: paid %s but couldn't clear the record: "
                        "%s", debt["entry_id"], exc)
        done += 1
    if done:
        log.info("owed-refunds: settled %d refund(s) that had not committed",
                 done)
    return done


def backlog() -> int:
    """How many refunds are still owed — for the operator diagnostics. A
    number that does not come back down is money a seller is still out."""
    return len(pending())
