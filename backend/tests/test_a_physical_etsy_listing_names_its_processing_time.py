"""Etsy refuses a physical listing without a processing profile.

Since mid-2025 createDraftListing answers "A readiness_state_id is required
for physical listings", and the mapping never sent one — so every real
publish failed on a field the editor had no name for. The profile now
follows the same rule as the shipping profile (a per-listing override, else
the account default from Settings), the listing says `type=physical` out
loud rather than relying on Etsy's default, and the checklist names the two
things Etsy checks that this app used to leave to chance: the processing
profile, and the return policy a listing needs before it can go live.
"""
from backend.marketplaces import mapping_etsy
from backend.models import Listing


def _listing(**etsy):
    fields = {"taxonomy_id": 1, "who_made": "someone_else", "when_made": "1990s",
              "shipping_profile_id": "77"}
    fields.update(etsy)
    return Listing(title="Vintage mug", description="Nice.", price=12.0,
                   quantity=1, images=["a.jpg"], etsy=fields)


def _errors(issues, level="error"):
    return {i["target"] for i in issues if i["level"] == level}


def test_the_payload_says_physical_and_names_the_profile():
    payload = mapping_etsy.build_listing_payload(
        _listing(readiness_state_id="42"), {"return_policy_id": "5"})
    assert payload["type"] == "physical"
    assert payload["readiness_state_id"] == 42
    assert payload["return_policy_id"] == 5


def test_the_account_default_fills_in_and_the_listing_wins():
    payload = mapping_etsy.build_listing_payload(
        _listing(), {"readiness_state_id": "9"})
    assert payload["readiness_state_id"] == 9
    override = mapping_etsy.build_listing_payload(
        _listing(readiness_state_id="42"), {"readiness_state_id": "9"})
    assert override["readiness_state_id"] == 42


def test_a_missing_profile_is_an_error_in_every_mode():
    """createDraftListing itself refuses without one, so a draft cannot
    leave it for later the way it can a return policy."""
    for mode in ("draft", "live", "revise"):
        issues = mapping_etsy.preflight(_listing(), {}, mode)
        assert "etsy_readiness_state" in _errors(issues), mode
    ok = mapping_etsy.preflight(_listing(readiness_state_id="42"),
                                {"return_policy_id": "5"}, "live")
    assert "etsy_readiness_state" not in _errors(ok)


def test_the_return_policy_blocks_a_live_publish_and_warns_a_draft():
    settings = {"readiness_state_id": "9"}
    assert "etsy_return_policy" in _errors(
        mapping_etsy.preflight(_listing(), settings, "live"))
    assert "etsy_return_policy" in _errors(
        mapping_etsy.preflight(_listing(), settings, "revise"))
    draft = mapping_etsy.preflight(_listing(), settings, "draft")
    assert "etsy_return_policy" not in _errors(draft)
    assert "etsy_return_policy" in _errors(draft, level="warn")


def test_an_id_that_is_not_a_number_is_treated_as_missing_not_a_crash():
    """int("abc") used to escape the provider's try block: the Settings
    route stored whatever the client posted, and the publish crashed with a
    Python sentence. Now it reads as no profile, and the checklist says so."""
    listing = _listing(shipping_profile_id="abc", readiness_state_id="4 2")
    payload = mapping_etsy.build_listing_payload(listing, {})
    assert "shipping_profile_id" not in payload
    assert "readiness_state_id" not in payload
    targets = _errors(mapping_etsy.preflight(listing, {}, "live"))
    assert {"etsy_shipping_profile", "etsy_readiness_state"} <= targets


def test_a_price_in_another_currency_than_the_shop_is_a_warning():
    issues = mapping_etsy.preflight(
        _listing(readiness_state_id="1", return_policy_id="1"),
        {"currency_code": "GBP"}, "live")
    warn = [i for i in issues if i["level"] == "warn" and i["target"] == "price"]
    assert warn and "GBP" in warn[0]["title"]
    assert not _errors(issues)


def test_a_variation_listing_is_refused_for_etsy():
    listing = _listing(readiness_state_id="1", return_policy_id="1")
    listing.has_variations = True
    assert "variations" in _errors(mapping_etsy.preflight(listing, {}, "live"))


def test_something_someone_else_made_recently_needs_a_partner():
    """Etsy's rule, in the app's words: not vintage, not a supply, not made
    by the seller — a production-partner listing, which is Etsy's polite
    name for resale. The issue points at the answers that would allow it."""
    settings = {"readiness_state_id": "1", "return_policy_id": "1"}
    recent = _listing(who_made="someone_else", when_made="2010_2019")
    issues = mapping_etsy.preflight(recent, settings, "live")
    partner = [i for i in issues if "production partner" in i["title"]]
    assert partner and partner[0]["target"] == "etsy_attribution"
    for allowed in (dict(who_made="someone_else", when_made="1990s"),
                    dict(who_made="someone_else", when_made="2010_2019", is_supply=True),
                    dict(who_made="i_did", when_made="2010_2019")):
        assert not [i for i in mapping_etsy.preflight(_listing(**allowed), settings, "live")
                    if "production partner" in i["title"]], allowed


def test_vintage_is_computed_against_the_calendar_not_a_list():
    assert mapping_etsy.is_vintage("1990s", year=2026)
    assert mapping_etsy.is_vintage("2000_2006", year=2026)
    assert mapping_etsy.is_vintage("before_2007", year=2026)
    assert not mapping_etsy.is_vintage("2007_2009", year=2026)
    assert mapping_etsy.is_vintage("2007_2009", year=2029)
    assert not mapping_etsy.is_vintage("made_to_order", year=2026)
    assert not mapping_etsy.is_vintage("", year=2026)
    assert mapping_etsy.when_made_latest_year("before_1700") == 1699
