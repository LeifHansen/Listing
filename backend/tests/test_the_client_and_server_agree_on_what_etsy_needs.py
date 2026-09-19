"""The browser's Etsy blockers and the server's Etsy preflight name the
same fields.

blockers.js mirrors the error-level rules of mapping_etsy.preflight so the
Publish button can say "3 things Etsy needs" before firing. The two are
written by hand on either side of an HTTP boundary, so this reads both and
compares the `etsy_*` targets each can raise — a rule added to one side
without the other fails here, on the day it is cheap to add.
"""
import pathlib
import re

from backend.marketplaces import mapping_etsy

_ROOT = pathlib.Path(mapping_etsy.__file__).resolve().parents[2]
_JS = _ROOT / "frontend" / "src" / "views" / "listing" / "blockers.js"


def _etsy_targets(text: str) -> set[str]:
    return set(re.findall(r'"(etsy_[a-z_]+)"', text))


def test_the_same_etsy_targets_on_both_sides():
    server = _etsy_targets(pathlib.Path(mapping_etsy.__file__).read_text())
    client = _etsy_targets(_JS.read_text())
    assert server, "the server preflight raises no etsy_* targets? regex drift"
    assert client == server, (
        f"only on the server: {sorted(server - client)}; "
        f"only in the browser: {sorted(client - server)}")


def test_the_vocabularies_are_copied_exactly():
    js = _JS.read_text()
    when_made = re.search(r"ETSY_WHEN_MADE = \[(.*?)\];", js, re.S).group(1)
    assert re.findall(r'"([a-z0-9_]+)"', when_made) == list(mapping_etsy.WHEN_MADE)
    who_made = re.search(r"ETSY_WHO_MADE = \[(.*?)\];", js).group(1)
    assert re.findall(r'"([a-z_]+)"', who_made) == list(mapping_etsy.WHO_MADE)
    assert f"ETSY_ALL_CAPS_WORD_LIMIT = {mapping_etsy.ALL_CAPS_WORD_LIMIT};" in js
    assert f"ETSY_MIN_PRICE = {mapping_etsy.PRICE_FLOOR:.2f};" in js
    assert f"ETSY_MAX_PHOTOS = {mapping_etsy.MAX_PHOTOS};" in js
