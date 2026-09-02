"""A server pass that filled NOTHING must not stand the client's fill down.

Item specifics get filled twice over, on purpose. The identify job fills them
server-side so a draft is SEO-ready even on the bulk "list live now" path, and
the editor has its own autofill effect as the fallback for when that pass
didn't run. The two are kept from doubling up (and double-charging) by one
flag on the identify result: specifics_autofilled.

That flag was `added is not None`, and the two values it conflates are exactly
the two that matter:

    None -> the enrichment never ran (no category, no API key, it raised)
    0    -> it RAN, and filled nothing: the vision pass came back empty, or
            every value it proposed was dropped as illegal for its aspect

`is not None` reported the second as "the specifics are filled". The editor
believed it and stood its fallback down, so a listing whose server pass came
back empty had every required specific left blank and nothing on either side
ever filled them — the one job the app exists to do.

There is nothing to protect from a re-charge when the server added zero. That
is precisely when the fallback should run.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend.main import _specifics_were_filled as _flag  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402


def test_a_pass_that_added_nothing_leaves_the_fallback_armed():
    assert _flag(0) is False


def test_a_pass_that_never_ran_leaves_the_fallback_armed():
    assert _flag(None) is False


def test_a_pass_that_actually_filled_stands_the_fallback_down():
    """The flag's real job: don't run the same vision passes twice, seconds
    apart, and charge the seller for both."""
    assert _flag(1) is True
    assert _flag(12) is True


def test_the_flag_defaults_to_unfilled():
    """A result nothing set the flag on must not suppress the fallback."""
    assert IdentifyResult(listing=Listing(title="A jacket")).specifics_autofilled is False


@pytest.mark.parametrize("added, expected", [(None, False), (0, False),
                                             (1, True), (7, True)])
def test_the_flag_says_filled_only_when_something_was_filled(added, expected):
    result = IdentifyResult(listing=Listing(title="A jacket"))
    result.specifics_autofilled = _flag(added)
    assert result.specifics_autofilled is expected
