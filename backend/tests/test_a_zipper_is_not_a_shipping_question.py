"""The account-level filter on missing_info matches words, not substrings.

It exists so the AI stops asking the seller for their own ship-from location
and ZIP on every listing — Settings already answers those. But "zip" matched
inside "zipper" and "location" inside "location of the signature", so the two
kinds of note a seller genuinely has to look at ("confirm the zipper works")
were thrown away with the noise.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from backend.services.claude_ai import _drop_account_level  # noqa: E402


@pytest.mark.parametrize("note", [
    "Confirm the zipper works",
    "Location of the artist's signature",
    "Is the zip-up hood detachable?",
    "Postal-style vintage stamp on the lining — check the date",
])
def test_a_note_about_the_item_is_kept(note):
    assert _drop_account_level([note]) == [note]


@pytest.mark.parametrize("note", [
    "Item location (city / ZIP code)",
    "Ship-from postal code",
    "Handling time",
    "Which return policy applies",
    "Seller name",
    "ZIP/postal code of the seller",
])
def test_a_note_settings_already_answers_is_dropped(note):
    assert _drop_account_level([note]) == []
