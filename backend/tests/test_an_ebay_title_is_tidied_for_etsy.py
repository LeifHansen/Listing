"""An eBay title is written to eBay's rules, and Etsy has its own.

Etsy refuses $ ^ and `, allows % : & and + once each, wants the first
character to be a letter or a number, and caps the words in capitals — and
"NIKE AIR MAX 90 VTG 90s USA $$ MINT" is what a resale title looks like. The
payload tidies the title to Etsy's rules; the preflight shows the seller
what Etsy will get, as a warning they can edit past rather than a stop.
"""
from backend.marketplaces import mapping_etsy
from backend.models import Listing

clean = mapping_etsy.clean_title


def test_the_banned_characters_come_off():
    assert clean("Mug $12 ^ `rare`") == "Mug 12 rare"


def test_the_once_only_characters_keep_their_first_use():
    assert clean("A & B & C: one: two + three + four") == "A & B C: one two + three four"


def test_it_starts_with_a_letter_or_a_number():
    assert clean("*** Vintage mug") == "Vintage mug"
    assert clean("~ 90s tee") == "90s tee"


def test_capitals_past_the_limit_lose_their_shouting():
    tidy = clean("NIKE AIR MAX 90 VTG LEVI'S NWT")
    assert tidy == "NIKE AIR MAX 90 Vtg Levi's Nwt"


def test_a_clean_title_is_left_alone_and_the_tidying_is_idempotent():
    title = "Vintage 1990s Levi's 501 Jeans 32x30"
    assert clean(title) == title
    messy = "$$ NIKE AIR MAX 90 VTG LEVI'S NWT & rare & mint"
    assert clean(clean(messy)) == clean(messy)


def test_nothing_usable_is_an_empty_title():
    assert clean("$^`") == ""
    assert clean("***") == ""


def _listing(title):
    return Listing(title=title, description="Nice.", price=12.0, quantity=1,
                   images=["a.jpg"],
                   etsy={"taxonomy_id": 1, "who_made": "someone_else",
                         "when_made": "1990s", "shipping_profile_id": "7",
                         "return_policy_id": "8", "readiness_state_id": "9"})


def test_the_payload_carries_the_tidied_title():
    payload = mapping_etsy.build_listing_payload(_listing("$$ NIKE AIR MAX 90 VTG LEVI'S"), {})
    assert payload["title"] == "NIKE AIR MAX 90 Vtg Levi's"


def test_the_preflight_warns_with_the_tidied_title_and_stops_on_an_empty_one():
    warned = mapping_etsy.preflight(_listing("$$ NIKE AIR MAX 90 VTG LEVI'S"), {})
    warn = [i for i in warned if i["target"] == "title"]
    assert warn and warn[0]["level"] == "warn"
    assert "NIKE AIR MAX 90 Vtg Levi's" in warn[0]["fix"]
    stopped = mapping_etsy.preflight(_listing("$^`"), {})
    assert [i for i in stopped if i["target"] == "title" and i["level"] == "error"]
    clean_run = mapping_etsy.preflight(_listing("Vintage mug"), {})
    assert not [i for i in clean_run if i["target"] == "title"]


def test_an_accented_tag_keeps_its_letters():
    listing = _listing("Mug")
    listing.etsy.tags = ["Café au lait", "crème brûlée"]
    assert mapping_etsy.build_tags(listing)[:2] == ["Cafe au lait", "creme brulee"]
