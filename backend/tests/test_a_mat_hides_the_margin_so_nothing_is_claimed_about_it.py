"""A print under a mat is not unsigned. It is a print nobody has looked at.

The lower margin of a print is where the pencil signature and the edition
fraction are, and those two marks are most of what separates a $15 poster from
a $1,500 hand-signed limited edition. A MAT COVERS THAT MARGIN. So does a
frame. Under either, the marks are not absent -- they are HIDDEN, and the
difference between those two words is the difference between two listings at
very different prices.

ART_RULE has always said the right thing about this: a mark that is not in the
photos is never claimed, and never DENIED either, because "unsigned" about a
margin under a mat is the same false claim in the cheaper direction -- and the
cheaper direction is the one that costs the seller the piece rather than
costing them a return.

But it said it to the MODEL. The only thing the server knew was whether the
model had happened to use the words "not visible", matched by a regex on its
phrasing (_NOT_VISIBLE_RE). A model that instead wrote "unsigned" into a
description was not something any later pass could retract.

A mat is different in kind from a phrasing. It is objective evidence, visible
in any whole-frame photo, that the margin CANNOT have been read. So the
presentation is now a structured field with a tri-state -- matted true, false,
or nobody knows -- and the caveat is derived from the fact rather than hoped
for from the wording.

The tri-state is the whole design. A bool cannot hold the difference between
"this print is unmatted" and "I could not see whether it is matted", and on
art that difference is the entire game.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import Presentation  # noqa: E402
from backend.services.experts.art import presentation as art_presentation  # noqa: E402


# --- the tri-state, which is the point --------------------------------------

def test_unknown_is_the_default_and_is_not_false():
    """None means "nobody established this". False means "there is no mat".
    Collapsing the two is how a listing comes to say "unsigned" about a
    margin nobody has seen."""
    blank = Presentation()
    assert blank.matted is None
    assert blank.margin_visible is None
    assert blank.matted is not False
    assert blank.margin_visible is not False


@pytest.mark.parametrize("answer", ["", "unknown", "not sure", "maybe", None,
                                    "cannot tell", "n/a"])
def test_anything_that_is_not_a_plain_yes_or_no_is_unknown(answer):
    """A hedge, a blank, a sentence, a missing key -- all None, never False.
    False is a claim, and this pass is not entitled to make it."""
    assert art_presentation._tristate(answer) is None


def test_a_plain_yes_and_a_plain_no_are_read():
    assert art_presentation._tristate("yes") is True
    assert art_presentation._tristate("no") is False


# --- what counts as covering the margin ------------------------------------

def test_a_mat_covers_the_margin():
    assert Presentation(mount="matted", matted=True).hides_the_margin()


def test_a_frame_covers_the_margin_even_when_no_mat_was_reported():
    """A frame's rebate covers the sheet's edge whether or not there is a mat
    behind the glass, and framed art nearly always has one."""
    assert art_presentation.from_lookup(
        {"presentation": "framed"}).hides_the_margin()


def test_a_bare_sheet_does_not():
    assert not Presentation(mount="loose_sheet", matted=False,
                            margin_visible=True).hides_the_margin()


def test_a_rolled_poster_does_not():
    assert not art_presentation.from_lookup(
        {"presentation": "rolled"}).hides_the_margin()


def test_shrink_wrap_does_not_because_film_is_transparent():
    """The margin reads straight through it. A caveat attached here would be
    a caveat attached to something nobody needs to act on."""
    assert not art_presentation.from_lookup(
        {"presentation": "shrink_wrapped"}).hides_the_margin()


def test_a_piece_nobody_has_looked_at_does_not_raise_the_caveat():
    """The asymmetry that keeps this useful. This question exists to ADD a
    warning, so it answers on positive evidence only -- a caveat raised on
    ignorance would be attached to every listing in the app, and a warning
    that is always there is a warning nobody reads."""
    assert not Presentation().hides_the_margin()
    assert not Presentation(mount="").hides_the_margin()


def test_a_lookup_that_looked_and_saw_the_margin_is_believed_over_the_mount():
    """Inference runs one way only. "The mount usually covers it" must not
    overrule "I can see it" -- the lookup has looked and the inference has
    not."""
    seen = art_presentation.from_lookup(
        {"presentation": "framed", "margin_visible": "yes"})
    assert seen.margin_visible is True
    assert not seen.hides_the_margin()


# --- and what the seller is told --------------------------------------------

def test_the_caveat_says_the_piece_is_not_unsigned_but_unseen():
    """The sentence that carries the whole point, in the seller's hands."""
    ask = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "matted"}))[0]
    assert "not unsigned and not an open edition" in ask
    assert "has not been seen" in ask


