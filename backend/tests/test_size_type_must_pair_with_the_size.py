"""The pairing eBay already published, applied instead of guessed.

The defect: a men's jeans draft went out with Size "33" and Size Type
"Regular", and eBay answered

    "Regular" is not a valid Size Type for the Size "33". Select a compatible
    Size Type and Size combination.

— after the photos had uploaded, with no listing. eBay does not treat the two
as independent boxes: it publishes, per Size value, the Size Types that value
may be paired with, in the same aspect list this app already fetches to know
which specifics are required. The app dropped that half of the answer on the
way in and then guessed at it.

So what these pin down is that the guess is gone: the pairing comes from
eBay's data, a blank is filled from it, a contradicted answer is corrected to
it, and where eBay named no constraint — or named several possibilities over
an answer the seller already gave — nothing is touched.
"""
from backend.models import ItemSpecific, Listing
from backend.services import taxonomy


def aspect(name, values, pairs=None, required=False):
    return {"name": name, "required": required, "mode": "SELECTION_ONLY",
            "values": values, "cardinality": "SINGLE", "data_type": "STRING",
            "format": "", "max_length": 0, "pairs_with": pairs or {}}


# Men's jeans, the shape eBay actually returns it in: the Size values carry
# the constraint, naming Size Type as the aspect they control.
MENS_JEANS = [
    aspect("Size Type", ["Regular", "Big & Tall"], required=True),
    aspect("Size", ["33", "34", "46"], pairs={
        "33": {"Size Type": ["Regular"]},
        "34": {"Size Type": ["Regular"]},
        "46": {"Size Type": ["Big & Tall"]},
    }),
]


def draft(specifics, **over):
    return Listing(title="Levis 510 Skinny Jeans", category_id="11483",
                   item_specifics=[ItemSpecific(name=n, value=v)
                                   for n, v in specifics], **over)


def value(listing, name):
    return next((s.value for s in listing.item_specifics
                 if s.name.lower() == name.lower()), None)


class TestTheSizeDecidesTheSizeType:
    def test_a_missing_size_type_is_answered_from_the_size(self):
        # The blocker the seller actually hit: required, unfilled, and eBay
        # had already said which one goes with a 33.
        listing = draft([("Size", "33")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS)
        assert value(listing, "Size Type") == "Regular"

    def test_a_big_size_gets_the_size_type_that_size_takes(self):
        listing = draft([("Size", "46")])
        taxonomy.fit_paired_aspects(listing, MENS_JEANS)
        assert value(listing, "Size Type") == "Big & Tall"

    def test_a_contradicted_size_type_is_corrected(self):
        # Exactly the rejected combination, rescued rather than resent.
        listing = draft([("Size", "46"), ("Size Type", "Regular")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS) == [
            ("Size Type", "Regular", "Big & Tall")]
        assert value(listing, "Size Type") == "Big & Tall"

    def test_a_blank_row_is_filled_in_place(self):
        # An aspect can own several rows and a blank one can sit in front of
        # the answer; filling the blank must not leave two Size Types.
        listing = draft([("Size", "33"), ("Size Type", "")])
        taxonomy.fit_paired_aspects(listing, MENS_JEANS)
        rows = [s for s in listing.item_specifics if s.name == "Size Type"]
        assert [s.value for s in rows] == ["Regular"]

    def test_a_compatible_answer_is_left_alone(self):
        listing = draft([("Size", "33"), ("Size Type", "Regular")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS) == []

    def test_case_and_spacing_still_count_as_compatible(self):
        listing = draft([("Size", "33"), ("Size Type", "regular")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS) == []


class TestItNeverGuesses:
    def test_an_unconstrained_aspect_is_untouched(self):
        # Most aspects carry no pairing at all; "not constrained" must read as
        # "anything is legal", never as "nothing is".
        listing = draft([("Color", "Blue")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS) == []
        assert value(listing, "Size Type") is None

    def test_an_answer_the_seller_gave_survives_an_ambiguous_pairing(self):
        # Two Size Types are legal beside this size: the size does not decide
        # it, and overwriting the seller would be the same guess pointing the
        # other way.
        aspects = [aspect("Size Type", ["Regular", "Big & Tall"]),
                   aspect("Size", ["XL"],
                          pairs={"XL": {"Size Type": ["Regular", "Big & Tall"]}})]
        listing = draft([("Size", "XL"), ("Size Type", "Big & Tall")])
        assert taxonomy.fit_paired_aspects(listing, aspects) == []
        assert value(listing, "Size Type") == "Big & Tall"

    def test_an_empty_size_says_nothing_about_the_size_type(self):
        listing = draft([("Size", ""), ("Size Type", "")])
        assert taxonomy.fit_paired_aspects(listing, MENS_JEANS) == []

    def test_no_aspect_list_changes_nothing(self):
        listing = draft([("Size", "33")])
        assert taxonomy.fit_paired_aspects(listing, []) == []
        assert value(listing, "Size Type") is None


class TestReadingEbaysAnswer:
    def test_value_constraints_are_kept_off_the_aspect_values(self):
        parsed = taxonomy._value_constraints([
            {"localizedValue": "33", "valueConstraints": [
                {"applicableForLocalizedAspectName": "Size Type",
                 "applicableForLocalizedAspectValues": ["Regular"]}]},
            {"localizedValue": "46", "valueConstraints": [
                {"applicableForLocalizedAspectName": "Size Type",
                 "applicableForLocalizedAspectValues": ["Big & Tall"]}]},
            {"localizedValue": "Loose"},
        ])
        assert parsed == {"33": {"Size Type": ["Regular"]},
                          "46": {"Size Type": ["Big & Tall"]}}

    def test_a_value_with_no_constraint_carries_no_entry(self):
        assert taxonomy._value_constraints(
            [{"localizedValue": "Loose", "valueConstraints": []}]) == {}

    def test_compatible_values_answers_for_one_aspect_value(self):
        assert taxonomy.compatible_values(MENS_JEANS, "Size", "46") == {
            "Size Type": ["Big & Tall"]}
        assert taxonomy.compatible_values(MENS_JEANS, "Size", "unheard-of") == {}
