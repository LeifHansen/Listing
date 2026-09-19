"""The client's copy of the field alignment map is the server's.

frontend/src/lib/fieldMap.js is a JSON literal pasted from
backend/marketplaces/field_map.py so a listing can be judged "Etsy-ready"
in the browser without a round trip. Two copies drift; this is what stops
them. It reads the JS file and parses the array as JSON, which is why that
file's comment forbids trailing commas and comments inside the array.
"""
import json
import pathlib
import re

from backend.marketplaces import field_map

_ROOT = pathlib.Path(field_map.__file__).resolve().parents[2]
_JS = _ROOT / "frontend" / "src" / "lib" / "fieldMap.js"
_COMPARED = ("key", "label", "fill", "derive", "required_for_etsy",
             "preflight_target", "note")


def _client_rows() -> list[dict]:
    text = _JS.read_text()
    match = re.search(r"export const FIELD_MAP = (\[.*?\n\]);", text, re.S)
    assert match, "fieldMap.js no longer carries `export const FIELD_MAP = [...]`"
    return json.loads(match.group(1))


def test_the_client_map_matches_the_server_map():
    server = [{k: row[k] for k in _COMPARED} for row in field_map.as_json()]
    assert _client_rows() == server, (
        "frontend/src/lib/fieldMap.js has drifted from "
        "backend/marketplaces/field_map.py — regenerate it from the Python")