def test_the_caveat_is_a_physical_instruction_not_a_wish():
    """"The signature could not be seen" changes nothing. "Lift the mat at
    both lower corners" is something a person can go and do."""
    ask = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "matted"}))[0]
    assert "Lift the mat" in ask
    assert "photograph them close up" in ask


def test_the_caveat_names_what_is_actually_in_the_way():
    """A seller told to "lift the mat" on a frame with no mat reads the
    caveat as boilerplate -- and then skims the next one, which might have
    been the one that mattered."""
    framed = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "framed"}))[0]
    assert "Take the piece out of the frame" in framed
    floated = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "float_mounted"}))[0]
    assert "Lift the sheet" in floated


def test_glass_and_acrylic_are_asked_about_rather_than_guessed():
    """They look alike in a photo and ship completely differently: glass is a
    fragile, oversized, double-boxed parcel and acrylic is not. A guess either
    way is a broken picture or a wasted box, so the seller gets the tap
    test."""
    asks = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "framed"}))
    glazing = [a for a in asks if "acrylic" in a]
    assert glazing, "a framed piece with no glazing answer must ask"
    assert "glass rings" in glazing[0] and "acrylic thuds" in glazing[0]


def test_a_glazing_that_was_established_is_not_asked_about_again():
    asks = art_presentation.cautions(
        art_presentation.from_lookup({"presentation": "framed",
                                      "glazing": "acrylic"}))
    assert not [a for a in asks if "glass rings" in a]


# --- the shape of the field --------------------------------------------------

def test_a_mount_nobody_recognises_is_dropped_rather_than_guessed_at():
    """The presentation is read by the shipping estimate. A mount nobody
    recognises must not become one that changes a parcel -- and a lookup
    whose ONLY answer was unrecognisable leaves no presentation at all."""
    assert art_presentation.from_lookup({"presentation": "hanging on a nail"}) is None
    # ...and where something else was established, the bad mount alone is
    # dropped rather than taking the rest of the answer down with it.
    kept = art_presentation.from_lookup(
        {"presentation": "hanging on a nail", "glazing": "glass"})
    assert kept is not None and kept.mount == "" and kept.glazing == "glass"


def test_an_empty_lookup_produces_no_presentation_at_all():
    """All-blanks would be indistinguishable from a field nobody filled in,
    and would put an empty card in the editor for every listing in the app."""
    assert art_presentation.from_lookup({}) is None
    assert art_presentation.from_lookup({"presentation": "", "glazing": ""}) is None


def test_an_outer_size_is_read_and_an_implausible_one_is_not():
    """The size feeds a shipping box, so a misread is a parcel that does not
    fit. A picture measured in feet or millimetres is a misread."""
    assert art_presentation._inches("24 x 18 in") == (24.0, 18.0)
    assert art_presentation._inches("24 × 18") == (24.0, 18.0)
    assert art_presentation._inches("610 x 457 mm") == (0.0, 0.0)
    assert art_presentation._inches("about a foot") == (0.0, 0.0)
    assert art_presentation._inches("") == (0.0, 0.0)


# --- end to end, through the merge -----------------------------------------

def test_a_matted_print_is_never_called_unsigned_and_is_asked_about_once():
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Vintage Art Print Landscape",
                      category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Marc Chagall", "work": "Le Bouquet", "confidence": "high",
        "signature": "not visible in these photos",
        "edition": "not visible in these photos",
        "presentation": "matted", "margin_visible": "no", "matted": "yes",
        "title": "Marc Chagall Le Bouquet Lithograph",
    })
    # Where a CLAIM would live: the title a buyer reads and the specifics
    # eBay publishes. The caveat in missing_info is excluded deliberately --
    # it is allowed to use the words, and indeed has to ("this piece is not
    # unsigned and not an open edition"), because explaining the distinction
    # is its whole job.
    claimed = " ".join([listing.title,
                        *(f"{s.name} {s.value}" for s in listing.item_specifics)
                        ]).lower()
    for denial in ("unsigned", "open edition", "unnumbered", "reproduction"):
        assert denial not in claimed, f"the draft claims {denial!r}: {claimed}"
    # ...and nothing claimed in the expensive one either.
    assert not [s for s in listing.item_specifics
                if s.name.strip().lower() in ("signed", "edition type")]
    # The mat is recorded, and the seller is asked ONCE -- the specific ask
    # replaces the generic one rather than joining it.
    assert listing.presentation.mount == "matted"
    margin_asks = [m for m in listing.missing_info
                   if "margin" in m.lower() and "signature" in m.lower()]
    assert len(margin_asks) == 1, margin_asks
    assert "Lift the mat" in margin_asks[0]


