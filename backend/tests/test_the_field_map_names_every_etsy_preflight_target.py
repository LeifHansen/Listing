"""The field alignment map is a map of the code, not a wish about it.

Every `etsy_*` target the Etsy preflight can emit has to be some rule's
preflight_target, and every preflight_target a rule names has to be one
the preflight can emit — so a rule added to the checklist reaches the
crosspost review and the README, and a rule the checklist dropped cannot
go on being promised.
"""
import pathlib
import re

from backend.marketplaces import field_map, mapping_etsy
from backend.models import ItemSpecific, Listing

_SOURCE = pathlib.Path(mapping_etsy.__file__).read_text()
_EMITTED = set(re.findall(r'add\("([a-z_]+)"', _SOURCE))


def test_every_preflight_target_has_a_rule_and_vice_versa():
    named = {rule.preflight_target for rule in field_map.FIELD_MAP
             if rule.preflight_target}
    assert _EMITTED, "the preflight emits nothing? the regex has drifted"
    assert _EMITTED - named == set(), "preflight targets no rule names"
    assert named - _EMITTED == set(), "rules naming targets the preflight never emits"


def test_the_map_is_well_formed():
    keys = [rule.key for rule in field_map.FIELD_MAP]
    assert len(keys) == len(set(keys))
    for rule in field_map.FIELD_MAP:
        assert rule.fill in field_map.FILLS, rule.key
        assert rule.ours and rule.label, rule.key
        if rule.fill == field_map.DERIVED:
            assert rule.derive, f"{rule.key} is derived by what?"
        if rule.fill == field_map.ABSENT:
            assert not rule.etsy, f"{rule.key} is absent on Etsy yet names a field"
    assert {r.key for r in field_map.etsy_required()} >= {
        "title", "price", "photos", "category", "who_made", "when_made",
        "shipping_profile", "readiness_state"}


def test_as_json_is_plain_data():
    rows = field_map.as_json()
    assert all(isinstance(r, dict) for r in rows)
    assert rows[0]["key"] == "title" and rows[0]["fill"] == "derived"


def test_readiness_names_what_is_missing_and_what_will_be_filled():
    listing = Listing(title="NIKE AIR MAX 90 VTG SHOES", description="Nice.",
                      price=45.0, quantity=1, images=["a.jpg"],
                      item_specifics=[ItemSpecific(name="Decade", value="1990s"),
                                      ItemSpecific(name="Material", value="Leather")])
    verdict = field_map.readiness(listing, {}, "live")
    assert verdict["ready"] is False
    assert verdict["missing"] == ["category", "who_made", "when_made",
                                  "shipping_profile", "return_policy",
                                  "readiness_state"]
    assert set(verdict["derived"]) == {"title", "description", "category",
                                       "when_made", "item_specifics", "materials"}
    listing.etsy.taxonomy_id = 1
    listing.etsy.who_made, listing.etsy.when_made = "someone_else", "1990s"
    settings = {"shipping_profile_id": "1", "return_policy_id": "2",
                "readiness_state_id": "3"}
    ready = field_map.readiness(listing, settings, "live")
    assert ready["ready"] is True and ready["missing"] == []
    assert "category" not in ready["derived"]


def test_the_readme_table_names_every_rule():
    readme = pathlib.Path(mapping_etsy.__file__).resolve().parents[2] / "README.md"
    text = readme.read_text()
    for rule in field_map.FIELD_MAP:
        assert f"| **{rule.label}**" in text, (
            f"README's field alignment table is missing {rule.key!r}")
