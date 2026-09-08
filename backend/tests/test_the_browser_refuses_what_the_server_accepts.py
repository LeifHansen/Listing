"""The picker in the browser and the decoder on the server agree on what a
photo is.

They are two lists in two languages — `IMAGE_EXT_RE` in frontend/src/lib/api.js
and `services/images._EXTS` — and they drifted. The client's was missing
`.avif`, which current Android phones produce, plus `.jfif` and `.jpe`.

Drift in that direction is invisible from both ends. The server never sees the
file to complain about it: the browser drops it before anything is uploaded,
and the seller is left looking at a screen where their photo simply did not
appear. That is how it was reported — "photos picked, nothing happened" — and
nothing in either suite could have said which end was refusing.

So the lists are pinned against each other here. Adding a format to the
decoder and forgetting the picker (or the reverse) fails this test, which is
the only place the two can be compared at all.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from backend.services import images  # noqa: E402

API_JS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api.js"


def _client_extensions() -> set[str]:
    """The extensions IMAGE_EXT_RE accepts, as a set of ".ext" strings.

    Read out of the regex source rather than hand-copied: a copy is a third
    list to keep in step, which is the problem this test exists for.
    """
    source = API_JS.read_text(encoding="utf-8")
    match = re.search(r"IMAGE_EXT_RE\s*=\s*\n?\s*/\\\.\(([^)]+)\)\$/i", source)
    assert match, "IMAGE_EXT_RE is not where (or what) this test expects"
    out: set[str] = set()
    for alt in match.group(1).split("|"):
        out.update(f".{e}" for e in _expand(alt.strip()))
    return out


def _expand(alt: str) -> set[str]:
    """Every literal an alternative stands for.

    `tiff?` is tif and tiff, `jpe?g` is jpg and jpeg — the optional character
    can sit anywhere, and a reader that only understood a trailing one
    reported `.jpg` as missing while the regex plainly accepted it. A guard
    whose failure message cannot be trusted is worse than none.
    """
    i = alt.find("?")
    if i < 0:
        return {alt}
    without = alt[:i - 1] + alt[i + 1:]
    with_it = alt[:i] + alt[i + 1:]
    return _expand(without) | _expand(with_it)


def test_the_browser_takes_every_format_the_server_can_decode():
    missing = set(images._EXTS) - _client_extensions()
    assert not missing, (
        "the server decodes these but the browser refuses them, so the photo "
        "never leaves the phone and nobody is told: "
        + ", ".join(sorted(missing)))


def test_the_browser_does_not_take_what_the_server_would_refuse():
    """The other direction is a worse failure, not a better one: the upload
    succeeds, the seller waits through it, and the pass drops the photo at the
    far end."""
    extra = _client_extensions() - set(images._EXTS)
    assert not extra, (
        "the browser accepts these but the server cannot decode them: "
        + ", ".join(sorted(extra)))