def test_a_visible_margin_still_gets_the_ordinary_ask():
    """The dedupe must not swallow the generic ask on a piece with nothing
    covering it -- there the marks really were looked for and not found."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Vintage Art Print",
                      category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Marc Chagall", "confidence": "high",
        "signature": "not visible in these photos",
        "edition": "not visible in these photos",
        "presentation": "loose_sheet", "margin_visible": "yes", "matted": "no",
    })
    assert [m for m in listing.missing_info if m.startswith("Verify: the")]


def test_the_framing_specific_is_answered_because_buyers_filter_on_it():
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Art Print", category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Marc Chagall", "confidence": "high",
        "presentation": "framed", "glazing": "glass", "frame": "gilt wood",
    })
    named = {s.name: s.value for s in listing.item_specifics}
    assert named.get("Framing") == "Framed"
    assert named.get("Frame Material") == "gilt wood"
    assert listing.title.endswith("Framed")


# --- the other consequence: what the parcel has to be -----------------------
#
# A framed picture behind glass is the worst thing in resale to post -- heavy,
# rigid, oversized, and when the glass breaks it cuts through the artwork on
# the way. A seller who ships one at the unframed weight in a flat mailer
# loses the item, the postage and the sale at once.
#
# What this does NOT do is write package dimensions. The seller's own defaults
# fill those (_apply_prefs), and a frame size estimated from a photograph is
# not something to turn into a parcel on the seller's behalf: a box that is
# wrong costs them a real shipment, and they are holding the thing and the
# server is not. So it is advice, addressed to the person who can measure it.

def test_glass_gets_the_packing_advice_an_experienced_seller_already_has():
    advice = art_presentation.cautions(art_presentation.from_lookup(
        {"presentation": "framed", "glazing": "glass"}))
    packing = [a for a in advice if "double-box" in a]
    assert packing, advice
    assert "Tape the glass in an X" in packing[0]
    assert "corner-protect" in packing[0]
    # ...and the cheaper answer, which most sellers do not know is acceptable.
    assert "glass removed for safe shipping" in packing[0]


def test_a_known_frame_size_becomes_a_box_size():
    advice = " ".join(art_presentation.cautions(art_presentation.from_lookup(
        {"presentation": "framed", "glazing": "glass",
         "outer_size": "24 x 18 in"})))
    assert "about 24 x 18 in over the frame" in advice
    assert "at least 30 x 24 in" in advice


def test_a_parcel_no_carrier_will_take_is_flagged_before_it_sells():
    """Past 108 inches of length plus girth the usual services stop carrying
    it at any price -- which a seller discovers at the counter, having already
    sold it at a flat rate."""
    advice = art_presentation.cautions(art_presentation.from_lookup(
        {"presentation": "framed", "glazing": "glass",
         "outer_size": "48 x 36 in"}))
    oversize = [a for a in advice if "108" in a]
    assert oversize, advice
    assert "before you list it, not after it sells" in oversize[0]


def test_an_ordinary_framed_print_is_not_told_it_is_oversized():
    advice = art_presentation.cautions(art_presentation.from_lookup(
        {"presentation": "framed", "glazing": "glass",
         "outer_size": "16 x 12 in"}))
    assert not [a for a in advice if "108" in a]


def test_acrylic_gets_no_glass_lecture():
    """Acrylic is none of the things glass is, and a warning attached to
    everything is a warning nobody reads."""
    advice = " ".join(art_presentation.cautions(art_presentation.from_lookup(
        {"presentation": "framed", "glazing": "acrylic"})))
    assert "double-box" not in advice
    assert "Tape the glass" not in advice


def test_the_presentation_never_writes_the_package_itself():
    """The seller's defaults own those fields. A frame size estimated off a
    photograph must not become a parcel on their behalf."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Art Print", category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Marc Chagall", "confidence": "high",
        "presentation": "framed", "glazing": "glass", "outer_size": "24 x 18 in",
    })
    assert listing.package_length_in == 0.0
    assert listing.package_width_in == 0.0
    assert listing.package_weight_lb == 0.0
    # ...but the seller was told, which is the part that helps them.
    assert [m for m in listing.missing_info if "double-box" in m]
