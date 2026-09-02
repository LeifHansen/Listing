"""A recorded failure points at code somebody here can change.

The error feed fingerprints a failure on the innermost frame of its
traceback, because that is the crash site and it is the same for every
occurrence. But "innermost of all" is often not ours: a client that hangs up
mid-upload breaks inside starlette, a bad argument into Pillow breaks inside
PIL. On 2026-09-02 the first triage run skipped one of the four rows in the
feed as "starlette/requests.py is not in the tree - recorded by an older
build" -- a wrong explanation of a row that could never have named this
repository, from a script whose whole purpose is to hand a fixer the line.

So the innermost frame OF OURS is the origin, with the true innermost as the
fallback for a traceback that never passes through this package at all. And
the triage script tells a dependency's module apart from one of ours that has
since been deleted, since only the second is evidence of an older build.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from backend.services import errorlog

REPO_ROOT = Path(__file__).resolve().parents[2]


def _from_inside_a_dependency():
    # The innermost frame is json.decoder's, two levels below this one.
    json.loads("{")


def _no_frame_of_ours():
    """An exception whose traceback has no frame in this package: raised
    and caught inside the stdlib with our frames stripped off."""
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        tb = exc.__traceback__
        while tb is not None and (tb.tb_frame.f_globals.get("__name__") or
                                  "").startswith("backend"):
            tb = tb.tb_next
        return exc.with_traceback(tb)


def test_the_origin_is_the_deepest_frame_of_ours_not_the_deepest_of_all():
    try:
        _from_inside_a_dependency()
    except json.JSONDecodeError as exc:
        module, func, line = errorlog.origin(exc)
    assert module == __name__
    assert func == "_from_inside_a_dependency"
    assert isinstance(line, int) and line > 0


def test_a_traceback_with_no_frame_of_ours_still_names_where_it_broke():
    """Better a dependency's line than nothing: a row with an empty module
    cannot be grouped with its siblings, let alone fixed."""
    exc = _no_frame_of_ours()
    module, func, _line = errorlog.origin(exc)
    assert module.startswith("json.")
    assert func


def _triage():
    path = REPO_ROOT / ".github" / "scripts" / "triage_errors.py"
    spec = importlib.util.spec_from_file_location("triage_errors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**over) -> dict:
    base = {"fingerprint": "ff00", "severity": "high", "count": 9,
            "traceback": "Traceback...", "exc_type": "TypeError",
            "module": "backend.main", "func": "x", "message": "m"}
    base.update(over)
    return base


def test_the_triage_tells_a_dependency_from_a_deleted_module_of_ours():
    triage = _triage()
    why = triage.why_not(_row(module="starlette.requests"), {}, str(REPO_ROOT))
    assert "dependency" in why
    assert "older build" not in why, (
        "a module that was never in this repository is not evidence of an "
        "older build")
    why = triage.why_not(_row(module="backend.deleted_module"), {},
                         str(REPO_ROOT))
    assert "older build" in why
    assert triage.why_not(_row(module="backend.main"), {}, str(REPO_ROOT)) is None
